"""Настройки NowPlaying: %LOCALAPPDATA%\\NowPlaying\\settings.json.

Их меняют окно настроек (/settings), меню значка и сервер. Часть, которая касается
вида на планшете, отдаётся ему в /api/now (ключ ui) — планшет применяет её сразу.
"""
import json
import re
import os
import threading
import time

import calendar_ics
import widgets

DEFAULTS = {
    # экран трека
    'theme': 'classic',       # раскладка экрана трека: classic, scene, poster, minimal, vinyl
    'track_bg': 'video',      # video — видео из Яндекс Музыки; gradient; blur; color; custom — своё
    'video_fallback': 'gradient',  # что за обложкой, если у трека нет видео: gradient или blur
    'track_dim': 55,          # затемнение видео или своего фона за треком, %
    'shadow': True,           # тень под обложкой
    'pulse': True,            # свечение за обложкой «дышит» под музыку
    'visualizer': 'off',      # полосы частот внизу экрана трека: off, bars, wave, dots
    # цвет экрана: поправка под матрицу устройства (у каждого своя), 0 — без поправки
    'color_sat': 0,           # насыщенность, % к обычной: -50…+100
    'color_contrast': 0,      # контраст, %: -50…+50
    'color_bright': 0,        # яркость, %: -30…+30
    'color_warm': 0,          # тепло: -50 (холоднее) … +50 (теплее)
    # экран часов
    'frame_on': False,             # фоторамка: фото из папки по очереди вместо своего фона
    'frame_dir': '',
    'frame_every': 5,              # менять фото раз в столько минут
    'clock_bg': True,         # показывать свой фон (самый новый файл из папки)
    'clock_dim': 35,          # затемнение фона часов, %
    'night': True,            # ночью красные тусклые часы
    'night_from': 23,
    'night_to': 8,
    'dark_after_min': 30,     # через сколько минут без музыки гасить экран (0 — не гасить)
    # погода под датой
    'weather': False,
    'weather_city': '',
    'weather_lat': None,
    'weather_lon': None,
    # виджеты на экране часов
    'widget_layout': 'shelf',      # shelf — ряд под часами, column — столбик справа, corners — по углам
    'widget_style': 'card',        # card — подложка, line — контур, none — без подложки
    'widget_slots': ['', '', '', ''],   # что на каждом месте: weather, city, countdown, note или пусто
    'city_name': '',               # второй город
    'city_tz': '',
    'forecast_mode': 'hours',      # прогноз: hours — по часам, days — на 3 дня
    'countdown_title': '',
    'countdown_date': '',          # ГГГГ-ММ-ДД
    'note_text': '',
    'calendar_url': '',            # ссылка на календарь в формате iCal (у Google — «секретный адрес»)
    'alarm_on': False,             # будильник на планшете
    'alarm_time': '07:30',
    'alarm_days': '12345',         # дни недели цифрами: 1 — пн … 7 — вс
    'alarm_sunrise': 20,           # рассвет: экран светлеет за столько минут до звонка (0 — без рассвета)
    'alarm_music': False,          # в момент звонка включить музыку на ПК
    'resume': True,                # карточка «Продолжить» на часах, когда музыка на паузе
    'wol': True,                   # кнопка «Включить компьютер», когда ПК не отвечает
    # подсветка: лампы Умного дома Яндекса в цвет обложки
    'lamp_on': False,
    'lamp_ids': [],                # какие лампы (id из Умного дома)
    'lamp_bright': 80,             # яркость во время музыки, %: 10…100
    'lamp_vivid': 'cover',         # calm — спокойнее, cover — как на обложке, vivid — ярче
    'lamp_pause': 'dim',           # на паузе: keep — не менять, dim — приглушить, off — выключить
    'lamp_idle': 'restore',        # музыка не играет 5 минут: restore — как было, warm — тёплый свет, off — выключить
    # программа
    'auto_update': True,
}

# то, что нужно планшету
UI_KEYS = ('theme', 'track_bg', 'track_dim', 'shadow', 'pulse', 'visualizer', 'clock_dim', 'night', 'night_from', 'night_to', 'dark_after_min', 'resume',
           'alarm_on', 'alarm_time', 'alarm_days', 'alarm_sunrise', 'alarm_music',
           'color_sat', 'color_contrast', 'color_bright', 'color_warm')


# что можно задать каждому устройству отдельно; остальное (погода, календарь, папки, программа) — общее
DEVICE_KEYS = UI_KEYS + ('video_fallback', 'clock_bg', 'frame_on', 'weather', 'wol',
                         'widget_layout', 'widget_style', 'widget_slots', 'forecast_mode')
