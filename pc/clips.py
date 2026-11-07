"""Видео на фон экрана трека из Яндекс Музыки: видеошот или превью клипа.

Трек ищется в открытом поиске Яндекс Музыки (без входа в аккаунт). Берём видеошот
трека (backgroundVideoUri, короткая нарезка из клипа), а если его нет, но есть клип, то
10-секундное превью клипа (тоже нарезка; весь клип без аккаунта не отдают). Видео
перекодируется под Android 4.2: без чёрных полос, кадр 16:9 по центру, размытие,
без звука, H.264 baseline, 854×480. Если в сети есть телевизор, рядом делается вторая версия
для него: в исходном качестве до 1080p и без размытия ({id}-hd.mp4). Готовые файлы лежат в %LOCALAPPDATA%\\NowPlaying\\clips:
не больше 20, а видео, которое 12 часов не показывалось, удаляется.
"""
import asyncio
import logging
import os
import re
import subprocess
import time

import aiohttp

import yandex
from background import NO_WINDOW, ffmpeg_exe

log = logging.getLogger('nowplaying')

KEEP = 20                  # не больше стольких видео
MAX_AGE = 12 * 3600        # видео, которое столько не показывалось, удаляется


class Clips:
    def __init__(self, cache_dir=None):
        local = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        self.cache_dir = cache_dir or os.path.join(local, 'NowPlaying', 'clips')
        self.enabled = True
        self.ready = {}     # track_id -> путь к готовому mp4
        self.ready_hd = {}  # track_id -> путь к версии для телевизора
        self.hd_tried = set()           # для этих треков версию для ТВ уже делали (или не вышло)
        self.want_hd = lambda: False    # есть ли в сети телевизор — подставляет server.py
        # у этих треков видео не нашлось: track_id -> (длительность, когда искали).
        # При смене трека Windows иногда отдаёт длительность прошлой песни, поэтому
        # с другой длительностью ищем снова, а отказ забываем через час
        self.missing = {}
        self.jobs = {}
        self.cleaned_at = 0
        os.makedirs(self.cache_dir, exist_ok=True)
        # лёгкие версии из прошлых сборок (512×288, сильное размытие) — пересоздаём
        marker = os.path.join(self.cache_dir, 'format-2')
        if not os.path.exists(marker):
            for name in os.listdir(self.cache_dir):
                if re.fullmatch(r'[0-9a-f]{12}\.mp4', name):
                    try:
                        os.remove(os.path.join(self.cache_dir, name))
                    except OSError:
                        pass
            open(marker, 'w').close()
        for name in os.listdir(self.cache_dir):
            if re.fullmatch(r'[0-9a-f]{12}\.mp4', name):
                self.ready[name[:12]] = os.path.join(self.cache_dir, name)
            elif re.fullmatch(r'[0-9a-f]{12}-hd\.mp4', name):
                self.ready_hd[name[:12]] = os.path.join(self.cache_dir, name)
            elif not name.startswith('format-'):  # недокачанное с прошлого запуска
                try:
                    os.remove(os.path.join(self.cache_dir, name))
                except OSError:
                    pass

    def info(self, tid):
        return tid if self.enabled and tid in self.ready else None

    def info_tv(self, tid):
        """Для телевизора: (id, есть ли версия в полном качестве). Пока она готовится,
        видео не отдаём совсем, чтобы на экране не сменялось плохое на хорошее."""
        if not self.enabled or not tid:
            return None, False
        if tid in self.ready_hd:
            return tid, True
        if tid in self.ready and (tid in self.hd_tried or tid not in self.jobs):
            return tid, False
        return None, False

    def touch(self, tid):
        """Видео показали — продлеваем ему жизнь (время изменения файла = последний показ)."""
        for d in (self.ready, self.ready_hd):
            try:
                os.utime(d[tid])
            except (KeyError, OSError):
                pass

    def request(self, http, tid, title, artist, dur_ms):
        """Запустить поиск видео для трека (один раз на трек)."""
        if time.time() - self.cleaned_at > 3600:   # заодно раз в час убираем старое
            self.cleanup()
        if not self.enabled or not tid or not title or tid in self.jobs:
            return
        need_sd = tid not in self.ready
        need_hd = tid not in self.ready_hd and tid not in self.hd_tried and self.want_hd()
        if not need_sd and not need_hd:
            return
        miss = self.missing.get(tid)
        if miss and miss[0] == dur_ms and time.time() - miss[1] < 3600:
            return
        self.jobs[tid] = asyncio.create_task(self._job(http, tid, title, artist, dur_ms, need_sd, need_hd))

    async def _job(self, http, tid, title, artist, dur_ms, need_sd=True, need_hd=False):
        try:
            kind, urls = await self.find(http, title, artist, dur_ms)
            got = False
            loop = asyncio.get_running_loop()
            skip = kind == 'clip preview'
            for url in urls:
                src = await self.download(http, tid, url)
                if not src:
                    continue
                try:
                    if need_sd:   # сначала лёгкая версия: планшет покажет её сразу
                        path = await loop.run_in_executor(None, self.prepare, src, tid, skip)
                        if not path:
                            continue
                        self.ready[tid] = path
                    got = True
                    if need_hd:
                        self.hd_tried.add(tid)
                        path = await loop.run_in_executor(None, self.prepare, src, tid, skip, True)
                        if path:
                            self.ready_hd[tid] = path
                finally:
                    try:
                        os.remove(src)
                    except OSError:
                        pass
                break
            if got:
                self.cleanup()
                log.info('clip for %s — %s: ready (%s%s)', artist, title, kind, ', tv' if tid in self.ready_hd else '')
            else:
                log.info('clip for %s — %s: none', artist, title)
                self.missing[tid] = (dur_ms, time.time())
        except Exception as e:
            log.warning('clip: %s', e)  # сеть: в следующий раз попробуем снова
        finally:
            self.jobs.pop(tid, None)

    async def find(self, http, title, artist, dur_ms):
        """(вид, [ссылки на видео по порядку предпочтения]) или (None, [])."""
        matches = await yandex.find_tracks(http, title, artist, dur_ms)
        for t in matches:
            if t.get('backgroundVideoUri'):
                return 'shot', [t['backgroundVideoUri']]
        # видеошота нет — ищем клип (clipIds есть только в полной карточке трека)
        for t in matches[:3]:
            full = await yandex.api(http, f'tracks/{t["id"]}')
            ids = (full[0] if full else {}).get('clipIds') or []
            if not ids:
                continue
            clip = await yandex.api(http, 'clips', {'clipIds': ','.join(str(i) for i in ids[:3])})
            for c in clip or []:
                prev = c.get('previewUrl') or ''
                if prev:
                    longer = re.sub(r'-\d+s-(\d+)\.mp4$', r'-10s-\1.mp4', prev)
                    return 'clip preview', [longer, prev] if longer != prev else [prev]
        return None, []

    async def download(self, http, tid, url):
        """Скачать исходное видео: путь к файлу или None."""
        src = os.path.join(self.cache_dir, f'{tid}.src')
        ok = False
        try:
            with open(src, 'wb') as f:
                async with http.get(url, timeout=aiohttp.ClientTimeout(total=180)) as r:
                    if r.status != 200:
                        return None   # например, 10-секундного превью нет — возьмём следующее
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        f.write(chunk)
            ok = True
            return src
        finally:
            if not ok:
                try:
                    os.remove(src)
                except OSError:
                    pass

    @staticmethod
    def analyze(src):
        """(рамка без чёрных полос или None, время первой смены сцены после 0.5 с или None)."""
        r = subprocess.run([ffmpeg_exe(), '-hide_banner', '-i', src, '-t', '12', '-an', '-vf',
                            "cropdetect=24:16:1,select='gt(scene,0.3)',showinfo", '-f', 'null', '-'],
                           capture_output=True, text=True, creationflags=NO_WINDOW, timeout=120)
        crops = re.findall(r'crop=(\d+):(\d+):(\d+):(\d+)', r.stderr)
        size = re.search(r'Video: .*?, (\d{2,5})x(\d{2,5})', r.stderr)
        crop = None
        if crops and size:
            w, h = int(size.group(1)), int(size.group(2))
            # самая частая рамка: тёмные сцены дают случайные узкие рамки, полосы — постоянные
            best = max(set(crops), key=crops.count)
            cw, ch = int(best[0]), int(best[1])
            if (cw, ch) != (w, h) and cw >= w * 0.5 and ch >= h * 0.5:
                crop = ':'.join(best)
        cuts = [float(t) for t in re.findall(r'pts_time:([\d.]+)', r.stderr)]
        cut = next((t for t in cuts if 0.5 <= t <= 2.5), None)
        return crop, cut

    def prepare(self, src, tid, skip_intro=False, hd=False):
        name = f'{tid}-hd' if hd else tid
        out = os.path.join(self.cache_dir, f'{name}.mp4')
        tmp = os.path.join(self.cache_dir, f'{name}.part.mp4')
        # у старых клипов чёрные полосы; превью клипа начинается с первой секунды клипа,
        # где часто заставка лейбла — пропускаем её до первой смены сцены
        crop, cut = self.analyze(src)
        start = ['-ss', f'{cut:.2f}'] if skip_intro and cut else []
        crop = f'crop={crop},' if crop else ''
        if hd:
            # телевизор: кадр 16:9 по центру в исходном качестве (не больше 1080p), без размытия
            vf = (crop + "crop='min(iw,ih*16/9)':'min(ih,iw*9/16)',"
                  "scale='2*trunc(min(1920,iw)/2)':-2,setsar=1,fps=30,format=yuv420p")
            codec = ['-profile:v', 'high', '-level', '4.1', '-preset', 'veryfast', '-crf', '19']
        else:
            # фон: кадр 16:9 по центру, маленький и размытый — планшет растянет на весь экран
            # 854×480 и лёгкое размытие: на 512×288 с сильным размытием тёмные клипы под
            # затемнением превращались в почти чёрный фон (2.10)
            vf = (crop + 'scale=854:480:force_original_aspect_ratio=increase,crop=854:480,'
                  'gblur=sigma=0.8,setsar=1,fps=30,format=yuv420p')
            codec = ['-profile:v', 'baseline', '-level', '3.1', '-preset', 'veryfast', '-crf', '23']
        cmd = [ffmpeg_exe(), '-y', '-v', 'error', *start, '-i', src, '-t', '30', '-an', '-vf', vf,
               '-c:v', 'libx264', *codec, '-movflags', '+faststart', tmp]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW, timeout=300)
        if r.returncode != 0 or not os.path.exists(tmp):
            log.warning('clip: ffmpeg failed: %s', r.stderr[-400:])
            return None
        os.replace(tmp, out)
        return out

    def cleanup(self):
        self.cleaned_at = time.time()
        by_age = sorted(self.ready, key=self._mtime)
        extra = max(0, len(by_age) - KEEP)
        for i, t in enumerate(by_age):
            if i < extra or time.time() - self._mtime(t) > MAX_AGE:
                for d in (self.ready, self.ready_hd):
                    try:
                        os.remove(d.pop(t))
                    except (KeyError, OSError):
                        pass
                self.hd_tried.discard(t)
        for t in [t for t in self.ready_hd if t not in self.ready]:   # версия для ТВ без основной
            try:
                os.remove(self.ready_hd.pop(t))
            except OSError:
                pass

    def _mtime(self, tid):
        try:
            return os.path.getmtime(self.ready[tid])
        except OSError:
            return 0
