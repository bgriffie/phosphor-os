"""Phosphor TV for the Phosphor OS relay: puts a Phosphor screen (clock, lights, message board,
art, background sound) on a Chromecast and lets the phone's TV app drive it like a remote.

Drop this file next to kasa_bridge.py, install one library, and start the relay with --cast:

    py -m pip install pychromecast
    py -m pip install imageio-ffmpeg      (optional: for the pixelated fireplace)
    py kasa_bridge.py --cast

Without --cast the TV stays off and nothing extra listens. Delete the file to remove it entirely.

How it works: the relay finds the Chromecast on the Wi-Fi and tells it to open the free DashCast
receiver, pointed at a small web server this file runs on the home network (port 7790). That
server only hands the Chromecast the TV page (fetched from bgriffie.github.io/phosphor-os/tv/ and
kept in tv_page.html) and one WebSocket. The phone never talks to the TV: it calls /tv/* on the
relay (127.0.0.1:7788, behind the relay key, through Tailscale) and the relay passes things on.

Security (agreed with Floyd's security review):
- Port 7790 is a separate aiohttp app with four routes: / (the page), /ws, /fire/<digit>.mp4, which only
  hands out the relay's own pixelated fireplace copies (tv_fire/fire0-3.mp4) by number, and /radio/<station>,
  which only passes on the stations in RADIO's fixed list. No /pc, no /lights, no other files or folders.
- A second TV can be a monitor on this PC (the living room TV over HDMI): the relay opens the same page there in
  a full-screen Chrome/Edge window (kiosk mode, own profile). It talks to the relay exactly like the Chromecast. It binds to this PC's home-network address, not Tailscale.
- It answers only with a TV token made fresh each time the relay starts and checked with
  compare_digest. The relay's own key never goes over 7790, which is plain HTTP.
- Control goes one way. The relay decides what the TV shows; the TV's WebSocket may only report
  status (which screen, which item is selected) and everything else it sends is ignored.
- Windows asks once whether Python may use the network: tick Private networks only.
"""
import asyncio, json, os, re, secrets, socket, subprocess, time
from pathlib import Path

from aiohttp import web, WSMsgType, ClientSession, ClientTimeout

TV_PORT = 7790
PAGE_URL = 'https://bgriffie.github.io/phosphor-os/tv/'
HERE = Path(__file__).resolve().parent
CONFIG = HERE / 'tv_control.json'
PAGE_CACHE = HERE / 'tv_page.html'
# Pixelated fireplaces: the Chromecast can't pixelate video itself, so the relay makes a low-res copy of each
# filmed fireplace once (ffmpeg from the imageio-ffmpeg package: py -m pip install imageio-ffmpeg) and the TV
# plays those. Same addresses and order as FILMS in the TV page's art.js. Copies go in tv_fire/ next to this file.
FIRE_FILMS = ('https://cdn.pixabay.com/video/2017/01/06/7070-198606760_large.mp4',
              'https://static.videezy.com/system/resources/previews/000/005/865/original/MVI_5664.mp4',
              'https://static.videezy.com/system/resources/previews/000/005/867/original/MVI_5666.mp4',
              'https://assets.mixkit.co/videos/30252/30252-720.mp4')
FIRE_DIR = HERE / 'tv_fire'
FIRE_VF = 'fps=24,scale=192:108:flags=area,scale=1920:1080:flags=neighbor'   # 108 rows of chunky pixels, sharp edges at 1080p
FIRE_MAX = 300 * 2**20     # largest download accepted, in bytes
PAGE_MAX_AGE = 600         # seconds before the TV page is fetched again
SCREENS = ('home', 'today', 'art', 'clock', 'lights', 'board', 'rain', 'dim', 'watch', 'alarm')   # alarm = Home Map's Intruder mode
# Background sound. The TV page makes the rain/fire/ocean sounds itself. Music is internet radio: the relay fetches
# each station once and passes the same stream to every TV (7790 /radio/<id>), so two TVs playing the same station
# stay in step; the TV page falls back to the station's own address if that doesn't work. Same list as sound.js.
_soma = lambda i: [f'https://ice{n}.somafm.com/{i}-128-mp3' for n in (1, 2, 4, 6)]
_srg = lambda i: [f'https://stream.srg-ssr.ch/m/{i}/mp3_128', f'https://stream.srg-ssr.ch/srgssr/{i}/mp3/128']
_rj = lambda n: [f'https://443-1.autopo.st/171/stream/{n}/']
RADIO = {
    'swiss-classic': _srg('rsc_de'), 'romantic-jazz': _rj(1) + _rj(3), 'swiss-jazz': _srg('rsj'), 'bossa': _soma('bossa'),
    'lounge': _soma('illstreet'), 'fluid': _soma('fluid'), 'groove-salad': _soma('deepspaceone'), 'drone-zone': _soma('dronezone'),
    'sonic-universe': ['https://boxradio-edge-00.streamafrica.net/jpopchill', 'https://stream.zeno.fm/ricmeoweux2uv'],
    'secret-agent': ['https://nonstopcasiopea.radioca.st/', 'https://nonstopcasiopea.radioca.st/stream'],
}
MUSIC = tuple(RADIO) + ('pc',)   # 'pc' = PC audio: whatever this PC plays on one output (Floyd's Spotify), sent to the kitchen
PC_AUDIO_LAG = 3.5                # seconds behind live for PC audio (radio uses RADIO_LAG); the living room hears it directly
RADIO_LAG = 4.0            # seconds behind the live edge every listener starts, so all TVs line up
RADIO_KEEP = 20.0          # seconds of each station kept, for the lag, the sync offset and JOIN_SPARE
JOIN_SPARE = 5.0           # extra seconds a TV gets on joining, so the relay can skip it forward to the exact spot
RADIO_MAX_OFFSET = 3000    # ms the living room can be shifted against the kitchen, either way
# Two TVs: 'cast' = the Chromecast (the kitchen CRT), 'pc' = a monitor on this PC (the living room TV, over HDMI)
ROOMS = ('cast', 'pc')
ROOM_NAMES = {'cast': 'Kitchen', 'pc': 'Living room'}
SINK_ID = re.compile(r'^[A-Za-z0-9+/=_.-]{1,200}$')
AMBIENCE = ('rain', 'storm', 'fire', 'ocean', 'wind', 'brown')
ART_MAX = 32               # the TV page checks the real number of art pieces
KEYS = ('up', 'down', 'left', 'right', 'ok', 'back', 'home')
DASHCAST = '84912283'
VERSION = '4.8'           # the phone app compares this to know which features the relay has
WEATHER_EVERY = 900       # seconds between weather updates while the TV is on
# Scenes: what the TV does at a set time, or when the phone (or an iPhone Shortcut) asks.
# Each scene has a page to land on, a music station, a background sound and, optionally, a look for some lights;
# all of it is Floyd's to change. off = Phosphor TV off.
SCENES = ('morning', 'away', 'home', 'romantic', 'bedtime', 'off')
SCENE_PAGES = ('today', 'fire', 'dim', 'night', 'art', 'clock', 'board', 'watch', 'lights')   # 'fire' = the fireplace, full screen
SCENE_DEFAULTS = {
    'morning': {'page': 'fire', 'music': 'swiss-classic', 'amb': 'fire', 'glow': '', 'lights': []},   # Floyd 2026-10-28
    'away': {'page': 'dim', 'music': 'swiss-classic', 'amb': '', 'glow': '', 'lights': []},
    'home': {'page': 'fire', 'music': 'swiss-jazz', 'amb': 'fire', 'glow': '', 'lights': []},
    'romantic': {'page': 'fire', 'music': 'romantic-jazz', 'amb': 'fire', 'glow': 'red', 'lights': None},   # None = lights named "living"
    'bedtime': {'page': 'night', 'music': '', 'amb': '', 'glow': 'off', 'lights': 'all'},   # 'night' = black with the faintest clock
}
GLOW = {'red': {'on': True, 'hsv': [0, 100, 10]}, 'warm': {'on': True, 'temp': 2700, 'brightness': 30},
        'on': {'on': True}, 'off': {'on': False}}   # '' leaves the lights as they are
# Screen shape: 'wide' = a normal 16:9 TV; for a 4:3 TV behind an HDMI-to-AV box, 'squeeze' = the box squashes the
# whole picture into 4:3, 'box' = the box shows only the middle 4:3 (or adds bars). Floyd picks whichever looks right.
SHAPES = ('wide', 'squeeze', 'box')
# Manual fit on top of the shape (Floyd: "manually set the ratio until it fits"): width/height in % (100 = as is),
# x/y shift in % of the screen. FIT_RANGE[k] = (min, max).
FIT_RANGE = {'w': (60, 140), 'h': (60, 140), 'x': (-20, 20), 'y': (-20, 20)}
FIT_DEFAULT = {'w': 100, 'h': 100, 'x': 0, 'y': 0}
LIGHT_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')
DEFAULT_SCHEDULE = [{'scene': 'morning', 'at': '07:30', 'days': [0, 1, 2, 3, 4, 5, 6]},
                    {'scene': 'away', 'at': '08:30', 'days': [0, 1, 2, 3, 4]},
                    {'scene': 'home', 'at': '17:30', 'days': [0, 1, 2, 3, 4]}]
