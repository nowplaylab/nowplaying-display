#!/bin/sh
# Собрать pc/mac/ — помощник для чтения системной панели «Сейчас играет» на macOS
# (mediaremote-adapter, BSD-3, github.com/ungive/mediaremote-adapter). Запускать на маке
# с Command Line Tools (xcode-select --install) и cmake (pip install cmake тоже подходит).
# Собирается сразу для Apple Silicon и Intel. Фреймворк кладём «плоским» (без Versions/ и
# символьных ссылок): скрипту нужен только сам файл MediaRemoteAdapter внутри .framework.
set -eu
TAG="${1:-v0.7.7}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
git clone -q --depth 1 --branch "$TAG" https://github.com/ungive/mediaremote-adapter.git "$TMP/mra"
cmake -S "$TMP/mra" -B "$TMP/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES="arm64;x86_64" >/dev/null
cmake --build "$TMP/build" --config Release >/dev/null
OUT="$ROOT/pc/mac"
rm -rf "$OUT" && mkdir -p "$OUT/MediaRemoteAdapter.framework/Resources"
cp "$TMP/build/MediaRemoteAdapter.framework/Versions/A/MediaRemoteAdapter" "$OUT/MediaRemoteAdapter.framework/"
cp "$TMP/build/MediaRemoteAdapter.framework/Versions/A/Resources/Info.plist" "$OUT/MediaRemoteAdapter.framework/Resources/"
cp "$TMP/build/MediaRemoteAdapterTestClient" "$TMP/mra/bin/mediaremote-adapter.pl" "$OUT/"
cp "$TMP/mra/LICENSE" "$OUT/LICENSE-mediaremote-adapter"
rm -rf "$TMP"
/usr/bin/perl "$OUT/mediaremote-adapter.pl" "$OUT/MediaRemoteAdapter.framework" "$OUT/MediaRemoteAdapterTestClient" test \
    && echo "готово: $OUT ($TAG), проверка прошла"