SEEN_SAVE_EVERY = 300


class Settings:
    def __init__(self, data_dir=None):
        local = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        self.path = os.path.join(data_dir or os.path.join(local, 'NowPlaying'), 'settings.json')
        self.lock = threading.Lock()
        self.data = dict(DEFAULTS)
        self.listeners = []
        self.devices_path = os.path.join(os.path.dirname(self.path), 'devices.json')
        self.devices = {}   # id -> {'name', 'model', 'ip', 'seen', 'version', 'over': {…}}
        self._devices_saved = 0
        try:
            with open(self.devices_path, encoding='utf-8') as f:
                self.devices = {k: v for k, v in json.load(f).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass
        try:
            with open(self.path, encoding='utf-8') as f:
                saved = json.load(f)
            if saved.get('clips') is False and 'track_bg' not in saved:
                saved['track_bg'] = 'gradient'   # из 1.5: «Видео за треком» было выключено
            self.data.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data[key]

    def get(self):
        return dict(self.data)

    def ui(self):
        return {k: self.data[k] for k in UI_KEYS}

    # ---------- несколько экранов ----------

    def view(self, dev=None):
        """Настройки глазами одного устройства: общие, поверх — его собственные."""
        with self.lock:
            out = dict(self.data)
            d = self.devices.get(dev) if dev else None
            if d:
                out.update({k: v for k, v in d.get('over', {}).items() if k in DEVICE_KEYS})
        return out

    def ui_for(self, dev=None):
        v = self.view(dev)
        return {k: v[k] for k in UI_KEYS}

    def anywhere(self, test):
        """Нужно ли что-то хоть одному устройству (или общим настройкам) — например, погода для виджета."""
        if test(self.view()):
            return True
        return any(test(self.view(dev)) for dev in list(self.devices))

    def seen(self, dev, model, ip, version, tv=False):
        """Устройство спросило /api/now: запомнить его (и сохранить на диск не чаще раза в 5 минут)."""
        now = time.time()
        with self.lock:
            d = self.devices.get(dev)
            new = d is None
            if new:
                d = self.devices[dev] = {'name': '', 'over': {}}
            d.update(model=model[:60], ip=ip, seen=now, version=version[:20], tv=bool(tv))
            due = new or now - self._devices_saved > SEEN_SAVE_EVERY
        if due:
            self._save_devices()

    def has_tv(self, days=7):
        """Был ли за последние дни хоть один телевизор: тогда клипам нужна версия в полном качестве."""
        now = time.time()
        with self.lock:
            return any(v.get('tv') and now - v.get('seen', 0) < days * 86400 for v in self.devices.values())

    def device_list(self):
        with self.lock:
            items = [(k, dict(v)) for k, v in self.devices.items()]
        out = []
        for k, v in sorted(items, key=lambda kv: -kv[1].get('seen', 0)):
            out.append({'id': k, 'name': v.get('name') or v.get('model') or 'Устройство', 'model': v.get('model', ''),
                        'ip': v.get('ip', ''), 'seen': int(time.time() - v['seen']) if v.get('seen') else None,
                        'version': v.get('version', ''), 'custom': sorted(v.get('over', {}))})
        return out

    def update_device(self, dev, changes):
        """Свои настройки устройства: только DEVICE_KEYS, остальное уходит в общие."""
        if dev not in self.devices:
            return self.update(changes)
        own = {k: v for k, v in changes.items() if k in DEVICE_KEYS}
        rest = {k: v for k, v in changes.items() if k not in DEVICE_KEYS}
        if rest:
            self.update(rest)
        clean = self._clean(own)
        with self.lock:
            self.devices[dev].setdefault('over', {}).update(clean)
        self._save_devices()
        self._notify()
        return self.view(dev)

    def reset_device(self, dev):
        with self.lock:
            if dev in self.devices:
                self.devices[dev]['over'] = {}
        self._save_devices()
        self._notify()

    def rename_device(self, dev, name):
        with self.lock:
            if dev in self.devices:
                self.devices[dev]['name'] = str(name).strip()[:40]
        self._save_devices()

    def forget_device(self, dev):
        with self.lock:
            self.devices.pop(dev, None)
        self._save_devices()
        self._notify()

    def _save_devices(self):
        with self.lock:
            snapshot = json.loads(json.dumps(self.devices))
            self._devices_saved = time.time()
        try:
            os.makedirs(os.path.dirname(self.devices_path), exist_ok=True)
            tmp = self.devices_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.devices_path)
        except OSError:
            pass

    def _notify(self):
        snapshot = self.get()
        for fn in self.listeners:
            fn(snapshot)

    def update(self, changes):
        """Применить изменения (лишние ключи и неверные типы отбрасываются). Возвращает новые настройки."""
        clean = self._clean(changes)
        with self.lock:
            self.data.update(clean)
            snapshot = dict(self.data)
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(snapshot, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
            except OSError:
                pass
        for fn in self.listeners:
            fn(snapshot)
        return snapshot

    def _clean(self, changes):
        clean = {}
        for k, v in changes.items():
            if k not in DEFAULTS:
                continue
            d = DEFAULTS[k]
            if isinstance(d, bool):
                v = bool(v)
            elif isinstance(d, int) and not isinstance(d, bool):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    continue
            elif k in ('weather_lat', 'weather_lon'):
                try:
                    v = None if v is None else float(v)
                except (TypeError, ValueError):
                    continue
            elif isinstance(d, str):
                v = str(v)[:1000 if k in ('calendar_url', 'frame_dir') else 200]
            clean[k] = v
        if 'lamp_ids' in changes:
            v = changes['lamp_ids']
            if isinstance(v, list):
                clean['lamp_ids'] = [str(x)[:100] for x in v[:20] if isinstance(x, str) and x]
            else:
                clean.pop('lamp_ids', None)
        for k, ok in (('lamp_vivid', ('calm', 'cover', 'vivid')), ('lamp_pause', ('keep', 'dim', 'off')),
                      ('lamp_idle', ('restore', 'warm', 'off'))):
            if k in clean and clean[k] not in ok:
                clean.pop(k)
        if 'widget_slots' in changes:
            v = changes['widget_slots']
            if isinstance(v, list):
                v = [str(x) if str(x) in widgets.KINDS else '' for x in v[:widgets.SLOTS]]
                clean['widget_slots'] = v + [''] * (widgets.SLOTS - len(v))
            else:
                clean.pop('widget_slots', None)
        if clean.get('widget_layout', 'shelf') not in widgets.LAYOUTS:
            clean.pop('widget_layout')
        if clean.get('forecast_mode', 'hours') not in widgets.FORECAST_MODES:
            clean.pop('forecast_mode')
        if clean.get('widget_style', 'card') not in widgets.STYLES:
            clean.pop('widget_style')
        if clean.get('city_tz') and not widgets.valid_tz(clean['city_tz']):
            clean.pop('city_tz')
        if clean.get('countdown_date') and not widgets.DATE_RE.match(clean['countdown_date']):
            clean.pop('countdown_date')
        if 'alarm_time' in clean and not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', clean['alarm_time']):
            clean.pop('alarm_time')
        if 'alarm_days' in clean:
            clean['alarm_days'] = ''.join(d for d in '1234567' if d in clean['alarm_days'])
        if clean.get('alarm_sunrise', 20) not in (0, 10, 20, 30):
            clean.pop('alarm_sunrise')
        if clean.get('visualizer', 'off') not in ('off', 'bars', 'wave', 'dots'):
            clean.pop('visualizer')
        if clean.get('frame_every', 5) not in (1, 5, 15, 30, 60):
            clean.pop('frame_every')
        if 'calendar_url' in clean:
            clean['calendar_url'] = clean['calendar_url'].strip()
            if not calendar_ics.valid_url(clean['calendar_url']):
                clean.pop('calendar_url')
        if 'note_text' in clean:
            clean['note_text'] = clean['note_text'][:80]
        if clean.get('track_bg', 'video') not in ('video', 'gradient', 'blur', 'color', 'custom'):
            clean.pop('track_bg')
        if clean.get('video_fallback', 'gradient') not in ('gradient', 'blur'):
            clean.pop('video_fallback')
        if clean.get('theme', 'classic') not in ('classic', 'scene', 'poster', 'minimal', 'vinyl'):
            clean.pop('theme')
        for k, lo, hi in (('track_dim', 0, 90), ('clock_dim', 0, 90), ('night_from', 0, 23),
                          ('night_to', 0, 23), ('dark_after_min', 0, 720), ('color_sat', -50, 100),
                          ('color_contrast', -50, 50), ('color_bright', -30, 30), ('color_warm', -50, 50),
                          ('lamp_bright', 10, 100)):
            if k in clean:
                clean[k] = max(lo, min(hi, clean[k]))
        return clean
