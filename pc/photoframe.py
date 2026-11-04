"""Фоторамка: фото из папки на ПК по очереди под часами на планшете.

Порядок случайный, без повторов, пока не пройдём всю папку (и подпапки).
Каждое фото поворачиваем по EXIF и ужимаем до 1280 px в JPEG — планшету легко.
Готовый файл лежит в %LOCALAPPDATA%\\NowPlaying\\frame (хранится только текущий).
"""
import asyncio
import hashlib
import logging
import os
import random
import subprocess
import sys
import time

from PIL import Image, ImageOps

log = logging.getLogger('nowplaying')

IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
MAX_FILES = 5000
RESCAN_EVERY = 5 * 60
NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


def pick_folder(start=''):
    """Окно Windows «Выбор папки». Возвращает путь или '' (отменили)."""
    start = start.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description = 'Папка с фото для фоторамки';"
        f"if ('{start}' -and (Test-Path '{start}')) {{ $d.SelectedPath = '{start}' }};"
        "if ($d.ShowDialog($f) -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; $d.SelectedPath }"
    )
    r = subprocess.run(['powershell', '-NoProfile', '-STA', '-Command', script], capture_output=True,
                       creationflags=NO_WINDOW, timeout=600)
    return r.stdout.decode('utf-8', 'replace').strip()


class PhotoFrame:
    def __init__(self, settings, cache_dir=None):
        self.settings = settings
        local = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        self.cache_dir = cache_dir or os.path.join(local, 'NowPlaying', 'frame')
        os.makedirs(self.cache_dir, exist_ok=True)
        self.current = None       # {'id', 'type', 'ext', 'path', 'source'}
        self.files = []
        self.queue = []
        self.scanned = (None, 0)  # (папка, когда)
        self.shown_at = 0
        self.wake = asyncio.Event()
        settings.listeners.append(lambda s: self._poke())

    def _poke(self):
        loop = getattr(self, 'loop', None)
        if loop:
            loop.call_soon_threadsafe(self.wake.set)

    def active(self):
        return self.settings['frame_on'] and bool(self.settings['frame_dir'])

    def info(self):
        c = self.current
        if not self.active() or not c:
            return None
        return {'id': c['id'], 'type': 'image', 'ext': 'jpg'}

    def status(self):
        if not self.settings['frame_dir']:
            return 'Папка не выбрана'
        if not os.path.isdir(self.settings['frame_dir']):
            return 'Папки больше нет — выбери другую'
        if self.scanned[0] != self.settings['frame_dir']:
            return 'Смотрю папку…'
        n = len(self.files)
        return 'В папке нет фото' if not n else f'Фото в папке: {n}'

    def scan(self, folder):
        found = []
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for name in filenames:
                if os.path.splitext(name)[1].lower() in IMAGE_EXT:
                    found.append(os.path.join(dirpath, name))
                    if len(found) >= MAX_FILES:
                        return found
        return found

    def prepare(self, path):
        st = os.stat(path)
        fid = hashlib.sha1(f'{path}|{st.st_size}|{st.st_mtime}'.encode()).hexdigest()[:12]
        out = os.path.join(self.cache_dir, f'{fid}.jpg')
        if not os.path.exists(out):
            img = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
            img.thumbnail((1280, 1280))
            img.save(out, 'JPEG', quality=86)
        for name in os.listdir(self.cache_dir):   # предыдущее фото больше не нужно
            if not name.startswith(fid):
                try:
                    os.remove(os.path.join(self.cache_dir, name))
                except OSError:
                    pass
        return {'id': fid, 'type': 'image', 'ext': 'jpg', 'path': out, 'source': os.path.basename(path)}

    async def next_photo(self):
        loop = asyncio.get_running_loop()
        for _ in range(5):   # битые файлы пропускаем
            if not self.queue:
                self.queue = self.files[:]
                random.shuffle(self.queue)
                if self.current and len(self.queue) > 1 and self.queue[-1].endswith(self.current['source']):
                    self.queue.insert(0, self.queue.pop())   # не повторять только что показанное
            if not self.queue:
                self.current = None
                return
            path = self.queue.pop()
            try:
                self.current = await loop.run_in_executor(None, self.prepare, path)
                self.shown_at = time.time()
                return
            except Exception as e:
                log.warning('frame: %s: %s', os.path.basename(path), e)

    async def watch(self):
        self.loop = asyncio.get_running_loop()
        while True:
            try:
                s = self.settings
                folder = s['frame_dir']
                if self.active() and os.path.isdir(folder):
                    if self.scanned[0] != folder or time.time() - self.scanned[1] > RESCAN_EVERY:
                        new = folder != self.scanned[0]
                        self.files = await self.loop.run_in_executor(None, self.scan, folder)
                        self.scanned = (folder, time.time())
                        if new:
                            self.queue, self.current = [], None
                    if self.current is None or time.time() - self.shown_at > s['frame_every'] * 60:
                        await self.next_photo()
                elif not self.active():
                    self.current = None
            except Exception:
                log.exception('frame')
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), 10)
            except asyncio.TimeoutError:
                pass
