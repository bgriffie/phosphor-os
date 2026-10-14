"""PC control for the Phosphor OS relay: the phone's PC app uses it as a trackpad and keyboard,
for media keys, and to put shows, videos and windows on a chosen monitor.

Drop this file next to kasa_bridge.py; the relay loads it on start. Nothing to install: it uses
only Windows itself (through ctypes). Delete the file to turn PC control off.
Every request needs the same secret key as the lights, and Tailscale keeps out everyone else.

    python pc_control.py --identify     show each monitor's number on that monitor for 3 s
"""
import asyncio, json, os, re, struct, subprocess, sys, time, zlib

from aiohttp import web, WSMsgType

WINDOWS = sys.platform == 'win32'

# ---------- Keys ----------
VK = {'enter': 0x0D, 'esc': 0x1B, 'tab': 0x09, 'backspace': 0x08, 'delete': 0x2E, 'insert': 0x2D, 'space': 0x20,
      'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28, 'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
      'win': 0x5B, 'ctrl': 0x11, 'alt': 0x12, 'shift': 0x10, 'printscreen': 0x2C, 'apps': 0x5D,
      'playpause': 0xB3, 'next': 0xB0, 'prev': 0xB1, 'stop': 0xB2, 'volup': 0xAF, 'voldown': 0xAE, 'mute': 0xAD}
VK.update({f'f{i}': 0x6F + i for i in range(1, 13)})
VK.update({c: ord(c.upper()) for c in 'abcdefghijklmnopqrstuvwxyz0123456789'})
EXTENDED = {0x25, 0x26, 0x27, 0x28, 0x24, 0x23, 0x21, 0x22, 0x2D, 0x2E, 0x5B, 0x5D, 0x2C,
            0xB3, 0xB0, 0xB1, 0xB2, 0xAF, 0xAE, 0xAD}
BUTTONS = {'left': (0x0002, 0x0004), 'right': (0x0008, 0x0010), 'middle': (0x0020, 0x0040)}
BROWSERS = {   # name -> (exe, places it is usually installed)
    'chrome': ('chrome.exe', [r'%ProgramFiles%\Google\Chrome\Application', r'%ProgramFiles(x86)%\Google\Chrome\Application', r'%LocalAppData%\Google\Chrome\Application']),
    'edge': ('msedge.exe', [r'%ProgramFiles(x86)%\Microsoft\Edge\Application', r'%ProgramFiles%\Microsoft\Edge\Application']),
    'brave': ('brave.exe', [r'%ProgramFiles%\BraveSoftware\Brave-Browser\Application', r'%LocalAppData%\BraveSoftware\Brave-Browser\Application']),
    'firefox': ('firefox.exe', [r'%ProgramFiles%\Mozilla Firefox', r'%ProgramFiles(x86)%\Mozilla Firefox']),
}
AUTO_ORDER = ['chrome', 'edge', 'brave', 'firefox']
URL_OK = re.compile(r'^https?://[^\s"]+$', re.I)


def key_vk(name):
    name = str(name).lower()
    if name not in VK:
        raise web.HTTPBadRequest(text=f'Unknown key: {name}')
    return VK[name]


def find_browsers():
    out = {}
    for name, (exe, dirs) in BROWSERS.items():
        for d in dirs:
            p = os.path.join(os.path.expandvars(d), exe)
            if os.path.exists(p):
                out[name] = p
                break
    return out


# ---------- Windows backend (ctypes, no packages) ----------
if WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    try:   # real pixels on scaled monitors, so window positions land where they should
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                    ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ('u',)
        _fields_ = [('type', wintypes.DWORD), ('u', _INPUTUNION)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                    ('dwFlags', wintypes.DWORD), ('szDevice', wintypes.WCHAR * 32)]

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumDisplayMonitors.argtypes = (wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM)
    user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW))
    user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    for f in ('IsWindow', 'IsWindowVisible', 'IsIconic', 'IsZoomed', 'SetForegroundWindow', 'BringWindowToTop'):
        getattr(user32, f).argtypes = (wintypes.HWND,)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD), ('biSizeImage', wintypes.DWORD),
                    ('biXPelsPerMeter', wintypes.LONG), ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]

    user32.GetDC.argtypes = (wintypes.HWND,); user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,); gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ); gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SetStretchBltMode.argtypes = (wintypes.HDC, ctypes.c_int)
    gdi32.SetBrushOrgEx.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
    gdi32.StretchBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,); gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    try:
        dwmapi = ctypes.WinDLL('dwmapi')
    except OSError:
        dwmapi = None


