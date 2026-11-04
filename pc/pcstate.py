"""Состояние ПК для виджета «Компьютер»: загрузка процессора, память, температура видеокарты NVIDIA.

Замеряем только пока виджет стоит на экране часов: процессор и память раз в 3 секунды,
видеокарту раз в 9 секунд через nvidia-smi (если её нет — вместо неё время работы ПК).
"""
import asyncio
import logging
import shutil
import subprocess
import sys
import time

log = logging.getLogger('nowplaying')

EVERY = 3
GPU_EVERY = 3   # в циклах: 3 × 3 с


def read_gpu_temp():
    exe = shutil.which('nvidia-smi')
    if not exe:
        return None
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    try:
        out = subprocess.run([exe, '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=5, creationflags=flags).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def uptime_text(seconds):
    h = int(seconds // 3600)
    if h < 1:
        return f'{max(1, int(seconds // 60))} мин'
    if h < 48:
        return f'{h} ч'
    return f'{h // 24} дн'


class PcState:
    def __init__(self, settings):
        self.settings = settings
        self.cpu = self.mem = self.gpu = None
        self.no_gpu = False   # nvidia-smi не нашёлся или не отвечает — больше не дёргаем

    def wanted(self):
        return self.settings.anywhere(lambda v: 'pc' in v['widget_slots'])

    def info(self):
        if not self.wanted() or self.cpu is None:
            return None
        import psutil
        return {'cpu': self.cpu, 'mem': self.mem, 'gpu': self.gpu,
                'uptime': uptime_text(time.time() - psutil.boot_time())}

    async def watch(self):
        import psutil
        loop = asyncio.get_running_loop()
        n = 0
        while True:
            if self.wanted():
                try:
                    self.cpu = round(psutil.cpu_percent(None))
                    self.mem = round(psutil.virtual_memory().percent)
                    if not self.no_gpu and n % GPU_EVERY == 0:
                        self.gpu = await loop.run_in_executor(None, read_gpu_temp)
                        if self.gpu is None:
                            self.no_gpu = True
                    n += 1
                except Exception as e:
                    log.warning('pc state: %s', e)
            else:
                self.cpu = None
                n = 0
            await asyncio.sleep(EVERY)
