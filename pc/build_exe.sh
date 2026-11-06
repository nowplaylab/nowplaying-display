#!/bin/sh
# Сборка NowPlayingServer.exe (Python встроен, окно консоли не открывается).
# Нужен venv с зависимостями: python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt pyinstaller
# PyInstaller плохо дружит с кириллицей в пути, поэтому сборка во временной ASCII-папке.
set -eu
SRC="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-$SRC/.venv/Scripts/python}"
B="${BUILD_DIR:-${TMPDIR:-/tmp}/np-pyi}"
rm -rf "$B"; mkdir -p "$B"
# все модули программы разом — по списку легко забыть новый (так было с vlc.py в 2.4)
cp "$SRC"/*.py "$SRC/icon.png" "$SRC/icon.ico" "$B/"
cp -r "$SRC/settings_ui" "$B/"
# секрет приложения Яндекса (pc/yandex_secret.py, не в git) кладём в сборку не открытым текстом
if [ -f "$B/yandex_secret.py" ]; then
    "$PY" - "$B/yandex_secret.py" <<'PYEOF'
import base64, re, sys
p = sys.argv[1]
s = re.search(r"SECRET\s*=\s*'([^']*)'", open(p, encoding='utf-8').read()).group(1)
if not s.startswith('x:'):
    x = bytes(c ^ ((0x5A + i * 29) & 0xFF) for i, c in enumerate(s.encode()))
    s = 'x:' + base64.b64encode(x).decode()
open(p, 'w', encoding='utf-8').write(f"SECRET = '{s}'\n")
PYEOF
else
    echo "ВНИМАНИЕ: нет pc/yandex_secret.py — вход в Яндекс (подсветка) в этой сборке работать не будет" >&2
fi
# приложение для планшета внутри программы — для установки через adb из окна настроек
cp "$SRC/../android/NowPlaying.apk" "$B/NowPlaying.apk"
cd "$B"
"$PY" -m PyInstaller --noconfirm --clean --onefile --windowed --name NowPlayingServer \
    --icon icon.ico --add-data "icon.png;." --add-data "settings_ui;settings_ui" --add-data "NowPlaying.apk;." \
    --collect-submodules winrt --collect-submodules pystray --collect-submodules pycaw --collect-submodules comtypes --collect-data tzdata --collect-data soundcard --collect-submodules icalendar --collect-data icalendar --collect-submodules recurring_ical_events --collect-submodules x_wr_timezone --collect-binaries imageio_ffmpeg nowplaying_app.py >pyinstaller.log 2>&1 \
    || { tail -20 pyinstaller.log; exit 1; }
mkdir -p "$SRC/../release"
cp dist/NowPlayingServer.exe "$SRC/../release/"
ls -l "$SRC/../release/NowPlayingServer.exe"
