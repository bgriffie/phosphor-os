"""Phosphor TV for the Phosphor OS relay: puts a Phosphor screen (clock, lights, message board,
Snake, rain) on a Chromecast and lets the phone's TV app drive it like a remote.

Drop this file next to kasa_bridge.py, install one library, and start the relay with --cast:

    py -m pip install pychromecast
    py kasa_bridge.py --cast

Without --cast the TV stays off and nothing extra listens. Delete the file to remove it entirely.

How it works: the relay finds the Chromecast on the Wi-Fi and tells it to open the free DashCast
receiver, pointed at a small web server this file runs on the home network (port 7790). That
server only hands the Chromecast the TV page (fetched from bgriffie.github.io/phosphor-os/tv/ and
kept in tv_page.html) and one WebSocket. The phone never talks to the TV: it calls /tv/* on the
relay (127.0.0.1:7788, behind the relay key, through Tailscale) and the relay passes things on.

Security (agreed with Floyd's security review):
- Port 7790 is a separate aiohttp app with exactly two routes, / and /ws. No /pc, no /lights,
  no files or folders. It binds to this PC's home-network address, not Tailscale.
- It answers only with a TV token made fresh each time the relay starts and checked with
  compare_digest. The relay's own key never goes over 7790, which is plain HTTP.
- Control goes one way. The relay decides what the TV shows; the TV's WebSocket may only report
  status (which screen, which item is selected) and everything else it sends is ignored.
- Windows asks once whether Python may use the network: tick Private networks only.
"""
import asyncio, json, os, secrets, socket, time
from pathlib import Path

from aiohttp import web, WSMsgType, ClientSession, ClientTimeout

TV_PORT = 7790
PAGE_URL = 'https://bgriffie.github.io/phosphor-os/tv/'
HERE = Path(__file__).resolve().parent
CONFIG = HERE / 'tv_control.json'
PAGE_CACHE = HERE / 'tv_page.html'
PAGE_MAX_AGE = 600         # seconds before the TV page is fetched again
SCREENS = ('home', 'clock', 'lights', 'board', 'snake', 'rain')
KEYS = ('up', 'down', 'left', 'right', 'ok', 'back', 'home')
DASHCAST = '84912283'


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(c):
    CONFIG.write_text(json.dumps(c, indent=2))


