#!/bin/sh
# NowPlaying Server для macOS: двойной щелчок — при первом запуске ставит всё нужное
# (нужен Python 3: https://www.python.org/downloads/macos/), потом запускает сервер
# и открывает окно настроек в браузере. Закрыть сервер — закрыть это окно Терминала.
# Что играет — берётся из Apple Music и Spotify. При первом запуске macOS спросит, можно ли
# «Терминалу» управлять Music/Spotify — нажми «Разрешить».
cd "$(dirname "$0")/../pc" || exit 1
PY=python3
command -v $PY >/dev/null 2>&1 || { echo "Нужен Python 3: https://www.python.org/downloads/macos/"; read -r _; exit 1; }
if [ ! -x .venv/bin/python ]; then
    echo "Первый запуск: ставлю нужное (пара минут)…"
    $PY -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt || { echo "Не получилось поставить"; read -r _; exit 1; }
fi
(sleep 4; open "http://127.0.0.1:47801/settings") &
echo "NowPlaying Server работает. Планшет найдёт этот компьютер сам. Закрой окно, чтобы остановить."
exec .venv/bin/python server.py
