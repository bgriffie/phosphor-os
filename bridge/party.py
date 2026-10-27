"""Party mode for the lights relay: every light flashes together until it's stopped.

Runs on the relay, not the phone, so a party keeps going when the app is closed or you switch
apps. Kasa Lights, Phosphor OS and Home Map all start, change and stop the same party.

    GET  /party   -> {"on": true, "mode": "strobe", "speed": "medium", "since": 1730000000, "lights": ["id", ...]}
    POST /party   {"on": true, "mode": "strobe|color|both", "speed": "slow|medium|fast"}  start, or change a running party
                  {"on": false}                                                          stop and put every light back

Loaded by kasa_bridge.py like pc_control.py and tv_control.py (drop this file next to it).
"""
import asyncio, time

from aiohttp import web

VERSION = '1.0'
MODES = ('strobe', 'color', 'both')
BEAT = {'slow': 1.2, 'medium': 0.6, 'fast': 0.25}   # seconds between flashes, at most as fast as the bulbs answer
STEP_TIMEOUT = 3     # a bulb that doesn't answer within this is skipped for that flash
GIVE_UP = 5          # flashes in a row where no light answered before the party stops by itself


class Party:
    def __init__(self, lights):
        self.lights = lights
        self.task = None
        self.mode, self.speed = 'strobe', 'medium'
        self.snap = []       # how each light was before the party
        self.since = 0
        self.halt = asyncio.Event()

    def state(self):
        on = self.task is not None and not self.task.done()
        return {'on': on, 'mode': self.mode, 'speed': self.speed, 'since': int(self.since) if on else 0,
                'lights': [l['id'] for l in self.snap] if on else [], 'version': VERSION}

    async def _send(self, lid, body):
        try:
            await asyncio.wait_for(self.lights.set(lid, body), STEP_TIMEOUT)
            return True
        except Exception:
            return False

    async def _run(self):
        beat, misses = 0, 0
        while not self.halt.is_set():
            t0 = time.monotonic()
            flash_on, hue = beat % 2 == 0, (beat * 137) % 360
            jobs = []
            for i, l in enumerate(self.snap):
                colour = 'hsv' in l and (self.mode == 'color' or (self.mode == 'both' and flash_on))
                if colour:
                    jobs.append(self._send(l['id'], {'hsv': [(hue + i * 60) % 360, 100, 100], 'on': True}))
                else:
                    jobs.append(self._send(l['id'], {'on': True if self.mode == 'color' else flash_on}))
            ok = await asyncio.gather(*jobs)
            misses = 0 if any(ok) else misses + 1
            if misses >= GIVE_UP:
                print('Party stopped: no light answered.')
                await self._restore()
                return
            beat += 1
            try:   # wait for the next flash, waking at once if the party is stopped
                await asyncio.wait_for(self.halt.wait(), max(0.01, BEAT[self.speed] - (time.monotonic() - t0)))
            except asyncio.TimeoutError:
                pass

    async def _restore(self):
        async def back(l):
            if not l.get('on'):
                return await self._send(l['id'], {'on': False})
            body = {'on': True}
            if l.get('hsv') and not l.get('temp'):
                body['hsv'] = l['hsv']
            elif l.get('temp'):
                body['temp'] = l['temp']
            if l.get('brightness') is not None:
                body['brightness'] = l['brightness']
            return await self._send(l['id'], body)
        await asyncio.gather(*(back(l) for l in self.snap))

    async def start(self, mode=None, speed=None):
        if mode in MODES:
            self.mode = mode
        if speed in BEAT:
            self.speed = speed
        if self.task is not None and not self.task.done():
            return   # already running: the new mode and speed apply from the next flash
        lights = [l for l in await self.lights.list() if l.get('online')]
        if not lights:
            raise web.HTTPConflict(text='No lights are responding right now.')
        self.snap = [dict(l) for l in lights]
        self.since = time.time()
        self.halt.clear()
        self.task = asyncio.create_task(self._run())
        print(f'Party on: {self.mode}, {self.speed}, {len(lights)} light(s).')

    async def stop(self):
        task, self.task = self.task, None
        if task is None or task.done():
            return
        self.halt.set()
        await task          # the flash in progress lands first, so it can't undo the restore
        await self._restore()
        print('Party off: lights restored.')


def setup(app, lights, demo=False):
    party = Party(lights)

    async def get_party(request):
        return web.json_response(party.state())

    async def set_party(request):
        body = await request.json()
        if body.get('on'):
            await party.start(body.get('mode'), body.get('speed'))
        elif 'on' in body:
            await party.stop()
        else:   # just a mode or speed change
            if body.get('mode') in MODES:
                party.mode = body['mode']
            if body.get('speed') in BEAT:
                party.speed = body['speed']
        return web.json_response(party.state())

    async def stop_on_exit(app):
        await party.stop()

    app.add_routes([web.get('/party', get_party), web.post('/party', set_party)])
    app.on_shutdown.append(stop_on_exit)
    print(f'Party mode: on (v{VERSION}).')
