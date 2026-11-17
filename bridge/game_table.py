"""Game table add-on for the Phosphor relay (kasa_bridge.py loads it when this file is next to it).

The projector game table (MTG and board games) is one page, game_table.html, kept next to this file.
It is served on the home network at  http://<this PC>:7792/game/  as plain HTTP with no key, so phones
on the Wi-Fi can join a seat by scanning its QR code. Nothing else of the relay is reachable on 7792.

Port 7792 (home network, no key), the same five routes the table's old server.py had:
    GET  /game/              the page (control window; #display = projector, #phone&seat=N&k=KEY = a seat)
    GET  /game/api/info      {ip, port}: the address phones use, for the QR codes
    POST /game/api/state     the control window's public state (no phone key in it)
    GET  /game/api/state     {state}: phones read it
    POST /game/api/action    a phone's request, queued (last 200)
    GET  /game/api/actions   the control window takes the queue
The control window decides everything; this is only a relay, in memory.

Behind the relay key (for the Table page in Phosphor OS):
    GET  /game        {on, url, local, live, players, phones}
    POST /game/open   opens the control window in this PC's browser
"""
import socket, time, webbrowser
from pathlib import Path

from aiohttp import web

VERSION = '1.0'
GAME_PORT = 7792
PAGE = Path(__file__).resolve().parent / 'game_table.html'
NO_STORE = {'Cache-Control': 'no-store'}


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def setup(app, demo=False):
    hub = {'state': None, 'at': 0, 'actions': []}

    async def page(request):
        if not PAGE.exists():
            return web.Response(status=404, text='game_table.html is missing. Save it into the relay folder next to game_table.py.', headers=NO_STORE)
        return web.FileResponse(PAGE, headers=NO_STORE)

    async def to_page(request):
        raise web.HTTPFound('/game/')

    async def info(request):
        return web.json_response({'ip': lan_ip(), 'port': GAME_PORT}, headers=NO_STORE)

    async def get_state(request):
        return web.json_response({'state': hub['state']}, headers=NO_STORE)

    async def set_state(request):
        hub['state'], hub['at'] = await request.json(), time.time()
        return web.json_response({'ok': True}, headers=NO_STORE)

    async def add_action(request):
        hub['actions'].append(await request.json())
        del hub['actions'][:-200]
        return web.json_response({'ok': True}, headers=NO_STORE)

    async def take_actions(request):
        out, hub['actions'] = hub['actions'], []
        return web.json_response(out, headers=NO_STORE)

    game_app = web.Application(client_max_size=4 * 1024 * 1024)
    game_app.add_routes([web.get('/', to_page), web.get('/game', to_page), web.get('/game/', page), web.get('/game/index.html', page),
                         web.get('/game/api/info', info), web.get('/game/api/state', get_state), web.post('/game/api/state', set_state),
                         web.post('/game/api/action', add_action), web.get('/game/api/actions', take_actions)])
    runner = web.AppRunner(game_app, access_log=None)

    # ---- behind the relay key: what the Table page in Phosphor OS shows ----
    def status():
        s = hub['state'] if isinstance(hub['state'], dict) else {}
        n = int(s.get('count') or 0)
        players = (s.get('players') or [])[:n]
        return {'on': PAGE.exists(), 'version': VERSION, 'url': f'http://{lan_ip()}:{GAME_PORT}/game/',
                'local': f'http://localhost:{GAME_PORT}/game/', 'live': time.time() - hub['at'] < 10,
                'game': s.get('game'), 'players': n, 'phones': sum(1 for p in players if isinstance(p, dict) and p.get('devTag'))}

    async def game_status(request):
        return web.json_response(status())

    async def game_open(request):
        if not PAGE.exists():
            raise web.HTTPBadRequest(text='game_table.html is missing from the relay folder.')
        if not demo:
            webbrowser.open(f'http://localhost:{GAME_PORT}/game/')
        return web.json_response(status())

    app.add_routes([web.get('/game', game_status), web.post('/game/open', game_open)])

    async def on_start(_app):
        await runner.setup()
        try:
            await web.TCPSite(runner, '0.0.0.0', GAME_PORT).start()
            print(f'Game table: on this PC open http://localhost:{GAME_PORT}/game/   (phones join at http://{lan_ip()}:{GAME_PORT}/game/)')
            if not PAGE.exists():
                print('    game_table.html is missing: save it into this folder.')
        except OSError as e:
            print(f'Game table: could not listen on port {GAME_PORT} ({e}). Is the relay already running in another window?')

    async def on_stop(_app):
        await runner.cleanup()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
