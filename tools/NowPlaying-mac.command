#!/bin/sh
# NowPlaying Server для macOS: двойной щелчок. При первом запуске ставит всё нужное в папку
# проекта (ничего не ставит в систему и не просит пароль), потом запускает сервер и открывает
# окно настроек в браузере. Остановить сервер — закрыть это окно Терминала.
#
# Что играет — из системной панели «Сейчас играет» (Яндекс Музыка, браузеры, Spotify,
# Apple Music, VLC — всё, что видно в Пункте управления), через pc/mac/ (mediaremote-adapter).
# Работает на Apple Silicon и Intel, macOS 10.15+.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/pc" || exit 1
# файлы из скачанного архива macOS помечает «из интернета» и не даёт загрузить помощник — снимаем пометку
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null
chmod +x mac/MediaRemoteAdapterTestClient 2>/dev/null

# Python 3.10+: свой в папке проекта, системный или с python.org
PY=""
for c in "$ROOT/python/bin/python3" python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "Скачиваю Python (около 25 МБ, один раз)…"
    case "$(uname -m)" in arm64) ARCH=aarch64 ;; *) ARCH=x86_64 ;; esac
    REL=20261026
    NAME="cpython-3.12.16+$REL-$ARCH-apple-darwin-install_only.tar.gz"
    URL="https://github.com/astral-sh/python-build-standalone/releases/download/$REL"
    TMP="$(mktemp -d)"
    curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors -o "$TMP/py.tgz" "$URL/$(printf %s "$NAME" | sed 's/+/%2B/')" \
        && WANT=$(curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors "$URL/SHA256SUMS" | awk -v n="$NAME" '$2 == n {print $1}') \
        && GOT=$(shasum -a 256 "$TMP/py.tgz" | cut -d' ' -f1) \
        && [ -n "$WANT" ] && [ "$WANT" = "$GOT" ] \
        && tar -xzf "$TMP/py.tgz" -C "$ROOT" \
        || { echo "Не получилось скачать Python. Поставьте его с https://www.python.org/downloads/macos/ и запустите снова."; rm -rf "$TMP"; read -r _; exit 1; }
    rm -rf "$TMP"
    PY="$ROOT/python/bin/python3"
fi

if [ ! -x .venv/bin/python ]; then
    echo "Первый запуск: ставлю нужное (пара минут)…"
    "$PY" -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt || { echo "Не получилось поставить"; read -r _; exit 1; }
fi
(sleep 4; open "http://127.0.0.1:47801/settings") &
echo "NowPlaying Server работает. Планшет найдёт этот компьютер сам. Закрой окно, чтобы остановить."
exec .venv/bin/python server.py
