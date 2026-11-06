"""NowPlaying: сервер на ПК для экрана «Сейчас играет» на планшете.

Берёт трек из системной панели Windows (её используют браузеры и Яндекс Музыка),
отдаёт по локальной сети:
  GET /api/now                — что играет, позиция, цвет фона
  GET /art/<track_id>.jpg     — обложка (уменьшенная)
  GET /api/lyrics/<track_id>  — текст песни с LRCLIB (синхронный, если есть)
и отвечает на UDP-поиск, чтобы планшет сам находил ПК.
Всё хранится только в памяти.
"""
import asyncio
import colorsys
import hashlib
import io
import json
import logging
import os
import re
import socket
import sys
import ssl
import time
import urllib.parse

import aiohttp
from aiohttp import web
from PIL import Image, ImageEnhance, ImageFilter
import truststore  # проверка сертификатов через хранилище Windows (достраивает цепочки, как браузер)
if sys.platform == 'win32':   # панель «Сейчас играет» Windows
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
else:                         # Linux — MPRIS, macOS — Apple Music и Spotify (см. media_mpris.py, media_mac.py)
    SessionManager = Status = Buffer = InputStreamOptions = None

import background
import clips
import lamp
import bands
import calendar_ics
import pcstate
import photoframe
import pulse
import settings
import settings_web
import vlc
import weather
import widgets
import updater
import volume
import yandex

VERSION = '2.6'     # версия программы для ПК (= тег релиза на GitHub)
HTTP_PORT = int(os.environ.get('NP_HTTP_PORT', '47801'))
UDP_PORT = int(os.environ.get('NP_UDP_PORT', '47800'))
POLL = 0.5           # секунд между опросами панели Windows
ART_SIZE = 480       # обложка для экрана 1024x600
ITUNES = 'https://itunes.apple.com/search'
LRCLIB = 'https://lrclib.net/api'
UA = 'NowPlaying-Tab3/1.0 (personal LAN display)'

log = logging.getLogger('nowplaying')
state = {'track_id': None, 'playing': False, 'title': '', 'artist': '', 'album': '', 'app': '',
         'position_ms': 0, 'duration_ms': 0, 'measured_at': 0.0, 'color': '#202020', 'colors': None, 'text_color': '#ffffff', 'art_rev': 0}
art = {}       # track_id -> (jpeg bytes, цвет фона)
lyrics = {}    # track_id -> dict
lyrics_jobs = {}
art_attempts = {}
current = {}  # выбранная сессия Windows (для кнопок управления)
seek_hold = {}  # после перемотки Windows ещё пару секунд отдаёт старую позицию — держим новую
itunes_jobs = {}
itunes_cache = {}  # 'артист|название' -> url обложки или None


def now_ms():
    return time.time() * 1000


def split_title(title, artist):
    """Браузеры иногда отдают «Артист - Песня» в названии и пустого артиста."""
    if not artist and ' - ' in title:
        a, t = title.split(' - ', 1)
        return t.strip(), a.strip()
    return title, artist


def clean_title(title):
    # убираем приписки вроде «(Official Video)», счётчик уведомлений «(3)» в начале
    title = re.sub(r'^\(\d+\)\s*', '', title)
    title = re.sub(r'\s*[\(\[](official|lyric|lyrics|audio|video|mv|hd|4k)[^\)\]]*[\)\]]', '', title, flags=re.I)
    return title.strip()


JUNK = re.compile(r'tiktok|youtube shorts|#shorts', re.I)
MIN_TRACK_MS = 45000   # короче — это ролик, уведомление или реклама, а не песня


def pick_session(mgr, eligible):
    sessions = [x for x in mgr.get_sessions() if x.source_app_user_model_id in eligible]
    for s in sessions:
        if s.get_playback_info().playback_status == Status.PLAYING:
            return s
    cur = mgr.get_current_session()
    if cur is not None and cur.source_app_user_model_id in eligible:
        return cur
    return sessions[0] if sessions else None


async def read_thumbnail(ref):
    stream = await ref.open_read_async()
    size = stream.size
    if not size or size > 20 * 1024 * 1024:
        return None
    buf = Buffer(size)
    res = await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
    return bytes(memoryview(res))


