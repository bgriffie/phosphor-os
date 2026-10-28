"""Chore alerts for the lights relay: buzzes the iPhone's lock screen when a chore comes due.

iPhone web apps can't schedule their own notifications, so this PC does it with Apple's web push
for home-screen apps (iOS 16.4 or later). Phosphor OS sends its chore list here whenever it changes;
at each chore's time this sends one alert per chore per day to every phone that turned alerts on.

    GET  /chores            -> {"version", "ready": pywebpush installed, "key": VAPID public key, "devices": n, "chores": n}
    POST /chores            {"chores": [...]}           the Chores app's list (same shape as its localStorage)
    POST /chores/subscribe  {"subscription": {...}}     a phone's PushSubscription.toJSON()
    POST /chores/unsubscribe {"endpoint": "..."}
    POST /chores/test       -> {"sent": n}             a test alert to every phone

Needs: py -m pip install pywebpush
Loaded by kasa_bridge.py like party.py (drop this file next to it). Keys and the list are kept in
chores.json next to this file.
"""
import asyncio, base64, datetime as dt, json, time
from pathlib import Path

from aiohttp import web

VERSION = '1.0'
HERE = Path(__file__).resolve().parent
STORE = HERE / 'chores.json'
PEM = HERE / 'chores_vapid.pem'
SUB = 'https://bgriffie.github.io'   # the VAPID contact: a mailto: or a bare https origin
ANY_TIME = '09:00'   # chores without a time buzz at this time

try:
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    from cryptography.hazmat.primitives import serialization
except ImportError:
    webpush = None


def load():
    try:
        d = json.loads(STORE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(d):
    tmp = STORE.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(STORE)


def vapid_key():
    """This relay's push key pair, made once. Returns the public key the phone subscribes with."""
    if not PEM.exists():
        v = Vapid()
        v.generate_keys()
        v.save_key(str(PEM))
    v = Vapid.from_file(str(PEM))
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


# ---- when a chore falls: the same rules as the app's src/due.js ----
def weeks_since(d):   # weeks since a fixed Sunday, counting Sunday-to-Saturday weeks
    sunday = d - dt.timedelta(days=(d.weekday() + 1) % 7)
    return round((sunday - dt.date(2024, 1, 7)).days / 7)


def occurs_on(c, d):
    if c.get('paused'):
        return False
    start = c.get('start')
    if start and d.isoformat() < start:
        return False
    every = max(1, int(c.get('every') or 1))
    rep = c.get('rep', 'week')
    if rep == 'day':
        s = dt.date.fromisoformat(start) if start else d
        return every == 1 or (d - s).days % every == 0
    if rep == 'month':
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        last = (nxt - dt.timedelta(days=1)).day
        return d.day == min(int(c.get('dom') or 1), last)
    if (d.weekday() + 1) % 7 not in (c.get('days') or []):   # the app counts Sunday = 0
        return False
    s = dt.date.fromisoformat(start) if start else d
    return every == 1 or (weeks_since(d) - weeks_since(s)) % every == 0


def done_for(c, day):
    return any(x.get('for') == day for x in c.get('done') or [])


def setup(app, demo=False):
    state = load()
    state.setdefault('subs', [])
    state.setdefault('chores', [])
    state.setdefault('sent', {})   # chore id -> the day it last buzzed for
    key = vapid_key() if webpush else None

    def send_one(sub, payload):
        try:
            webpush(subscription_info=sub, data=json.dumps(payload), vapid_private_key=str(PEM),
                    vapid_claims={'sub': SUB}, ttl=6 * 3600, timeout=15)
            return 'ok'
        except WebPushException as e:
            code = getattr(e.response, 'status_code', 0)
            if code in (404, 410):
                return 'gone'   # the phone turned alerts off or the app was removed
            print('  Chore alert:', code or '', str(e)[:200])
            return 'error'
        except Exception as e:
            print('  Chore alert:', str(e)[:200])
            return 'error'

    async def send_all(payload):
        if not webpush:
            return 0
        loop = asyncio.get_running_loop()
        sent, keep = 0, []
        for sub in list(state['subs']):
            r = await loop.run_in_executor(None, send_one, sub, payload)
            if r != 'gone':
                keep.append(sub)
            sent += r == 'ok'
        if len(keep) != len(state['subs']):
            state['subs'] = keep
            save(state)
        return sent

    async def info(request):
        return web.json_response({'version': VERSION, 'ready': webpush is not None, 'key': key,
                                  'devices': len(state['subs']), 'chores': len(state['chores'])})

    async def put_chores(request):
        body = await request.json()
        chores = body.get('chores')
        if not isinstance(chores, list):
            raise web.HTTPBadRequest(text='Send {"chores": [...]}.')
        state['chores'] = [c for c in chores if isinstance(c, dict) and c.get('id')][:100]
        save(state)
        return web.json_response({'ok': True, 'chores': len(state['chores'])})

    async def subscribe(request):
        if not webpush:
            raise web.HTTPServiceUnavailable(text='The PC needs pywebpush: py -m pip install pywebpush, then restart the relay.')
        sub = (await request.json()).get('subscription') or {}
        if not str(sub.get('endpoint', '')).startswith('https://') or not (sub.get('keys') or {}).get('auth'):
            raise web.HTTPBadRequest(text='Not a push subscription.')
        state['subs'] = [s for s in state['subs'] if s.get('endpoint') != sub['endpoint']] + [sub]
        save(state)
        return web.json_response({'ok': True, 'devices': len(state['subs'])})

    async def unsubscribe(request):
        ep = (await request.json()).get('endpoint')
        state['subs'] = [s for s in state['subs'] if s.get('endpoint') != ep]
        save(state)
        return web.json_response({'ok': True, 'devices': len(state['subs'])})

    async def test(request):
        if not webpush:
            raise web.HTTPServiceUnavailable(text='The PC needs pywebpush: py -m pip install pywebpush, then restart the relay.')
        n = await send_all({'title': 'Chores', 'body': 'Alerts are on. You\'ll get one when a chore is due.', 'tag': 'chores-test'})
        return web.json_response({'ok': True, 'sent': n})

    async def watch():
        """Every 30 seconds, by this PC's clock: buzz for any chore whose time today has come."""
        while True:
            try:
                now = dt.datetime.now()
                today = now.date().isoformat()
                for c in state['chores']:
                    if state['sent'].get(c['id']) == today or done_for(c, today) or not occurs_on(c, now.date()):
                        continue
                    h, m = (c.get('at') or ANY_TIME).split(':')
                    if now < now.replace(hour=int(h), minute=int(m), second=0, microsecond=0):
                        continue
                    state['sent'][c['id']] = today
                    save(state)
                    if state['subs']:
                        n = await send_all({'title': str(c.get('name', 'Chore'))[:60], 'body': 'Due today. Tap to tick it off.',
                                            'tag': 'chore-' + c['id'], 'url': './#/app/chores'})
                        print(f"  Chore alert: {c.get('name')} -> {n} phone(s)")
            except Exception as e:
                print('  Chore alerts:', e)
            await asyncio.sleep(30)

    async def start(app):
        app['chores_watch'] = asyncio.create_task(watch())

    async def stop(app):
        app['chores_watch'].cancel()

    app.add_routes([web.get('/chores', info), web.post('/chores', put_chores), web.post('/chores/subscribe', subscribe),
                    web.post('/chores/unsubscribe', unsubscribe), web.post('/chores/test', test)])
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    print(f'Chore alerts: on (v{VERSION}).' if webpush else 'Chore alerts: off until you run  py -m pip install pywebpush')
