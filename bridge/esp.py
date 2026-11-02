"""ESP32 gadgets for the Phosphor relay (v1.4). Put this file next to kasa_bridge.py.

Boards broadcast a small hello on UDP port 7791 every 10 s, with their name and latest
readings. This add-on keeps the latest one from each board and serves them behind the key:
  GET  /esp          -> {"devices": [{name, ip, seen, info, state}, ...]}
  GET  /esp/{name}   -> fresh /state straight from the board
  POST /esp/{name}   -> body passed to the board's /set (for gadgets with controls)
Battery scene buttons reach us through the button gateway board ({"press": {...}} messages):
  GET  /esp/buttons  -> {"buttons": {name: {seen, mv, last}}, "bind": {...}, "scenes": [names]}
  POST /esp/buttons  -> {"bind": {"hall": {"1": {"tap": "movie", "double": ..., "triple": ..., "hold": "all-off"}}}}
  POST /esp/scenes   -> {"scenes": {name: {"lights": [{"id", "set"}], "tv": [{"scene", "room"}]}}}
The phone sends the scenes already worked out (which light gets what), so the relay just plays them.
Nothing to pip install: it uses aiohttp, which the relay already has.
"""
import asyncio, json, socket, time
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout

VERSION = '1.4'
HERE = Path(__file__).resolve().parent
SAVED = HERE / 'esp.json'   # button bindings and scenes

# Scenes that work before the Map has sent its own (a Map scene with the same name wins).
# 'lights': {'id': '*'} means every light, {'name': ...} one light (or a list) by its Kasa name, and
# 'except' leaves the named lights out. TV rooms: 'cast' = kitchen, 'pc' = living room. TV parts
# need the relay started with --cast.
SMALL, BIG, PIANO, PORCH = 'Living Room Lamp Small', 'Living Room Lamp Big', 'Piano Lamp', 'Outdoor Lights'
LIVING = [SMALL, BIG]
BOTH_TVS = lambda scene: [{'scene': scene, 'room': 'cast'}, {'scene': scene, 'room': 'pc'}]
OFF = {'on': False}
GLOW = lambda b: {'on': True, 'brightness': b, 'temp': 2500}   # Kasa's warmest white
BUILTIN = {
    # Fireplace: everything off except the small living room lamp as a very warm low glow,
    # and the fireplace on both TVs
    'fireplace': {'lights': [{'id': '*', 'except': [SMALL], 'set': OFF}, {'name': SMALL, 'set': GLOW(15)}],
                  'tv': BOTH_TVS('home')},
    # Cosy: the fireplace with both living room lamps and the piano lamp warm at 30%
    'cosy': {'lights': [{'id': '*', 'except': LIVING + [PIANO], 'set': OFF}, {'name': LIVING + [PIANO], 'set': GLOW(30)}],
             'tv': BOTH_TVS('home')},
    # Movie: living room lamps barely on, everything else off, the kitchen TV off (the living room
    # TV is left alone, since that's what you're watching)
    'movie': {'lights': [{'id': '*', 'except': LIVING, 'set': OFF}, {'name': LIVING, 'set': GLOW(5)}],
              'tv': [{'scene': 'off', 'room': 'cast'}]},
    'intermission': {'lights': [{'name': LIVING, 'set': GLOW(40)}]},
    'bright': {'lights': [{'id': '*', 'set': {'on': True, 'brightness': 100, 'temp': 4000}}]},
    'tvs-off': {'tv': BOTH_TVS('off')},
    # Away: every light off and both TVs on their Away screen
    'away': {'lights': [{'id': '*', 'set': OFF}], 'tv': BOTH_TVS('away')},
    'porch': {'lights': [{'name': PORCH, 'set': {'on': True}}]},
    'warm': {'lights': [{'id': '*', 'set': GLOW(60)}]},
    'bedtime': {'lights': [{'id': '*', 'set': OFF}], 'tv': [{'scene': 'bedtime', 'room': 'cast'}]},
    'all-off': {'lights': [{'id': '*', 'set': OFF}], 'tv': BOTH_TVS('off')},
}
# 'party' taps Party mode on and off; 'party-fast' turns it on (or speeds it up) at the fast beat.
# Every remote uses these until esp.json says otherwise for its name.
DEFAULT_BIND = {'1': {'tap': 'fireplace', 'double': 'cosy', 'triple': 'party', 'hold': 'all-off'},
                '2': {'tap': 'movie', 'double': 'intermission', 'triple': 'bright', 'hold': 'tvs-off'},
                '3': {'tap': 'away', 'double': 'porch', 'triple': 'bedtime', 'hold': 'all-off'}}