def lan_ip():
    """This PC's address on the home network (the one its default route uses). No packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 9))
        ip = s.getsockname()[0]
        return None if ip.startswith(('127.', '0.', '100.')) else ip   # 100.x is Tailscale
    except OSError:
        return None
    finally:
        s.close()


# ---------- Chromecasts ----------

class RealCasts:
    """Chromecasts on the Wi-Fi, through pychromecast. Every call here blocks: run it in a thread."""
    fake = False

    def __init__(self):
        import pychromecast, zeroconf
        self.pc = pychromecast
        self.zc = zeroconf.Zeroconf()
        self.browser = pychromecast.CastBrowser(pychromecast.SimpleCastListener(), self.zc)
        self.browser.start_discovery()
        self.objs = {}

    def devices(self):
        out = []
        for uuid, info in list(self.browser.devices.items()):
            if info.cast_type == 'group':
                continue
            out.append({'id': str(uuid), 'name': info.friendly_name or info.host, 'model': info.model_name or '',
                        'video': info.cast_type != 'audio'})
        return sorted(out, key=lambda d: d['name'].lower())

    def get(self, cid):
        cc = self.objs.get(cid)
        if cc is None:
            info = next((i for u, i in self.browser.devices.items() if str(u) == cid), None)
            if info is None:
                raise web.HTTPNotFound(text='That Chromecast isn\'t on the Wi-Fi right now. Is it plugged in?')
            cc = self.objs[cid] = self.pc.get_chromecast_from_cast_info(info, self.zc, tries=2, timeout=8)
        cc.wait(timeout=10)
        if not cc.status:
            raise web.HTTPGatewayTimeout(text='The Chromecast didn\'t answer.')
        return cc

    def status(self, cid):
        cc = self.get(cid)
        st = cc.status
        d = {'app': st.display_name or '', 'appId': st.app_id or '', 'idle': cc.is_idle,
             'volume': round((st.volume_level or 0) * 100), 'muted': bool(st.volume_muted), 'media': None}
        if not cc.is_idle and st.app_id != DASHCAST:
            m = cc.media_controller.status
            if m and m.player_state not in ('UNKNOWN', 'IDLE'):
                d['media'] = {'title': m.title or '', 'sub': m.artist or m.series_title or m.album_name or '',
                              'state': m.player_state}
        return d

    def start(self, cid, url):
        from pychromecast.controllers.dashcast import DashCastController
        cc = self.get(cid)
        dc = DashCastController()
        cc.register_handler(dc)
        # force: the TV page is plain http on the home network, so it must be the whole page, not a frame
        dc.load_url(url, force=True)

    def stop(self, cid):
        self.get(cid).quit_app()

    def volume(self, cid, body):
        cc = self.get(cid)
        if 'mute' in body:
            cc.set_volume_muted(bool(body['mute']))
        if 'level' in body:
            cc.set_volume(max(0, min(100, int(body['level']))) / 100)
        elif 'delta' in body:
            cc.set_volume(max(0.0, min(1.0, (cc.status.volume_level or 0) + int(body['delta']) / 100)))

    def media(self, cid, cmd):
        mc = self.get(cid).media_controller
        {'play': mc.play, 'pause': mc.pause, 'stop': mc.stop}[cmd]()


class FakeCasts:
    """A pretend Chromecast for --demo. Starting it only flips its status: open the printed
    demo screen link in a browser to see the TV."""
    fake = True

    def __init__(self):
        self.st = {'app': '', 'appId': '', 'idle': True, 'volume': 30, 'muted': False, 'media': None}

    def devices(self):
        return [{'id': 'demo-tv', 'name': 'Living Room TV', 'model': 'Chromecast (demo)', 'video': True},
                {'id': 'demo-speaker', 'name': 'Kitchen speaker', 'model': 'Nest Mini (demo)', 'video': False}]

    def check(self, cid):
        if cid not in ('demo-tv', 'demo-speaker'):
            raise web.HTTPNotFound(text='That Chromecast isn\'t on the Wi-Fi right now.')

    def status(self, cid):
        self.check(cid)
        return dict(self.st)

    def start(self, cid, url):
        self.check(cid)
        self.st.update(app='DashCast', appId=DASHCAST, idle=False, media=None)

    def stop(self, cid):
        self.check(cid)
        self.st.update(app='', appId='', idle=True, media=None)

    def volume(self, cid, body):
        self.check(cid)
        if 'mute' in body:
            self.st['muted'] = bool(body['mute'])
        if 'level' in body:
            self.st['volume'] = max(0, min(100, int(body['level'])))
        elif 'delta' in body:
            self.st['volume'] = max(0, min(100, self.st['volume'] + int(body['delta'])))

    def media(self, cid, cmd):
        self.check(cid)


# ---------- Setup ----------

def setup(app, demo=False, lights=None, cast=False):
    """Add the /tv routes to the relay (behind its key check) and, with --cast, the TV's own server."""
    cfg = load_config()
    enabled = cast or demo
    token = secrets.token_urlsafe(18)           # new every start; never the relay key
    host = '127.0.0.1' if demo else lan_ip()
    hub = {'socks': set(), 'screen': '', 'focus': '', 'want': None, 'started': 0.0, 'board': cfg.get('board', [])[-12:]}
    page = {'html': None, 'at': 0.0}
    casts, problem = None, ''
    if not enabled:
        problem = 'TV is off on the relay. Start it with:  py kasa_bridge.py --cast'
    else:
        try:
            casts = FakeCasts() if demo else RealCasts()
        except ImportError:
            problem = 'pychromecast isn\'t installed on the PC. Run:  py -m pip install pychromecast'

    def need():
        if casts is None:
            raise web.HTTPServiceUnavailable(text=problem)
        return casts

    def tv_url():
        return f'http://{host or lan_ip() or "127.0.0.1"}:{TV_PORT}/?k={token}'

    def pick(body):
        cid = str(body.get('id') or cfg.get('cast') or '')
        if not cid:
            raise web.HTTPBadRequest(text='Pick your Chromecast first.')
        return cid

    async def send_tv(msg):
        sent = False
        for s in list(hub['socks']):
            try:
                await s.send_json(msg); sent = True
            except Exception:
                hub['socks'].discard(s)
        return sent

    async def lights_list():
        if lights is None:
            return {'lights': False}
        try:
            return {'lights': await lights.list()}
        except Exception as e:
            return {'lights': [], 'lightsError': str(e) or 'Lights didn\'t answer'}

    async def toggle_focused():
        """OK pressed on the phone while the TV shows Lights: switch the light the TV has selected."""
        f = hub['focus']
        if lights is None or not f:
            return
        try:
            if f in ('all-on', 'all-off'):
                for l in await lights.list():
                    if l['online']:
                        await lights.set(l['id'], {'on': f == 'all-on'})
            else:
                cur = next((l for l in await lights.list() if l['id'] == f), None)
                if cur and cur['online']:
                    await lights.set(f, {'on': not cur['on']})
        except Exception as e:
            await send_tv({'lightsError': getattr(e, 'text', None) or str(e) or 'Light didn\'t answer'})
        await send_tv(await lights_list())

    # ---- phone side: 127.0.0.1:7788 through Tailscale, behind the relay key ----
    async def body_of(request):
        try:
            return await request.json()
        except Exception:
            return {}

    async def info(request):
        c = need()
        devs = await asyncio.to_thread(c.devices)
        cid = cfg.get('cast') or ''
        video = [d for d in devs if d['video']]
        if not cid and len(video) == 1:
            cid = cfg['cast'] = video[0]['id']; save_config(cfg)
        st, err = None, ''
        if cid:
            try:
                st = await asyncio.wait_for(asyncio.to_thread(c.status, cid), 15)
            except web.HTTPException as e:
                err = e.text
            except Exception as e:
                err = 'The Chromecast didn\'t answer: ' + (str(e) or type(e).__name__)
        casting = bool(st and st['appId'] == DASHCAST)
        linked = bool(hub['socks'])
        since = time.time() - hub['started']
        return web.json_response({
            'ok': True, 'demo': c.fake, 'devices': devs, 'id': cid, 'cast': st, 'error': err,
            'tv': {'on': linked or (casting and bool(hub['want'])), 'linked': linked, 'screen': hub['screen'] if linked else '',
                   'waiting': bool(hub['want']) and not linked and since < 30,
                   'stuck': bool(hub['want']) and not linked and since >= 30},
            'board': hub['board'], 'lights': lights is not None,
            'screenLink': tv_url() if c.fake else ''})

    async def choose(request):
        body = await body_of(request)
        cfg['cast'] = pick(body); save_config(cfg)
        return web.json_response({'ok': True})

    async def start(request):
        body = await body_of(request)
        c, cid = need(), pick(body)
        if cfg.get('cast') != cid:
            cfg['cast'] = cid; save_config(cfg)
        hub['want'], hub['started'] = cid, time.time()
        await asyncio.wait_for(asyncio.to_thread(c.start, cid, tv_url()), 20)
        return web.json_response({'ok': True})

    async def stop(request):
        body = await body_of(request)
        c, cid = need(), pick(body)
        hub['want'] = None
        await asyncio.wait_for(asyncio.to_thread(c.stop, cid), 15)
        for s in list(hub['socks']):
            await s.close()
        return web.json_response({'ok': True})

    async def volume(request):
        body = await body_of(request)
        c, cid = need(), pick(body)
        await asyncio.wait_for(asyncio.to_thread(c.volume, cid, body), 10)
        return web.json_response({'ok': True})

    async def media(request):
        body = await body_of(request)
        cmd = body.get('cmd')
        if cmd not in ('play', 'pause', 'stop'):
            raise web.HTTPBadRequest(text='Unknown media command.')
        c, cid = need(), pick(body)
        await asyncio.wait_for(asyncio.to_thread(c.media, cid, cmd), 10)
        return web.json_response({'ok': True})

    async def key(request):
        body = await body_of(request)
        k = body.get('key')
        if k not in KEYS:
            raise web.HTTPBadRequest(text='Unknown key.')
        if not await send_tv({'key': k}):
            raise web.HTTPConflict(text='Phosphor TV isn\'t on the TV right now.')
        if k == 'ok' and hub['screen'] == 'lights':
            await toggle_focused()
        return web.json_response({'ok': True})

    async def open_(request):
        body = await body_of(request)
        s = body.get('screen')
        if s not in SCREENS:
            raise web.HTTPBadRequest(text='Unknown screen.')
        if not await send_tv({'open': s}):
            raise web.HTTPConflict(text='Phosphor TV isn\'t on the TV right now.')
        return web.json_response({'ok': True})

    async def say(request):
        body = await body_of(request)
        text = ' '.join(str(body.get('text', '')).split())[:140]
        if not text:
            raise web.HTTPBadRequest(text='Type a message first.')
        msg = {'text': text, 't': int(time.time() * 1000)}
        hub['board'] = (hub['board'] + [msg])[-12:]
        cfg['board'] = hub['board']; save_config(cfg)
        shown = await send_tv({'say': msg, 'board': hub['board']})
        return web.json_response({'ok': True, 'shown': shown, 'board': hub['board']})

    async def clear(request):
        hub['board'] = []
        cfg['board'] = []; save_config(cfg)
        await send_tv({'board': []})
        return web.json_response({'ok': True, 'board': []})

    app.add_routes([web.get('/tv', info), web.post('/tv/choose', choose), web.post('/tv/start', start), web.post('/tv/stop', stop),
                    web.post('/tv/volume', volume), web.post('/tv/media', media), web.post('/tv/key', key),
                    web.post('/tv/open', open_), web.post('/tv/say', say), web.post('/tv/board/clear', clear)])

    if casts is None:
        print('TV: off.', problem)
        return None

    # ---- TV side: port 7790 on the home network, TV token only, two routes ----
    @web.middleware
    async def tv_guard(request, handler):
        if not secrets.compare_digest(request.query.get('k', ''), token):
            return web.Response(status=403, text='Phosphor TV: wrong key.')
        return await handler(request)

    async def get_page():
        override = os.environ.get('PHOSPHOR_TV_PAGE')   # a local copy, for working on the page
        if override:
            return Path(override).read_text(encoding='utf-8')
        if page['html'] and time.time() - page['at'] < PAGE_MAX_AGE:
            return page['html']
        try:
            async with ClientSession(timeout=ClientTimeout(total=8)) as s:
                async with s.get(PAGE_URL, headers={'Cache-Control': 'no-cache'}) as r:
                    if r.status != 200:
                        raise RuntimeError(f'HTTP {r.status}')
                    html = await r.text()
            PAGE_CACHE.write_text(html, encoding='utf-8')
        except Exception as e:
            print('  TV page: using the saved copy, could not fetch it:', e)
            try:
                html = PAGE_CACHE.read_text(encoding='utf-8')
            except OSError:
                html = ('<!doctype html><body style="background:#030703;color:#39FF6A;font:5vh monospace;padding:10vh">'
                        'Phosphor TV could not download its screen. Is the PC online?</body>')
        page.update(html=html, at=time.time())
        return html

    async def tv_page(request):
        return web.Response(text=await get_page(), content_type='text/html',
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})

    async def tv_ws(request):
        sock = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        await sock.prepare(request)
        hub['socks'].add(sock)
        print('TV: the screen is connected.')
        try:
            await sock.send_json({'board': hub['board'], **(await lights_list() if lights is not None else {'lights': False})})
            async for msg in sock:
                if msg.type != WSMsgType.TEXT:
                    continue
                # Status only: which screen, which item is selected. Anything else is ignored.
                try:
                    d = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get('screen') in SCREENS:
                    entering = d['screen'] == 'lights' and hub['screen'] != 'lights'
                    hub['screen'] = d['screen']
                    if entering:
                        await sock.send_json(await lights_list())
                if isinstance(d.get('focus'), str):
                    hub['focus'] = d['focus'][:80]
        finally:
            hub['socks'].discard(sock)
            if not hub['socks']:
                hub['screen'] = hub['focus'] = ''
                print('TV: the screen disconnected.')
        return sock

    tv_app = web.Application(middlewares=[tv_guard])
    tv_app.add_routes([web.get('/', tv_page), web.get('/ws', tv_ws)])
    runner = web.AppRunner(tv_app, access_log=None)

    async def keep_alive():
        """If the Chromecast drops the screen by itself, open it again. Casting something else
        (Netflix, YouTube) counts as turning Phosphor TV off."""
        while True:
            await asyncio.sleep(30)
            cid = hub['want']
            if not cid or hub['socks'] or time.time() - hub['started'] < 40:
                continue
            try:
                st = await asyncio.to_thread(casts.status, cid)
                if st['idle'] or st['appId'] == DASHCAST:
                    print('TV: the screen dropped off, opening it again.')
                    hub['started'] = time.time()
                    await asyncio.to_thread(casts.start, cid, tv_url())
                else:
                    print(f'TV: {st["app"] or "another app"} took over the TV, Phosphor TV is off.')
                    hub['want'] = None
            except Exception as e:
                print('  TV check failed:', e)

    async def on_start(_app):
        await runner.setup()
        bind = host or '0.0.0.0'
        try:
            await web.TCPSite(runner, bind, TV_PORT).start()
            print(f'TV: listening on the home network at {bind}:{TV_PORT} for the Chromecast (TV token only).')
            if casts.fake:
                print(f'    Demo TV screen: {tv_url()}')
        except OSError as e:
            print(f'TV: could not listen on {bind}:{TV_PORT} ({e}). Is the relay already running in another window?')
        hub['task'] = asyncio.create_task(keep_alive())

    async def on_stop(_app):
        if hub.get('task'):
            hub['task'].cancel()
        for s in list(hub['socks']):
            await s.close()
        await runner.cleanup()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    if not host and not demo:
        print('TV: could not tell this PC\'s home-network address, so port 7790 listens on all networks.')
    print('TV: on' + (' (demo).' if casts.fake else '. Looking for Chromecasts on the Wi-Fi.'))
    return casts


if __name__ == '__main__':
    print(__doc__)
