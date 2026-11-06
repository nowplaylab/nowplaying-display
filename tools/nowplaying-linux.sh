#!/bin/sh
# NowPlaying Server для Linux: при первом запуске ставит всё нужное в pc/.venv, потом
# запускает сервер и открывает окно настроек в браузере. Запускать из сеанса рабочего стола
# (нужна D-Bus сессия: через неё плееры сообщают, что играет — MPRIS).
# Нужны python3 и python3-venv (Ubuntu/Debian: sudo apt install python3-venv),
# для громкости — pactl (есть в PulseAudio и PipeWire).
cd "$(dirname "$0")/../pc" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "Первый запуск: ставлю нужное…"
    python3 -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt || exit 1
fi
(sleep 4; xdg-open "http://127.0.0.1:47801/settings" >/dev/null 2>&1) &
echo "NowPlaying Server работает. Остановить — Ctrl+C."
exec .venv/bin/python server.py