class WinPC:
    """Real mouse, keyboard, monitors and windows."""
    fake = False

    def __init__(self):
        self.fx = self.fy = 0.0   # leftover fractions of a pixel from the trackpad

    # --- input ---
    def _send(self, *items):
        arr = (INPUT * len(items))(*items)
        if user32.SendInput(len(items), arr, ctypes.sizeof(INPUT)) != len(items):
            raise web.HTTPConflict(text='Windows blocked the input. This happens over the lock screen and in apps running as administrator.')

    @staticmethod
    def _mouse(flags, data=0):
        i = INPUT(type=0); i.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0); return i

    @staticmethod
    def _key(vk=0, up=False, char=None):
        i = INPUT(type=1)
        if char is not None:   # a typed character, whatever the keyboard layout
            i.ki = KEYBDINPUT(0, char, 0x0004 | (0x0002 if up else 0), 0, 0)
        else:
            i.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), (0x0001 if vk in EXTENDED else 0) | (0x0002 if up else 0), 0, 0)
        return i

    def move(self, dx, dy):
        self.fx += dx; self.fy += dy
        ix, iy = int(self.fx), int(self.fy)
        self.fx -= ix; self.fy -= iy
        if ix or iy:
            p = wintypes.POINT(); user32.GetCursorPos(ctypes.byref(p))
            user32.SetCursorPos(p.x + ix, p.y + iy)

    def button(self, name, down=None, count=1):
        d, u = BUTTONS.get(name, BUTTONS['left'])
        if down is None:
            self._send(*[self._mouse(f) for _ in range(max(1, min(3, count))) for f in (d, u)])
        else:
            self._send(self._mouse(d if down else u))

    def scroll(self, dx, dy):
        items = []
        if dy: items.append(self._mouse(0x0800, int(dy)))
        if dx: items.append(self._mouse(0x1000, int(dx)))
        if items: self._send(*items)

    def type(self, text):
        items = []
        for ch in text.replace('\r\n', '\n'):
            if ch == '\n':
                items += [self._key(VK['enter']), self._key(VK['enter'], True)]
                continue
            for unit in [int.from_bytes(ch.encode('utf-16-le')[i:i + 2], 'little') for i in range(0, len(ch.encode('utf-16-le')), 2)]:
                items += [self._key(char=unit), self._key(char=unit, up=True)]
        if items: self._send(*items)

    def keys(self, names):
        vks = [key_vk(n) for n in names]
        self._send(*[self._key(v) for v in vks], *[self._key(v, True) for v in reversed(vks)])

    def point(self, n, fx, fy):
        """Put the pointer at a spot on monitor n (fractions of its width and height), for the live view."""
        m = next((m for m in self.monitors() if m['n'] == n), None)
        if m:
            user32.SetCursorPos(m['x'] + round(max(0, min(1, fx)) * (m['w'] - 1)), m['y'] + round(max(0, min(1, fy)) * (m['h'] - 1)))

    def cursor(self):
        p = wintypes.POINT(); user32.GetCursorPos(ctypes.byref(p)); return p.x, p.y

    def shot(self, m, width):
        """A picture of monitor m, scaled to width: (width, height, BGRA bytes)."""
        x, y, w, h = m['x'], m['y'], m['w'], m['h']
        tw = max(32, min(int(width), w)); th = max(18, round(h * tw / w))
        hdc = user32.GetDC(None); mdc = gdi32.CreateCompatibleDC(hdc)
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), tw, -th, 1, 32, 0, 0, 0, 0, 0, 0)   # top-down
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(mdc, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
        try:
            old = gdi32.SelectObject(mdc, bmp)
            gdi32.SetStretchBltMode(mdc, 4); gdi32.SetBrushOrgEx(mdc, 0, 0, None)   # HALFTONE: smooth scaling
            gdi32.StretchBlt(mdc, 0, 0, tw, th, hdc, x, y, w, h, 0x00CC0020 | 0x40000000)   # SRCCOPY | CAPTUREBLT
            data = ctypes.string_at(bits, tw * th * 4)
            gdi32.SelectObject(mdc, old)
        finally:
            gdi32.DeleteObject(bmp); gdi32.DeleteDC(mdc); user32.ReleaseDC(None, hdc)
        return tw, th, data

    # --- monitors ---
    def monitors(self):
        if time.time() - getattr(self, '_mt', 0) < 3:   # the live view asks often; the layout rarely changes
            return self._mons
        found = []
        def cb(hmon, hdc, rect, lp):
            mi = MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
            r, w = mi.rcMonitor, mi.rcWork
            found.append({'handle': int(hmon), 'x': r.left, 'y': r.top, 'w': r.right - r.left, 'h': r.bottom - r.top,
                          'work': [w.left, w.top, w.right - w.left, w.bottom - w.top], 'primary': bool(mi.dwFlags & 1),
                          'device': mi.szDevice})
            return True
        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
        self._mons, self._mt = number_monitors(found), time.time()
        return self._mons

    # --- windows ---
    @staticmethod
    def _title(h):
        n = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1); user32.GetWindowTextW(h, buf, n + 1); return buf.value

    @staticmethod
    def _exe(h):
        pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        hp = kernel32.OpenProcess(0x1000, False, pid.value)
        if not hp:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(512); size = wintypes.DWORD(512)
            return os.path.basename(buf.value) if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)) else ''
        finally:
            kernel32.CloseHandle(hp)

    @staticmethod
    def _cloaked(h):
        if not dwmapi:
            return False
        v = ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(wintypes.HWND(h), 14, ctypes.byref(v), ctypes.sizeof(v))
        return bool(v.value)

    def _app_windows(self):
        hs = []
        def cb(h, lp):
            if (user32.IsWindowVisible(h) and not user32.GetWindow(h, 4)   # no owner
                    and not (user32.GetWindowLongW(h, -20) & 0x80)          # not a tool window
                    and user32.GetWindowTextLengthW(h) and not self._cloaked(h)):
                hs.append(h)
            return True
        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return hs

    def windows(self):
        mons = {m['handle']: m['n'] for m in self.monitors()}
        out = []
        for h in self._app_windows():
            t = self._title(h)
            if t in ('Program Manager', 'Windows Input Experience'):
                continue
            exe = self._exe(h)
            out.append({'id': int(h), 'title': t, 'app': nice_app(exe),
                        'monitor': mons.get(int(user32.MonitorFromWindow(h, 2) or 0)), 'min': bool(user32.IsIconic(h))})
        return out

    def focus(self, h):
        if user32.IsIconic(h):
            user32.ShowWindow(h, 9)
        # Windows only lets the app the user last touched take focus; a tap of Alt counts as that
        self._send(self._key(VK['alt']), self._key(VK['alt'], True))
        user32.SetForegroundWindow(h); user32.BringWindowToTop(h)

    def place(self, h, mon, fullscreen=False):
        if not user32.IsWindow(h):
            raise web.HTTPNotFound(text='That window has closed. Refresh the list.')
        x, y, w, hh = mon['work']
        user32.ShowWindow(h, 9)   # restore, so it can move
        user32.SetWindowPos(h, None, x + w // 8, y + hh // 8, w * 3 // 4, hh * 3 // 4, 0x0004 | 0x0010)
        user32.ShowWindow(h, 3)   # maximise on the new monitor
        self.focus(h)
        if fullscreen:
            time.sleep(0.25); self.keys(['f11'])

    def window_action(self, h, action):
        if not user32.IsWindow(h):
            raise web.HTTPNotFound(text='That window has closed. Refresh the list.')
        if action == 'focus':
            self.focus(h)
        elif action == 'fullscreen':
            self.focus(h); time.sleep(0.2); self.keys(['f11'])
        elif action == 'minimize':
            user32.ShowWindow(h, 6)
        elif action == 'close':
            user32.PostMessageW(h, 0x0010, 0, 0)   # WM_CLOSE: the app can still ask to save first
        else:
            raise web.HTTPBadRequest(text='Unknown action')

    def open_url(self, url, mon, fullscreen, browser):
        found = find_browsers()
        name = browser if browser in found else next((b for b in AUTO_ORDER if b in found), None)
        if not name:
            raise web.HTTPNotFound(text='No Chrome, Edge, Brave or Firefox found on this PC.')
        exe = BROWSERS[name][0].lower()
        before = set(self._app_windows())
        flag = '-new-window' if name == 'firefox' else '--new-window'
        subprocess.Popen([found[name], flag, url], close_fds=True)
        # Wait for the browser's new window, then put it on the chosen monitor
        end = time.time() + 10
        while time.time() < end:
            time.sleep(0.3)
            new = [h for h in self._app_windows() if h not in before and self._exe(h).lower() == exe]
            if new:
                time.sleep(0.4)   # let it finish drawing before it moves
                self.place(new[0], mon, fullscreen)
                return name
        raise web.HTTPGatewayTimeout(text=f'{name.title()} opened the link but its window didn\'t show up to move. Try again.')


class FakePC:
    """For trying the app on a computer that isn't Windows (or with --demo): logs what it would do."""
    fake = True

    def __init__(self):
        self.mons = number_monitors([
            {'handle': 1, 'x': 0, 'y': 0, 'w': 2560, 'h': 1440, 'work': [0, 0, 2560, 1400], 'primary': True, 'device': 'DISPLAY1'},
            {'handle': 2, 'x': 2560, 'y': 0, 'w': 1920, 'h': 1080, 'work': [2560, 0, 1920, 1040], 'primary': False, 'device': 'DISPLAY2'},
            {'handle': 3, 'x': -1920, 'y': 200, 'w': 1920, 'h': 1080, 'work': [-1920, 200, 1920, 1040], 'primary': False, 'device': 'DISPLAY3'}])
        self.wins = [{'id': 101, 'title': 'Severance | Apple TV+ - Google Chrome', 'app': 'Chrome', 'monitor': 2, 'min': False},
                     {'id': 102, 'title': 'Spotify Premium', 'app': 'Spotify', 'monitor': 1, 'min': False},
                     {'id': 103, 'title': 'Discord', 'app': 'Discord', 'monitor': 3, 'min': True}]
        self.moved = [0, 0]

    def move(self, dx, dy): self.moved[0] += dx; self.moved[1] += dy
    def button(self, name, down=None, count=1): print('  [pc] click', name, 'down' if down else 'up' if down is False else f'x{count}')
    def scroll(self, dx, dy): print('  [pc] scroll', dx, dy)
    def type(self, text): print('  [pc] type', repr(text))
    def keys(self, names): [key_vk(n) for n in names]; print('  [pc] keys', '+'.join(names))
    def monitors(self): return self.mons
    def windows(self): return self.wins
    def point(self, n, fx, fy):
        m = next(m for m in self.mons if m['n'] == n); self.cur = (m['x'] + int(fx * m['w']), m['y'] + int(fy * m['h'])); print('  [pc] point', n, round(fx, 2), round(fy, 2))
    def cursor(self): return getattr(self, 'cur', (100, 100))

    def shot(self, m, width):
        # a test card: dark screen, a green band per window on it, brighter on the main monitor
        tw = max(32, min(int(width), m['w'])); th = max(18, round(m['h'] * tw / m['w']))
        row = bytearray([10, 20 + 15 * m['n'], 8, 255]) * tw
        band = bytearray([60, 200, 90, 255]) * (tw // 2) + bytearray([10, 30, 10, 255]) * (tw - tw // 2)
        rows = []
        on = [w for w in self.wins if w['monitor'] == m['n'] and not w['min']]
        for yy in range(th):
            k = yy * 8 // th
            rows.append(band if k < len(on) * 2 and k % 2 == 0 else row)
        return tw, th, bytes(b''.join(rows))

    def _win(self, h):
        w = next((w for w in self.wins if w['id'] == h), None)
        if not w: raise web.HTTPNotFound(text='That window has closed. Refresh the list.')
        return w

    def place(self, h, mon, fullscreen=False):
        w = self._win(h); w['monitor'] = mon['n']; w['min'] = False; print('  [pc] window', h, '-> monitor', mon['n'], 'full screen' if fullscreen else '')

    def window_action(self, h, action):
        w = self._win(h); print('  [pc] window', h, action)
        if action == 'close': self.wins.remove(w)
        if action == 'minimize': w['min'] = True

    def open_url(self, url, mon, fullscreen, browser):
        print('  [pc] open', url, 'on monitor', mon['n'], 'full screen' if fullscreen else '', browser)
        self.wins.insert(0, {'id': 200 + len(self.wins), 'title': url, 'app': 'Chrome', 'monitor': mon['n'], 'min': False})
        return 'chrome'


def number_monitors(found):
    """Monitor 1, 2, 3… counted left to right (then top to bottom), the way they sit on the desk."""
    found.sort(key=lambda m: (m['x'], m['y']))
    for i, m in enumerate(found, 1):
        m['n'] = i
    return found


def nice_app(exe):
    base = exe[:-4] if exe.lower().endswith('.exe') else exe
    return {'msedge': 'Edge', 'chrome': 'Chrome', 'firefox': 'Firefox', 'brave': 'Brave', 'explorer': 'File Explorer',
            'vlc': 'VLC', 'applicationframehost': 'App'}.get(base.lower(), base[:1].upper() + base[1:])


# ---------- Pictures ----------
try:
    from PIL import Image   # optional: with Pillow the live view sends small JPEGs; without it, PNGs
except ImportError:
    Image = None


def encode(w, h, bgra, quality=60):
    """BGRA pixels -> (bytes, content type)."""
    if Image:
        import io
        out = io.BytesIO()
        Image.frombuffer('RGB', (w, h), bgra, 'raw', 'BGRX', 0, 1).save(out, 'JPEG', quality=quality)
        return out.getvalue(), 'image/jpeg'
    rgb = bytearray(w * h * 3)
    rgb[0::3] = bgra[2::4]; rgb[1::3] = bgra[1::4]; rgb[2::3] = bgra[0::4]
    rw = w * 3
    raw = b''.join(b'\x00' + rgb[i * rw:(i + 1) * rw] for i in range(h))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 3)) + chunk(b'IEND', b'')
    return png, 'image/png'


