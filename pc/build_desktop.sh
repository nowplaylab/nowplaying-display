#!/bin/sh
# Сборка NowPlaying Server для macOS (.app в .dmg) и Linux (.AppImage).
# Запускается на той системе, под которую собираем (на GitHub — .github/workflows/desktop-builds.yml).
# Нужен Python 3.10+ с зависимостями: python3 -m pip install -r pc/requirements.txt pyinstaller
#   sh pc/build_desktop.sh <имя файла-результата без расширения>
# Результат — в release/: NowPlayingServer-mac-*.dmg или NowPlayingServer-linux-*.AppImage.
#
# Для Mac: macOS сама рисует иконку и окно .dmg. Иконка — design/macos/ (icon-1024.png, menubar*.png,
# если есть; иначе — запасная из design/icon_v2/icon_src_512.png). Фон окна .dmg — design/dmg/background.png
# (660×400) и, по желанию, background@2x.png (1320×800).
set -eu
OUT_NAME=$1
SRC="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SRC/.." && pwd)"
PY="${PYTHON:-python3}"
B="${BUILD_DIR:-${TMPDIR:-/tmp}/np-desktop}"
VERSION=$(sed -n "s/^VERSION = '\([^']*\)'.*/\1/p" "$SRC/server.py")
rm -rf "$B"; mkdir -p "$B" "$ROOT/release"
cp "$SRC"/*.py "$SRC/icon.png" "$B/"
cp -r "$SRC/settings_ui" "$B/"
if [ -f "$B/yandex_secret.py" ]; then
    "$PY" "$ROOT/tools/hide_secret.py" "$B/yandex_secret.py"
else
    echo "ВНИМАНИЕ: нет pc/yandex_secret.py — вход в Яндекс (подсветка) в этой сборке работать не будет" >&2
fi
ICON_SRC="$ROOT/design/macos/icon-1024.png"
[ -f "$ICON_SRC" ] || ICON_SRC="$ROOT/design/icon_v2/icon_src_512.png"
cd "$B"

COMMON="--noconfirm --clean --windowed --name NowPlaying --add-data icon.png:. --add-data settings_ui:settings_ui \
  --collect-submodules pystray --collect-data tzdata --collect-data soundcard --collect-submodules icalendar \
  --collect-data icalendar --collect-submodules recurring_ical_events --collect-submodules x_wr_timezone \
  --collect-binaries imageio_ffmpeg"

case "$(uname -s)" in
Darwin)
    # иконка .icns из PNG (sips + iconutil есть в любой macOS)
    mkdir -p icon.iconset
    for s in 16 32 128 256 512; do
        sips -z $s $s "$ICON_SRC" --out "icon.iconset/icon_${s}x${s}.png" >/dev/null
        sips -z $((s * 2)) $((s * 2)) "$ICON_SRC" --out "icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns icon.iconset -o icon.icns
    EXTRA=""
    for f in menubar-glyph@2x.png menubar@2x.png; do
        [ -f "$ROOT/design/macos/$f" ] && cp "$ROOT/design/macos/$f" . && EXTRA="$EXTRA --add-data $f:."
    done
    # pc/mac — помощник для чтения панели «Сейчас играет» (mediaremote-adapter)
    "$PY" -m PyInstaller $COMMON $EXTRA --icon icon.icns --osx-bundle-identifier com.nowplaylab.server \
        --add-data "$SRC/mac:mac" --hidden-import PyObjCTools.AppHelper nowplaying_desktop.py >pyinstaller.log 2>&1 \
        || { tail -30 pyinstaller.log; exit 1; }
    APP=dist/NowPlaying.app
    PL="$APP/Contents/Info.plist"
    # без значка в Dock (только в строке меню), понятное имя и версия
    /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PL"
    /usr/libexec/PlistBuddy -c "Set :CFBundleName NowPlaying" "$PL"
    /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string NowPlaying" "$PL" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PL"
    /usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string MIT, nowplaylab" "$PL" 2>/dev/null || true
    # иконка в новом формате macOS 26+ (Icon Composer), если есть: Assets.car + имя в Info.plist
    if [ -d "$ROOT/design/macos/NowPlaying.icon" ] && command -v xcrun >/dev/null && xcrun --find actool >/dev/null 2>&1; then
        mkdir -p actool-out
        if xcrun actool "$ROOT/design/macos/NowPlaying.icon" --compile actool-out --app-icon NowPlaying \
            --include-all-app-icons --output-format human-readable-text --notices --warnings --errors \
            --enable-on-demand-resources NO --development-region en --target-device mac \
            --platform macosx --minimum-deployment-target 11.0 --output-partial-info-plist actool.plist >actool.log 2>&1 \
            && [ -f actool-out/Assets.car ]; then
            cp actool-out/Assets.car "$APP/Contents/Resources/"
            /usr/libexec/PlistBuddy -c "Add :CFBundleIconName string NowPlaying" "$PL"
        else
            echo "actool не собрал NowPlaying.icon — остаётся обычная .icns" >&2; head -25 actool.log >&2 || true
        fi
    fi
    codesign --force --deep --sign - "$APP"     # подпись «для себя»: без неё Apple Silicon не запустит
    # .dmg: программа слева, ярлык «Программы» справа, свой фон, если нарисован
    BG=""
    if [ -f "$ROOT/design/dmg/background@2x.png" ] && [ -f "$ROOT/design/dmg/background.png" ]; then
        tiffutil -cathidpicheck "$ROOT/design/dmg/background.png" "$ROOT/design/dmg/background@2x.png" -out bg.tiff >/dev/null
        BG="--background bg.tiff"
    elif [ -f "$ROOT/design/dmg/background.png" ]; then
        BG="--background $ROOT/design/dmg/background.png"
    fi
    rm -f "$ROOT/release/$OUT_NAME.dmg"
    mkdir dmg-src && cp -R "$APP" dmg-src/
    # высота окна 432 = фон 400 + заголовок окна Finder (32 pt), иначе низ фона обрезается
    # create-dmg расставляет иконки через Finder; на сервере GitHub без экрана это иногда падает — тогда простой .dmg
    create-dmg --volname "NowPlaying" --volicon icon.icns $BG --window-size 660 432 --icon-size 96 --text-size 12 \
        --icon NowPlaying.app 120 285 --app-drop-link 120 84 --hide-extension NowPlaying.app \
        --no-internet-enable "$ROOT/release/$OUT_NAME.dmg" dmg-src \
        || { echo "create-dmg не справился, делаю простой .dmg" >&2
             rm -f "$ROOT/release/$OUT_NAME.dmg"; ln -s /Applications dmg-src/Applications
             hdiutil create -volname NowPlaying -srcfolder dmg-src -ov -format UDZO "$ROOT/release/$OUT_NAME.dmg"; }
    ls -l "$ROOT/release/$OUT_NAME.dmg"
    ;;
Linux)
    "$PY" -m PyInstaller $COMMON --collect-submodules dbus_next --hidden-import pystray._xorg \
        nowplaying_desktop.py >pyinstaller.log 2>&1 || { tail -30 pyinstaller.log; exit 1; }
    AD=NowPlaying.AppDir
    mkdir -p "$AD/usr/bin"
    cp -R dist/NowPlaying/. "$AD/usr/bin/"
    "$PY" -c "from PIL import Image; Image.open('$ICON_SRC').convert('RGBA').resize((256, 256)).save('$AD/nowplaying-server.png')"
    cp "$AD/nowplaying-server.png" "$AD/.DirIcon"
    cat > "$AD/nowplaying-server.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NowPlaying Server
Comment=Now playing screen for old Android tablets and TVs
Exec=NowPlaying
Icon=nowplaying-server
Categories=AudioVideo;Audio;
Terminal=false
X-AppImage-Version=$VERSION
EOF
    cat > "$AD/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/NowPlaying" "$@"
EOF
    chmod +x "$AD/AppRun"
    TOOL="${APPIMAGETOOL:-appimagetool}"
    rm -f "$ROOT/release/$OUT_NAME.AppImage"
    ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$AD" "$ROOT/release/$OUT_NAME.AppImage"
    ls -l "$ROOT/release/$OUT_NAME.AppImage"
    ;;
*)
    echo "Этот скрипт — для macOS и Linux. exe для Windows собирает pc/build_exe.sh" >&2; exit 1 ;;
esac
