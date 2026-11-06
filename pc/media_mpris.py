"""Linux: что играет — через MPRIS (D-Bus). Его поддерживают Spotify, браузеры (Chrome, Firefox),
VLC, Rhythmbox, Strawberry и почти все плееры.

Ищем на сессионной шине имена org.mpris.MediaPlayer2.*, берём играющий плеер (или последний
на паузе), читаем Metadata и Position. Управление — методами PlayPause/Next/Previous/SetPosition.
"""
import asyncio
import logging
import os
import time
import urllib.parse

import aiohttp

log = logging.getLogger('nowplaying')

ROOT = '/org/mpris/MediaPlayer2'
PLAYER = 'org.mpris.MediaPlayer2.Player'


def _v(x):
    return getattr(x, 'value', x)


class Mpris:
    name = 'mpris'

    def __init__(self):
        self.bus = None
        self.player = None      # имя на шине выбранного плеера
        self.trackid = None
        self.art_for = None
        self.error = ''
        self._last_try = 0

    async def _connect(self):
        if self.bus is not None:
            return self.bus
        if time.time() - self._last_try < 10:
            return None
        self._last_try = time.time()
        try:
            from dbus_next.aio import MessageBus
            self.bus = await MessageBus().connect()
            self.error = ''
        except Exception as e:     # нет D-Bus сессии (сервер запущен без рабочего стола) или нет dbus-next
            self.error = str(e)
            log.warning('mpris: %s', e)
            self.bus = None
        return self.bus

    async def _call(self, dest, path, iface, member, sig='', body=None):
        from dbus_next import Message, MessageType
        reply = await asyncio.wait_for(self.bus.call(Message(destination=dest, path=path, interface=iface,
                                                             member=member, signature=sig, body=body or [])), 2)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f'{member}: {reply.error_name}')
        return reply.body

    async def now(self, http):
        """dict(title, artist, album, playing, pos_ms, dur_ms, art_url, app) или None."""
        bus = await self._connect()
        if bus is None:
            return None
        try:
            names = (await self._call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                      'ListNames'))[0]
        except Exception as e:
            log.debug('mpris: %s', e)
            self.bus = None     # шина отвалилась — переподключимся
            return None
        found = []
        for name in names:
            if not name.startswith('org.mpris.MediaPlayer2.'):
                continue
            try:
                props = (await self._call(name, ROOT, 'org.freedesktop.DBus.Properties', 'GetAll', 's', [PLAYER]))[0]
            except Exception:
                continue
            status = _v(props.get('PlaybackStatus', 'Stopped'))
            meta = {k: _v(v) for k, v in _v(props.get('Metadata', {})).items()}
            title = str(meta.get('xesam:title') or '').strip()
            if status == 'Stopped' or not title:
                continue
            artist = meta.get('xesam:artist') or meta.get('xesam:albumArtist') or ''
            if isinstance(artist, (list, tuple)):
                artist = ', '.join(str(a) for a in artist)
            found.append({'bus': name, 'trackid': meta.get('mpris:trackid'),
                          'title': title, 'artist': str(artist).strip(), 'album': str(meta.get('xesam:album') or '').strip(),
                          'playing': status == 'Playing', 'pos_ms': int(_v(props.get('Position', 0)) or 0) / 1000,
                          'dur_ms': int(meta.get('mpris:length') or 0) / 1000, 'art_url': str(meta.get('mpris:artUrl') or ''),
                          'app': name.split('org.mpris.MediaPlayer2.', 1)[1].split('.')[0]})
        if not found:
            self.player = None
            return None
        # играющий важнее; среди равных — тот, что выбрали в прошлый раз
        found.sort(key=lambda f: (not f['playing'], f['bus'] != self.player))
        best = found[0]
        self.player, self.trackid = best['bus'], best['trackid']
        return best

    async def art(self, http, url):
        if url.startswith('file://'):
            try:
                with open(urllib.parse.unquote(urllib.parse.urlparse(url).path), 'rb') as f:
                    return f.read()
            except OSError:
                return None
        if url.startswith(('http://', 'https://')):
            try:
                async with http.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    return await r.read() if r.status == 200 else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None
        return None

    async def control(self, http, action):
        if not self.player or self.bus is None:
            return False
        member = {'toggle': 'PlayPause', 'next': 'Next', 'prev': 'Previous'}[action]
        try:
            await self._call(self.player, ROOT, PLAYER, member)
            return True
        except Exception as e:
            log.warning('mpris %s: %s', member, e)
            return False

    async def seek(self, http, ms):
        if not self.player or self.bus is None or not self.trackid:
            return False
        try:
            await self._call(self.player, ROOT, PLAYER, 'SetPosition', 'ox', [str(self.trackid), int(ms * 1000)])
            return True
        except Exception as e:
            log.warning('mpris seek: %s', e)
            return False
