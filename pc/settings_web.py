"""Окно настроек: страница /settings и её запросы /api/settings/*.

Менять настройки можно только с этого компьютера (127.0.0.1): планшет и другие
устройства в сети получают только то, что нужно для показа (/api/now).
"""
import asyncio
import os
import socket
import sys
import time

from aiohttp import web

import adbtool
import background
import weather
import widgets

MEDIA_EXT = background.IMAGE_EXT | background.VIDEO_EXT


def resource(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


LOCAL_HOSTS = ('127.0.0.1', 'localhost', '[::1]')


def local_only(handler, page=False):
    """Только с этого компьютера и только со страницы настроек.

    request.remote == 127.0.0.1 мало: любой сайт, открытый в браузере, тоже ходит
    с 127.0.0.1. Поэтому ещё проверяем Host (защита от DNS rebinding) и требуем
    заголовок X-NowPlaying: чужой сайт не может его поставить без разрешения CORS,
    а CORS сервер не разрешает."""
    async def wrapped(request):
        if request.remote not in ('127.0.0.1', '::1'):
            raise web.HTTPForbidden(text='настройки меняются только на этом компьютере')
        host = (request.host or '').rsplit(':', 1)[0].lower()
        if host not in LOCAL_HOSTS:
            raise web.HTTPForbidden(text='неверный адрес')
        if not page and request.headers.get('X-NowPlaying') != '1':
            raise web.HTTPForbidden(text='запрос не из окна настроек')
        return await handler(request)
    return wrapped


def routes(srv):
    """srv — модуль server (SETTINGS, BG, TRACK_BG, UPDATER, …)."""

    def media_info(bg):
        c = bg.current
        if not c:
            return None
        url = ('/bg/' if bg is srv.BG else '/bg-track/') + f"{c['id']}.{c['ext']}"
        return {'type': c['type'], 'url': url, 'name': c.get('source', '')}

    def state():
        tablet = srv.LAST_CLIENT
        return {
            'settings': srv.SETTINGS.get(),
            'version': srv.VERSION,
            'clock_media': media_info(srv.BG),
            'track_media': media_info(srv.TRACK_BG),
            'preparing': {'clock': bool(srv.BG.pending), 'track': bool(srv.TRACK_BG.pending)},
            'weather_now': srv.WEATHER.info(),
            'calendar': srv.CALENDAR.status(),
            'tablet': {'ip': tablet.get('ip'), 'seen': int(time.time() - tablet['at']) if tablet.get('at') else None,
                       'app_version': tablet.get('version')},
            'adb': bool(adbtool.adb_path()),
            'wol': {'mac': widgets.LAST.get('mac'), 'name': socket.gethostname()},
        }

    async def page(request):
        return web.FileResponse(resource(os.path.join('settings_ui', 'index.html')),
                                headers={'Cache-Control': 'no-store'})

    async def get_state(request):
        return srv.jsonr(state())

    async def post_settings(request):
        changes = await request.json()
        srv.SETTINGS.update(changes if isinstance(changes, dict) else {})
        return srv.jsonr(state())

    async def upload(request):
        kind = request.match_info['kind']
        bg = srv.BG if kind == 'clock' else srv.TRACK_BG
        reader = await request.multipart()
        part = await reader.next()
        if part is None or not part.filename:
            raise web.HTTPBadRequest(text='нет файла')
        ext = os.path.splitext(part.filename)[1].lower()
        if ext not in MEDIA_EXT:
            raise web.HTTPBadRequest(text='нужно фото, гифка или видео')
        # прошлые загрузки из окна настроек больше не нужны (свои файлы пользователя не трогаем)
        for name in os.listdir(bg.src_dir):
            if name.startswith('nowplaying-upload-'):
                try:
                    os.remove(os.path.join(bg.src_dir, name))
                except OSError:
                    pass
        path = os.path.join(bg.src_dir, f'nowplaying-upload-{int(time.time())}{ext}')
        size = 0
        with open(path, 'wb') as f:
            while True:
                chunk = await part.read_chunk(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > 2 * 1024 ** 3:
                    f.close()
                    os.remove(path)
                    raise web.HTTPRequestEntityTooLarge(max_size=2 * 1024 ** 3, actual_size=size)
                f.write(chunk)
        if kind == 'clock':
            srv.SETTINGS.update({'clock_bg': True})
        else:
            srv.SETTINGS.update({'track_bg': 'custom'})
        asyncio.create_task(bg.refresh())   # подготовка видео может занять минуту
        return srv.jsonr(state())

    async def clear(request):
        kind = request.match_info['kind']
        if kind == 'clock':
            srv.SETTINGS.update({'clock_bg': False})
        else:
            bg = srv.TRACK_BG
            for name in os.listdir(bg.src_dir):
                if name.startswith('nowplaying-upload-'):
                    try:
                        os.remove(os.path.join(bg.src_dir, name))
                    except OSError:
                        pass
            await bg.refresh()
            if srv.SETTINGS['track_bg'] == 'custom':
                srv.SETTINGS.update({'track_bg': 'video'})
        return srv.jsonr(state())

    async def open_folder(request):
        kind = request.match_info['kind']
        os.startfile((srv.BG if kind == 'clock' else srv.TRACK_BG).src_dir)
        return srv.jsonr({'ok': True})

    async def geocode(request):
        q = request.query.get('q', '').strip()
        if len(q) < 2:
            return srv.jsonr([])
        return srv.jsonr(await weather.search_city(request.app['http'], q))

    async def adb_status(request):
        if not adbtool.adb_path():
            return srv.jsonr({'adb': False, 'devices': []})
        loop = asyncio.get_running_loop()
        ip = srv.LAST_CLIENT.get('ip')
        if request.query.get('wifi') and ip:
            await loop.run_in_executor(None, adbtool.connect, ip)
        try:
            devs = await loop.run_in_executor(None, adbtool.devices)
        except Exception as e:
            return srv.jsonr({'adb': True, 'devices': [], 'error': str(e)})
        return srv.jsonr({'adb': True, 'devices': devs})

    async def adb_download(request):
        try:
            await adbtool.download(request.app['http'])
        except Exception as e:
            return srv.jsonr({'ok': False, 'error': str(e)})
        return srv.jsonr({'ok': True})

    async def adb_install(request):
        serial = (await request.json()).get('serial', '')
        apk = srv.tablet_apk()
        if not apk:
            return srv.jsonr({'ok': False, 'error': 'нет файла приложения'})
        try:
            await asyncio.get_running_loop().run_in_executor(None, adbtool.install, serial, apk)
        except Exception as e:
            return srv.jsonr({'ok': False, 'error': str(e)})
        return srv.jsonr({'ok': True})

    return [
        web.static('/settings-ui', resource('settings_ui')),
        web.get('/settings', local_only(page, page=True)),
        web.get('/api/settings', local_only(get_state)),
        web.post('/api/settings', local_only(post_settings)),
        web.post(r'/api/settings/upload/{kind:clock|track}', local_only(upload)),
        web.post(r'/api/settings/clear/{kind:clock|track}', local_only(clear)),
        web.post(r'/api/settings/open/{kind:clock|track}', local_only(open_folder)),
        web.get('/api/settings/geocode', local_only(geocode)),
        web.get('/api/settings/adb', local_only(adb_status)),
        web.post('/api/settings/adb/download', local_only(adb_download)),
        web.post('/api/settings/adb/install', local_only(adb_install)),
    ]
