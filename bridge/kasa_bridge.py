"""Phosphor OS relay: lights, plus PC control and Phosphor TV when pc_control.py / tv_control.py sit next to this file.

Runs on a computer at home that stays on. It finds the Kasa bulbs and plugs on the home
network and lets the Phosphor OS Lights app control them over HTTPS (through Tailscale).

    python kasa_bridge.py            first run asks for your Kasa login (only newer bulbs need it)
    python kasa_bridge.py --demo     fake lights, for trying the app without any bulbs

Only answers requests that carry the secret key printed at start-up. Listens on this computer
only (127.0.0.1); `tailscale serve` is what makes it reachable from your phone.
"""
import argparse, asyncio, json, os, secrets, shutil, subprocess, time
from pathlib import Path

from aiohttp import web

PORT = 7788
ORIGINS = {'https://bgriffie.github.io', 'http://localhost:8765'}
HERE = Path(__file__).resolve().parent
CONFIG = HERE / 'kasa_bridge.json'
RESCAN_EVERY = 300   # seconds


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(c):
    CONFIG.write_text(json.dumps(c, indent=2))
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass


def tailscale_url():
    """https://<this computer>.<tailnet>.ts.net, if Tailscale is installed and signed in."""
    exe = shutil.which('tailscale') or next((p for p in (
        r'C:\Program Files\Tailscale\tailscale.exe',
        '/Applications/Tailscale.app/Contents/MacOS/Tailscale') if os.path.exists(p)), None)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, 'status', '--json'], capture_output=True, text=True, timeout=10).stdout
        name = json.loads(out)['Self']['DNSName'].rstrip('.')
        return 'https://' + name if name else None
    except Exception:
        return None


# ---------- Lights ----------

class KasaLights:
    def __init__(self, username=None, password=None):
        from kasa import Credentials
        self.creds = Credentials(username, password) if username and password else None
        self.devices = {}          # id -> kasa Device
        self.scanned = 0
        self.lock = asyncio.Lock()

    async def scan(self, rounds=1, verbose=False):
        """Find the bulbs and plugs. A broadcast can miss a device whose reply is late or lost, so a full scan
        broadcasts twice, and every scan also asks each address seen before directly (saved as 'hosts' in
        kasa_bridge.json), so a light found once isn't dropped after a restart."""
        from kasa import Discover
        async with self.lock:
            known = load_config().get('hosts', [])
            have = {d.host for d in self.devices.values()}

            async def broadcast():
                got = {}
                for _ in range(rounds):
                    try:
                        got.update(await Discover.discover(credentials=self.creds, discovery_timeout=6 if rounds > 1 else 4))
                    except Exception as e:
                        print(f'  discovery failed: {e}')
                return got

            async def probe(host):
                try:
                    return await Discover.discover_single(host, credentials=self.creds, discovery_timeout=4)
                except Exception:
                    return None

            asked = [h for h in known if h not in have]
            found, *probed = await asyncio.gather(broadcast(), *(probe(h) for h in asked))
            for host, dev in zip(asked, probed):
                if dev is not None and host not in found:
                    found[host] = dev
            for dev in found.values():
                if dev.host in have and rounds == 1:
                    continue   # already connected; a quick rescan only adds new ones
                try:
                    await dev.update()
                except Exception as e:
                    print(f'  could not read {dev.host}: {e}')
                    continue
                key = dev.device_id or dev.mac or dev.host
                old = self.devices.get(key)
                if old is not None and old is not dev:
                    try:
                        await old.disconnect()
                    except Exception:
                        pass
                self.devices[key] = dev
            hosts = sorted({d.host for d in self.devices.values()} | set(known))
            if hosts != known:
                cfg = load_config()
                cfg['hosts'] = hosts
                save_config(cfg)
            if verbose:
                for dev in sorted(self.devices.values(), key=lambda d: (d.alias or '').lower()):
                    print(f'  {dev.alias or "(no name)":24} {dev.host}')
                missing = [h for h in known if h not in {d.host for d in self.devices.values()}]
                for h in missing:
                    print(f'  {"(not answering)":24} {h}')
            self.scanned = time.time()
            return len(self.devices)

    @staticmethod
    def describe(key, dev, online=True):
        from kasa import Module
        d = {'id': key, 'name': dev.alias or dev.host, 'model': dev.model, 'on': bool(dev.is_on), 'online': online,
             'kind': 'light' if Module.Light in dev.modules else 'plug'}
        light = dev.modules.get(Module.Light)
        if light:
            if light.has_feature('brightness'):
                d['brightness'] = light.brightness
            if light.has_feature('hsv'):
                h, s, v = light.hsv
                d['hsv'] = [h, s, v]
            if light.has_feature('color_temp'):
                f = light.get_feature('color_temp')
                d['temp'] = light.color_temp
                d['tempRange'] = [f.minimum_value, f.maximum_value]
        return d

    async def list(self):
        if not self.devices or time.time() - self.scanned > RESCAN_EVERY:
            await self.scan()
        out = []
        for key, dev in list(self.devices.items()):
            try:
                await asyncio.wait_for(dev.update(), 4)
                out.append(self.describe(key, dev))
            except Exception:
                out.append(self.describe(key, dev, online=False))
        return sorted(out, key=lambda d: d['name'].lower())

    async def set(self, key, body):
        from kasa import Module
        dev = self.devices.get(key)
        if dev is None:
            raise web.HTTPNotFound(text='No such light. Try Rescan.')
        light = dev.modules.get(Module.Light)
        if light and 'hsv' in body and light.has_feature('hsv'):
            h, s, v = (int(x) for x in body['hsv'])
            await light.set_hsv(h % 360, max(0, min(100, s)), max(1, min(100, v)))
        elif light and 'temp' in body and light.has_feature('color_temp'):
            await light.set_color_temp(int(body['temp']))
        if light and 'brightness' in body and light.has_feature('brightness'):
            await light.set_brightness(max(1, min(100, int(body['brightness']))))
        if 'on' in body:
            await (dev.turn_on() if body['on'] else dev.turn_off())
        await dev.update()
        return self.describe(key, dev)


