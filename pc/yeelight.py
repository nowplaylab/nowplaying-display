"""Лента или лампа Yeelight напрямую по домашней сети (без Яндекса и интернета).

На ленте в приложении Yeelight должно быть включено «Управление по локальной сети» (LAN control).
Протокол открытый: поиск — SSDP на 239.255.255.250:1982, команды — строки JSON по TCP 55443.
Обычные команды лента принимает не чаще 60 в минуту, поэтому:
- «Цвет обложки» — одна команда на трек, плавный переход делает сама лента;
- «В такт» — музыкальный режим (set_music): лента сама подключается к серверу и принимает
  команды без ответа и без ограничения частоты — яркость следует за басами, цвет — обложке.

Проверка без ленты: tools/yeelight-emu/yeelight_emu.py.
"""
import asyncio
import colorsys
import json
import logging
import socket
import time

import lamp

log = logging.getLogger('nowplaying')

SSDP = ('239.255.255.250', 1982)
SEARCH = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1982\r\nMAN: "ssdp:discover"\r\n'
          'ST: wifi_bulb\r\n\r\n').encode()
BEAT_FPS = 20


def discover(timeout=2.0):
    """Найти ленты и лампы Yeelight в сети: [{id, name, model, ip, port, color}] (блокирующий вызов)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.3)
    found = {}
    try:
        end = time.time() + timeout
        s.sendto(SEARCH, SSDP)
        sent_again = False
        while time.time() < end:
            if not sent_again and time.time() > end - timeout / 2:
                s.sendto(SEARCH, SSDP)   # UDP может потеряться — спросим ещё раз
                sent_again = True
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            h = {}
            for line in data.decode(errors='replace').split('\r\n')[1:]:
                if ':' in line:
                    k, v = line.split(':', 1)
                    h[k.strip().lower()] = v.strip()
            loc = h.get('location', '')
            if not loc.startswith('yeelight://') or not h.get('id'):
                continue
            host, _, port = loc[len('yeelight://'):].partition(':')
            sup = h.get('support', '').split()
            found[h['id']] = {'id': h['id'], 'name': h.get('name') or '', 'model': h.get('model', ''),
                              'ip': host, 'port': int(port or 55443),
                              'color': 'set_rgb' in sup or 'set_hsv' in sup, 'music': 'set_music' in sup}
    finally:
        s.close()
    return list(found.values())


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
        self._music = None         # (reader, writer) музыкального режима
        self._music_srv = None
        self._next_id = 1

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

    async def _cmd(self, method, params, want_reply=True):
        """Одна обычная команда (своё соединение — так проще и надёжнее при обрывах)."""
        d = self.device()
        if not d:
            return None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(d['ip'], d['port']), 3)
        except (OSError, asyncio.TimeoutError):
            self.error = 'Лента не отвечает'
            return None
        try:
            cid = self._next_id = self._next_id + 1
            writer.write(json.dumps({'id': cid, 'method': method, 'params': params}).encode() + b'\r\n')
            await writer.drain()
            if not want_reply:
                return True
            end = time.time() + 3
            while time.time() < end:
                line = await asyncio.wait_for(reader.readline(), 3)
                if not line:
                    break
                msg = json.loads(line)
                if msg.get('id') == cid:
                    if 'error' in msg:
                        self.error = 'Лента: ' + str(msg['error'].get('message'))
                        return None
                    self.error = ''
                    return msg.get('result')
        except (OSError, ValueError, asyncio.TimeoutError):
            self.error = 'Лента не отвечает'
        finally:
            writer.close()
        return None

    # ---------- музыкальный режим ----------

    async def _music_start(self):
        d = self.device()
        if not d or not d.get('music'):
            return False
        got = asyncio.get_running_loop().create_future()

        def on_conn(r, w):
            if not got.done():
                got.set_result((r, w))
            else:
                w.close()
        self._music_srv = await asyncio.start_server(on_conn, '0.0.0.0', 0)
        port = self._music_srv.sockets[0].getsockname()[1]
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((d['ip'], 1))
            my_ip = probe.getsockname()[0]
        finally:
            probe.close()
        if await self._cmd('set_music', [1, my_ip, port]) is None:
            await self._music_stop()
            return False
        try:
            self._music = await asyncio.wait_for(got, 5)
        except asyncio.TimeoutError:
            self.error = 'Лента не включила музыкальный режим'
            await self._music_stop()
            return False
        log.info('yeelight: music mode on (%s)', d['ip'])
        return True

    async def _music_stop(self):
        if self._music:
            self._music[1].close()
            self._music = None
        if self._music_srv:
            self._music_srv.close()
            self._music_srv = None

    def _music_send(self, method, params):
        if not self._music:
            return False
        if self._music[1].is_closing() or self._music[0].at_eof():   # лента выключилась или перезапустилась
            self._music = None
            self.sent = None
            return False
        try:
            self._music[1].write(json.dumps({'id': 1, 'method': method, 'params': params}).encode() + b'\r\n')
            return True
        except (OSError, RuntimeError):
            self._music = None
            return False

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