def process_art(data):
    img = Image.open(io.BytesIO(data)).convert('RGB')
    # браузеры часто дают превью 16:9 с полями; вырезаем центральный квадрат
    w, h = img.size
    if w > h * 1.2:
        img = img.crop(((w - h) // 2, 0, (w + h) // 2, h))
    img.thumbnail((ART_SIZE, ART_SIZE))
    # цвет фона: средний цвет обложки, затемнённый
    r, g, b = img.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    k = 0.45
    bg = (int(r * k), int(g * k), int(b * k))
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=85)
    return out.getvalue(), '#%02x%02x%02x' % bg, gradient_colors(img)


def gradient_colors(img):
    """Три цвета для градиента фона (слева сверху → по центру → справа снизу): в каждой
    части обложки берём самый частый цвет и приглушаем его, чтобы белый текст читался."""
    small = img.resize((48, 48))
    parts = [(0, 0, 24, 24), (12, 12, 36, 36), (24, 24, 48, 48)]
    colors = []
    for box in parts:
        q = small.crop(box).quantize(colors=4, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()
        idx = max(q.getcolors(), key=lambda c: c[0])[1]
        r, g, b = (x / 255 for x in pal[idx * 3: idx * 3 + 3])
        h, sat, v = colorsys.rgb_to_hsv(r, g, b)
        v = min(v, 0.42) * 0.9 + 0.04          # не светлее ~45%, но и не совсем чёрный
        sat = min(1.0, sat * 1.1)
        r, g, b = colorsys.hsv_to_rgb(h, sat, v)
        colors.append('#%02x%02x%02x' % (int(r * 255), int(g * 255), int(b * 255)))
    return colors


def set_art(tid, jpeg, color, colors):
    for k in [k for k in art if k != tid]:
        art.pop(k)
    art[tid] = (jpeg, color, colors)
    state.update(color=color, colors=colors)
    state['art_rev'] += 1


def norm(text):
    text = text.lower().replace('ё', 'е')
    text = re.sub(r'[\(\[].*?[\)\]]', ' ', text)            # (feat. ...), [Remix] и т.п.
    text = re.sub(r'\b(feat|ft|featuring)\b.*', ' ', text)
    return ' '.join(re.findall(r'\w+', text))


def artists_of(text):
    return {norm(a) for a in re.split(r',|&|\bx\b|\bfeat\.?|\bft\.?|\band\b|\sи\s', text.lower()) if norm(a)}


def same_track(title, artist, cand):
    t1, t2 = norm(title), norm(cand.get('trackName', ''))
    if not t1 or not t2 or not (t1 == t2 or t1 in t2.split(' - ')[0] or t2 in t1):
        return False
    return bool(artists_of(artist) & artists_of(cand.get('artistName', '')))


async def find_cover(app, title, artist):
    """Ищет обложку: сначала iTunes (600 px), потом Deezer (1000 px, много русской музыки)."""
    http = app['http']
    first_artist = re.split(r',|&', artist)[0].strip()
    for country in ('ru', 'us'):
        params = {'term': f'{first_artist} {title}', 'entity': 'song', 'limit': '10', 'country': country}
        try:
            async with http.get(ITUNES, params=params) as r:
                found = (await r.json(content_type=None)).get('results', []) if r.status == 200 else []
        except Exception as e:
            log.warning('itunes: %s', e)
            found = []
        match = next((c for c in found if same_track(title, artist, c)), None)
        if match and match.get('artworkUrl100'):
            return match['artworkUrl100'].replace('100x100bb', '600x600bb')
    try:
        async with http.get('https://api.deezer.com/search', params={'q': f'{first_artist} {title}', 'limit': '15'}) as r:
            found = (await r.json(content_type=None)).get('data', []) if r.status == 200 else []
    except Exception as e:
        log.warning('deezer: %s', e)
        found = []
    for c in found:
        cand = {'trackName': c.get('title', ''), 'artistName': (c.get('artist') or {}).get('name', '')}
        cover = (c.get('album') or {}).get('cover_xl')
        if cover and same_track(title, artist, cand):
            return cover
    return None


async def improve_art(app, tid, title, artist):
    """Чёткая обложка 600x600 из iTunes, только если трек точно совпал."""
    key = f'{norm(artist)}|{norm(title)}'
    url = itunes_cache.get(key, '')
    try:
        if url == '':
            url = await find_cover(app, title, artist)
            itunes_cache[key] = url
            if len(itunes_cache) > 300:
                itunes_cache.pop(next(iter(itunes_cache)))
        if url:
            async with app['http'].get(url) as r:
                data = await r.read() if r.status == 200 else None
            if data and tid == state['track_id']:
                jpeg, color, colors = await asyncio.get_running_loop().run_in_executor(None, process_art, data)
                set_art(tid, jpeg, color, colors)
                art_attempts[tid] = 99  # больше не перезаписывать обложкой из Windows
        log.info('sharp art for %s — %s: %s', artist, title, url or 'no match')
    except Exception as e:
        log.warning('sharp art: %s', e)
    finally:
        if len(itunes_jobs) > 50:
            itunes_jobs.pop(next(iter(itunes_jobs)))


async def poll_loop(app):
    if SessionManager is None:
        return await poll_other(app)
    mgr = await SessionManager.request_async()
    current['mgr'] = mgr
    while True:
        try:
            await update_state(app, mgr)
        except Exception as e:  # сессия могла закрыться посреди опроса
            log.debug('poll: %s', e)
        await asyncio.sleep(POLL)


async def poll_other(app):
    """Linux и macOS: спрашиваем свой источник (MPRIS или AppleScript), потом VLC."""
    while True:
        try:
            v, src = None, None
            if MEDIA is not None:
                v, src = await MEDIA.now(app['http']), MEDIA
            vs = await VLC.now(app['http'])
            if vs and (v is None or (vs['playing'] and not v['playing'])):
                v, src = vs, VLC
            junk = v and (JUNK.search(f"{v['title']} {v['artist']}") or 0 < v['dur_ms'] < MIN_TRACK_MS)
            if v and not junk:
                current.update(session=None, src=src)
                await use_source(app, src, v)
            else:
                current.update(session=None, src=None)
                state.update(track_id=None, playing=False, title='', artist='', album='', app='')
        except Exception as e:
            log.debug('poll: %s', e)
        await asyncio.sleep(POLL)


async def update_state(app, mgr):
    eligible = set()
    for x in mgr.get_sessions():
        try:
            pr = await x.try_get_media_properties_async()
            t = x.get_timeline_properties()
            length = (t.end_time - t.start_time).total_seconds() * 1000 if t.end_time else 0
            if pr.title and not JUNK.search(f'{pr.title} {pr.artist}') and not (0 < length < MIN_TRACK_MS):
                eligible.add(x.source_app_user_model_id)
        except Exception:
            pass
    s = pick_session(mgr, eligible)
    # VLC в панель Windows не попадает — спрашиваем его самого. Берём его, если через Windows
    # ничего не играет (или там пауза, а VLC играет)
    vs = await VLC.now(app['http'])
    if vs and (s is None or (vs['playing'] and s.get_playback_info().playback_status != Status.PLAYING)):
        current.update(session=None, src=VLC)
        await use_source(app, VLC, vs)
        return
    current.update(session=s, src=None)
    if s is None:
        state.update(track_id=None, playing=False, title='', artist='', album='', app='')
        return
    props = await s.try_get_media_properties_async()
    tl = s.get_timeline_properties()
    pb = s.get_playback_info()
    title, artist = split_title(clean_title(props.title or ''), props.artist or props.album_artist or '')
    app_id = s.source_app_user_model_id or ''
    tid = hashlib.sha1(f'{app_id}|{title}|{artist}'.encode()).hexdigest()[:12] if title else None
    playing = pb.playback_status == Status.PLAYING

    pos = tl.position.total_seconds() * 1000 if tl.position else 0
    dur = (tl.end_time - tl.start_time).total_seconds() * 1000 if tl.end_time else 0
    upd = tl.last_updated_time.timestamp() * 1000 if tl.last_updated_time else now_ms()
    if playing and 0 < upd <= now_ms():
        pos += now_ms() - upd  # Windows обновляет позицию редко — досчитываем
    if seek_hold.get('tid') == tid and now_ms() < seek_hold['until']:
        pos = seek_hold['pos'] + (now_ms() - seek_hold['at'] if playing else 0)
    if dur:
        pos = min(pos, dur)

    if tid != state['track_id']:
        log.info('track: %s — %s [%s]', artist, title, app_id)
        state.update(color=art[tid][1] if tid in art else '#202020', colors=art[tid][2] if tid in art else None)
        art_attempts.clear()
    # обложку пробуем несколько раз: Яндекс Музыка отдаёт её чуть позже названия
    if tid and tid not in art and props.thumbnail and art_attempts.get(tid, 0) < 6:
        art_attempts[tid] = art_attempts.get(tid, 0) + 1
        try:
            data = await read_thumbnail(props.thumbnail)
            if data:
                jpeg, color, colors = await asyncio.get_running_loop().run_in_executor(None, process_art, data)
                if tid not in art:  # iTunes мог успеть раньше
                    set_art(tid, jpeg, color, colors)
        except Exception as e:
            log.warning('art: %s', e)
    if tid and artist and tid not in itunes_jobs:
        itunes_jobs[tid] = asyncio.create_task(improve_art(app, tid, title, artist))
    # текст ищем, когда известны и название, и (по возможности) длительность
    if tid and tid not in lyrics and tid not in lyrics_jobs and (dur > 0 or tid == state['track_id']):
        lyrics_jobs[tid] = asyncio.create_task(fetch_lyrics(app, tid, title, artist, props.album_title or '', dur))
    if tid and dur > 0:
        CLIPS.request(app['http'], tid, title, artist, dur)

    state.update(track_id=tid, playing=playing, title=title, artist=artist, album=props.album_title or '',
                 app=app_id, position_ms=int(pos), duration_ms=int(dur), measured_at=now_ms())


def parse_lrc(text):
    lines = []
    for raw in (text or '').splitlines():
        stamps = re.findall(r'\[(\d+):(\d+(?:\.\d+)?)\]', raw)
        words = re.sub(r'\[[^\]]*\]', '', raw).strip()
        for m, s in stamps:
            lines.append({'t': int((int(m) * 60 + float(s)) * 1000), 'text': words})
    return sorted(lines, key=lambda l: l['t'])


async def fetch_lyrics(app, tid, title, artist, album, dur):
    """Текст из LRCLIB. Синхронный берём только если длительность совпала (±4 с),
    иначе тайминги разъедутся (ремикс, live, другая версия)."""
    result = {'synced': [], 'plain': ''}
    try:
        http = app['http']
        q = {'track_name': title, 'artist_name': artist}
        cands = []
        if artist:
            params = dict(q, album_name=album, duration=int(dur / 1000)) if dur else dict(q)
            async with http.get(f'{LRCLIB}/get', params=params) as r:
                if r.status == 200:
                    cands.append(await r.json())
        async with http.get(f'{LRCLIB}/search', params=q if artist else {'q': title}) as r:
            if r.status == 200:
                cands += await r.json()

        def diff(c):
            return abs((c.get('duration') or 0) * 1000 - dur) if dur and c.get('duration') else None

        limit_synced = 4000 if artist else 2000    # без артиста — строже
        synced = [c for c in cands if c.get('syncedLyrics') and
                  (diff(c) is None and dur == 0 and artist or diff(c) is not None and diff(c) <= limit_synced)]
        synced.sort(key=lambda c: diff(c) or 0)
        plain = [c for c in cands if c.get('plainLyrics') and (artist or (diff(c) is not None and diff(c) <= 5000))]
        plain.sort(key=lambda c: diff(c) if diff(c) is not None else 10 ** 9)
        if synced:
            result = {'synced': parse_lrc(synced[0]['syncedLyrics']), 'plain': synced[0].get('plainLyrics') or ''}
        elif plain:
            result = {'synced': [], 'plain': plain[0]['plainLyrics']}
    except Exception as e:
        log.warning('lyrics: %s', e)
    source = 'lrclib'
    # запасной источник обычного текста — Яндекс Музыка
    if not result['synced'] and not result['plain']:
        try:
            result['plain'] = await yandex.plain_lyrics(app['http'], title, artist, dur)
            source = 'yandex'
        except Exception as e:
            log.warning('lyrics yandex: %s', e)
    log.info('lyrics for %s — %s: synced %d, plain %d (%s)', artist, title, len(result['synced']),
             len(result['plain']), source)
    lyrics[tid] = result
    lyrics_jobs.pop(tid, None)
    if len(lyrics) > 50:
        lyrics.pop(next(iter(lyrics)))


def jsonr(data):
    return web.Response(text=json.dumps(data, ensure_ascii=False), content_type='application/json', charset='utf-8')


async def h_now(request):
    dev = request.query.get('dev', '')[:40] or None
    tv = request.query.get('tv') == '1'
    if dev and request.query.get('v'):
        SETTINGS.seen(dev, request.query.get('name', ''), request.remote or '', request.query['v'], tv)
    S = SETTINGS.view(dev)    # общие настройки и поверх — свои у этого устройства
    s = dict(state)
    if s['playing'] and s['duration_ms']:
        s['position_ms'] = int(min(s['position_ms'] + now_ms() - s['measured_at'], s['duration_ms']))
    s.pop('measured_at')
    s['server_time_ms'] = int(now_ms())
    s['has_art'] = s['track_id'] in art
    s['lyrics_ready'] = s['track_id'] in lyrics
    s['bg'] = (FRAME.info() if S['frame_on'] else None) or (BG.info() if S['clock_bg'] else None)   # фоторамка важнее своего фона
    if tv:   # телевизору — видео в полном качестве; у уже скачанного трека доделаем его
        if s['track_id'] in CLIPS.ready and s['duration_ms']:
            CLIPS.request(request.app['http'], s['track_id'], s['title'], s['artist'], s['duration_ms'])
        s['clip'], s['clip_hd'] = CLIPS.info_tv(s['track_id'])
    else:
        s['clip'] = CLIPS.info(s['track_id'])      # видео из Яндекс Музыки (если включено)
    s['track_media'] = TRACK_BG.info() if S['track_bg'] == 'custom' else None
    # размытая обложка: выбрана сама или как запасной фон, пока у трека нет видео из клипа
    fallback_blur = S['track_bg'] == 'video' and not s['clip'] and S['video_fallback'] == 'blur'
    if (S['track_bg'] == 'blur' or fallback_blur) and s['has_art']:
        rev = f"{s['track_id']}r{s['art_rev']}"   # новая (чёткая) обложка — новый размытый фон
        s['track_media'] = {'id': rev, 'type': 'image', 'ext': 'jpg', 'url': f"/art/{s['track_id']}-blur.jpg?r={s['art_rev']}"}
    s['ui'] = SETTINGS.ui_for(dev)
    s['weather'] = WEATHER.info() if S['weather'] else None   # строка под датой
    s['widgets'] = widgets.build(S, WEATHER.info(), WEATHER.forecast_info(), PC_STATE.info(), CALENDAR.info())
    s['wol'] = widgets.wol_info(request, S['wol'])
    if request.remote not in ('127.0.0.1', '::1') and request.query.get('v'):
        LAST_CLIENT.update(ip=request.remote, at=time.time(), version=request.query['v'][:20])
    s['apk_version'] = UPDATER.apk[0] if UPDATER.apk else None  # планшет сравнит со своей
    s['volume'], s['muted'] = await current_volume()
    return jsonr(s)


VOLUME_CACHE = {'at': 0.0, 'value': (None, False)}


async def current_volume():
    """Громкость ПК; читается не чаще раза в секунду — планшет спрашивает /api/now постоянно."""
    if time.time() - VOLUME_CACHE['at'] > 1:
        VOLUME_CACHE['at'] = time.time()
        try:
            VOLUME_CACHE['value'] = await asyncio.get_running_loop().run_in_executor(None, volume.get)
        except Exception:  # нет устройства вывода и т. п.
            VOLUME_CACHE['value'] = (None, False)
    return VOLUME_CACHE['value']


async def use_source(app, src, v):
    """Трек не из панели Windows (VLC, MPRIS на Linux, Music/Spotify на Mac): то же самое,
    только обложку отдаёт сам источник."""
    title, artist = split_title(clean_title(v['title']), v['artist'])
    tid = hashlib.sha1(f'{src.name}|{title}|{artist}'.encode()).hexdigest()[:12]
    pos, dur = v['pos_ms'], v['dur_ms']
    if seek_hold.get('tid') == tid and now_ms() < seek_hold['until']:
        pos = seek_hold['pos'] + (now_ms() - seek_hold['at'] if v['playing'] else 0)
    if dur:
        pos = min(pos, dur)
    if tid != state['track_id']:
        log.info('track: %s — %s [%s]', artist, title, v.get('app') or src.name)
        state.update(color=art[tid][1] if tid in art else '#202020', colors=art[tid][2] if tid in art else None)
    if tid not in art and v['art_url'] and src.art_for != tid:
        src.art_for = tid
        data = await src.art(app['http'], v['art_url'])
        if data:
            try:
                jpeg, color, colors = await asyncio.get_running_loop().run_in_executor(None, process_art, data)
                if tid not in art:
                    set_art(tid, jpeg, color, colors)
            except Exception as e:
                log.warning('%s art: %s', src.name, e)
    if artist and tid not in itunes_jobs:
        itunes_jobs[tid] = asyncio.create_task(improve_art(app, tid, title, artist))
    if tid not in lyrics and tid not in lyrics_jobs:
        lyrics_jobs[tid] = asyncio.create_task(fetch_lyrics(app, tid, title, artist, v['album'], dur))
    if dur > 0:
        CLIPS.request(app['http'], tid, title, artist, dur)
    state.update(track_id=tid, playing=v['playing'], title=title, artist=artist, album=v['album'],
                 app=v.get('app') or src.name, position_ms=int(pos), duration_ms=int(dur), measured_at=now_ms())


def control_session():
    s = current.get('session')
    if s is None and current.get('mgr') is not None:
        # на паузе плеер может ненадолго «спрятать» трек — берём текущую или любую сессию Windows
        mgr = current['mgr']
        s = mgr.get_current_session() or next(iter(mgr.get_sessions()), None)
    if s is None:
        raise web.HTTPNotFound()
    return s


async def h_seek(request):
    ms = int(request.match_info['ms'])
    if current.get('src'):
        ok = await current['src'].seek(request.app['http'], ms)
    else:
        s = control_session()
        ok = await s.try_change_playback_position_async(ms * 10000)  # в «тиках» по 100 нс
    if ok:
        t = now_ms()
        seek_hold.update(tid=state['track_id'], pos=ms, at=t, until=t + 3000)
        state.update(position_ms=ms, measured_at=t)
    return jsonr({'ok': bool(ok)})


async def h_volume(request):
    level = int(request.match_info['level'])
    try:
        now = await asyncio.get_running_loop().run_in_executor(None, volume.set_level, level)
    except Exception as e:
        log.warning('volume: %s', e)
        raise web.HTTPServiceUnavailable()
    VOLUME_CACHE.update(at=time.time(), value=(now, False))
    return jsonr({'volume': now})


async def h_control(request):
    action = request.match_info['action']
    if current.get('src'):
        return jsonr({'ok': await current['src'].control(request.app['http'], action)})
    s = control_session()
    ok = await {'toggle': s.try_toggle_play_pause_async, 'next': s.try_skip_next_async,
                'prev': s.try_skip_previous_async}[action]()
    return jsonr({'ok': bool(ok)})


async def h_art(request):
    item = art.get(request.match_info['id'])
    if not item:
        raise web.HTTPNotFound()
    return web.Response(body=item[0], content_type='image/jpeg', headers={'Cache-Control': 'max-age=3600'})


blur_cache = {}   # track_id -> (jpeg обложки, размытая картинка)


def blur_art(jpeg):
    """Очень сильно размытая обложка на фон экрана 16:9: сначала ужимаем до 48 px (уходят все
    детали, остаются пятна цвета), размываем, растягиваем и размываем ещё раз — без ступенек."""
    img = Image.open(io.BytesIO(jpeg)).convert('RGB').resize((48, 48), Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(5)).resize((320, 320), Image.BICUBIC)
    img = img.crop((0, 70, 320, 250)).filter(ImageFilter.GaussianBlur(14))
    img = ImageEnhance.Color(img).enhance(1.25)
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=82)
    return out.getvalue()


async def h_art_blur(request):
    tid = request.match_info['id']
    item = art.get(tid)
    if not item:
        raise web.HTTPNotFound()
    cached = blur_cache.get(tid)
    if not cached or cached[0] is not item[0]:
        data = await asyncio.get_running_loop().run_in_executor(None, blur_art, item[0])
        blur_cache.clear()
        blur_cache[tid] = cached = (item[0], data)
    return web.Response(body=cached[1], content_type='image/jpeg', headers={'Cache-Control': 'max-age=3600'})


async def h_lyrics(request):
    tid = request.match_info['id']
    if tid in lyrics_jobs:
        try:
            await asyncio.wait_for(asyncio.shield(lyrics_jobs[tid]), 10)
        except asyncio.TimeoutError:
            pass
    return jsonr(lyrics.get(tid) or {'synced': [], 'plain': '', 'pending': tid in lyrics_jobs})


class Discovery(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.strip() == b'NOWPLAYING_DISCOVER':
            reply = f'NOWPLAYING_HERE {HTTP_PORT} {socket.gethostname()}'.encode()
            self.transport.sendto(reply, addr)


SETTINGS = settings.Settings()
BG = background.Background()
# своё фото или видео за экраном трека — из подпапки track рядом с фоном часов
TRACK_BG = background.Background(src_dir=os.path.join(background.default_dir(), 'track'),
                                 cache_dir=os.path.join(os.path.dirname(BG.cache_dir), 'bg_track'))
VLC = vlc.Vlc()
if sys.platform.startswith('linux'):
    import media_mpris
    MEDIA = media_mpris.Mpris()
elif sys.platform == 'darwin':
    import media_mac
    MEDIA = media_mac.MacMedia()
else:
    MEDIA = None   # на Windows — панель «Сейчас играет»
CLIPS = clips.Clips()
CLIPS.want_hd = lambda: SETTINGS.has_tv()
UPDATER = updater.Updater(VERSION)
WEATHER = weather.Weather(SETTINGS)
PC_STATE = pcstate.PcState(SETTINGS)
CALENDAR = calendar_ics.Calendar(SETTINGS)
FRAME = photoframe.PhotoFrame(SETTINGS)
BANDS = bands.Bands()
PULSE = pulse.Pulse()
LAMP = lamp.Lamp(SETTINGS)
LAST_CLIENT = {}   # последний планшет, который спрашивал /api/now: ip, время, версия приложения


def apply_settings(s):
    CLIPS.enabled = s['track_bg'] == 'video'
    UPDATER.enabled = s['auto_update']


apply_settings(SETTINGS.get())
SETTINGS.listeners.append(apply_settings)


def tablet_apk():
    """Файл приложения для установки по adb: самый новый из скачанного с релиза и вложенного
    в программу (вложенный — той же версии, что программа)."""
    own = settings_web.resource('NowPlaying.apk')
    dev = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'android', 'NowPlaying.apk')
    bundled = own if os.path.exists(own) else (dev if os.path.exists(dev) else None)
    released = UPDATER.apk[1] if UPDATER.apk and os.path.exists(UPDATER.apk[1]) else None
    if released and (not bundled or updater.version_tuple(UPDATER.apk[0]) > updater.version_tuple(VERSION)):
        return released
    return bundled


async def h_pulse(request):
    """Поток уровня звука для «дыхания» фона: строка «0.42» 25 раз в секунду.
    Планшет держит соединение открытым; через 10 минут сервер его закрывает — планшет
    переподключится (так не копятся забытые соединения)."""
    resp = web.StreamResponse(headers={'Content-Type': 'text/plain', 'Cache-Control': 'no-store'})
    await resp.prepare(request)
    PULSE.clients += 1
    try:
        end = time.time() + 600
        while time.time() < end:
            on = state['playing'] and SETTINGS.anywhere(lambda v: v['pulse'])
            PULSE.want(on)
            await resp.write(b'%.2f\n' % (PULSE.level if on else 0.0))
            await asyncio.sleep(1 / 25)
    except Exception:   # планшет отключился
        pass
    finally:
        PULSE.clients -= 1
        if PULSE.clients <= 0:
            PULSE.want(False)
    return resp


async def h_bands(request):
    """Поток полос частот для визуализатора: строка из 24 чисел 0–99, 30 раз в секунду.
    Как и /api/pulse — через 10 минут сервер закрывает соединение, планшет переподключится."""
    resp = web.StreamResponse(headers={'Content-Type': 'text/plain', 'Cache-Control': 'no-store'})
    await resp.prepare(request)
    BANDS.clients += 1
    try:
        end = time.time() + 600
        while time.time() < end:
            # полосы нужны визуализатору и пятнам свечения (без видео за треком)
            on = state['playing'] and SETTINGS.anywhere(lambda v: v['visualizer'] != 'off' or v['pulse'])
            BANDS.want(on)
            await resp.write((BANDS.line() if on else '0 ' * 23 + '0').encode() + b'\n')
            await asyncio.sleep(1 / 30)
    except Exception:   # планшет отключился
        pass
    finally:
        BANDS.clients -= 1
        if BANDS.clients <= 0:
            BANDS.want(False)
    return resp


async def h_track_bg(request):
    c = TRACK_BG.current
    if not c or c['id'] != request.match_info['id'] or c['ext'] != request.match_info['ext']:
        raise web.HTTPNotFound()
    return web.FileResponse(c['path'], headers={'Cache-Control': 'max-age=86400'})


async def h_apk(request):
    if not UPDATER.apk or not os.path.exists(UPDATER.apk[1]):
        raise web.HTTPNotFound()
    return web.FileResponse(UPDATER.apk[1], headers={'Content-Type': 'application/vnd.android.package-archive'})


async def h_clip(request):
    path = (CLIPS.ready_hd if request.match_info.get('hd') else CLIPS.ready).get(request.match_info['id'])
    if not path or not os.path.exists(path):
        raise web.HTTPNotFound()
    CLIPS.touch(request.match_info['id'])
    return web.FileResponse(path, headers={'Cache-Control': 'max-age=86400'})


async def h_bg(request):
    c = BG.current
    if FRAME.current and FRAME.current['id'] == request.match_info['id']:
        c = FRAME.current
    if not c or c['id'] != request.match_info['id'] or c['ext'] != request.match_info['ext']:
        raise web.HTTPNotFound()
    return web.FileResponse(c['path'], headers={'Cache-Control': 'max-age=86400'})


async def on_startup(app):
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    app['http'] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), headers={'User-Agent': UA},
                                        connector=aiohttp.TCPConnector(ssl=ctx))
    await asyncio.get_running_loop().run_in_executor(None, VLC.configure)   # веб-интерфейс VLC
    app['poll'] = asyncio.create_task(poll_loop(app))
    app['bg'] = asyncio.create_task(BG.watch())
    app['track_bg'] = asyncio.create_task(TRACK_BG.watch())
    app['weather'] = asyncio.create_task(WEATHER.watch(app['http']))
    app['pcstate'] = asyncio.create_task(PC_STATE.watch())
    app['calendar'] = asyncio.create_task(CALENDAR.watch(app['http']))
    app['frame'] = asyncio.create_task(FRAME.watch())
    app['update'] = asyncio.create_task(UPDATER.watch(app['http']))
    app['lamp'] = asyncio.create_task(LAMP.watch(app['http'], lambda: (state['playing'], state['track_id']), art))
    loop = asyncio.get_running_loop()
    app['udp'], _ = await loop.create_datagram_endpoint(Discovery, local_addr=('0.0.0.0', UDP_PORT))


