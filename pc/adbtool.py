"""Установка приложения на планшет через adb (по USB или по Wi-Fi).

adb берётся из PATH, а если его нет — по кнопке скачиваются официальные Android
platform-tools с dl.google.com (около 7 МБ) в %LOCALAPPDATA%\\NowPlaying\\adb.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import zipfile

import aiohttp

NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0
TOOLS_URL = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'
PACKAGE = 'com.nowplaylab.nowplaying'
OLD_PACKAGE = 'com.tab3.nowplaying'   # имя приложения до 2.8 — после установки нового убираем


def _dir():
    local = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(local, 'NowPlaying', 'adb')


def adb_path():
    own = os.path.join(_dir(), 'adb.exe')
    if os.path.exists(own):
        return own
    return shutil.which('adb')


def run(*args, timeout=60):
    exe = adb_path()
    if not exe:
        raise FileNotFoundError('adb')
    r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
                       encoding='utf-8', errors='replace')
    return r.returncode, (r.stdout + r.stderr).strip()


async def download(http):
    """Скачать adb.exe и его две библиотеки из platform-tools."""
    async with http.get(TOOLS_URL, timeout=aiohttp.ClientTimeout(total=300)) as r:
        if r.status != 200:
            raise RuntimeError(f'Google ответил {r.status}')
        data = await r.read()
    os.makedirs(_dir(), exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in ('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll'):
            with z.open(f'platform-tools/{name}') as src, open(os.path.join(_dir(), name), 'wb') as dst:
                shutil.copyfileobj(src, dst)


def devices():
    """Подключённые устройства: [{'serial', 'state', 'model'}]."""
    code, out = run('devices', '-l', timeout=20)
    found = []
    for line in out.splitlines()[1:]:
        m = re.match(r'(\S+)\s+(device|unauthorized|offline)\b(.*)', line)
        if m:
            model = re.search(r'model:(\S+)', m.group(3))
            found.append({'serial': m.group(1), 'state': m.group(2),
                          'model': model.group(1).replace('_', ' ') if model else ''})
    return found


def connect(ip):
    """adb по Wi-Fi (на планшете должна быть включена отладка по сети, порт 5555)."""
    code, out = run('connect', f'{ip}:5555', timeout=15)
    return 'connected' in out


def install(serial, apk):
    code, out = run('-s', serial, 'install', '-r', apk, timeout=180)
    if code != 0 or 'Success' not in out:
        raise RuntimeError(out[-300:] or 'не удалось поставить')
    # до 2.8 приложение называлось иначе — Android считает его другим, убираем, чтобы не было двух
    run('-s', serial, 'uninstall', OLD_PACKAGE, timeout=60)
    run('-s', serial, 'shell', 'am', 'start', '-n', f'{PACKAGE}/.MainActivity', timeout=20)
    return out
