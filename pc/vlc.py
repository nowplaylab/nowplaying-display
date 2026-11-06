"""VLC: что играет, обложка и управление через встроенный веб-интерфейс VLC.

VLC 3 не сообщает Windows, что играет (в панели мультимедиа его нет), зато у него есть
веб-интерфейс: http://127.0.0.1:8080/requests/status.json. Он выключен, пока в настройках
VLC не включён интерфейс «Web» и не задан пароль. Сервер делает это сам (configure): дописывает
в %APPDATA%\\vlc\\vlcrc интерфейс http только для этого компьютера (127.0.0.1) и случайный
пароль. VLC читает настройки при запуске, так что после первой настройки его надо перезапустить.
"""
import asyncio
import logging
import os
import re
import secrets
import shutil
import time
import urllib.parse

import aiohttp

log = logging.getLogger('nowplaying')

PORT = 8080


def vlcrc_path():
    return os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'vlc', 'vlcrc')


def installed():
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'), os.environ.get('LOCALAPPDATA')):
        if base and os.path.exists(os.path.join(base, 'VideoLAN', 'VLC', 'vlc.exe')):
            return True
    return os.path.exists(vlcrc_path())


def _read_opt(text, key):
    m = re.search(rf'^{re.escape(key)}=(.*)$', text, re.M)
    return m.group(1).strip() if m else None


class Vlc:
    name = 'vlc'

    def __init__(self):
        self.password = None
        self.port = PORT
        self.status = 'none'      # none — VLC нет; restart — настроили, ждём перезапуска VLC; ready — ждём запуска; ok — отвечает
        self.art_for = None       # для какого трека уже брали обложку
        self._read_at = 0

    # ---------- настройка vlcrc ----------

    def configure(self):
        """Включить веб-интерфейс VLC, если он ещё не включён. Свои настройки пользователя не трогаем:
        если интерфейс и пароль уже есть, только читаем их."""
        path = vlcrc_path()
        if not installed():
            self.status = 'none'
            return
        try:
            text = open(path, encoding='utf-8', errors='replace').read() if os.path.exists(path) else ''
        except OSError as e:
            log.warning('vlc: vlcrc: %s', e)
            return
        intf = _read_opt(text, 'extraintf') or ''
        pw = _read_opt(text, 'http-password') or ''
        port = _read_opt(text, 'http-port')
        if port and port.isdigit():
            self.port = int(port)
        changed = False
        if 'http' not in re.split(r'[:,\s]+', intf):
            text = self._set(text, 'extraintf', ':'.join(x for x in (intf, 'http') if x))
            if not _read_opt(text, 'http-host'):
                text = self._set(text, 'http-host', '127.0.0.1')   # только с этого компьютера
            changed = True
        if not pw:
            pw = secrets.token_urlsafe(12)
            text = self._set(text, 'http-password', pw)
            changed = True
        self.password = pw
        if self.status == 'none':
            self.status = 'ready'
        if changed:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.exists(path) and not os.path.exists(path + '.nowplaying.bak'):
                    shutil.copy2(path, path + '.nowplaying.bak')   # на всякий случай — как было до нас
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(text)
                self.status = 'restart'
                log.info('vlc: web interface enabled in vlcrc (restart VLC to apply)')
            except OSError as e:
                log.warning('vlc: cannot write vlcrc: %s', e)

    @staticmethod
    def _set(text, key, value):
        line = f'{key}={value}'
        if re.search(rf'^{re.escape(key)}=.*$', text, re.M):
            return re.sub(rf'^{re.escape(key)}=.*$', line, text, count=1, flags=re.M)
        if re.search(rf'^#{re.escape(key)}=.*$', text, re.M):       # закомментированная строка по умолчанию
            return re.sub(rf'^#{re.escape(key)}=.*$', line, text, count=1, flags=re.M)
        return text + ('' if text.endswith('\n') or not text else '\n') + line + '\n'

    # ---------- запросы ----------

    def _auth(self):
        return aiohttp.BasicAuth('', self.password or '')

    async def _get(self, http, path, raw=False):
        url = f'http://127.0.0.1:{self.port}{path}'
        async with http.get(url, auth=self._auth(), timeout=aiohttp.ClientTimeout(total=1.5)) as r:
            if r.status != 200:
                if r.status == 401 and time.time() - self._read_at > 30:   # пароль поменяли в VLC
                    self._read_at = time.time()
                    self.configure()
                return None
            return await r.read() if raw else await r.json(content_type=None)

    async def now(self, http):
        """Что играет в VLC: dict(title, artist, album, playing, pos_ms, dur_ms) или None."""
        if not self.password:
            return None
        try:
            st = await self._get(http, '/requests/status.json')
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            return None    # VLC не запущен или веб-интерфейс выключен
        if not st:
            return None
        self.status = 'ok'
        state = st.get('state')
        if state not in ('playing', 'paused'):
            return None
        meta = ((st.get('information') or {}).get('category') or {}).get('meta') or {}
        title = (meta.get('title') or '').strip()
        artist = (meta.get('artist') or meta.get('album_artist') or '').strip()
        if not title:   # у файла без тегов — имя файла без расширения
            title = os.path.splitext(urllib.parse.unquote(meta.get('filename') or ''))[0].strip()
        if not title:
            return None
        length = float(st.get('length') or 0)
        pos = float(st.get('position') or 0) * length if length else float(st.get('time') or 0)
        return {'title': title, 'artist': artist, 'album': (meta.get('album') or '').strip(),
                'playing': state == 'playing', 'pos_ms': pos * 1000, 'dur_ms': length * 1000,
                'art_url': meta.get('artwork_url') or ''}

    async def art(self, http, url):
        """Обложка: VLC кладёт её файлом на этот же компьютер (file:///…) — читаем с диска."""
        if url.startswith('file:///'):
            path = urllib.parse.unquote(url[len('file:///'):]).replace('/', os.sep)
            try:
                with open(path, 'rb') as f:
                    return f.read()
            except OSError:
                pass
        try:
            return await self._get(http, '/art', raw=True)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return None

    async def control(self, http, action):
        return await self.command(http, {'toggle': 'pl_pause', 'next': 'pl_next', 'prev': 'pl_previous'}[action])

    async def seek(self, http, ms):
        return await self.command(http, 'seek', val=str(int(ms) // 1000))

    async def command(self, http, cmd, **params):
        q = urllib.parse.urlencode(dict(command=cmd, **params))
        try:
            return await self._get(http, f'/requests/status.json?{q}') is not None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            return False