class DemoLights:
    """Pretend bulbs, so the app can be tried without any hardware."""
    def __init__(self):
        self.devices = {
            'desk': {'id': 'desk', 'name': 'Desk lamp', 'model': 'KL130', 'kind': 'light', 'on': True, 'online': True,
                     'brightness': 70, 'hsv': [120, 80, 70], 'temp': 0, 'tempRange': [2500, 9000]},
            'bed': {'id': 'bed', 'name': 'Bedroom', 'model': 'KL125', 'kind': 'light', 'on': False, 'online': True,
                    'brightness': 40, 'hsv': [30, 100, 40], 'temp': 2700, 'tempRange': [2500, 6500]},
            'hall': {'id': 'hall', 'name': 'Hallway', 'model': 'KL110', 'kind': 'light', 'on': True, 'online': True, 'brightness': 100},
            'fan': {'id': 'fan', 'name': 'Fan plug', 'model': 'HS103', 'kind': 'plug', 'on': False, 'online': True},
        }

    async def scan(self, rounds=1, verbose=False):
        return len(self.devices)

    async def list(self):
        return sorted(self.devices.values(), key=lambda d: d['name'].lower())

    async def set(self, key, body):
        d = self.devices.get(key)
        if d is None:
            raise web.HTTPNotFound(text='No such light.')
        if 'hsv' in body and 'hsv' in d:
            d['hsv'] = [int(x) for x in body['hsv']]; d['temp'] = 0; d['brightness'] = d['hsv'][2]
        elif 'temp' in body and 'tempRange' in d:
            d['temp'] = int(body['temp'])
        if 'brightness' in body and 'brightness' in d:
            d['brightness'] = int(body['brightness'])
            if 'hsv' in d:
                d['hsv'][2] = d['brightness']
        if 'on' in body:
            d['on'] = bool(body['on'])
        return d


# ---------- HTTP ----------

