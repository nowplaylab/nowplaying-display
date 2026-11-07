"""Подсветка в цвет обложки: лампы из Умного дома Яндекса.

Лампами Яндекса нельзя управлять напрямую по домашней сети, только через API Умного дома
(api.iot.yandex.net). Вход — по коду: программа просит у Яндекса код, человек вводит его на
oauth.yandex.ru/device и разрешает доступ к устройствам. Пароль программа не видит.
Токен хранится на этом компьютере, зашифрованный средствами Windows (DPAPI).

Цвет меняется один раз на трек (Яндекс не любит частые команды), плавный переход делает сама лампа.
"""
import asyncio
import base64
import colorsys
import ctypes
import io
import json
import logging
import os
import socket
import sys
import time
import uuid

import yandex_smarthome
from PIL import Image
from yandex_smarthome import AuthError, DeviceLogin, Light, LoginError, SmartHome, YandexError, light_actions

log = logging.getLogger('nowplaying')

# приложение NowPlaying на oauth.yandex.ru (права iot:view и iot:control)
CLIENT_ID = os.environ.get('NP_YA_CLIENT_ID', '4428504217ee4b63a3973a99d1fd1b1b')
try:   # секрет лежит не в git, а в pc/yandex_secret.py — он попадает только в собранную программу
    from yandex_secret import SECRET as _SECRET
except ImportError:
    _SECRET = ''


def _unhide(s):
    """При сборке exe секрет записывается не открытым текстом, а 'x:<base64>' (см. pc/build_exe.sh)."""
    if not s.startswith('x:'):
        return s
    raw = base64.b64decode(s[2:])
    return bytes(c ^ ((0x5A + i * 29) & 0xFF) for i, c in enumerate(raw)).decode()


CLIENT_SECRET = os.environ.get('NP_YA_CLIENT_SECRET', _unhide(_SECRET))
OAUTH = os.environ.get('NP_YA_OAUTH', yandex_smarthome.OAUTH)
IOT = os.environ.get('NP_YA_IOT', yandex_smarthome.IOT)

IDLE_AFTER = 300        # секунд без музыки до «музыка не играет»
MIN_GAP = 1.0           # не чаще одной команды в секунду
WARM = 2700


# ---------- цвет по обложке ----------

def cover_color(jpeg):
    """Самый «живой» цвет обложки: насыщенный и заметный по площади, а не средний и не самый частый.
    -> ('color', h 0..360, s 0..1) или ('white', кельвины). Бесцветная обложка — тёплый или нейтральный белый."""
    img = Image.open(io.BytesIO(jpeg)).convert('RGB').resize((64, 64), Image.LANCZOS)
    q = img.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    cands = []
    for count, idx in q.getcolors():
        r, g, b = pal[idx * 3: idx * 3 + 3]
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        share = count / 4096
        score = (s ** 2) * (min(v, 0.85) ** 0.8) * (share ** 0.45)
        cands.append((score, h, s, v, share, r, b))
    best = max(cands)
    vivid = sum(c[4] for c in cands if c[2] > 0.12 and c[3] > 0.18)
    if best[2] < 0.12 or vivid < 0.015:
        rr = sum(c[5] * c[4] for c in cands)
        bb = sum(c[6] * c[4] for c in cands)
        return ('white', WARM if rr >= bb else 4500)
    _, h, s, v, *_ = best
    s = min(1.0, max(0.3, s * 1.3))      # пастель остаётся пастелью
    if v < 0.5:
        s *= 0.8                          # тёмный цвет обложки не превращаем в кислотный
    return ('color', round(h * 360) % 360, s)


def kelvin_for(h, s):
    """Цвет обложки -> оттенок белого для ламп без цвета: красные, оранжевые и жёлтые — тёплый,
    зелёные — нейтральный, голубые и синие — холодный, фиолетовые — между ними."""
    if s < 0.25:
        return 4000
    for top, k in ((50, 2700), (75, 3200), (165, 4200), (200, 5500), (255, 6500), (290, 5000), (330, 3500), (361, 2700)):
        if h < top:
            return k
    return 4000


def hex_of(target):
    if not target or not target[0]:
        return None
    if target[0] == 'white':
        return '#ffc489' if target[1] <= 3000 else '#ffe4ce'
    r, g, b = colorsys.hsv_to_rgb(target[1] / 360, target[2], 1)
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


# ---------- шифрование токена средствами Windows ----------

class _Blob(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_uint32), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    if sys.platform != 'win32':
        return data
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    ok = fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst))
    if not ok:
        raise OSError('DPAPI')
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


