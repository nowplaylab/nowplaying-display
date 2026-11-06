#!/bin/sh
# Проверка сервера на Linux с подставным MPRIS-плеером (своя D-Bus сессия, порт 47811).
# Запуск: sh tools/linux-test/run_test.sh <папка с server.py и .venv>
set -eu
NP="${1:-$HOME/np}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export NP NP_PY="$NP/.venv/bin/python" NP_HERE="$HERE" NP_HOME="$(mktemp -d)"
export NP_HTTP_PORT=47811 NP_UDP_PORT=47812
dbus-run-session -- sh "$HERE/inner.sh"
