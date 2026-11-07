#!/bin/sh
# Сборка APK «Сейчас играет» без Gradle: aapt2 -> javac -> d8 -> zipalign -> apksigner.
# Инструменты Android ломаются на путях с кириллицей, поэтому сборка идёт во временной ASCII-папке.
set -eu
SDK="${ANDROID_SDK:-${ANDROID_HOME:-}}"
[ -n "$SDK" ] || { echo "Укажите путь к Android SDK: ANDROID_SDK=/c/android-sdk sh android/build.sh" >&2; exit 1; }
BT="$SDK/build-tools/34.0.0"
JAR="$SDK/platforms/android-17/android.jar"
# ресурсы и манифест связываем с android-21: там есть android:banner для Android TV
# (старые Android этот атрибут просто не замечают); код по-прежнему проверяется по API 17
# с 2.4 — android-34: атрибуты для новых Android (usesCleartextTraffic, exported)
RJAR="$SDK/platforms/android-34/android.jar"
[ -f "$RJAR" ] || RJAR="$SDK/platforms/android-21/android.jar"
[ -f "$RJAR" ] || RJAR="$JAR"
SRC="$(cd "$(dirname "$0")" && pwd)"
B="${BUILD_DIR:-${TMPDIR:-/tmp}/np-build}"
KS="${KEYSTORE:-$SDK/nowplaying.keystore}"

rm -rf "$B"; mkdir -p "$B/gen" "$B/classes" "$B/dex"
cp -r "$SRC/res" "$SRC/src" "$SRC/AndroidManifest.xml" "$B/"
if [ -d "$SRC/assets" ]; then cp -r "$SRC/assets" "$B/"; fi
cd "$B"

"$BT/aapt2.exe" compile --dir res -o res.zip
"$BT/aapt2.exe" link -I "$RJAR" --manifest AndroidManifest.xml --java gen \
    --min-sdk-version 16 --target-sdk-version 23 -o base.apk res.zip

javac -encoding UTF-8 -source 8 -target 8 -nowarn -Xlint:-options -bootclasspath "$JAR" -d classes \
    $(find src gen -name '*.java')
"$BT/d8.bat" --min-api 16 --release --lib "$JAR" --output dex $(find classes -name '*.class')

cp base.apk unsigned.apk
# assets добавляем сами: aapt2 на Windows пишет в APK пути с «\» (assets/fonts\x.ttf),
# и Android такие файлы не находит. jar пишет нормальные «/», 0 — без сжатия.
if [ -d assets ]; then jar uf0 unsigned.apk assets; fi
(cd dex && jar uf ../unsigned.apk classes.dex 2>/dev/null || python -c "import zipfile;z=zipfile.ZipFile('../unsigned.apk','a');z.write('classes.dex');z.close()")
"$BT/zipalign.exe" -f -p 4 unsigned.apk aligned.apk

if [ ! -f "$KS" ]; then
    keytool -genkeypair -keystore "$KS" -alias np -keyalg RSA -keysize 2048 -validity 36500 \
        -storepass nowplaying -keypass nowplaying -dname "CN=NowPlaying Tab3" >/dev/null
fi
"$BT/apksigner.bat" sign --ks "$KS" --ks-pass pass:nowplaying --key-pass pass:nowplaying \
    --v1-signing-enabled true --v2-signing-enabled true --min-sdk-version 16 --out NowPlaying.apk aligned.apk
"$BT/apksigner.bat" verify --min-sdk-version 16 NowPlaying.apk
cp NowPlaying.apk "$SRC/NowPlaying.apk"
ls -l "$SRC/NowPlaying.apk"
