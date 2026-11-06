#!/bin/sh
# Черновик релиза в очередь на ежедневный выпуск (.github/workflows/daily-release.yml публикует
# самый младший черновик новее latest раз в день).
#   sh tools/make-draft.sh <версия> <полный sha коммита> <файл с описанием.md> [версия APK]
# Перед этим: собрать exe (pc/build_exe.sh -> release/NowPlayingServer.exe) и APK (android/build.sh),
# запустить exe на тестовом порту и убедиться, что он стартует.
# Токен GitHub берётся из Git Credential Manager (тот же вход, что для git push).
set -eu
V=$1; SHA=$2; BODY=$3; AV=${4:-$1}   # AV — версия приложения внутри APK (если планшетная часть не менялась)
NP="$(cd "$(dirname "$0")/.." && pwd)"
D="${TMPDIR:-/tmp}/np-release-$V"
mkdir -p "$D"
cp "$NP/release/NowPlayingServer.exe" "$D/"
cp "$NP/android/NowPlaying.apk" "$D/NowPlaying-$AV.apk"   # имя ОБЯЗАНО совпадать с versionName в APK
cd "$D"
sha256sum NowPlayingServer.exe "NowPlaying-$AV.apk" > SHA256SUMS.txt
python -c "import json,sys;print(json.dumps({'tag_name':'v'+sys.argv[1],'target_commitish':sys.argv[2],'name':'NowPlaying '+sys.argv[1],'body':open(sys.argv[3],encoding='utf-8').read(),'draft':True}))" "$V" "$SHA" "$BODY" > req.json
T=$(printf 'protocol=https\nhost=github.com\n\n' | GCM_INTERACTIVE=never git credential fill 2>/dev/null | sed -n 's/^password=//p')
R=https://api.github.com/repos/nowplaylab/nowplaying-display
ID=$(curl -s -X POST -H "Authorization: Bearer $T" -H "Accept: application/vnd.github+json" $R/releases --data-binary @req.json | python -c "import json,sys;r=json.load(sys.stdin);print(r['id'])")
for f in NowPlayingServer.exe "NowPlaying-$AV.apk" SHA256SUMS.txt; do
  curl -s -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/octet-stream" --data-binary @"$f" \
    "https://uploads.github.com/repos/nowplaylab/nowplaying-display/releases/$ID/assets?name=$f" \
    | python -c "import json,sys;r=json.load(sys.stdin);print(r.get('name'),r.get('size'),r.get('state'))"
done
echo "draft $V id=$ID"