HELLO_PORT = 7791
GONE_AFTER = 60   # seconds without a hello before a board shows as offline


class Boards(asyncio.DatagramProtocol):
    def __init__(self, on_press):
        self.devices = {}   # name -> {ip, port, seen, info, state}
        self.http = None
        self.on_press = on_press

    def datagram_received(self, data, addr):
        try:
            msg = json.loads(data.decode())
        except ValueError:
            return
        name = str(msg.get('name', ''))[:40]
        if msg.get('phosphor') != 1 or not name:
            return
        if isinstance(msg.get('press'), dict):
            self.on_press(msg['press'])
            return
        d = self.devices.setdefault(name, {'info': None})
        if name not in self._announced:
            self._announced.add(name)
            print(f'ESP32 found: {name} at {addr[0]}')
        d.update(ip=addr[0], port=int(msg.get('port', 80)), seen=time.time(), state=msg.get('state'))
        if d['info'] is None and d['port']:
            asyncio.ensure_future(self._fetch_info(name))

    _announced = set()

    async def _fetch_info(self, name):
        d = self.devices[name]
        try:
            d['info'] = await self.call(name, 'GET', '/info')
        except Exception as e:
            print(f'ESP32 {name}: no /info ({e})')

    async def call_self(self, path, body=None):
        cfg = json.loads((HERE / 'kasa_bridge.json').read_text())
        if self.http is None:
            self.http = ClientSession(timeout=ClientTimeout(total=90))
        async with self.http.request('GET' if body is None else 'POST', f'http://127.0.0.1:7788{path}', json=body,
                                     headers={'Authorization': 'Bearer ' + cfg['key']}) as r:
            return await r.json(content_type=None)

    async def call(self, name, method, path, body=None):
        d = self.devices[name]
        if self.http is None:
            self.http = ClientSession(timeout=ClientTimeout(total=90))
        async with self.http.request(method, f"http://{d['ip']}:{d['port']}{path}", json=body,
                                     timeout=ClientTimeout(total=4)) as r:
            return await r.json(content_type=None)

    def listing(self):
        now = time.time()
        return [{'name': n, 'ip': d['ip'], 'online': now - d['seen'] < GONE_AFTER,
                 'seen': round(d['seen']), 'info': d['info'], 'state': d['state']}
                for n, d in sorted(self.devices.items())]


def load_saved():
    try:
        return json.loads(SAVED.read_text())
    except (OSError, ValueError):
        return {}