WATCH_KEY = re.compile(r'^(movie|tv)-[0-9]{1,9}$')
WATCH_POSTER = re.compile(r'^/[A-Za-z0-9_-]{4,64}\.(jpg|png)$')   # a TMDB poster path; the TV page adds the fixed image.tmdb.org address
WATCH_SVCS = ('hulu', 'prime', 'peacock')
FIREPLACE = 1              # the filmed fireplace's place in the TV page's art list
SLEEP_FADE = 60            # seconds the sound takes to fade before the sleep timer turns the TV off
FAHRENHEIT = ('US', 'LR', 'MM', 'BS', 'BZ', 'KY', 'PW', 'FM', 'MH')


async def geocode(city):
    """City name -> place, through Open-Meteo (free, no key)."""
    async with ClientSession(timeout=ClientTimeout(total=10)) as s:
        async with s.get('https://geocoding-api.open-meteo.com/v1/search', params={'name': city, 'count': 1, 'language': 'en'}) as r:
            d = await r.json()
    hit = (d.get('results') or [None])[0]
    if not hit:
        raise web.HTTPNotFound(text=f'Couldn\'t find "{city}". Try adding the state or country, like "Portland, Oregon".')
    label = ', '.join(x for x in (hit.get('name'), hit.get('admin1') if hit.get('country_code') in FAHRENHEIT else hit.get('country')) if x)
    return {'name': label, 'lat': hit['latitude'], 'lon': hit['longitude'], 'cc': hit.get('country_code', '')}


async def forecast(place, units):
    """Today's weather for the TV's Today page, from Open-Meteo, in the place's own time zone."""
    f = units == 'F'
    q = {'latitude': place['lat'], 'longitude': place['lon'], 'timezone': 'auto', 'forecast_days': 2,
         'current': 'temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day',
         'hourly': 'temperature_2m,precipitation_probability,weather_code,is_day',
         'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset,weather_code',
         'temperature_unit': 'fahrenheit' if f else 'celsius', 'wind_speed_unit': 'mph' if f else 'kmh'}
    async with ClientSession(timeout=ClientTimeout(total=12)) as s:
        async with s.get('https://api.open-meteo.com/v1/forecast', params=q) as r:
            if r.status != 200:
                raise RuntimeError(f'weather service said {r.status}')
            d = await r.json()
    cur, hr, dy = d['current'], d['hourly'], d['daily']
    start = next((i for i, t in enumerate(hr['time']) if t[:13] == cur['time'][:13]), 0)
    hours = [{'t': hr['time'][i], 'temp': hr['temperature_2m'][i], 'rain': hr['precipitation_probability'][i],
              'code': hr['weather_code'][i], 'day': bool(hr['is_day'][i])} for i in range(start + 1, min(start + 13, len(hr['time'])))]
    return {'place': place['name'], 'units': units, 'at': int(time.time()),
            'now': {'temp': cur['temperature_2m'], 'feels': cur['apparent_temperature'], 'code': cur['weather_code'],
                    'wind': cur['wind_speed_10m'], 'day': bool(cur['is_day'])},
            'today': {'hi': dy['temperature_2m_max'][0], 'lo': dy['temperature_2m_min'][0], 'rain': dy['precipitation_probability_max'][0],
                      'sunrise': dy['sunrise'][0], 'sunset': dy['sunset'][0], 'code': dy['weather_code'][0]},
            'hours': hours}


def fake_forecast(place, units):
    """Pretend weather for --demo (no network needed)."""
    import datetime as dt
    now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    base = 52 if units == 'F' else 11
    hours = [{'t': (now + dt.timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M'), 'temp': base + 6 - abs(i - 5), 'rain': [0, 0, 5, 10, 30, 60, 70, 40, 20, 5, 0, 0][i - 1],
              'code': [1, 1, 2, 3, 3, 61, 63, 61, 3, 2, 1, 0][i - 1], 'day': 7 <= (now.hour + i) % 24 < 18} for i in range(1, 13)]
    day = now.strftime('%Y-%m-%d')
    return {'place': place['name'], 'units': units, 'at': int(time.time()),
            'now': {'temp': base + 1, 'feels': base - 2, 'code': 2, 'wind': 9, 'day': 7 <= now.hour < 18},
            'today': {'hi': base + 7, 'lo': base - 6, 'rain': 70, 'sunrise': day + 'T07:41', 'sunset': day + 'T18:12', 'code': 61},
            'hours': hours}


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
        # A connection that has ended (the Chromecast restarted, changed address or dropped off the Wi-Fi)
        # can't be started again, so make a fresh one instead
        if cc is not None and not cc.socket_client.is_alive() and cc.socket_client.ident is not None:
            self.forget(cid)
            cc = None
        if cc is None:
            info = next((i for u, i in self.browser.devices.items() if str(u) == cid), None)
            if info is None:
                raise web.HTTPNotFound(text='That Chromecast isn\'t on the Wi-Fi right now. Is it plugged in?')
            cc = self.objs[cid] = self.pc.get_chromecast_from_cast_info(info, self.zc, tries=2, timeout=8)
        try:
            cc.wait(timeout=10)
        except Exception:
            self.forget(cid)
            raise web.HTTPGatewayTimeout(text='The Chromecast didn\'t answer. Try again in a moment.')
        if not cc.status:
            self.forget(cid)
            raise web.HTTPGatewayTimeout(text='The Chromecast didn\'t answer.')
        return cc

    def forget(self, cid):
        cc = self.objs.pop(cid, None)
        if cc is not None:
            try:
                cc.disconnect(timeout=1)
            except Exception:
                pass

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

    def release(self, cid):
        """Pause what's casting (so the service saves the spot), note it, then close the app on the Chromecast."""
        cc = self.get(cid)
        st, d = cc.status, {'app': cc.status.display_name or '', 'title': '', 'video': '', 'at': 0}
        m = cc.media_controller.status if not cc.is_idle else None
        if m and m.player_state not in ('UNKNOWN', 'IDLE'):
            d.update(title=m.title or '', video=m.content_id or '', at=int(m.adjusted_current_time or m.current_time or 0))
            if m.player_state == 'PLAYING':
                try:
                    cc.media_controller.pause(); time.sleep(1.5)
                except Exception:
                    pass
        if st.app_id and st.app_id != DASHCAST:
            cc.quit_app()
        return d

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
        if os.environ.get('PHOSPHOR_TV_DEMO_APP'):   # pretend something like Hulu is casting, to try "Move to living room"
            self.st.update(app=os.environ['PHOSPHOR_TV_DEMO_APP'], appId='demo-app', idle=False,
                           media={'title': 'Demo show', 'sub': 'S1 E2', 'state': 'PLAYING'})

    def release(self, cid):
        self.check(cid)
        d = {'app': self.st['app'], 'title': (self.st['media'] or {}).get('title', ''), 'video': '', 'at': 0}
        self.st.update(app='', appId='', idle=True, media=None)
        return d

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


# ---------- The PC's own TV: a full-screen kiosk window on one monitor ----------
# The living-room TV is a monitor on this PC (HDMI). Phosphor TV opens there as its own browser window: Chrome or
# Edge in kiosk mode with a separate profile, so it has no tabs, address bar or taskbar, isn't mixed in with the
# PC's own browser windows, and stays put while the PC is used on the other monitors. It loads the same TV page as
# the Chromecast, from this relay, and the phone drives it the same way.
KIOSK_BROWSERS = (('chrome.exe', (r'%ProgramFiles%\Google\Chrome\Application', r'%ProgramFiles(x86)%\Google\Chrome\Application',
                                  r'%LocalAppData%\Google\Chrome\Application')),
                  ('msedge.exe', (r'%ProgramFiles(x86)%\Microsoft\Edge\Application', r'%ProgramFiles%\Microsoft\Edge\Application')))
KIOSK_PROFILE = HERE / 'tv_kiosk_profile'
# "Move to living room": what the Chromecast is casting -> where that service's own site continues it on the PC TV.
# Keys are matched against the Chromecast's app name (lower case). The cast can't tell us more than the app and,
# for YouTube, the video, so for the others Floyd picks the show from the service's Continue Watching row.
MOVE_SITES = {'hulu': 'https://www.hulu.com/hub/home', 'prime video': 'https://www.amazon.com/gp/video/storefront',
              'peacock': 'https://www.peacocktv.com/watch/home', 'netflix': 'https://www.netflix.com/browse',
              'disney': 'https://www.disneyplus.com/home', 'max': 'https://play.max.com/', 'youtube': 'https://www.youtube.com/'}
YT_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')


def move_site(app):
    a = (app or '').lower()
    return next(((k, u) for k, u in MOVE_SITES.items() if k in a), (None, None))


def win_monitors():
    """This PC's monitors, numbered 1, 2, 3… left to right like PC Remote does. [] if not on Windows."""
    if os.name != 'nt':
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32')
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                    ('dwFlags', wintypes.DWORD), ('szDevice', wintypes.WCHAR * 32)]
    found = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, hdc, rect, lp):
        mi = MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        found.append({'x': r.left, 'y': r.top, 'w': r.right - r.left, 'h': r.bottom - r.top, 'primary': bool(mi.dwFlags & 1)})
        return True
    user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    found.sort(key=lambda m: (m['x'], m['y']))
    for i, m in enumerate(found, 1):
        m['n'] = i
    return found


