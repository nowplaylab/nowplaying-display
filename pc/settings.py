"""Настройки NowPlaying: %LOCALAPPDATA%\\NowPlaying\\settings.json.

Их меняют окно настроек (/settings), меню значка и сервер. Часть, которая касается
вида на планшете, отдаётся ему в /api/now (ключ ui) — планшет применяет её сразу.
"""
import json
import re
import os
import threading

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
    # программа
    'auto_update': True,
}

# то, что нужно планшету
UI_KEYS = ('theme', 'track_bg', 'track_dim', 'shadow', 'pulse', 'clock_dim', 'night', 'night_from', 'night_to', 'dark_after_min', 'resume',
           'alarm_on', 'alarm_time', 'alarm_days', 'alarm_sunrise', 'alarm_music')


class Settings:
    def __init__(self, data_dir=None):
        local = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        self.path = os.path.join(data_dir or os.path.join(local, 'NowPlaying'), 'settings.json')
        self.lock = threading.Lock()
        self.data = dict(DEFAULTS)
        self.listeners = []
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

    def update(self, changes):
        """Применить изменения (лишние ключи и неверные типы отбрасываются). Возвращает новые настройки."""
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
                          ('night_to', 0, 23), ('dark_after_min', 0, 720)):
            if k in clean:
                clean[k] = max(lo, min(hi, clean[k]))
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