# ---------- HTTP ----------
def setup(app, demo=False):
    """Add the /pc routes to the relay's aiohttp app (behind its key check)."""
    pc = FakePC() if demo or not WINDOWS else WinPC()
    lock = asyncio.Lock()   # one window move or launch at a time

    def monitor(n):
        mons = pc.monitors()
        m = next((m for m in mons if m['n'] == int(n or 0)), None)
        if not m:
            raise web.HTTPNotFound(text=f'There is no monitor {n}. This PC has {len(mons)}.')
        return m

    def handle(ev):
        """One input event from the trackpad or keyboard."""
        if 'm' in ev: pc.move(float(ev['m'][0]), float(ev['m'][1]))
        elif 's' in ev: pc.scroll(int(ev['s'][0]), int(ev['s'][1]))
        elif 'c' in ev: pc.button(ev['c'], None, int(ev.get('n', 1)))
        elif 'down' in ev: pc.button(ev['down'], True)
        elif 'up' in ev: pc.button(ev['up'], False)
        elif 't' in ev: pc.type(str(ev['t'])[:2000])
        elif 'k' in ev: pc.keys(ev['k'] if isinstance(ev['k'], list) else [ev['k']])
        elif 'at' in ev: pc.point(int(ev['at'][0]), float(ev['at'][1]), float(ev['at'][2]))

    def public(m):
        return {k: m[k] for k in ('n', 'x', 'y', 'w', 'h', 'primary')}

    async def info(request):
        return web.json_response({'ok': True, 'demo': pc.fake, 'live': True, 'stream': True, 'monitors': [public(m) for m in pc.monitors()],
                                  'browsers': sorted(find_browsers()) if not pc.fake else ['chrome', 'edge']})

    async def windows(request):
        return web.json_response({'windows': await asyncio.to_thread(pc.windows)})

    async def input_(request):
        for ev in (await request.json()).get('events', [])[:500]:
            handle(ev)
        return web.json_response({'ok': True})

    async def open_(request):
        b = await request.json()
        url = str(b.get('url', '')).strip()
        if not URL_OK.match(url):
            raise web.HTTPBadRequest(text='That isn\'t a web link. It should start with https://')
        m = monitor(b.get('monitor', 1))
        async with lock:
            used = await asyncio.to_thread(pc.open_url, url, m, bool(b.get('fullscreen', True)), b.get('browser', 'auto'))
        return web.json_response({'ok': True, 'browser': used, 'monitor': m['n']})

    async def window(request):
        b = await request.json()
        h = int(b.get('id', 0))
        async with lock:
            if b.get('action', 'move') == 'move':
                await asyncio.to_thread(pc.place, h, monitor(b.get('monitor')), bool(b.get('fullscreen')))
            else:
                await asyncio.to_thread(pc.window_action, h, b['action'])
        return web.json_response({'windows': await asyncio.to_thread(pc.windows)})

    async def shot(request):
        m = monitor(request.query.get('mon', 1))
        width = max(64, min(1600, int(request.query.get('w', 480))))
        w, h, px = await asyncio.to_thread(pc.shot, m, width)
        body, ctype = await asyncio.to_thread(encode, w, h, px, int(request.query.get('q', 60)))
        cx, cy = pc.cursor()
        on = m['x'] <= cx < m['x'] + m['w'] and m['y'] <= cy < m['y'] + m['h']
        return web.Response(body=body, content_type=ctype, headers={
            'Cache-Control': 'no-store', 'Access-Control-Expose-Headers': 'X-Cursor',
            'X-Cursor': f'{(cx - m["x"]) / m["w"]:.4f},{(cy - m["y"]) / m["h"]:.4f}' if on else ''})

    async def identify(request):
        if not pc.fake:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), '--identify'], close_fds=True)
        return web.json_response({'ok': True})

    async def ws(request):
        # The key check happens before this, using ?key= (browsers can't add headers to a WebSocket)
        sock = web.WebSocketResponse(heartbeat=20)
        await sock.prepare(request)
        # Live view: {"stream": {"mon": 2, "w": 1280, "q": 55}} starts a stream of pictures of that
        # monitor down this socket (a JSON "frame" line, then the JPEG/PNG), {"stream": null} stops it.
        # The phone acks each picture; with two unacked the stream waits, so a slow link never lags behind.
        st = {'cfg': None, 'unacked': 0, 'kick': asyncio.Event()}

        async def streamer():
            while not sock.closed:
                cfg = st['cfg']
                if not cfg or st['unacked'] >= 2:
                    st['kick'].clear()
                    try:
                        await asyncio.wait_for(st['kick'].wait(), 3)
                    except asyncio.TimeoutError:
                        st['unacked'] = 0   # a lost ack shouldn't stall the stream for good
                    continue
                t0 = time.monotonic()
                try:
                    m = monitor(cfg['mon'])
                    w, h, px = await asyncio.to_thread(pc.shot, m, cfg['w'])
                    body, ctype = await asyncio.to_thread(encode, w, h, px, cfg['q'])
                    cx, cy = pc.cursor()
                    on = m['x'] <= cx < m['x'] + m['w'] and m['y'] <= cy < m['y'] + m['h']
                    if sock.closed or st['cfg'] is not cfg:
                        continue
                    await sock.send_json({'frame': {'mon': m['n'], 'type': ctype,
                                                    'cursor': f'{(cx - m["x"]) / m["w"]:.4f},{(cy - m["y"]) / m["h"]:.4f}' if on else ''}})
                    await sock.send_bytes(body)
                    st['unacked'] += 1
                except web.HTTPException as e:
                    await sock.send_json({'error': e.text}); await asyncio.sleep(2)
                except Exception as e:
                    if sock.closed:
                        break
                    await sock.send_json({'error': 'Picture failed: ' + str(e)}); await asyncio.sleep(2)
                await asyncio.sleep(max(0.0, 1 / 15 - (time.monotonic() - t0)))   # at most ~15 pictures a second

        task = asyncio.create_task(streamer())
        try:
            async for msg in sock:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    for ev in json.loads(msg.data):
                        if 'stream' in ev:
                            c = ev['stream']
                            st['cfg'] = c and {'mon': int(c.get('mon', 1)), 'w': max(64, min(1600, int(c.get('w', 960)))), 'q': int(c.get('q', 55))}
                            st['unacked'] = 0; st['kick'].set()
                        elif 'ack' in ev:
                            st['unacked'] = max(0, st['unacked'] - 1); st['kick'].set()
                        else:
                            handle(ev)
                except web.HTTPException as e:
                    await sock.send_json({'error': e.text})
                except Exception as e:
                    await sock.send_json({'error': str(e)})
        finally:
            task.cancel()
        return sock

    app.add_routes([web.get('/pc', info), web.get('/pc/windows', windows), web.post('/pc/input', input_),
                    web.post('/pc/open', open_), web.post('/pc/window', window), web.post('/pc/identify', identify), web.get('/pc/shot', shot),
                    web.get('/pc/ws', ws)])
    n = len(pc.monitors())
    print(f'PC control: on ({n} monitor{"s" if n != 1 else ""}{", demo" if pc.fake else ""}).')
    return pc


# ---------- Identify: a big number on each monitor ----------
def identify():
    import tkinter as tk
    root = tk.Tk(); root.withdraw()
    for m in WinPC().monitors() if WINDOWS else FakePC().monitors():
        s = 360
        w = tk.Toplevel(root); w.overrideredirect(True); w.attributes('-topmost', True); w.configure(bg='#030703')
        w.geometry(f'{s}x{s}+{m["x"] + (m["w"] - s) // 2}+{m["y"] + (m["h"] - s) // 2}')
        tk.Label(w, text=str(m['n']), fg='#39FF6A', bg='#030703', font=('Consolas', 220, 'bold')).pack(expand=True, fill='both')
    root.after(3000, root.destroy); root.mainloop()


if __name__ == '__main__':
    if '--identify' in sys.argv:
        identify()
    else:
        print(__doc__)