class Kiosk:
    """Opens and closes the full-screen Phosphor TV window on one of this PC's monitors."""
    fake = False

    def __init__(self):
        self.proc = None

    def browser(self):
        for exe, dirs in KIOSK_BROWSERS:
            for d in dirs:
                p = os.path.join(os.path.expandvars(d), exe)
                if os.path.exists(p):
                    return p
        return None

    def monitors(self):
        return [{'n': m['n'], 'w': m['w'], 'h': m['h'], 'primary': m['primary']} for m in win_monitors()]

    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def open(self, url, n, tv=True):
        mon = next((m for m in win_monitors() if m['n'] == n), None)
        if mon is None:
            raise web.HTTPBadRequest(text=f'There is no monitor {n} right now. Is the TV on?')
        exe = self.browser()
        if not exe:
            raise web.HTTPBadRequest(text='Phosphor TV needs Google Chrome or Microsoft Edge on the PC for this.')
        self.close()
        # A point well inside that monitor; kiosk mode then fills the whole monitor it lands on
        x, y = mon['x'] + mon['w'] // 4, mon['y'] + mon['h'] // 4
        self.proc = subprocess.Popen([
            exe, f'--user-data-dir={KIOSK_PROFILE}', '--kiosk', f'--app={url}', '--new-window',
            f'--window-position={x},{y}', '--autoplay-policy=no-user-gesture-required',
            '--no-first-run', '--no-default-browser-check', '--disable-session-crashed-bubble', '--noerrdialogs',
            '--disable-translate', '--disable-features=Translate,HardwareMediaKeyHandling', '--overscroll-history-navigation=0']
            # lets the page list the sound outputs by name, so its sound can go to the TV instead of the PC speakers
            + (['--use-fake-ui-for-media-stream',   # (not --use-fake-device-for-media-stream: that hides the real outputs)
                # the TV page is plain http on the LAN, where Chrome hides sound outputs and setSinkId; this
                # kiosk-only profile treats just that one address as secure
                '--unsafely-treat-insecure-origin-as-secure=' + '/'.join(url.split('/')[:3])] if tv else []),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        self.proc = None


class FakeKiosk(Kiosk):
    """--demo: pretends there are two monitors and a window, without opening anything."""
    fake = True

    def monitors(self):
        return [{'n': 1, 'w': 2560, 'h': 1440, 'primary': True}, {'n': 2, 'w': 1920, 'h': 1080, 'primary': False}]

    def running(self):
        return bool(self.proc)

    def open(self, url, n, tv=True):
        if n not in (1, 2):
            raise web.HTTPBadRequest(text=f'There is no monitor {n} right now. Is the TV on?')
        self.proc = {'url': url, 'n': n}

    def close(self):
        self.proc = None


# ---------- Setup ----------

class Station:
    """One internet radio station, fetched once by the relay and handed to every TV that plays it.
    Listeners start a fixed time (RADIO_LAG, plus their room's sync offset) behind the live edge, counted in bytes
    of the stream, so two TVs that join at different moments still play the same moment together."""

    def __init__(self, sid, on_idle):
        self.sid, self.on_idle = sid, on_idle
        self.buf, self.base, self.rate = bytearray(), 0, 16000   # bytes kept, stream position of buf[0], bytes a second
        self.ctype, self.listeners, self.ready = 'audio/mpeg', set(), asyncio.Event()
        self.failed, self.idle_t, self.lag = False, None, RADIO_LAG
        self.task = asyncio.create_task(self.pull())

    def feed(self, chunk):
        self.buf += chunk
        extra = len(self.buf) - int(RADIO_KEEP * self.rate)
        if extra > 0:
            del self.buf[:extra]; self.base += extra
        if not self.ready.is_set() and self.base + len(self.buf) >= self.lag * self.rate:
            self.ready.set()
        for q in list(self.listeners):
            if q.qsize() > 400:   # a listener that stopped reading
                self.listeners.discard(q); q.put_nowait(None)
            else:
                q.put_nowait(chunk)

    async def pull(self):
        fails = 0
        while fails < 3:
            for url in RADIO[self.sid]:
                t0 = time.time()
                try:
                    async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=10, sock_read=20)) as s:
                        async with s.get(url, headers={'Icy-MetaData': '0', 'User-Agent': 'PhosphorTV'}) as r:
                            if r.status != 200:
                                raise RuntimeError(f'HTTP {r.status}')
                            self.ctype = r.headers.get('Content-Type', 'audio/mpeg').split(';')[0]
                            if not self.ctype.startswith('audio/'):
                                raise RuntimeError('not audio')
                            br = r.headers.get('icy-br', '').split(',')[0].strip()
                            self.rate = int(br) * 125 if br.isdigit() and 32 <= int(br) <= 320 else 16000
                            async for chunk in r.content.iter_chunked(4096):
                                self.feed(chunk)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f'  Radio {self.sid}: {url}:', str(e) or type(e).__name__)
                if time.time() - t0 > 60:   # it played for a while and then dropped: start over from the first address
                    fails = 0
                    break
            else:
                fails += 1
            await asyncio.sleep(2)
        self.failed = True
        for q in list(self.listeners):
            q.put_nowait(None)
        self.on_idle(self)

    def join(self, delay_ms):
        """A new listener: (backlog to send first, queue of live chunks, stream byte the backlog starts at)."""
        q = asyncio.Queue()
        end = self.base + len(self.buf)
        start = max(self.base, end - int((self.lag + delay_ms / 1000) * self.rate))
        self.listeners.add(q)
        if self.idle_t:
            self.idle_t.cancel(); self.idle_t = None
        return bytes(self.buf[start - self.base:]), q, start

    def leave(self, q):
        self.listeners.discard(q)
        if not self.listeners and not self.idle_t:
            self.idle_t = asyncio.get_running_loop().call_later(15, lambda: self.on_idle(self) if not self.listeners else None)

    def stop(self):
        self.task.cancel()