class Lamp:
    def __init__(self, settings, data_dir=None):
        self.settings = settings
        base = data_dir or os.path.dirname(settings.path)
        self.path = os.path.join(base, 'yandex_home.dat')
        self.token = {}           # access, refresh, expires_at, device_id
        self.lamps = []           # лампы из Умного дома: id, name, room, model, tmin, tmax
        self.lamps_at = 0
        self.login = None         # идёт вход: user_code, url, until
        self.error = ''
        self.sent = None          # что последним отправили лампам
        self.sent_at = 0
        self.saved = None         # как лампы светили до нас — вернём, когда музыка закончится
        self.color_cache = {}     # (track_id, размер обложки) -> цвет
        self.last_play = 0
        self.test_req = False
        self._login_task = None
        self._load()

    # ---------- хранение входа ----------

    def _load(self):
        try:
            with open(self.path, 'rb') as f:
                self.token = json.loads(_dpapi(f.read(), False).decode())
        except (OSError, ValueError):
            self.token = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(_dpapi(json.dumps(self.token).encode(), True))
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning('lamp: cannot save token: %s', e)

    def device_id(self):
        if not self.token.get('device_id'):
            self.token['device_id'] = uuid.uuid4().hex
        return self.token['device_id']

    @property
    def connected(self):
        return bool(self.token.get('access'))

    def status(self):
        if not CLIENT_ID:
            return 'no_app'
        if self.login:
            return 'login'
        return 'ok' if self.connected else 'off'

    def info(self):
        s = self.settings.get()
        return {'status': self.status(), 'error': self.error,
                'login': {'code': self.login['user_code'], 'url': self.login['url']} if self.login else None,
                'lamps': self.lamps, 'color': hex_of(self.sent[0]) if self.sent and self.sent[0][0] != 'off' else None,
                'active': bool(s['lamp_on'] and self.connected and s['lamp_ids'])}

    # ---------- вход по коду (библиотека yandex-smarthome) ----------

    def _auth(self):
        return DeviceLogin(CLIENT_ID, CLIENT_SECRET, oauth=OAUTH)

    async def start_login(self, http):
        if not CLIENT_ID:
            self.error = 'В этой версии программы ещё нет ключа приложения для Яндекса'
            return
        self.error = ''
        login = self._auth()
        try:
            code = await login.start(http, self.device_id(), f'NowPlaying ({socket.gethostname()})')
        except YandexError as e:
            self.error = 'Яндекс не дал код для входа, проверь интернет'
            log.warning('lamp: device/code: %s', e)
            return
        self.login = {'user_code': code.user_code, 'url': code.url, 'until': code.expires_at}
        if self._login_task:
            self._login_task.cancel()
        self._login_task = asyncio.create_task(self._wait_login(http, login, code))

    def cancel_login(self):
        if self._login_task:
            self._login_task.cancel()
        self._login_task = None
        self.login = None

    async def _wait_login(self, http, login, code):
        try:
            while self.login and time.time() < code.expires_at:
                await asyncio.sleep(max(code.interval, 2))
                try:
                    token = await login.poll(http, code)
                except LoginError as e:
                    self.error = {'expired': 'Код устарел, попробуй ещё раз',
                                  'denied': 'Доступ не разрешили'}.get(e.reason, f'Не получилось войти: {e}')
                    break
                if token:
                    self._take_token(token)
                    self.login = None
                    self.error = ''
                    log.info('lamp: connected to Yandex smart home')
                    await self.refresh_lamps(http, force=True)
                    return
            else:
                if self.login:
                    self.error = 'Время на ввод кода вышло, попробуй ещё раз'
        finally:
            self.login = None

    def _take_token(self, t):
        self.token.update(access=t.access, refresh=t.refresh or self.token.get('refresh'), expires_at=t.expires_at)
        self._save()

    def logout(self):
        self.cancel_login()
        did = self.token.get('device_id')
        self.token = {'device_id': did} if did else {}
        self.lamps = []
        self.saved = None
        self.sent = None
        try:
            os.remove(self.path)
        except OSError:
            pass

    async def _refresh_token(self, http):
        if not self.token.get('refresh'):
            return False
        t = await self._auth().refresh(http, self.token['refresh'])
        if t:
            self._take_token(t)
        return bool(t)

    # ---------- API Умного дома ----------

    async def _home(self, http, call, retry=True):
        """call(SmartHome) -> корутина. Обновляет токен, ошибки кладёт в self.error.
        Возвращает (True, результат) или (False, None)."""
        if not self.connected:
            return False, None
        exp = self.token.get('expires_at') or 0    # токен живёт долго; обновляем, когда до конца меньше месяца
        if exp and exp - time.time() < 30 * 86400 and retry:
            await self._refresh_token(http)
        try:
            return True, await call(SmartHome(self.token['access'], iot=IOT))
        except AuthError:
            if retry and await self._refresh_token(http):
                return await self._home(http, call, retry=False)
            self.error = 'Вход в Яндекс больше не действует, подключи заново'
            self.token.pop('access', None)
            self._save()
        except YandexError as e:
            self.error = 'Умный дом Яндекса не отвечает или ответил ошибкой'
            log.warning('lamp: %s', e)
        return False, None

    async def refresh_lamps(self, http, force=False):
        if not force and time.time() - self.lamps_at < 600:
            return self.lamps
        ok, lights = await self._home(http, lambda home: home.lights(http))
        if not ok:
            return self.lamps
        self.lamps = [{'id': l.id, 'name': l.name, 'room': l.room, 'model': l.model, 'tmin': l.tmin,
                       'tmax': l.tmax, 'bright': l.brightness} for l in lights]
        self.lamps_at = time.time()
        self.error = ''
        return self.lamps

    async def _snapshot(self, http, ids):
        """Как лампы светят сейчас — чтобы вернуть, когда музыка закончится."""
        ok, lights = await self._home(http, lambda home: home.lights(http))
        return {l.id: l.state for l in lights if l.id in ids} if ok else None

    def _actions(self, lamp, target, bright):
        """target: ('color', h, s) | ('white', K) | ('off',)."""
        light = Light(lamp['id'], lamp.get('name', ''), model=lamp.get('model'), tmin=lamp.get('tmin'),
                      tmax=lamp.get('tmax'), brightness=lamp.get('bright', False))
        if target[0] == 'off':
            return light_actions(light, on=False)
        if target[0] == 'color' and light.color:
            return light_actions(light, hsv=(target[1], target[2]), brightness=bright)
        # лампа без цвета (только белый): тёплая обложка — тёплый свет, холодная — холодный
        k = target[1] if target[0] == 'white' else kelvin_for(target[1], target[2])
        return light_actions(light, kelvin=k, brightness=bright)

    async def _send(self, http, lamps, target, bright):
        acts = {l['id']: self._actions(l, target, bright) for l in lamps}
        ok, _ = await self._home(http, lambda home: home.actions(http, acts))
        return ok

    async def _restore(self, http):
        if not self.saved:
            return
        saved = self.saved
        await self._home(http, lambda home: home.actions(http, saved))
        self.saved = None

    # ---------- что сейчас должно гореть ----------

    def target_for(self, playing, color, s):
        """('color'|'white'|'off', …, яркость) или None — ничего не менять / вернуть как было."""
        now = time.time()
        if playing:
            self.last_play = now
        bright = s['lamp_bright']
        idle = not playing and now - self.last_play > IDLE_AFTER
        if idle:
            mode = s['lamp_idle']
            if mode == 'warm':
                return ('white', WARM), max(30, bright * 0.7)
            if mode == 'off':
                return ('off',), 0
            return None, 0                      # вернуть как было
        if not color:
            return 'keep', 0
        if color[0] == 'color':
            k = {'calm': 0.7, 'cover': 1.0, 'vivid': 1.3}.get(s['lamp_vivid'], 1.0)
            color = ('color', color[1], max(0.15, min(1.0, color[2] * k)))
        if not playing:
            if s['lamp_pause'] == 'off':
                return ('off',), 0
            if s['lamp_pause'] == 'dim':
                return color, max(5, bright * 0.3)
        return color, bright

    async def watch(self, http, now_state, art):
        """now_state() -> (playing, track_id); art — словарь обложек сервера."""
        while True:
            try:
                await self._tick(http, now_state, art)
            except Exception as e:
                log.warning('lamp: %s', e)
            await asyncio.sleep(1)

    async def _tick(self, http, now_state, art):
        s = self.settings.get()
        ids = set(s['lamp_ids'])
        if not (s['lamp_on'] and self.connected and ids):
            if self.saved:                       # подсветку выключили — вернуть лампы как были
                await self._restore(http)
            self.sent = None
            return
        lamps = [l for l in await self.refresh_lamps(http) if l['id'] in ids]
        if not lamps:
            return
        playing, tid = now_state()
        color = None
        if tid and tid in art:
            key = (tid, len(art[tid][0]))
            if key not in self.color_cache:
                self.color_cache = {key: await asyncio.get_running_loop().run_in_executor(None, cover_color, art[tid][0])}
            color = self.color_cache[key]
        target, bright = self.target_for(playing, color, s)
        if self.test_req:
            self.test_req = False
            t = self.sent or (('white', WARM), s['lamp_bright'])
            await self._send(http, lamps, ('off',), 0)
            await asyncio.sleep(0.9)
            await self._send(http, lamps, t[0] if t[0][0] != 'off' else ('white', WARM), t[1] or s['lamp_bright'])
            self.sent_at = time.time()
            return
        if target == 'keep':
            return
        if target is None:                       # музыка давно не играет: вернуть как было
            if self.saved:
                await self._restore(http)
                self.sent = None
            return
        want = (target, round(bright))
        if want == self.sent or time.time() - self.sent_at < MIN_GAP:
            return
        if self.saved is None:
            self.saved = await self._snapshot(http, ids) or {}
        if await self._send(http, lamps, target, bright):
            self.sent = want
            self.error = ''
        self.sent_at = time.time()

    def test(self):
        self.test_req = True
