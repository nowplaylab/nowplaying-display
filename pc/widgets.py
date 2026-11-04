"""Виджеты на экране часов: что показать на каждом месте и адрес ПК для Wake on LAN.

Сервер собирает для планшета готовые подписи и значения, планшет только рисует.
Время второго города и разницу с местным планшет считает сам по смещению от UTC.
"""
import datetime
import logging
import re
import socket
import zoneinfo

log = logging.getLogger('nowplaying')

LAYOUTS = ('shelf', 'column', 'corners')
STYLES = ('card', 'line', 'none')
KINDS = ('', 'weather', 'forecast', 'pc', 'city', 'countdown', 'note')
FORECAST_MODES = ('hours', 'days')
WEEKDAYS = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
SLOTS = 4          # в «Углах» четыре места, в остальных раскладках три
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def valid_tz(name):
    try:
        zoneinfo.ZoneInfo(name)
        return True
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False


def plural_days(n):
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return 'день'
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 'дня'
    return 'дней'


def split_two(text):
    """Длинную подпись — в две строки по пробелу ближе к середине («переменная\\nоблачность»)."""
    if len(text) <= 11 or ' ' not in text:
        return text
    mid = len(text) // 2
    spaces = [i for i, ch in enumerate(text) if ch == ' ']
    i = min(spaces, key=lambda k: abs(k - mid))
    return text[:i] + '\n' + text[i + 1:]


def temp_text(t):
    t = round(t)
    return ('+' if t > 0 else '') + f'{t}°'.replace('-', '−')


def icon_for(code):
    """Код погоды WMO -> простой значок, который планшет рисует сам."""
    if code in (0, 1):
        return 'sun'
    if code == 2:
        return 'partly'
    if code in (95, 96, 99):
        return 'storm'
    if code in (71, 73, 75, 77, 85, 86):
        return 'snow'
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return 'rain'
    if code in (45, 48):
        return 'fog'
    return 'cloud'


def forecast_widget(fc, mode, now=None):
    """Три колонки: через 3, 6, 9 часов (на круглые часы, кратные трём) или три следующих дня."""
    if not fc:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    local = (now + datetime.timedelta(seconds=fc['offset'])).replace(tzinfo=None)
    cols = []
    if mode == 'days':
        today = local.date()
        for day, hi, lo, code in fc['daily']:
            try:
                d = datetime.date.fromisoformat(day)
            except ValueError:
                continue
            if d <= today:
                continue
            cols.append({'t': WEEKDAYS[d.weekday()],
                         'v': temp_text(hi), 'lo': temp_text(lo), 'i': icon_for(code)})
            if len(cols) == 3:
                break
        label = 'На 3 дня'
    else:
        for t, temp, code in fc['hourly']:
            try:
                at = datetime.datetime.fromisoformat(t)
            except ValueError:
                continue
            if at <= local + datetime.timedelta(minutes=30) or at.hour % 3:
                continue
            cols.append({'t': at.strftime('%H:%M'), 'v': temp_text(temp), 'i': icon_for(code)})
            if len(cols) == 3:
                break
        label = 'По часам'
    return {'kind': 'forecast', 'label': label, 'cols': cols} if len(cols) == 3 else None


def pc_widget(st):
    """Три колонки: процессор, память и температура видеокарты (или сколько ПК уже работает)."""
    if not st:
        return None
    cols = [{'t': 'Процессор', 'v': f"{st['cpu']}%"}, {'t': 'Память', 'v': f"{st['mem']}%"}]
    if st.get('gpu') is not None:
        cols.append({'t': 'Видеокарта', 'v': f"{st['gpu']}°"})
    else:
        cols.append({'t': 'Работает', 'v': st['uptime']})
    return {'kind': 'pc', 'label': 'Компьютер', 'cols': cols}


def weather_widget(weather):
    if not weather:
        return None
    return {'kind': 'weather', 'label': 'Сейчас', 'value': temp_text(weather['temp']),
            'aside': split_two(weather.get('text') or '')}


def city_widget(s, now=None):
    name, tz = s['city_name'], s['city_tz']
    if not name or not tz or not valid_tz(tz):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    there = now.astimezone(zoneinfo.ZoneInfo(tz)).utcoffset()
    # разницу с местным временем планшет считает сам — по своим часам
    return {'kind': 'city', 'label': name, 'offset_min': int(there.total_seconds() // 60)}


def countdown_widget(s, today=None):
    d = s['countdown_date']
    if not DATE_RE.match(d or ''):
        return None
    try:
        target = datetime.date.fromisoformat(d)
    except ValueError:
        return None
    days = (target - (today or datetime.date.today())).days
    if days < 0:
        return None   # событие прошло — место пустует, пока не поставят новую дату
    label = s['countdown_title'] or 'Осталось'
    if days == 0:
        return {'kind': 'countdown', 'label': label, 'value': 'Сегодня', 'aside': ''}
    return {'kind': 'countdown', 'label': label, 'value': str(days), 'aside': plural_days(days)}


def note_widget(s):
    text = (s['note_text'] or '').strip()
    return {'kind': 'note', 'label': 'Заметка', 'text': text} if text else None


def build(s, weather, forecast=None, pc=None):
    """Для /api/now: раскладка, стиль и по виджету на каждое место (None — место пустое)."""
    out = []
    for kind in (s['widget_slots'] + [''] * SLOTS)[:SLOTS]:
        w = None
        try:
            if kind == 'weather':
                w = weather_widget(weather)
            elif kind == 'forecast':
                w = forecast_widget(forecast, s['forecast_mode'])
            elif kind == 'pc':
                w = pc_widget(pc)
            elif kind == 'city':
                w = city_widget(s)
            elif kind == 'countdown':
                w = countdown_widget(s)
            elif kind == 'note':
                w = note_widget(s)
        except Exception as e:   # один сломанный виджет не должен ломать ответ планшету
            log.warning('widget %s: %s', kind, e)
        out.append(w)
    return {'layout': s['widget_layout'], 'style': s['widget_style'], 'slots': out}


# ---------- Wake on LAN ----------
_mac_cache = {}
LAST = {}   # что последний раз отдали планшету — для окна настроек


def mac_for(local_ip):
    """MAC-адрес сетевой карты, через которую планшет достучался до ПК (её и будить)."""
    if local_ip in _mac_cache:
        return _mac_cache[local_ip]
    mac = None
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            if any(a.family == socket.AF_INET and a.address == local_ip for a in addrs):
                link = [a.address for a in addrs if a.family == psutil.AF_LINK]
                if link and re.match(r'^([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}$', link[0]):
                    mac = link[0].replace('-', ':').upper()
                break
    except Exception as e:
        log.warning('mac: %s', e)
    _mac_cache[local_ip] = mac
    return mac


def wol_info(request, enabled):
    if not enabled:
        return {'on': False}
    sock = request.transport.get_extra_info('sockname') if request.transport else None
    mac = mac_for(sock[0]) if sock else None
    if mac and request.remote not in ('127.0.0.1', '::1'):
        LAST.update(mac=mac, ip=sock[0])
    return {'on': bool(mac), 'mac': mac, 'name': socket.gethostname()}