class PcAudio(Station):
    """PC audio: records one of this PC's sound outputs (WASAPI loopback, the 'soundcard' package), turns it into an
    MP3 stream with ffmpeg (imageio-ffmpeg, as for the fire copies) and hands it out like a radio station.
    Floyd sets Spotify's output to the living room TV in Windows' Volume mixer and picks that output here."""
    RATE, CH = 48000, 2

    def __init__(self, on_idle, out_name, demo=False):
        self.out_name, self.demo, self.error = out_name, demo, ''
        super().__init__('pc', on_idle)
        self.lag, self.rate = PC_AUDIO_LAG, 16000   # 128 kbit/s

    @staticmethod
    def clash(loop, sink):
        """A warning if the living room page would play into the very output PC audio records (silent TV, echo)."""
        if sink:
            return ''
        try:
            import soundcard
            d = soundcard.default_speaker().name
        except Exception:
            return ''
        if 'CABLE' in d or (loop and d == loop):
            return ('The PC\'s default output is "' + d + '", so the living room TV\'s sound goes there too and the TV stays quiet. '
                    'Set Windows\' default output back to your speakers and pick the TV under "Sound comes out of" above.')
        return ''

    @staticmethod
    def outputs():
        """Names of this PC's sound outputs (for the phone), or [] if 'soundcard' is missing."""
        try:
            import soundcard
            return [sp.name for sp in soundcard.all_speakers()][:16]
        except Exception:
            return []

    def capture(self, ff, stop):
        """Thread: PCM from the chosen output into ffmpeg. Silence (nothing playing) is padded so the stream never stalls."""
        try:
            if self.demo:   # a quiet 440 Hz tone instead of a real output
                import math, struct
                block = b''.join(struct.pack('<hh', v, v) for v in (int(3000 * math.sin(2 * math.pi * 440 * i / self.RATE)) for i in range(4800)))
                t0, n = time.time(), 0
                while not stop.is_set():   # paced by the clock, like a real sound card
                    ff.stdin.write(block); ff.stdin.flush(); n += 1
                    time.sleep(max(0, t0 + n * .1 - time.time()))
                return
            if os.name == 'nt':
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0)   # soundcard needs COM in this thread
            import soundcard
            import numpy
            sp = next((x for x in soundcard.all_speakers() if x.name == self.out_name), None) or soundcard.default_speaker()
            # What that output plays (by its exact id). Loopback needs no microphone permission; for VB-Cable the
            # cable's recording side "CABLE Output" is the fallback (that one needs Windows' microphone access).
            try:
                mic = soundcard.get_microphone(id=sp.id, include_loopback=True)
            except Exception:
                mic = next((m for m in soundcard.all_microphones() if 'CABLE Output' in m.name), None) if 'CABLE' in sp.name else None
                if mic is None:
                    raise
            print(f'TV: PC audio is recording "{mic.name}".')
            import warnings
            warnings.filterwarnings('ignore', message='data discontinuity')   # a dropped packet now and then; don't flood the window
            # Big buffer, read in steady 50 ms blocks with no pauses: reading too little too often made Windows drop
            # audio ("data discontinuity") and the kitchen heard it garbled. soundcard itself returns silence while
            # nothing plays, so the stream keeps going.
            with mic.recorder(samplerate=self.RATE, channels=self.CH, blocksize=self.RATE // 5) as rec:
                peak, said = 0.0, time.time()
                while not stop.is_set():
                    data = rec.record(numframes=self.RATE // 20)
                    ff.stdin.write((numpy.clip(data, -1, 1) * 32767).astype('<i2').tobytes()); ff.stdin.flush()
                    peak = max(peak, float(numpy.abs(data).max()) if len(data) else 0.0)
                    if time.time() - said > 10:   # a level now and then, so a silent source is easy to spot
                        print(f'TV: PC audio level {round(peak * 100)}%' + ('  (silent: is anything playing into it?)' if peak < .001 else ''))
                        peak, said = 0.0, time.time()
        except ImportError:
            self.error = 'PC audio needs:  py -m pip install soundcard numpy'
        except Exception as e:
            if not stop.is_set():
                self.error = 'PC audio: ' + (str(e) or type(e).__name__)
        finally:
            try:
                ff.stdin.close()
            except Exception:
                pass

    async def pull(self):
        import threading
        loop, stop = asyncio.get_running_loop(), threading.Event()
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            self.error = 'PC audio needs:  py -m pip install imageio-ffmpeg'
            exe = None
        ff = None
        try:
            if exe:
                ff = subprocess.Popen([exe, '-loglevel', 'error', '-f', 's16le', '-ar', str(self.RATE), '-ac', str(self.CH), '-i', 'pipe:0',
                                       '-c:a', 'libmp3lame', '-b:a', '128k', '-f', 'mp3', '-flush_packets', '1', 'pipe:1'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                threading.Thread(target=self.capture, args=(ff, stop), daemon=True).start()
                while True:
                    chunk = await asyncio.to_thread(ff.stdout.read1, 4096)
                    if not chunk:
                        break
                    self.feed(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error = self.error or 'PC audio: ' + (str(e) or type(e).__name__)
        finally:
            stop.set()
            if ff and ff.poll() is None:
                ff.kill()
            if self.error:
                print(' ', self.error)
            self.failed = True
            for q in list(self.listeners):
                q.put_nowait(None)
            loop.call_soon(self.on_idle, self)


def setup(app, demo=False, lights=None, cast=False):
    """Add the /tv routes to the relay (behind its key check) and, with --cast, the TV's own server."""
    cfg = load_config()
    enabled = cast or demo
    token = secrets.token_urlsafe(18)           # new every start; never the relay key
    host = '127.0.0.1' if demo else lan_ip()
    # Old single-TV settings become the kitchen's
    rcfg = cfg.setdefault('rooms', {})
    rcfg.setdefault('cast', {})
    rcfg.setdefault('pc', {})
    for k in ('sound', 'shape'):
        if k in cfg:
            rcfg['cast'].setdefault(k, cfg.pop(k))
    cfg['schedule'] = [dict(r, room=r.get('room', 'cast')) for r in cfg['schedule']] if isinstance(cfg.get('schedule'), list) else None
    if cfg['schedule'] is None:
        del cfg['schedule']
    hub = {'board': cfg.get('board', [])[-12:], 'weather': None, 'weather_at': 0.0, 'ran': {}, 'stations': {}}

    def new_room(r):
        return {'id': r, 'socks': set(), 'screen': '', 'focus': '', 'want': None, 'app': '', 'started': 0.0, 'art': None, 'heard': None,
                'scene': '', 'scene_t': 0.0, 'watch': [], 'sleep_at': 0.0, 'fading': False, 'outs': [], 'play': None, 'behind': None,
                'sound': {'music': '', 'amb': '', 'mv': 70, 'av': 60, **rcfg[r].get('sound', {})}}
    rooms = {r: new_room(r) for r in ROOMS}
    page = {'html': None, 'at': 0.0}
    casts, problem = None, ''
    if not enabled:
        problem = 'TV is off on the relay. Start it with:  py kasa_bridge.py --cast'
    else:
        try:
            casts = FakeCasts() if demo else RealCasts()
        except ImportError:
            problem = 'pychromecast isn\'t installed on the PC. Run:  py -m pip install pychromecast'
    kiosk = FakeKiosk() if demo else Kiosk() if os.name == 'nt' else None
    # Once (Floyd, 2026-10-28): both TVs wake at 7:30 every day to the fireplace, its crackle and classical music.
    # His own saved schedule and Morning scene are updated, the rest of what he set is kept.
    if cfg.get('routine') != 1:
        cfg['routine'] = 1
        sc = cfg.setdefault('scenes', {})
        sc['morning'] = {**(sc.get('morning') if isinstance(sc.get('morning'), dict) else {}), 'page': 'fire', 'music': 'swiss-classic', 'amb': 'fire'}
        rows = [r for r in cfg.get('schedule', [dict(r, room='cast') for r in DEFAULT_SCHEDULE]) if r['scene'] != 'morning']
        every = [0, 1, 2, 3, 4, 5, 6]
        cfg['schedule'] = [{'scene': 'morning', 'at': '07:30', 'days': every, 'room': 'cast'}] + \
            ([{'scene': 'morning', 'at': '07:30', 'days': every, 'room': 'pc'}] if kiosk is not None else []) + rows
        save_config(cfg)
        print('TV: both TVs now wake at 7:30 to the fireplace and classical music (change it on the phone under Schedule).')

    def need():
        if casts is None:
            raise web.HTTPServiceUnavailable(text=problem)
        return casts

    def room_of(request, body=None):
        r = (body or {}).get('room') or request.query.get('room') or 'cast'
        if r not in ROOMS:
            raise web.HTTPBadRequest(text='Unknown TV.')
        if r == 'pc' and kiosk is None:
            raise web.HTTPBadRequest(text='The living room TV needs the relay to run on Windows.')
        return rooms[r]

    def tv_url(r='cast'):
        return f'http://{host or lan_ip() or "127.0.0.1"}:{TV_PORT}/?k={token}' + ('&room=pc' if r == 'pc' else '')

    def pick(body):
        cid = str(body.get('id') or cfg.get('cast') or '')
        if not cid:
            raise web.HTTPBadRequest(text='Pick your Chromecast first.')
        return cid

    async def send_tv(room, msg):
        sent = False
        for s in list(room['socks']):
            try:
                await s.send_json(msg); sent = True
            except Exception:
                room['socks'].discard(s)
        return sent

    async def send_all(msg):
        sent = False
        for room in rooms.values():
            sent = await send_tv(room, msg) or sent
        return sent

    async def lights_list():
        if lights is None:
            return {'lights': False}
        try:
            return {'lights': await lights.list()}
        except Exception as e:
            return {'lights': [], 'lightsError': str(e) or 'Lights didn\'t answer'}

    def units():
        u = cfg.get('units') or ''
        return u if u in ('F', 'C') else ('F' if (cfg.get('place') or {}).get('cc') in FAHRENHEIT else 'C')

    async def refresh_weather(push=True):
        p = cfg.get('place')
        if not p:
            hub['weather'] = None
        else:
            try:
                hub['weather'] = fake_forecast(p, units()) if demo else await forecast(p, units())
            except Exception as e:
                print('  Weather failed:', e)
                old = hub.get('weather')
                if not old or time.time() - old.get('at', 0) > 3 * 3600:
                    hub['weather'] = {'error': 'couldn\'t get the forecast', 'place': p['name']}
        hub['weather_at'] = time.time()
        if push:
            await send_all({'weather': hub['weather']})

    async def toggle_focused(room):
        """OK pressed on the phone while the TV shows Lights: switch the light the TV has selected."""
        f = room['focus']
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
            await send_tv(room, {'lightsError': getattr(e, 'text', None) or str(e) or 'Light didn\'t answer'})
        await send_all(await lights_list())

    # ---- the two TVs: on, off, and whether their screen is connected ----
    def linked(room):
        return bool(room['socks'])

    async def tv_on(room):
        """Open Phosphor TV on that TV: the Chromecast casts the page; the PC opens its full-screen window."""
        room['started'] = time.time()
        if room['id'] == 'pc':
            room['app'] = ''
            n = rcfg['pc'].get('monitor')
            if not isinstance(n, int):
                raise web.HTTPBadRequest(text='Pick which monitor is the living room TV first (Settings › Living room TV).')
            room['want'] = 'pc'
            await asyncio.to_thread(kiosk.open, tv_url('pc'), n)
        else:
            cid = cfg.get('cast')
            if not cid:
                raise web.HTTPBadRequest(text='Pick your Chromecast first.')
            room['want'] = cid
            await asyncio.wait_for(asyncio.to_thread(need().start, cid, tv_url('cast')), 20)

    async def tv_off(room):
        room['want'], room['scene'], room['sleep_at'], room['fading'] = None, '', 0.0, False
        if room['id'] == 'pc':
            room['app'] = ''
            await asyncio.to_thread(kiosk.close)
        elif cfg.get('cast'):
            await asyncio.wait_for(asyncio.to_thread(need().stop, cfg['cast']), 15)
        for s in list(room['socks']):
            await s.close()

    def sync_on():
        return bool(cfg.get('sync')) and kiosk is not None

    def offset():
        o = cfg.get('offset', 0)
        return o if isinstance(o, int) else 0

    def delay_of(r):   # ms this TV's music runs behind the other: + delays the living room, - the kitchen
        return max(0, offset()) if r == 'pc' else max(0, -offset())

    # ---- phone side: 127.0.0.1:7788 through Tailscale, behind the relay key ----
    async def body_of(request):
        try:
            b = await request.json()
            return b if isinstance(b, dict) else {}
        except Exception:
            return {}

    def room_view(room):
        on = linked(room)
        since = time.time() - room['started']
        r = room['id']
        app = ''
        if r == 'pc':
            power = on or (bool(room['want']) and kiosk.running())
            app = room['app'] if room['app'] and not on and kiosk.running() else ''
        else:
            power = None   # worked out from the Chromecast's status below
        return {'on': power, 'linked': on, 'app': app, 'screen': room['screen'] if on else '',
                'waiting': bool(room['want']) and not on and since < 30, 'stuck': bool(room['want']) and not on and since >= 30}

    async def info(request):
        c = need()
        room = room_of(request)
        r = room['id']
        devs = await asyncio.to_thread(c.devices)
        cid = cfg.get('cast') or ''
        video = [d for d in devs if d['video']]
        if not cid and len(video) == 1:
            cid = cfg['cast'] = video[0]['id']; save_config(cfg)
        st, err = None, ''
        if cid and r == 'cast':
            try:
                st = await asyncio.wait_for(asyncio.to_thread(c.status, cid), 15)
            except web.HTTPException as e:
                err = e.text
            except Exception as e:
                err = 'The Chromecast didn\'t answer: ' + (str(e) or type(e).__name__)
        tv = room_view(room)
        if r == 'cast':
            tv['on'] = tv['linked'] or (bool(st and st['appId'] == DASHCAST) and bool(room['want']))
        on = tv['linked']
        return web.json_response({
            'ok': True, 'version': VERSION, 'demo': c.fake, 'devices': devs, 'id': cid, 'cast': st, 'error': err,
            'room': r, 'rooms': [{'id': x, 'name': ROOM_NAMES[x], 'on': bool(rooms[x]['socks'])} for x in ROOMS if x == 'cast' or kiosk is not None],
            'pc': {'monitor': rcfg['pc'].get('monitor'), 'monitors': kiosk.monitors() if kiosk else [], 'outs': rooms['pc']['outs'],
                   'sink': rcfg['pc'].get('sink', ''), 'loop': rcfg['pc'].get('loop', ''),
                   'loops': ['Demo TV (HDMI)', 'Speakers'] if kiosk.fake else await asyncio.to_thread(PcAudio.outputs),
                   'audioErr': hub.get('pc_audio_err', '') or ('' if kiosk.fake else await asyncio.to_thread(PcAudio.clash, rcfg['pc'].get('loop', ''), rcfg['pc'].get('sink', '')))} if kiosk else None,
            'sync': sync_on(), 'offset': offset(), 'behind': {x: rooms[x]['behind'] if rooms[x]['play'] else None for x in ROOMS},
            'tv': tv, 'board': hub['board'], 'lights': lights is not None,
            'place': (cfg.get('place') or {}).get('name', ''), 'units': cfg.get('units') or '', 'unitsNow': units() if cfg.get('place') else '',
            'art': room['art'] if on else None, 'sound': room['sound'], 'heard': room['heard'] if on else None,
            'shape': shape(r), 'fit': fit(r), 'fires': {'ready': len(fires()), 'of': len(FIRE_FILMS), 'busy': hub.get('fire_busy', 0), 'error': hub.get('fire_err', '')},
            'scene': room['scene'] if on else '', 'focus': room['focus'] if on and room['screen'] == 'watch' else '', 'scenes': scenes(), 'schedule': schedule(), 'auto': cfg.get('auto', True),
            'sleep': max(0, int(room['sleep_at'] - time.time())) if room['sleep_at'] else 0, 'clock': time.strftime('%H:%M'),
            'screenLink': tv_url(r) if c.fake else ''})

    async def choose(request):
        body = await body_of(request)
        cfg['cast'] = pick(body); save_config(cfg)
        return web.json_response({'ok': True})

    async def place(request):
        """The Today page's weather: {city} to look a place up ('' clears it), and/or {units: 'F'|'C'|''} ('' = automatic)."""
        body = await body_of(request)
        if 'units' in body:
            u = str(body['units']).upper()
            if u not in ('', 'F', 'C'):
                raise web.HTTPBadRequest(text='Units are F, C or automatic.')
            cfg['units'] = u
        if 'city' in body:
            city = ' '.join(str(body['city']).split())[:80]
            if city:
                try:
                    cfg['place'] = {'name': city, 'lat': 0, 'lon': 0, 'cc': 'US'} if demo else await geocode(city)
                except web.HTTPException:
                    raise
                except Exception as e:
                    raise web.HTTPBadGateway(text='Couldn\'t look the city up: ' + (str(e) or type(e).__name__))
            else:
                cfg.pop('place', None)
        save_config(cfg)
        await refresh_weather()
        return web.json_response({'ok': True, 'place': (cfg.get('place') or {}).get('name', ''), 'units': cfg.get('units') or '',
                                  'unitsNow': units() if cfg.get('place') else '', 'weather': hub['weather']})

    def shape(r):
        s = rcfg[r].get('shape')
        return s if s in SHAPES else 'wide'

    def fit(r):
        f = rcfg[r].get('fit') if isinstance(rcfg[r].get('fit'), dict) else {}
        return {k: (f[k] if type(f.get(k)) is int and lo <= f[k] <= hi else FIT_DEFAULT[k]) for k, (lo, hi) in FIT_RANGE.items()}

    async def screen_shape(request):
        """{shape: 'wide'|'squeeze'|'box'} and/or {fit: {w, h, x, y}}; the TV shows a test pattern for a little while after a change."""
        body = await body_of(request)
        room = room_of(request, body)
        if 'shape' not in body and 'fit' not in body:
            raise web.HTTPBadRequest(text='Pick a screen shape.')
        if 'shape' in body:
            if body['shape'] not in SHAPES:
                raise web.HTTPBadRequest(text='Pick a screen shape.')
            rcfg[room['id']]['shape'] = body['shape']
        if 'fit' in body:
            f = body['fit']
            if not isinstance(f, dict) or any(type(f.get(k)) is not int or not lo <= f[k] <= hi for k, (lo, hi) in FIT_RANGE.items()):
                raise web.HTTPBadRequest(text='That screen fit is out of range.')
            rcfg[room['id']]['fit'] = {k: f[k] for k in FIT_RANGE}
        save_config(cfg)
        shown = await send_tv(room, {'shape': shape(room['id']), 'fit': fit(room['id']), 'test': True})
        return web.json_response({'ok': True, 'shape': shape(room['id']), 'fit': fit(room['id']), 'shown': shown})

    async def pc_setup(request):
        """The living room TV: {monitor: n} which of this PC's monitors it is, {sink: id} which sound output is its
        speakers ('' = the PC's default), {sync: bool} same music as the kitchen, {offset: ms} lining the two up."""
        body = await body_of(request)
        if kiosk is None:
            raise web.HTTPBadRequest(text='The living room TV needs the relay to run on Windows.')
        pc = rooms['pc']
        if 'monitor' in body:
            n = body['monitor']
            if not isinstance(n, int) or not any(m['n'] == n for m in kiosk.monitors()):
                raise web.HTTPBadRequest(text='No such monitor.')
            rcfg['pc']['monitor'] = n
            if pc['want'] or kiosk.running():   # move the window over
                await tv_on(pc)
        if 'sink' in body:
            s = body['sink']
            if not isinstance(s, str) or (s and not SINK_ID.match(s)):
                raise web.HTTPBadRequest(text='Unknown sound output.')
            rcfg['pc']['sink'] = s
            await send_tv(pc, {'sink': s})
        if 'loop' in body:   # which output PC audio records for the kitchen ('' = the PC's default output)
            n = body['loop']
            if not isinstance(n, str) or len(n) > 200:
                raise web.HTTPBadRequest(text='Unknown sound output.')
            rcfg['pc']['loop'] = n
            hub['pc_audio_err'] = ''
            old = hub['stations'].get('pc')
            if old:
                old.stop()
                await send_all({'resync': True})   # whoever plays PC audio rejoins on the new output
        resync = False
        if isinstance(body.get('sync'), bool):
            cfg['sync'] = body['sync']
            if body['sync']:   # the living room takes the kitchen's music and background
                for k in ('music', 'amb'):
                    pc['sound'][k] = rooms['cast']['sound'][k]
                rcfg['pc']['sound'] = pc['sound']
                await send_tv(pc, {'sound': pc['sound']})
                resync = True
        if isinstance(body.get('offset'), int):
            cfg['offset'] = max(-RADIO_MAX_OFFSET, min(RADIO_MAX_OFFSET, body['offset']))
            hub['align_until'] = time.time() + 15   # keep_in_step() lines the TVs up for the next 15 s, then leaves them be
        save_config(cfg)
        for r in ROOMS:
            await send_tv(rooms[r], {'delay': delay_of(r) if sync_on() else 0})
        if resync and sync_on():
            await send_all({'resync': True})   # turning sync on: both TVs rejoin the station
        return web.json_response({'ok': True, 'sync': sync_on(), 'offset': offset(), 'monitor': rcfg['pc'].get('monitor'),
                                  'sink': rcfg['pc'].get('sink', ''), 'loop': rcfg['pc'].get('loop', '')})

    async def start(request):
        body = await body_of(request)
        room = room_of(request, body)
        if room['id'] == 'cast':
            c, cid = need(), pick(body)
            if cfg.get('cast') != cid:
                cfg['cast'] = cid; save_config(cfg)
            room['want'], room['started'] = cid, time.time()
            await asyncio.wait_for(asyncio.to_thread(c.start, cid, tv_url('cast')), 20)
        else:
            await tv_on(room)
        return web.json_response({'ok': True})

    async def stop(request):
        body = await body_of(request)
        room = room_of(request, body)
        if room['id'] == 'cast':
            pick(body)
        await tv_off(room)
        return web.json_response({'ok': True})

    async def move(request):
        """Move to living room: whatever the kitchen Chromecast is casting (Hulu, Prime Video…) stops there and that
        service's site opens full screen on the living room TV, where Floyd picks it up from Continue Watching."""
        c, room = need(), rooms['pc']
        cid = cfg.get('cast')
        if kiosk is None:
            raise web.HTTPBadRequest(text='The living room TV needs the relay on Windows.')
        n = rcfg['pc'].get('monitor')
        if not isinstance(n, int):
            raise web.HTTPBadRequest(text='Pick which monitor is the living room TV first (Settings › Living room TV).')
        if not cid:
            raise web.HTTPBadRequest(text='Pick your Chromecast first.')
        st = await asyncio.wait_for(asyncio.to_thread(c.status, cid), 15)
        name, url = move_site(st['app'])
        if not url:
            if st['idle'] or st['appId'] == DASHCAST:
                raise web.HTTPBadRequest(text='Nothing is casting to the kitchen TV right now.')
            raise web.HTTPBadRequest(text=f'{st["app"] or "That app"} can\'t move yet. Works with Hulu, Prime Video, Peacock, Netflix, Disney+, Max and YouTube.')
        got = await asyncio.wait_for(asyncio.to_thread(c.release, cid), 20)
        if name == 'youtube' and YT_ID.match(got['video'] or ''):
            url = f'https://www.youtube.com/watch?v={got["video"]}&t={max(0, got["at"] - 3)}s'   # YouTube says which video and where
        if room['socks']:
            for s in list(room['socks']):
                await s.close()
        room['want'], room['scene'], room['sleep_at'] = None, '', 0.0
        await asyncio.to_thread(kiosk.open, url, n, False)
        room['app'] = got['app'] or st['app']
        print(f'TV: moved {room["app"]} from the kitchen to the living room.')
        return web.json_response({'ok': True, 'app': room['app'], 'title': got['title'], 'exact': url.startswith('https://www.youtube.com/watch')})

    async def volume(request):
        body = await body_of(request)
        room = room_of(request, body)
        if room['id'] == 'pc':
            raise web.HTTPBadRequest(text='Use the TV\'s own remote for its volume.')
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
        room = room_of(request, body)
        k = body.get('key')
        if k not in KEYS:
            raise web.HTTPBadRequest(text='Unknown key.')
        if not await send_tv(room, {'key': k}):
            raise web.HTTPConflict(text='Phosphor TV isn\'t on the TV right now.')
        if k == 'ok' and room['screen'] == 'lights':
            await toggle_focused(room)
        return web.json_response({'ok': True})

    async def open_(request):
        body = await body_of(request)
        room = room_of(request, body)
        s = body.get('screen')
        if s not in SCREENS:
            raise web.HTTPBadRequest(text='Unknown screen.')
        if not await send_tv(room, {'open': s}):
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
        shown = await send_all({'say': msg, 'board': hub['board']})
        return web.json_response({'ok': True, 'shown': shown, 'board': hub['board']})

    async def art(request):
        body = await body_of(request)
        room = room_of(request, body)
        msg = {}
        if isinstance(body.get('piece'), int) and 0 <= body['piece'] < ART_MAX:
            msg['piece'] = body['piece']
        if body.get('step') in (-1, 1):
            msg['step'] = body['step']
        for k in ('hold', 'full', 'pix'):
            if isinstance(body.get(k), bool):
                msg[k] = body[k]
        if not msg:
            raise web.HTTPBadRequest(text='Nothing to change.')
        if not await send_tv(room, {'art': msg}):
            raise web.HTTPConflict(text='Phosphor TV isn\'t on the TV right now.')
        return web.json_response({'ok': True})

    async def set_sound(room, change):
        """Change one TV's sound; with sync on, the music and background follow on the other TV too."""
        snd = room['sound']
        snd.update(change)
        rcfg[room['id']]['sound'] = snd
        shown = await send_tv(room, {'sound': snd})
        if sync_on():
            shared = {k: change[k] for k in ('music', 'amb') if k in change}
            if shared:
                other = rooms['pc' if room['id'] == 'cast' else 'cast']
                other['sound'].update(shared); rcfg[other['id']]['sound'] = other['sound']
                await send_tv(other, {'sound': other['sound']})
        save_config(cfg)
        return shown

    async def sound(request):
        body = await body_of(request)
        room = room_of(request, body)
        change = {}
        if 'music' in body:
            if body['music'] not in MUSIC + ('',):
                raise web.HTTPBadRequest(text='Unknown station.')
            change['music'] = body['music']
        if 'amb' in body:
            if body['amb'] not in AMBIENCE + ('',):
                raise web.HTTPBadRequest(text='Unknown sound.')
            change['amb'] = body['amb']
        for k in ('mv', 'av'):
            if isinstance(body.get(k), (int, float)):
                change[k] = max(0, min(100, int(body[k])))
        shown = await set_sound(room, change)
        return web.json_response({'ok': True, 'shown': shown, 'sound': room['sound']})

    # ---- scenes, the schedule and the sleep timer ----
    def scenes():
        saved = cfg.get('scenes') or {}
        return {k: {**v, **{f: saved[k][f] for f in v if isinstance(saved.get(k), dict) and f in saved[k]}} for k, v in SCENE_DEFAULTS.items()}

    def scene_screen(name):
        page = scenes().get(name, {}).get('page', '')
        return 'art' if page == 'fire' else 'dim' if page == 'night' else page

    def schedule():
        return cfg['schedule'] if isinstance(cfg.get('schedule'), list) else [dict(r, room='cast') for r in DEFAULT_SCHEDULE]

    def scene_view(name):
        page = scenes().get(name, {}).get('page')
        if page == 'fire':
            return {'art': {'piece': FIREPLACE, 'hold': True, 'full': True}}
        if page == 'art':
            return {'art': {'hold': False, 'full': False}}
        return {'open': page} if page in SCENE_PAGES else {}

    async def scene_lights(name):
        """Set the scene's lights (e.g. Romantic: the living room dim red). Failures only go to the log."""
        sc = scenes().get(name, {})
        look = GLOW.get(sc.get('glow') or '')
        if lights is None or not look:
            return
        try:
            found = await asyncio.wait_for(lights.list(), 30)   # also finds the lights if the relay just started
            ids = sc.get('lights')
            if ids == 'all':
                ids = [d['id'] for d in found]
            elif not isinstance(ids, list):
                ids = [d['id'] for d in found if 'living' in d['name'].lower()]
            for i in ids:
                try:
                    await asyncio.wait_for(lights.set(i, dict(look)), 10)
                except Exception as e:
                    print(f'  Scene lights: {i}:', getattr(e, 'text', '') or str(e) or type(e).__name__)
        except Exception as e:
            print('  Scene lights:', str(e) or type(e).__name__)

    async def wait_linked(room, seconds=45):
        """Open Phosphor TV on that TV if it isn't on, and wait for its screen to connect."""
        if room['socks']:
            return True
        await tv_on(room)
        for _ in range(seconds * 2):
            if room['socks']:
                await asyncio.sleep(1.5)   # let it settle in before giving it the scene
                return True
            await asyncio.sleep(.5)
        return False

    async def run_scene(room, name, why):
        print(f'TV ({ROOM_NAMES[room["id"]]}): {name} ({why}).')
        if name == 'off':
            await tv_off(room)
            return True
        room['scene'] = ''
        asyncio.ensure_future(scene_lights(name))
        if not await wait_linked(room):
            raise web.HTTPGatewayTimeout(text='Phosphor TV didn\'t come on. Try turning it on from the remote.')
        await set_sound(room, {f: scenes()[name][f] for f in ('music', 'amb')})
        room['scene'], room['scene_t'] = name, time.time()
        return await send_tv(room, scene_view(name))

    async def scene(request):
        body = await body_of(request)
        room = room_of(request, body)
        name = body.get('scene')
        if name not in SCENES:
            raise web.HTTPBadRequest(text='Unknown scene.')
        need()
        if name == 'bedtime':   # Bedtime puts every TV to sleep, not just the one the remote is on
            more = [r for r in rooms.values() if r is not room and (r['socks'] or r['want'])]
            for r, res in zip(more, await asyncio.gather(*(run_scene(r, name, 'bedtime everywhere') for r in more), return_exceptions=True)):
                if isinstance(res, Exception):
                    print(f'  Bedtime ({ROOM_NAMES[r["id"]]}):', getattr(res, 'text', '') or str(res) or type(res).__name__)
        shown = await run_scene(room, name, 'asked from the phone')
        return web.json_response({'ok': True, 'shown': shown})

    async def plan(request):
        """Save the schedule {schedule: [{scene, at: 'HH:MM', days: [0-6, Monday = 0], room}], auto: bool,
        scenes: {name: {page, music, amb, glow, lights: [light ids]}}}."""
        body = await body_of(request)
        if 'schedule' in body:
            rows = body['schedule']
            if not isinstance(rows, list) or len(rows) > 20:
                raise web.HTTPBadRequest(text='Up to 20 times.')
            clean = []
            for r in rows:
                at = r.get('at') if isinstance(r, dict) else None
                ok = isinstance(at, str) and len(at) == 5 and at[2] == ':' and at[:2].isdigit() and at[3:].isdigit() and int(at[:2]) < 24 and int(at[3:]) < 60
                days = sorted({d for d in r.get('days', []) if isinstance(d, int) and 0 <= d <= 6}) if ok else []
                if not ok or r.get('scene') not in SCENES:
                    raise web.HTTPBadRequest(text='Each time needs a scene and a time like 07:30.')
                clean.append({'scene': r['scene'], 'at': at, 'days': days, 'room': r.get('room') if r.get('room') in ROOMS else 'cast'})
            cfg['schedule'] = clean
        if isinstance(body.get('auto'), bool):
            cfg['auto'] = body['auto']
        if isinstance(body.get('scenes'), dict):
            saved = cfg.get('scenes') or {}
            for k, v in body['scenes'].items():
                if k in SCENE_DEFAULTS and isinstance(v, dict):
                    one = saved.setdefault(k, {})
                    if v.get('page', None) in SCENE_PAGES:
                        one['page'] = v['page']
                    if v.get('music', None) in MUSIC + ('',):
                        one['music'] = v['music']
                    if v.get('amb', None) in AMBIENCE + ('',):
                        one['amb'] = v['amb']
                    if v.get('glow', None) in tuple(GLOW) + ('',):
                        one['glow'] = v['glow']
                    if v.get('lights') == 'all':
                        one['lights'] = 'all'
                    elif isinstance(v.get('lights'), list) and len(v['lights']) <= 30 and all(isinstance(i, str) and LIGHT_ID.match(i) for i in v['lights']):
                        one['lights'] = v['lights']
            cfg['scenes'] = saved
        save_config(cfg)
        return web.json_response({'ok': True, 'schedule': schedule(), 'auto': cfg.get('auto', True), 'scenes': scenes()})

    async def sleep(request):
        """Sleep timer: {minutes: 1-240} turns that TV's Phosphor TV off then (the sound fades first); 0 cancels."""
        body = await body_of(request)
        room = room_of(request, body)
        m = body.get('minutes')
        if not isinstance(m, int) or not 0 <= m <= 240:
            raise web.HTTPBadRequest(text='Pick up to 4 hours.')
        if m and room['id'] == 'cast' and not cfg.get('cast'):
            raise web.HTTPBadRequest(text='Pick your Chromecast first.')
        room['sleep_at'] = time.time() + m * 60 if m else 0.0
        if room['fading'] and not m:
            await send_tv(room, {'sound': room['sound']})   # cancelled mid-fade: bring the sound back
        room['fading'] = False
        await send_tv(room, {'sleep': m * 60})
        return web.json_response({'ok': True, 'sleep': m * 60})

    async def clockwork():
        """Runs the schedule and the sleep timers, by this PC's clock."""
        while True:
            await asyncio.sleep(10)
            now = time.time()
            for room in rooms.values():
                try:
                    if room['sleep_at']:
                        if not room['fading'] and now >= room['sleep_at'] - SLEEP_FADE:
                            room['fading'] = True
                            await send_tv(room, {'fade': SLEEP_FADE})
                        if now >= room['sleep_at']:
                            await run_scene(room, 'off', 'sleep timer')
                except Exception as e:
                    print('  Sleep timer:', getattr(e, 'text', '') or str(e) or type(e).__name__)
            if not cfg.get('auto', True):
                continue
            t = time.localtime(now)
            hhmm, today = time.strftime('%H:%M', t), time.strftime('%Y-%m-%d', t)
            due = []
            for i, r in enumerate(schedule()):
                tag = f'{i}:{r["scene"]}:{r["at"]}:{r.get("room")}'
                if r['at'] == hhmm and t.tm_wday in r['days'] and hub['ran'].get(tag) != today:
                    hub['ran'][tag] = today
                    room = rooms.get(r.get('room')) or rooms['cast']
                    if room['id'] == 'pc' and kiosk is None:
                        continue
                    due.append(run_scene(room, r['scene'], 'scheduled for ' + r['at']))
            for res in await asyncio.gather(*due, return_exceptions=True):   # together, so both TVs wake at once
                if isinstance(res, Exception):
                    print('  Schedule:', getattr(res, 'text', '') or str(res) or type(res).__name__)

    async def watch(request):
        """What to Watch rows for the TV, sent by the phone (which holds the TMDB key; it never comes here).
        Only short text, a title key, a year, a TMDB poster path and service ids are passed on."""
        body = await body_of(request)
        room = room_of(request, body)
        rows = body.get('rows')
        if not isinstance(rows, list):
            raise web.HTTPBadRequest(text='Nothing to show.')
        clean = []
        for r in rows[:5]:
            if not isinstance(r, dict) or not isinstance(r.get('items'), list):
                continue
            items = []
            for it in r['items'][:20]:
                if not isinstance(it, dict) or not isinstance(it.get('k'), str) or not WATCH_KEY.match(it['k']):
                    continue
                poster, year = it.get('poster'), it.get('year')
                items.append({'k': it['k'], 'title': ' '.join(str(it.get('title') or '').split())[:80] or 'Untitled',
                              'year': year if isinstance(year, str) and len(year) == 4 and year.isdigit() else '',
                              'poster': poster if isinstance(poster, str) and WATCH_POSTER.match(poster) else '',
                              'svcs': [x for x in (it.get('svcs') or []) if x in WATCH_SVCS][:3] if isinstance(it.get('svcs'), list) else []})
            if items:
                clean.append({'title': ' '.join(str(r.get('title') or '').split())[:40], 'items': items})
        room['watch'] = clean
        msg = {'watch': clean}
        if body.get('show') is True:
            msg['open'] = 'watch'
        shown = await send_tv(room, msg)
        return web.json_response({'ok': True, 'shown': shown, 'rows': len(clean)})

    async def clear(request):
        hub['board'] = []
        cfg['board'] = []; save_config(cfg)
        await send_all({'board': []})
        return web.json_response({'ok': True, 'board': []})

    app.add_routes([web.get('/tv', info), web.post('/tv/choose', choose), web.post('/tv/start', start), web.post('/tv/stop', stop), web.post('/tv/move', move),
                    web.post('/tv/volume', volume), web.post('/tv/media', media), web.post('/tv/key', key),
                    web.post('/tv/open', open_), web.post('/tv/say', say), web.post('/tv/board/clear', clear), web.post('/tv/place', place),
                    web.post('/tv/shape', screen_shape), web.post('/tv/pc', pc_setup),
                    web.post('/tv/art', art), web.post('/tv/sound', sound),
                    web.post('/tv/scene', scene), web.post('/tv/schedule', plan), web.post('/tv/sleep', sleep),
                    web.post('/tv/watch', watch)])

    if casts is None:
        print('TV: off.', problem)
        return None

    # ---- TV side: port 7790 on the home network, TV token only: the page, its socket, the fire copies, the radio ----
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
        room = rooms['pc' if request.query.get('room') == 'pc' else 'cast']
        sock = web.WebSocketResponse(heartbeat=20, max_msg_size=8192)
        await sock.prepare(request)
        room['socks'].add(sock)
        print(f'TV ({ROOM_NAMES[room["id"]]}): the screen is connected.')
        try:
            if cfg.get('place') and time.time() - hub['weather_at'] > WEATHER_EVERY:
                await refresh_weather(push=False)
            hello = {'room': room['id'], 'radio': True, 'delay': delay_of(room['id']) if sync_on() else 0, 'shape': shape(room['id']), 'fit': fit(room['id']), 'fires': fires(), 'board': hub['board'], 'weather': hub['weather'],
                     'sound': room['sound'], 'watch': room['watch'], 'sleep': max(0, int(room['sleep_at'] - time.time())) if room['sleep_at'] else 0,
                     **(await lights_list() if lights is not None else {'lights': False})}
            if room['id'] == 'pc':
                hello['sink'] = rcfg['pc'].get('sink', '')
            await sock.send_json(hello)
            if room['scene']:   # the screen came back (it dropped off, or the PC restarted it): carry on with the scene
                room['scene_t'] = time.time()
                await sock.send_json(scene_view(room['scene']))
            async for msg in sock:
                if msg.type != WSMsgType.TEXT:
                    continue
                # Status only: which screen, which item is selected, what it hears. Anything else is ignored.
                try:
                    d = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get('screen') in SCREENS:
                    # Moving off the scene's screen by hand ends the scene (the TV takes a moment to get there first)
                    if room['scene'] and d['screen'] != scene_screen(room['scene']) and time.time() - room.get('scene_t', 0) > 5:
                        room['scene'] = ''
                    entering = d['screen'] in ('lights', 'today') and room['screen'] != d['screen']
                    room['screen'] = d['screen']
                    if entering:
                        await sock.send_json(await lights_list())
                if isinstance(d.get('focus'), str):
                    room['focus'] = d['focus'][:80]
                a = d.get('art')
                if isinstance(a, dict) and isinstance(a.get('i'), int) and 0 <= a['i'] < ART_MAX:
                    room['art'] = {'i': a['i'], 'hold': bool(a.get('hold')), 'full': bool(a.get('full')), 'pix': bool(a.get('pix'))}
                h = d.get('heard')
                if isinstance(h, dict) and h.get('state') in ('playing', 'loading', 'error', 'blocked', 'off'):
                    room['heard'] = {'state': h['state'], 'what': h.get('what') if h.get('what') in MUSIC + AMBIENCE else ''}
                if isinstance(d.get('pos'), (int, float)):
                    await keep_in_step(room, float(d['pos']), sock)
                outs = d.get('outs')   # the living room PC's sound outputs, by name, for picking the TV's speakers
                if room['id'] == 'pc' and isinstance(outs, list):
                    room['outs'] = [{'id': o['id'], 'label': ' '.join(str(o.get('label') or '').split())[:80] or 'Unnamed output'}
                                    for o in outs[:16] if isinstance(o, dict) and isinstance(o.get('id'), str) and SINK_ID.match(o['id'])]
        finally:
            room['socks'].discard(sock)
            if not room['socks']:
                room['screen'] = room['focus'] = ''
                room['art'] = room['heard'] = None
                print(f'TV ({ROOM_NAMES[room["id"]]}): the screen disconnected.')
        return sock

    def fires():
        """The pixelated copies made so far, as {film address: path on 7790}."""
        return {u: f'/fire/{i}.mp4?k={token}' for i, u in enumerate(FIRE_FILMS) if (FIRE_DIR / f'fire{i}.mp4').is_file()}

    async def tv_fire(request):
        """One of the relay's own pixelated fireplace copies, by number. Nothing else on disk is reachable."""
        n = int(request.match_info['n'])
        out = FIRE_DIR / f'fire{n}.mp4'
        if n >= len(FIRE_FILMS) or not out.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(out, headers={'Content-Type': 'video/mp4', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})

    def station_idle(st):
        if hub['stations'].get(st.sid) is st:
            del hub['stations'][st.sid]
        st.stop()

    async def keep_in_step(room, pos, sock):
        """Sync, measured by the relay itself: the TV reports how far into its stream it is playing (seconds since
        the first byte it got). The relay knows which stream byte that was and where the live edge is now, so it
        knows exactly how far behind live the TV plays, whatever its start-up time or buffering. It nudges the TV's
        speed (up to 5%) to hold lag + delay, or rejoins it at the right place when it's far off."""
        p = room.get('play')
        if not p or pos < 0:
            return
        st = p['st']
        behind = (st.base + len(st.buf) - (p['start'] + pos * st.rate)) / st.rate
        want = st.lag + (delay_of(room['id']) / 1000 if sync_on() else 0)
        err = behind - want
        room['behind'] = round(behind, 2)
        now = time.time()
        if now > hub.get('align_until', 0) or now - p['at'] < 2:
            return   # only lines up while the offset is being changed in Settings; otherwise it just measures
        if abs(err) > .15 and now - p['fix'] > 3:
            p['fix'] = now
            # Too far behind: jump forward within what the TV already holds. Too early: rejoin further back, then jump.
            await sock.send_json({'skip': round(err, 2)} if err > 0 else {'resync': True})

    async def tv_radio(request):
        """A station from RADIO's fixed list, fetched once by the relay and shared by both TVs."""
        sid = request.match_info['sid']
        if sid not in MUSIC or (sid == 'pc' and kiosk is None):
            raise web.HTTPNotFound()
        r = 'pc' if request.query.get('room') == 'pc' else 'cast'
        st = hub['stations'].get(sid)
        if st is None or st.failed:
            st = hub['stations'][sid] = PcAudio(station_idle, rcfg['pc'].get('loop', ''), kiosk.fake) if sid == 'pc' else Station(sid, station_idle)
        try:
            await asyncio.wait_for(st.ready.wait(), 15)
        except asyncio.TimeoutError:
            if not st.listeners:
                station_idle(st)
            if isinstance(st, PcAudio) and st.error:
                hub['pc_audio_err'] = st.error
            raise web.HTTPBadGateway(text='The station didn\'t answer.')
        spare = JOIN_SPARE if time.time() < hub.get('align_until', 0) else 0   # room to jump forward while lining up
        backlog, q, start = st.join((delay_of(r) if sync_on() else 0) + spare * 1000)
        play = rooms[r]['play'] = {'st': st, 'start': start, 'at': time.time(), 'fix': 0.0}   # for keep_in_step()
        resp = web.StreamResponse(headers={'Content-Type': st.ctype, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        try:
            await resp.prepare(request)
            await resp.write(backlog)
            while True:
                chunk = await q.get()
                if chunk is None:
                    break
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            st.leave(q)
            if rooms[r].get('play') is play:
                rooms[r]['play'] = None
        return resp

    async def make_fires():
        """Download each filmed fireplace once and save a pixelated copy. Runs in the background at start."""
        todo = [i for i in range(len(FIRE_FILMS)) if not (FIRE_DIR / f'fire{i}.mp4').is_file()]
        if not todo:
            return
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            hub['fire_err'] = 'needs imageio-ffmpeg'
            print('TV: for the pixelated fireplace, run once:  py -m pip install imageio-ffmpeg   then restart the relay.')
            return
        FIRE_DIR.mkdir(exist_ok=True)
        for i in todo:
            src, part, out = FIRE_DIR / f'src{i}.tmp', FIRE_DIR / f'fire{i}.part.mp4', FIRE_DIR / f'fire{i}.mp4'
            hub['fire_busy'] = i + 1
            try:
                print(f'TV: making pixelated fireplace {i + 1} of {len(FIRE_FILMS)} (a minute or two)...')
                async with ClientSession(timeout=ClientTimeout(total=600)) as s:
                    async with s.get(FIRE_FILMS[i]) as r:
                        if r.status != 200:
                            raise RuntimeError(f'download failed, HTTP {r.status}')
                        n = 0
                        with open(src, 'wb') as f:
                            async for chunk in r.content.iter_chunked(1 << 16):
                                n += len(chunk)
                                if n > FIRE_MAX:
                                    raise RuntimeError('video too big')
                                f.write(chunk)
                cmd = [exe, '-y', '-loglevel', 'error', '-i', str(src), '-t', '120', '-an', '-vf', FIRE_VF,
                       '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(part)]
                p = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=1200)
                if p.returncode:
                    raise RuntimeError(p.stderr.decode(errors='replace').strip()[-300:] or 'ffmpeg failed')
                part.replace(out)
                print(f'TV: pixelated fireplace {i + 1} is ready.')
                await send_all({'fires': fires()})
            except Exception as e:
                print(f'  Pixelated fireplace {i + 1}:', str(e) or type(e).__name__)
            finally:
                for t in (src, part):
                    try:
                        t.unlink()
                    except OSError:
                        pass
        hub['fire_busy'] = 0

    tv_app = web.Application(middlewares=[tv_guard])
    tv_app.add_routes([web.get('/', tv_page), web.get('/ws', tv_ws), web.get(r'/fire/{n:\d}.mp4', tv_fire),
                       web.get(r'/radio/{sid:[a-z-]{1,24}}', tv_radio)])
    runner = web.AppRunner(tv_app, access_log=None)

    async def keep_alive():
        """If a TV drops the screen by itself, open it again. On the Chromecast, casting something else
        (Netflix, YouTube) counts as turning Phosphor TV off; on the PC, closing the window does."""
        while True:
            await asyncio.sleep(30)
            if any(r['socks'] for r in rooms.values()) and cfg.get('place') and time.time() - hub['weather_at'] > WEATHER_EVERY:
                await refresh_weather()
            pc = rooms['pc']
            if pc['want'] and not pc['socks'] and time.time() - pc['started'] >= 40:
                if kiosk.running():
                    try:
                        print('TV (Living room): the screen dropped off, opening it again.')
                        await tv_on(pc)
                    except Exception as e:
                        print('  TV check failed:', getattr(e, 'text', '') or e)
                else:
                    print('TV (Living room): the window was closed, Phosphor TV is off there.')
                    pc['want'] = None
            room = rooms['cast']
            cid = room['want']
            if not cid or room['socks'] or time.time() - room['started'] < 40:
                continue
            try:
                st = await asyncio.to_thread(casts.status, cid)
                if st['idle'] or st['appId'] == DASHCAST:
                    print('TV (Kitchen): the screen dropped off, opening it again.')
                    room['started'] = time.time()
                    await asyncio.to_thread(casts.start, cid, tv_url('cast'))
                else:
                    print(f'TV (Kitchen): {st["app"] or "another app"} took over the TV, Phosphor TV is off.')
                    room['want'] = None
            except Exception as e:
                print('  TV check failed:', e)

    async def on_start(_app):
        await runner.setup()
        bind = host or '0.0.0.0'
        try:
            await web.TCPSite(runner, bind, TV_PORT).start()
            print(f'TV: listening on the home network at {bind}:{TV_PORT} for the TVs (TV token only).')
            if casts.fake:
                print(f'    Demo TV screens: {tv_url("cast")}  and  {tv_url("pc")}')
        except OSError as e:
            print(f'TV: could not listen on {bind}:{TV_PORT} ({e}). Is the relay already running in another window?')
        hub['task'] = asyncio.create_task(keep_alive())
        hub['clock'] = asyncio.create_task(clockwork())
        hub['fire'] = asyncio.create_task(make_fires())

    async def on_stop(_app):
        for k in ('task', 'clock', 'fire'):
            if hub.get(k):
                hub[k].cancel()
        for st in list(hub['stations'].values()):
            st.stop()
        for room in rooms.values():
            for s in list(room['socks']):
                await s.close()
        if kiosk:
            kiosk.close()
        await runner.cleanup()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    if not host and not demo:
        print('TV: could not tell this PC\'s home-network address, so port 7790 listens on all networks.')
    print('TV: on' + (' (demo).' if casts.fake else '. Looking for Chromecasts on the Wi-Fi.')
          + (' The living room TV (a monitor on this PC) is available too.' if kiosk and not casts.fake else ''))
    return casts


if __name__ == '__main__':
    print(__doc__)
