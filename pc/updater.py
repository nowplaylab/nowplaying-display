"""Обновления из последнего релиза на GitHub.

Раз в 6 часов сервер смотрит последний релиз. Если там версия новее, скачивает
NowPlayingServer.exe (только в собранной программе) и NowPlaying-<версия>.apk, сверяет
их с SHA256SUMS.txt из того же релиза и складывает в %LOCALAPPDATA%\\NowPlaying\\update.
Программа для ПК меняет себя сама (nowplaying_app.py), APK сервер отдаёт планшету
по локальной сети: Android 4.2 до GitHub сам не достучится (старые TLS и сертификаты).
"""
import asyncio
import hashlib
import logging
import os
import re
import sys

import aiohttp

log = logging.getLogger('nowplaying')

REPO = 'nowplaylab/nowplaying-display'
CHECK_EVERY = 6 * 3600


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or ''))


class Updater:
    def __init__(self, version, data_dir=None):
        local = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        self.dir = os.path.join(data_dir or os.path.join(local, 'NowPlaying'), 'update')
        self.version = version
        self.enabled = True
        self.latest = None     # версия последнего релиза
        self.exe = None        # путь к скачанному новому exe, когда он готов
        self.apk = None        # (версия, путь) — последний APK для планшета
        os.makedirs(self.dir, exist_ok=True)
        self._pick_up()

    def _pick_up(self):
        """Что уже скачано в прошлые запуски."""
        best = None
        for name in os.listdir(self.dir):
            m = re.fullmatch(r'NowPlaying-(.+)\.apk', name)
            if m and (best is None or version_tuple(m.group(1)) > version_tuple(best)):
                best = m.group(1)
        if best:
            self.apk = (best, os.path.join(self.dir, f'NowPlaying-{best}.apk'))

    def info(self):
        return {'version': self.version, 'latest': self.latest,
                'apk': self.apk[0] if self.apk else None}

    async def watch(self, http):
        await asyncio.sleep(20)   # не мешаем запуску
        while True:
            if self.enabled:
                try:
                    await self.check(http)
                except Exception as e:
                    log.warning('update: %s', e)
            await asyncio.sleep(CHECK_EVERY)

    async def check(self, http):
        async with http.get(f'https://api.github.com/repos/{REPO}/releases/latest',
                            headers={'Accept': 'application/vnd.github+json'}) as r:
            if r.status != 200:
                log.warning('update: github %s', r.status)
                return
            rel = await r.json()
        tag = (rel.get('tag_name') or '').lstrip('v')
        assets = {a['name']: a['browser_download_url'] for a in rel.get('assets') or []}
        if not tag or 'SHA256SUMS.txt' not in assets:
            return
        self.latest = tag
        async with http.get(assets['SHA256SUMS.txt']) as r:
            sums = dict((name, h.lower()) for h, name in
                        re.findall(r'([0-9a-fA-F]{64})\s+\*?(\S+)', await r.text()))

        apk_name = next((n for n in assets if re.fullmatch(r'NowPlaying-.+\.apk', n)), None)
        if apk_name and apk_name in sums:
            apk_ver = apk_name[len('NowPlaying-'):-len('.apk')]
            if not self.apk or version_tuple(apk_ver) > version_tuple(self.apk[0]):
                path = await self.download(http, assets[apk_name], apk_name, sums[apk_name])
                if path:
                    self.apk = (apk_ver, path)
                    log.info('update: app for tablet %s downloaded', apk_ver)

        newer = version_tuple(tag) > version_tuple(self.version)
        if newer and getattr(sys, 'frozen', False) and 'NowPlayingServer.exe' in sums and not self.exe:
            path = await self.download(http, assets['NowPlayingServer.exe'], f'NowPlayingServer-{tag}.exe',
                                       sums['NowPlayingServer.exe'])
            if path:
                self.exe = path
                log.info('update: server %s downloaded', tag)
        self.cleanup()

    async def download(self, http, url, name, sha):
        path = os.path.join(self.dir, name)
        if os.path.exists(path) and self.sha256(path) == sha:
            return path
        part = path + '.part'
        h = hashlib.sha256()
        with open(part, 'wb') as f:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=900)) as r:
                if r.status != 200:
                    return None
                async for chunk in r.content.iter_chunked(256 * 1024):
                    f.write(chunk)
                    h.update(chunk)
        if h.hexdigest() != sha:
            log.warning('update: checksum mismatch for %s', name)
            os.remove(part)
            return None
        os.replace(part, path)
        return path

    @staticmethod
    def sha256(path):
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()

    def cleanup(self):
        """Оставляем только последний APK и ожидающий exe."""
        keep = {os.path.basename(p) for p in (self.exe, self.apk[1] if self.apk else None) if p}
        for name in os.listdir(self.dir):
            if name not in keep:
                try:
                    os.remove(os.path.join(self.dir, name))
                except OSError:
                    pass