def make_app(lights, key, cast=False):
    @web.middleware
    async def guard(request, handler):
        origin = request.headers.get('Origin', '')
        cors = {'Access-Control-Allow-Origin': origin, 'Vary': 'Origin',
                'Access-Control-Allow-Headers': 'Authorization, Content-Type',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Max-Age': '600'} if origin in ORIGINS else {}
        if request.method == 'OPTIONS':
            return web.Response(status=204, headers=cors)
        # ?key= is for the PC app's WebSocket, which can't send an Authorization header
        auth = request.headers.get('Authorization') or 'Bearer ' + request.query.get('key', '')
        if not secrets.compare_digest(auth, 'Bearer ' + key):
            return web.json_response({'error': 'Wrong or missing key'}, status=401, headers=cors)
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = web.json_response({'error': e.text or e.reason}, status=e.status)
        except Exception as e:
            print('error:', e)
            resp = web.json_response({'error': str(e) or 'Light did not respond'}, status=502)
        resp.headers.update(cors)
        return resp

    async def list_lights(request):
        return web.json_response({'lights': await lights.list()})

    async def rescan(request):
        await lights.scan(rounds=2)
        return web.json_response({'lights': await lights.list()})

    async def set_light(request):
        body = await request.json()
        return web.json_response(await lights.set(request.match_info['id'], body))

    async def set_all(request):
        body = await request.json()
        out = []
        for d in await lights.list():
            if d['online']:
                try:
                    out.append(await lights.set(d['id'], {'on': bool(body.get('on'))}))
                except Exception as e:
                    print('  ', d['name'], e)
        return web.json_response({'lights': await lights.list()})

    app = web.Application(middlewares=[guard])
    app.add_routes([web.get('/lights', list_lights), web.post('/lights/rescan', rescan),
                    web.post('/lights/all', set_all), web.post('/lights/{id}', set_light),
                    web.route('OPTIONS', '/{tail:.*}', lambda r: web.Response())])
    # Add-ons next to this file, if they're there: pc_control.py (trackpad, keyboard, monitors)
    # tv_control.py (Phosphor TV on a Chromecast), party.py (Party mode that keeps going) and chores.py (chore alerts). Each adds its routes behind the key check above.
    import importlib, inspect
    extras = {'lights': lights,   # for add-ons that show the lights, like the TV
              'cast': cast}       # --cast: let tv_control.py open its home-network port for the Chromecast
    for name in ('pc_control', 'tv_control', 'party', 'chores'):
        try:
            mod = importlib.import_module(name)
        except ImportError as e:
            if e.name != name:
                print(f'{name}.py could not start: {e}')
            continue
        wants = inspect.signature(mod.setup).parameters
        mod.setup(app, demo=isinstance(lights, DemoLights), **{k: v for k, v in extras.items() if k in wants})
    return app


def main():
    ap = argparse.ArgumentParser(description='Phosphor OS lights relay')
    ap.add_argument('--demo', action='store_true', help='fake lights, no bulbs needed')
    ap.add_argument('--login', action='store_true', help='enter (or change) your Kasa login')
    ap.add_argument('--new-key', action='store_true', help='make a new secret key (the phone must be set up again)')
    ap.add_argument('--cast', action='store_true', help='Phosphor TV: let the Chromecast reach this PC on the home network (port 7790)')
    args = ap.parse_args()

    cfg = load_config()
    if args.new_key or not cfg.get('key'):
        cfg['key'] = secrets.token_urlsafe(18)
    if not args.demo and (args.login or 'username' not in cfg):
        print('Newer Kasa bulbs need your Kasa (TP-Link) app login. Older ones work without it.')
        print('Press Enter to skip.')
        cfg['username'] = input('Kasa email: ').strip()
        if cfg['username']:
            import getpass
            cfg['password'] = getpass.getpass('Kasa password (hidden): ')
        else:
            cfg['password'] = ''
    save_config(cfg)

    lights = DemoLights() if args.demo else KasaLights(cfg.get('username'), cfg.get('password'))

    async def start(app):
        n = await lights.scan(rounds=2, verbose=True)
        print(f'Found {n} light(s) and plug(s).' + ('' if n else ' Is this computer on the same Wi-Fi as the bulbs?'))
    app = make_app(lights, cfg['key'], cast=args.cast)
    app.on_startup.append(start)

    url = tailscale_url()
    print('\n=== Phosphor OS lights relay ===')
    if url:
        print('Lights link (paste into Phosphor OS > Lights > Setup):\n')
        print(f'    {url}#{cfg["key"]}\n')
        print(f'If the phone can\'t connect, run this once in another window:\n    tailscale serve --bg {PORT}\n')
    else:
        print('Tailscale not found. Install it and sign in, run  tailscale serve --bg', PORT, ' then restart this.')
        print(f'Your key is: {cfg["key"]}\n')
    print('Leave this window open. Press Ctrl+C to stop.\n')
    web.run_app(app, host='127.0.0.1', port=PORT, print=None)


if __name__ == '__main__':
    main()