async def on_cleanup(app):
    app['poll'].cancel()
    app['bg'].cancel()
    app['track_bg'].cancel()
    app['weather'].cancel()
    app['update'].cancel()
    app['lamp'].cancel()
    app['udp'].close()
    await app['http'].close()


def setup_logging(log_dir=None):
    fmt = '%(asctime)s %(message)s'
    if log_dir or sys.stderr is None:  # без консоли (pythonw, exe) — пишем в файл до 1 МБ
        from logging.handlers import RotatingFileHandler
        path = os.path.join(log_dir or os.path.dirname(os.path.abspath(__file__)), 'server.log')
        logging.basicConfig(level=logging.INFO, format=fmt,
                            handlers=[RotatingFileHandler(path, maxBytes=1_000_000, backupCount=1, encoding='utf-8')])
        return path
    logging.basicConfig(level=logging.INFO, format=fmt)
    return None


def create_app():
    app = web.Application()
    app.add_routes([
        web.get('/api/now', h_now),
        web.get(r'/art/{id:[0-9a-f]{12}}.jpg', h_art),
        web.get(r'/art/{id:[0-9a-f]{12}}-blur.jpg', h_art_blur),
        web.get(r'/api/lyrics/{id:[0-9a-f]{12}}', h_lyrics),
        web.get(r'/api/control/{action:toggle|next|prev}', h_control),
        web.get(r'/api/control/seek/{ms:\d{1,9}}', h_seek),
        web.get(r'/api/control/volume/{level:\d{1,3}}', h_volume),
        web.get(r'/bg/{id:[0-9a-f]{12}}.{ext:jpg|mp4}', h_bg),
        web.get(r'/clip/{id:[0-9a-f]{12}}{hd:(-hd)?}.mp4', h_clip),
        web.get('/update/app.apk', h_apk),
        web.get('/api/pulse', h_pulse),
        web.get('/api/bands', h_bands),
        web.get(r'/bg-track/{id:[0-9a-f]{12}}.{ext:jpg|mp4}', h_track_bg),
        *settings_web.routes(sys.modules[__name__]),
        web.get('/health', lambda r: web.Response(text='ok')),
    ])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    setup_logging()
    web.run_app(create_app(), host='0.0.0.0', port=HTTP_PORT, access_log=None, print=None)


if __name__ == '__main__':
    main()
