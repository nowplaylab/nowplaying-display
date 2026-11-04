"""Погода для экрана часов: Open-Meteo (открытый сервис, без ключа). Обновляется раз в 15 минут."""
import asyncio
import logging
import time

log = logging.getLogger('nowplaying')

GEO = 'https://geocoding-api.open-meteo.com/v1/search'
FORECAST = 'https://api.open-meteo.com/v1/forecast'
EVERY = 15 * 60

# коды погоды WMO -> короткое описание
CODES = {
    0: 'ясно', 1: 'почти ясно', 2: 'переменная облачность', 3: 'пасмурно',
    45: 'туман', 48: 'изморозь',
    51: 'морось', 53: 'морось', 55: 'сильная морось', 56: 'ледяная морось', 57: 'ледяная морось',
    61: 'небольшой дождь', 63: 'дождь', 65: 'ливень', 66: 'ледяной дождь', 67: 'ледяной дождь',
    71: 'небольшой снег', 73: 'снег', 75: 'снегопад', 77: 'снежная крупа',
    80: 'ливень', 81: 'ливень', 82: 'сильный ливень',
    85: 'снегопад', 86: 'сильный снегопад',
    95: 'гроза', 96: 'гроза с градом', 99: 'гроза с градом',
}


async def search_city(http, name):
    """Города по названию: [{name, region, country, lat, lon}]."""
    async with http.get(GEO, params={'name': name, 'count': '8', 'language': 'ru', 'format': 'json'}) as r:
        data = await r.json(content_type=None) if r.status == 200 else {}
    out = []
    for c in (data or {}).get('results') or []:
        out.append({'name': c.get('name'), 'region': c.get('admin1') or '', 'country': c.get('country') or '',
                    'lat': c.get('latitude'), 'lon': c.get('longitude'), 'tz': c.get('timezone') or ''})
    return out


class Weather:
    def __init__(self, settings):
        self.settings = settings
        self.current = None     # {'temp': -3, 'text': 'снег'}
        self.forecast = None    # {'offset': секунды от UTC, 'hourly': [(время, t, код)], 'daily': [(дата, max, min, код)]}
        self.place = None       # для каких координат current
        self.fetched_at = 0
        self.wake = asyncio.Event()
        settings.listeners.append(lambda s: self._poke())

    def _poke(self):
        try:
            asyncio.get_running_loop().call_soon_threadsafe(self.wake.set)
        except RuntimeError:   # вызвали из другого потока (меню значка)
            loop = getattr(self, 'loop', None)
            if loop:
                loop.call_soon_threadsafe(self.wake.set)

    def wanted(self):
        """Погода нужна под датой или в виджете «Погода сейчас»."""
        s = self.settings
        return s['weather'] or 'weather' in s['widget_slots'] or 'forecast' in s['widget_slots']

    def info(self):
        s = self.settings
        if not self.wanted() or self.place != (s['weather_lat'], s['weather_lon']):
            return None
        return self.current

    def forecast_info(self):
        s = self.settings
        if 'forecast' not in s['widget_slots'] or self.place != (s['weather_lat'], s['weather_lon']):
            return None
        return self.forecast

    async def watch(self, http):
        self.loop = asyncio.get_running_loop()
        while True:
            s = self.settings
            place = (s['weather_lat'], s['weather_lon'])
            if self.wanted() and None not in place and (place != self.place or time.time() - self.fetched_at > EVERY):
                try:
                    await self.fetch(http, *place)
                except Exception as e:
                    log.warning('weather: %s', e)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), 60)
            except asyncio.TimeoutError:
                pass

    async def fetch(self, http, lat, lon):
        params = {'latitude': str(lat), 'longitude': str(lon), 'current': 'temperature_2m,weather_code',
                  'hourly': 'temperature_2m,weather_code', 'forecast_hours': '30',
                  'daily': 'temperature_2m_max,temperature_2m_min,weather_code', 'forecast_days': '5',
                  'timezone': 'auto'}
        async with http.get(FORECAST, params=params) as r:
            if r.status != 200:
                return
            data = await r.json(content_type=None)
        cur = data.get('current') or {}
        h, d = data.get('hourly') or {}, data.get('daily') or {}
        try:
            self.forecast = {
                'offset': int(data.get('utc_offset_seconds') or 0),
                'hourly': [x for x in zip(h['time'], h['temperature_2m'], h['weather_code']) if x[1] is not None],
                'daily': [x for x in zip(d['time'], d['temperature_2m_max'], d['temperature_2m_min'],
                                         d['weather_code']) if x[1] is not None],
            }
        except (KeyError, TypeError) as e:
            log.warning('forecast: %s', e)
            self.forecast = None
        t = cur.get('temperature_2m')
        if t is None:
            return
        self.current = {'temp': round(t), 'text': CODES.get(cur.get('weather_code'), '')}
        self.place = (lat, lon)
        self.fetched_at = time.time()
