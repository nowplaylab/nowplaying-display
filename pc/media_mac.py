"""macOS: что играет — из системной панели «Сейчас играет» (Пункт управления).

Туда попадает всё: Яндекс Музыка, браузеры, Spotify, Apple Music, VLC. С macOS 15.4 Apple
пускает к ней (фреймворк MediaRemote) только свои программы, поэтому ходим через проект
mediaremote-adapter (BSD-3, github.com/ungive/mediaremote-adapter): системный /usr/bin/perl
имеет право на MediaRemote и загружает маленький помощник, который печатает JSON.
Помощник лежит рядом: pc/mac/ (собрать: tools/build-mac-adapter.sh).

Если помощника нет или он перестал работать (новая macOS), берём Apple Music, iTunes и
Spotify напрямую через AppleScript.
"""
import asyncio
import base64
import json
import logging
import os
import tempfile
import time

import aiohttp

log = logging.getLogger('nowplaying')

HERE = os.path.dirname(os.path.abspath(__file__))
ADAPTER_DIR = os.path.join(HERE, 'mac')
SCRIPT = os.path.join(ADAPTER_DIR, 'mediaremote-adapter.pl')
FRAMEWORK = os.path.join(ADAPTER_DIR, 'MediaRemoteAdapter.framework')
TEST_CLIENT = os.path.join(ADAPTER_DIR, 'MediaRemoteAdapterTestClient')

SEP = '\x1f'

# ---------- запасной путь: AppleScript ----------

SPOTIFY = '''
if application "Spotify" is running then
  tell application "Spotify"
    if player state is not stopped then
      set t to current track
      return "Spotify" & (ASCII character 31) & (name of t) & (ASCII character 31) & (artist of t) & (ASCII character 31) & (album of t) & (ASCII character 31) & (player position as text) & (ASCII character 31) & ((duration of t) / 1000 as text) & (ASCII character 31) & (player state as text) & (ASCII character 31) & (artwork url of t)
    end if
  end tell
end if
return ""
'''


def player_script(app):
    """Apple Music (macOS 10.15+) и iTunes (до 10.15) понимают одни и те же команды."""
    return '''
if application "%s" is running then
  tell application "%s"
    if player state is not stopped then
      set t to current track
      return "%s" & (ASCII character 31) & (name of t) & (ASCII character 31) & (artist of t) & (ASCII character 31) & (album of t) & (ASCII character 31) & (player position as text) & (ASCII character 31) & (duration of t as text) & (ASCII character 31) & (player state as text) & (ASCII character 31) & ""
    end if
  end tell
end if
return ""
''' % (app, app, app)


MUSIC = player_script('Music')
ITUNES = player_script('iTunes')

MUSIC_ART = '''
tell application "%s"
  set d to raw data of artwork 1 of current track
end tell
set f to open for access POSIX file "%s" with write permission
set eof f to 0
write d to f
close access f
'''


async def run(args, timeout=3):
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return None
    if p.returncode != 0:
        log.debug('%s: %s', os.path.basename(args[0]), err.decode(errors='replace').strip()[:200])
        return None
    return out.decode('utf-8', errors='replace').strip()


async def osascript(script, timeout=3):
    return await run(['osascript', '-e', script], timeout)


def _num(s):
    try:
        return float(str(s).replace(',', '.'))     # в русской локали AppleScript пишет «12,5»
    except ValueError:
        return 0.0


# понятные имена плееров по bundle id — для журнала
APPS = {'ru.yandex.desktop.music': 'Яндекс Музыка', 'com.apple.Music': 'Apple Music', 'com.spotify.client': 'Spotify',
        'org.videolan.vlc': 'VLC', 'com.apple.Safari': 'Safari', 'com.google.Chrome': 'Chrome',
        'org.mozilla.firefox': 'Firefox', 'company.thebrowser.Browser': 'Arc',
        'ru.yandex.desktop.yandex-browser': 'Яндекс Браузер', 'com.apple.QuickTimePlayerX': 'QuickTime'}


