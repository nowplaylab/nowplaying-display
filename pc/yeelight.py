"""Лента или лампа Yeelight напрямую по домашней сети (без Яндекса и интернета).

На ленте в приложении Yeelight должно быть включено «Управление по локальной сети» (LAN control).
Сам протокол — в библиотеке yeelight-lan (github.com/nowplaylab/yeelight-lan). Обычные команды
лента принимает не чаще 60 в минуту, поэтому:
- «Цвет обложки» — одна команда на трек, плавный переход делает сама лента;
- «В такт» — музыкальный режим (set_music): лента сама подключается к серверу и принимает
  команды без ответа и без ограничения частоты — яркость следует за басами, цвет — обложке.

Проверка без ленты: python -m yeelight_lan.emulator (или tools/yeelight-emu).
"""
import asyncio
import colorsys
import logging
import time

import yeelight_lan
from yeelight_lan import Bulb, YeelightError

import lamp

log = logging.getLogger('nowplaying')

BEAT_FPS = 20


def discover(timeout=2.0):
    """Найти ленты и лампы Yeelight в сети: [{id, name, model, ip, port, color, music}] (блокирующий вызов)."""
    return [{'id': d.id, 'name': d.name, 'model': d.model, 'ip': d.ip, 'port': d.port,
             'color': d.color, 'music': d.music} for d in yeelight_lan.discover(timeout)]