def setup(app, lights, demo=False):
    saved = load_saved()
    saved.setdefault('bind', {}); saved.setdefault('scenes', {})
    buttons = {}   # name -> {seen, mv, last, n}

    async def play(name):
        if name in ('party', 'party-fast'):
            now = await boards.call_self('/party')
            if name == 'party' and now.get('on'):
                body = {'on': False}
            else:
                body = {'on': True, 'mode': now.get('mode') or 'both',
                        'speed': 'fast' if name == 'party-fast' else now.get('speed') or 'medium'}
            await boards.call_self('/party', body)
            return
        sc = saved['scenes'].get(name) or BUILTIN.get(name)
        if not sc:
            print(f'  no scene called {name!r}')
            return
        everyone = None
        jobs = []
        for l in sc.get('lights', []):
            if l.get('id') == '*' or 'name' in l:
                everyone = everyone or await lights.list()
                skip = {n.lower() for n in l.get('except', [])}
                pick = l.get('name', [])
                pick = {n.lower() for n in ([pick] if isinstance(pick, str) else pick)}
                for d in everyone:
                    named = d['name'].lower()
                    if named in pick or (l.get('id') == '*' and named not in skip):
                        jobs.append(lights.set(d['id'], l['set']))
            else:
                jobs.append(lights.set(l['id'], l['set']))
        for t in sc.get('tv', []):   # through our own front door, so tv_control does the usual
            jobs.append(boards.call_self('/tv/scene', t))
        for r in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(r, Exception):
                print('  ', r)

    async def safe_play(name):   # so a failed scene says why in the relay window
        try:
            await play(name)
        except Exception as e:
            print(f'  {name} failed: {e}')

    def on_press(p):
        name, key, kind, n = str(p.get('b', ''))[:40], str(p.get('k', 0)), p.get('p'), p.get('n')
        b = buttons.setdefault(name, {})
        repeat = b.get('n') == n and time.time() - b.get('seen', 0) < 5
        b.update(seen=time.time(), mv=p.get('mv') or None, n=n)
        if repeat or kind == 'hello':
            return
        b['last'] = {'key': key, 'press': kind, 'at': round(time.time())}
        scene = saved['bind'].get(name, DEFAULT_BIND).get(key, {}).get(kind)
        print(f'Button {name} {key} {kind}' + (f' -> {scene}' if scene else ' (not set up)'))
        if scene:
            asyncio.ensure_future(safe_play(scene))

    boards = Boards(on_press)

    def keep():
        SAVED.write_text(json.dumps(saved, indent=1))

    async def start(app):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', HELLO_PORT))
        await asyncio.get_running_loop().create_datagram_endpoint(lambda: boards, sock=sock)
        if demo:
            boards.devices['demo-room'] = {'ip': '127.0.0.1', 'port': 80, 'seen': 1e12, 'state': {
                'name': 'demo-room', 'temp': 71.3, 'humidity': 42, 'pressure': 1013, 'light': 230},
                'info': {'phosphor': 1, 'name': 'demo-room', 'type': 'room-sensor', 'controls': [],
                         'readings': [{'id': 'temp', 'label': 'Temp', 'unit': '°F'},
                                      {'id': 'humidity', 'label': 'Humidity', 'unit': '%'},
                                      {'id': 'light', 'label': 'Light', 'unit': 'lx'}]}}

    async def stop(app):
        if boards.http:
            await boards.http.close()

    def board(request):
        name = request.match_info['name']
        if name not in boards.devices:
            raise web.HTTPNotFound(text=json.dumps({'error': 'No such board'}), content_type='application/json')
        return name

    async def list_boards(request):
        return web.json_response({'devices': boards.listing()})

    async def get_board(request):
        name = board(request)
        try:
            boards.devices[name]['state'] = await boards.call(name, 'GET', '/state')
        except Exception as e:
            return web.json_response({'error': f'{name} did not answer ({e})'}, status=502)
        return web.json_response(boards.devices[name]['state'])

    async def get_buttons(request):
        return web.json_response({'buttons': {k: {x: v.get(x) for x in ('seen', 'mv', 'last')} for k, v in buttons.items()},
                                  'bind': saved['bind'], 'default_bind': DEFAULT_BIND,
                                  'scenes': sorted(set(saved['scenes']) | set(BUILTIN) | {'party', 'party-fast'})})

    async def set_buttons(request):
        body = await request.json()
        if isinstance(body.get('bind'), dict):
            saved['bind'] = body['bind']; keep()
        return await get_buttons(request)

    async def set_scenes(request):
        body = await request.json()
        if isinstance(body.get('scenes'), dict):
            saved['scenes'] = body['scenes']; keep()
        return web.json_response({'scenes': sorted(saved['scenes'])})

    async def set_board(request):
        name = board(request)
        try:
            return web.json_response(await boards.call(name, 'POST', '/set', await request.json()))
        except Exception as e:
            return web.json_response({'error': f'{name} did not answer ({e})'}, status=502)

    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    app.add_routes([web.get('/esp', list_boards), web.get('/esp/buttons', get_buttons),
                    web.post('/esp/buttons', set_buttons), web.post('/esp/scenes', set_scenes),
                    web.get('/esp/{name}', get_board),
                    web.post('/esp/{name}', set_board)])
    print(f'ESP32 gadgets: listening on UDP {HELLO_PORT} (v{VERSION}).')
