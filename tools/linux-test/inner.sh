#!/bin/sh
# Внутри своей D-Bus сессии (запускает run_test.sh): подставной плеер + сервер + проверки.
HOME="$NP_HOME" "$NP_PY" "$NP_HERE/fake_mpris.py" > /tmp/np-fake.log 2>&1 &
P1=$!
sleep 1.5
HOME="$NP_HOME" "$NP_PY" "$NP/server.py" > /tmp/np-server.log 2>&1 &
P2=$!
sleep 5
U=http://127.0.0.1:47811
now() { curl -s -m 3 $U/api/now; }
field() { now | "$NP_PY" -c "import json,sys;d=json.load(sys.stdin);print($1)"; }
echo '--- сейчас играет'; field "d['title'], '/', d['artist'], d['playing'], d['position_ms']//1000, '/', d['duration_ms']//1000, d['app']"
echo '--- обложка'; curl -s -o /dev/null -w '%{http_code} %{size_download} байт\n' "$U/art/$(field "d['track_id']").jpg"
echo '--- пауза'; curl -s -m 3 $U/api/control/toggle; echo; sleep 1.2; field "'играет:', d['playing']"
echo '--- следующий'; curl -s -m 3 $U/api/control/next; echo; sleep 1.2; field "d['title'], '/', d['artist'], d['duration_ms']//1000"
echo '--- перемотка на 60 с'; curl -s -m 3 $U/api/control/seek/60000; echo; sleep 1.2; field "'позиция', d['position_ms']//1000"
echo '--- журнал плеера'; cat /tmp/np-fake.log
echo '--- журнал сервера'; tail -6 /tmp/np-server.log
kill $P1 $P2 2>/dev/null