class MacMedia:
    name = 'mac'

    def __init__(self):
        self.player = None      # bundle id (MediaRemote) или 'Spotify'/'Music'/'iTunes' (AppleScript)
        self.art_for = None
        self.error = ''
        self.art_path = os.path.join(tempfile.gettempdir(), 'nowplaying-music-art')
        self._found = None
        self._found_at = 0
        self._adapter = None    # None — ещё не проверяли; True/False — работает ли помощник
        self._art = None        # последняя обложка из MediaRemote: (ключ трека, байты)

    # ---------- MediaRemote ----------

    async def _adapter_ok(self):
        if self._adapter is None:
            if not (os.path.exists(SCRIPT) and os.path.isdir(FRAMEWORK)):
                self._adapter = False
                log.info('mac: mediaremote-adapter not found, using AppleScript (Music, Spotify)')
            else:
                args = ['/usr/bin/perl', SCRIPT, FRAMEWORK]
                if os.path.exists(TEST_CLIENT):
                    args.append(TEST_CLIENT)
                self._adapter = await run(args + ['test'], timeout=10) is not None
                log.info('mac: mediaremote-adapter %s', 'works' if self._adapter else 'failed, using AppleScript')
        return self._adapter

    @staticmethod
    def _key(bundle, title, artist):
        return 'mediaremote:' + '|'.join((bundle or '', title or '', artist or ''))

    async def _adapter_now(self):
        out = await run(['/usr/bin/perl', SCRIPT, FRAMEWORK, 'get', '--now'], timeout=4)
        if out is None:
            return None
        try:
            d = json.loads(out)
        except ValueError:
            return None
        if not d or not d.get('title'):
            return None
        bundle = d.get('bundleIdentifier') or ''
        key = self._key(bundle, d.get('title'), d.get('artist'))
        if d.get('artworkData'):
            try:
                self._art = (key, base64.b64decode(d['artworkData']))
            except ValueError:
                pass
        dur = _num(d.get('duration') or 0)
        pos = _num(d.get('elapsedTimeNow') if d.get('elapsedTimeNow') is not None else d.get('elapsedTime') or 0)
        if dur:
            pos = min(pos, dur)
        self.player = bundle
        return {'app': APPS.get(bundle, bundle.split('.')[-1] if bundle else 'mac'),
                'title': str(d['title']).strip(), 'artist': str(d.get('artist') or '').strip(),
                'album': str(d.get('album') or '').strip(), 'playing': bool(d.get('playing')),
                'pos_ms': pos * 1000, 'dur_ms': dur * 1000, 'art_url': key}

    # ---------- AppleScript ----------

    def _scripts(self):
        """Только установленные плееры: если в скрипте упомянуть приложение, которого нет,
        macOS при каждом опросе спрашивает «Где находится …?»."""
        if self._found is None or time.time() - self._found_at > 300:
            home = os.path.expanduser('~/Applications')

            def has(name, *dirs):
                return any(os.path.isdir(os.path.join(d, name + '.app')) for d in dirs)
            self._found = [sc for sc, ok in (
                (SPOTIFY, has('Spotify', '/Applications', home)),
                (MUSIC, has('Music', '/System/Applications', '/Applications')),
                (ITUNES, has('iTunes', '/Applications'))) if ok]
            self._found_at = time.time()
        return self._found

    async def _script_now(self):
        found = []
        for script in self._scripts():
            out = await osascript(script)
            if not out:
                continue
            p = out.split(SEP)
            if len(p) < 8 or not p[1].strip():
                continue
            found.append({'app': p[0], 'title': p[1].strip(), 'artist': p[2].strip(), 'album': p[3].strip(),
                          'pos_ms': _num(p[4]) * 1000, 'dur_ms': _num(p[5]) * 1000, 'playing': p[6].strip() == 'playing',
                          'art_url': p[7].strip() or ('artwork:' + p[0] if p[0] in ('Music', 'iTunes') else '')})
        if not found:
            return None
        found.sort(key=lambda f: (not f['playing'], f['app'] != self.player))
        self.player = found[0]['app']
        return found[0]

    # ---------- общее ----------

    async def now(self, http):
        v = await (self._adapter_now() if await self._adapter_ok() else self._script_now())
        if v is None:
            self.player = None
        return v

    async def art(self, http, url):
        if url.startswith('mediaremote:'):
            # обложка приходит от плеера не сразу — спросим ещё пару раз
            for _ in range(3):
                if self._art and self._art[0] == url:
                    return self._art[1]
                await asyncio.sleep(1)
                await self._adapter_now()
            return None
        if url.startswith('artwork:'):   # обложку Music/iTunes отдают только файлом
            if await osascript(MUSIC_ART % (url.split(':', 1)[1], self.art_path), timeout=5) is None:
                return None
            try:
                with open(self.art_path, 'rb') as f:
                    return f.read()
            except OSError:
                return None
        if url.startswith(('http://', 'https://')):
            try:
                async with http.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    return await r.read() if r.status == 200 else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None
        return None

    async def control(self, http, action):
        if not self.player:
            return False
        if self._adapter:
            cmd = {'toggle': '2', 'next': '4', 'prev': '5'}[action]
            return await run(['/usr/bin/perl', SCRIPT, FRAMEWORK, 'send', cmd], timeout=4) is not None
        cmd = {'toggle': 'playpause', 'next': 'next track', 'prev': 'previous track'}[action]
        return await osascript(f'tell application "{self.player}" to {cmd}') is not None

    async def seek(self, http, ms):
        if not self.player:
            return False
        if self._adapter:
            return await run(['/usr/bin/perl', SCRIPT, FRAMEWORK, 'seek', str(int(ms * 1000))], timeout=4) is not None
        return await osascript(f'tell application "{self.player}" to set player position to {ms / 1000:.1f}') is not None