class Strip:
    def __init__(self, settings):
        self.settings = settings
        self.devices = []          # последний поиск
        self.searched_at = 0
        self.error = ''
        self.sent = None
        self.sent_at = 0
        self.saved = None          # как светила до нас: get_prop
        self.last_play = 0
        self.level_fn = lambda: 0.0   # уровень звука 0..1 (подставляет сервер)
        self.color_cache = {}
        self._music = None         # yeelight_lan.MusicMode

    # ---------- соединение ----------

    def device(self):
        sid = self.settings['strip_id']
        for d in self.devices:
            if d['id'] == sid:
                return d
        return None

    async def search(self):
        self.devices = await asyncio.get_running_loop().run_in_executor(None, discover)
        self.searched_at = time.time()
        self.error = '' if self.devices else 'Лента не нашлась. Проверь, что в приложении Yeelight включено «Управление по локальной сети»'
        return self.devices

    async def _cmd(self, method, params):
        """Одна обычная команда. Результат или None (ошибка — в self.error)."""
        d = self.device()
        if not d:
            return None
        try:
            res = await Bulb(d['ip'], d['port']).command(method, params)
        except YeelightError as e:
            self.error = 'Лента не отвечает'
            log.debug('yeelight: %s', e)
            return None
        self.error = ''
        return res

    # ---------- музыкальный режим ----------

    async def _music_start(self):
        d = self.device()
        if not d or not d.get('music'):
            return False
        m = Bulb(d['ip'], d['port']).music()
        try:
            await m.start()
        except YeelightError as e:
            self.error = 'Лента не включила музыкальный режим'
            log.info('yeelight: %s', e)
            return False
        self._music = m
        log.info('yeelight: music mode on (%s)', d['ip'])
        return True

    async def _music_stop(self):
        if self._music:
            await self._music.stop()
            self._music = None

    def _music_send(self, method, params):
        if not self._music:
            return False
        if not self._music.alive:     # лента выключилась или перезапустилась
            self._music = None
            self.sent = None
            return False
        return self._music.send(method, params)

    # ---------- басы ----------

    BASS = range(0, 5)   # полосы 0–4 из bands.py: примерно 40–140 Гц — бочка и бас

    def bass(self, values):
        """Уровень басов 0..1 для «в такт»: нижние полосы, удар выделяем относительно
        их среднего за последние полсекунды — так лента вспыхивает на бочке, а не горит ровно."""
        if not values:
            return 0.0
        low = [values[i] for i in self.BASS if i < len(values)]
        now = 0.6 * max(low) + 0.4 * sum(low) / len(low)
        avg = getattr(self, '_bass_avg', now)
        self._bass_avg = avg * 0.85 + now * 0.15
        return max(0.0, min(1.0, 0.35 * now + 1.4 * max(0.0, now - avg)))

    # ---------- что светить ----------

    def info(self):
        s = self.settings.get()
        return {'devices': self.devices, 'error': self.error, 'searched': bool(self.searched_at),
                'active': bool(s['strip_on'] and self.device()), 'music': bool(self._music),
                'color': lamp.hex_of(self.sent[0]) if self.sent and self.sent[0] and self.sent[0][0] != 'off' else None}

    @staticmethod
    def _rgb(target):
        if target[0] == 'white':
            return None
        r, g, b = colorsys.hsv_to_rgb(target[1] / 360, target[2], 1)
        return (round(r * 255) << 16) | (round(g * 255) << 8) | round(b * 255)

    async def watch(self, now_state, art, want_level):
        """now_state() -> (playing, track_id); want_level(bool) — включить замер басов."""
        while True:
            try:
                await self._tick(now_state, art, want_level)
            except Exception as e:
                log.warning('yeelight: %s', e)
            s = self.settings.get()
            await asyncio.sleep(1 / BEAT_FPS if s['strip_on'] and s['strip_mode'] == 'beat' and self._music else 1)

    async def _tick(self, now_state, art, want_level):
        s = self.settings.get()
        if not s['strip_on'] or not s['strip_id']:
            want_level(False)
            if self._music:
                await self._music_stop()
            if self.saved:
                await self._restore()
            self.sent = None
            return
        if not self.device():
            if time.time() - self.searched_at > 60:
                await self.search()
            if not self.device():
                return
        d = self.device()
        if d and (d['id'], d['ip']) != getattr(self, '_dev_key', None):   # другая лента или новый адрес
            self._dev_key = (d['id'], d['ip'])
            await self._music_stop()
            self.sent = None
        playing, tid = now_state()
        if playing:
            self.last_play = time.time()
        color = None
        if tid and tid in art:
            key = (tid, len(art[tid][0]))
            if key not in self.color_cache:
                self.color_cache = {key: await asyncio.get_running_loop().run_in_executor(None, lamp.cover_color, art[tid][0])}
            color = self.color_cache[key]
        bright = s['lamp_bright']
        idle = not playing and time.time() - self.last_play > lamp.IDLE_AFTER
        beat = s['strip_mode'] == 'beat' and playing and color is not None
        want_level(beat)
        if not beat and self._music:
            await self._music_stop()
        if idle:
            if s['lamp_idle'] == 'off':
                target, bright = ('off',), 0
            elif s['lamp_idle'] == 'warm':
                target, bright = ('white', lamp.WARM), max(30, bright * 0.7)
            else:
                if self.saved:
                    await self._restore()
                    self.sent = None
                return
        elif color is None:
            return
        elif not playing:
            if s['lamp_pause'] == 'off':
                target, bright = ('off',), 0
            elif s['lamp_pause'] == 'dim':
                target, bright = color, max(5, bright * 0.3)
            else:
                target = color
        else:
            target = color
        if self.saved is None:
            props = await self._cmd('get_prop', ['power', 'bright', 'color_mode', 'rgb', 'ct', 'hue', 'sat'])
            self.saved = props or []
        if beat:
            # в такт: каждый кадр яркость по басам, цвет — обложки
            if not self._music and not await self._music_start():
                beat = False
            else:
                lvl = max(0.0, min(1.0, self.level_fn()))
                b = max(3, round(bright * (0.25 + 0.75 * lvl)))
                rgb = self._rgb(target)
                if self.sent is None or self.sent[0] != target:
                    if rgb is not None:
                        self._music_send('set_rgb', [rgb, 'sudden', 30])
                    else:
                        self._music_send('set_ct_abx', [target[1], 'sudden', 30])
                    self.sent = (target, None)
                if not self._music_send('set_bright', [b, 'sudden', 30]):
                    await self._music_stop()   # соединение оборвалось — в следующий раз включим заново
                return
        want = (target, round(bright))
        if want == self.sent or time.time() - self.sent_at < 1.2:
            return
        self.sent_at = time.time()
        if target[0] == 'off':
            ok = await self._cmd('set_power', ['off', 'smooth', 800])
        else:
            rgb = self._rgb(target)
            ok = await (self._cmd('set_rgb', [rgb, 'smooth', 800]) if rgb is not None
                        else self._cmd('set_ct_abx', [target[1], 'smooth', 800]))
            if ok is not None:
                await asyncio.sleep(1.05)   # лимит: не больше команды в секунду
                ok = await self._cmd('set_bright', [max(1, min(100, int(bright))), 'smooth', 800])
        if ok is not None:
            self.sent = want

    async def _restore(self):
        p = self.saved or []
        self.saved = None
        if len(p) < 7:
            return
        power, bright, mode, rgb, ct = p[0], p[1], p[2], p[3], p[4]
        if mode == '2' and ct:
            await self._cmd('set_ct_abx', [int(ct), 'smooth', 800])
        elif rgb:
            await self._cmd('set_rgb', [int(rgb), 'smooth', 800])
        await asyncio.sleep(1.05)
        if bright:
            await self._cmd('set_bright', [int(bright), 'smooth', 800])
        if power == 'off':
            await asyncio.sleep(1.05)
            await self._cmd('set_power', ['off', 'smooth', 800])
