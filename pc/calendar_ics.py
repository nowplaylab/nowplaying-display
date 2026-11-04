"""Календарь для виджета на экране часов: ближайшие события по ссылке на файл iCal (ICS).

Ссылку даёт любой календарь: у Google — «Секретный адрес в формате iCal» в настройках календаря.
Скачиваем раз в 15 минут, повторяющиеся события раскрываем, планшету отдаём несколько ближайших.
Время событий — в секундах от 1970 (UTC), а «сегодня/завтра» и часы планшет считает сам.
"""
import asyncio
import datetime
import logging
import time

log = logging.getLogger('nowplaying')

EVERY = 15 * 60
AHEAD_DAYS = 14
KEEP = 6                 # сколько событий отдаём; планшет сам выбросит прошедшие
MAX_BYTES = 8 * 1024 * 1024


def normalize_url(url):
    url = (url or '').strip()
    if url.lower().startswith('webcal://'):
        url = 'https://' + url[9:]
    return url


def valid_url(url):
    return url == '' or url.lower().startswith(('https://', 'http://', 'webcal://'))


def parse(data, now=None):
    """Байты .ics -> ближайшие события [{'text', 'start', 'end', 'all_day', 'date'}]."""
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(data)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    local_tz = datetime.datetime.now().astimezone().tzinfo
    start, end = now - datetime.timedelta(days=1), now + datetime.timedelta(days=AHEAD_DAYS)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get('STATUS', '')).upper() == 'CANCELLED':
            continue
        text = str(ev.get('SUMMARY', '') or '').strip() or 'Без названия'
        ds = ev.get('DTSTART')
        if ds is None:
            continue
        s = ds.dt
        de = ev.get('DTEND')
        e = de.dt if de is not None else None
        if isinstance(s, datetime.datetime):
            if s.tzinfo is None:          # «плавающее» время — считаем местным для ПК
                s = s.replace(tzinfo=local_tz)
            if e is None:
                e = s
            elif isinstance(e, datetime.datetime) and e.tzinfo is None:
                e = e.replace(tzinfo=local_tz)
            if e < now:
                continue
            out.append({'text': text[:80], 'start': int(s.timestamp()), 'end': int(e.timestamp()), 'all_day': False})
        else:                              # на весь день: только дата, без часового пояса
            last = (e - datetime.timedelta(days=1)) if e else s
            if last < now.astimezone(local_tz).date() - datetime.timedelta(days=1):
                continue
            out.append({'text': text[:80], 'date': s.isoformat(), 'last': max(s, last).isoformat(), 'all_day': True,
                        'start': int(datetime.datetime.combine(s, datetime.time(), local_tz).timestamp())})
    out.sort(key=lambda x: (x['start'], not x['all_day']))
    return out[:KEEP]


class Calendar:
    def __init__(self, settings):
        self.settings = settings
        self.events = None
        self.error = ''
        self.url = None          # для какой ссылки events
        self.fetched_at = 0
        self.wake = asyncio.Event()
        settings.listeners.append(lambda s: self._poke())

    def _poke(self):
        loop = getattr(self, 'loop', None)
        if loop:
            loop.call_soon_threadsafe(self.wake.set)

    def wanted(self):
        s = self.settings
        return 'calendar' in s['widget_slots'] and bool(s['calendar_url'])

    def info(self):
        if not self.wanted() or self.url != self.settings['calendar_url']:
            return None
        return self.events

    def status(self):
        """Для окна настроек."""
        if not self.settings['calendar_url']:
            return ''
        if self.url != self.settings['calendar_url']:
            return 'Загружаю…' if self.wanted() else ''
        if self.error:
            return self.error
        n = len(self.events or [])
        return 'Ближайших событий нет' if not n else f'Ближайших событий: {n}'

    async def watch(self, http):
        self.loop = asyncio.get_running_loop()
        while True:
            url = self.settings['calendar_url']
            if self.wanted() and (url != self.url or time.time() - self.fetched_at > EVERY):
                try:
                    await self.fetch(http, url)
                except Exception as e:
                    log.warning('calendar: %s', e)
                    self.error = 'Не получилось загрузить календарь'
                    self.url, self.fetched_at = url, time.time()
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), 60)
            except asyncio.TimeoutError:
                pass

    async def fetch(self, http, url):
        async with http.get(normalize_url(url), timeout=30) as r:
            if r.status != 200:
                self.error = f'Календарь не отдаётся (ошибка {r.status}). Проверь ссылку'
                self.url, self.fetched_at = url, time.time()
                return
            data = await r.content.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('слишком большой файл')
        try:
            events = await asyncio.get_running_loop().run_in_executor(None, parse, data)
        except ValueError:
            self.error = 'По ссылке не календарь. Нужен адрес в формате iCal'
            self.url, self.fetched_at = url, time.time()
            return
        self.events, self.error = events, ''
        self.url, self.fetched_at = url, time.time()
