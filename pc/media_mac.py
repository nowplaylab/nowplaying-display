"""macOS: что играет в Apple Music (или iTunes на macOS до 10.15) и Spotify — через AppleScript (osascript).

Общей «панели Сейчас играет», доступной программам, у macOS нет (MediaRemote закрыт),
поэтому спрашиваем плееры напрямую. Скрипт проверяет «is running» и не запускает плеер сам.
При первом запросе macOS спросит, можно ли программе управлять Music/Spotify — надо разрешить.
"""
import asyncio
import logging
import os
import tempfile
import time

import aiohttp

log = logging.getLogger('nowplaying')

SEP = '\x1f'

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


async def osascript(script, timeout=3):
    p = await asyncio.create_subprocess_exec('osascript', '-e', script, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return None
    if p.returncode != 0:
        log.debug('osascript: %s', err.decode(errors='replace').strip())
        return None
    return out.decode('utf-8', errors='replace').strip()


def _num(s):
    try:
        return float(s.replace(',', '.'))     # в русской локали AppleScript пишет «12,5»
    except ValueError:
        return 0.0


class MacMedia:
    name = 'mac'

    def __init__(self):
        self.player = None     # 'Spotify' или 'Music'
        self.art_for = None
        self.error = ''
        self.art_path = os.path.join(tempfile.gettempdir(), 'nowplaying-music-art')
        self._found = None
        self._found_at = 0

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

    async def now(self, http):
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
            self.player = None
            return None
        found.sort(key=lambda f: (not f['playing'], f['app'] != self.player))
        self.player = found[0]['app']
        return found[0]

    async def art(self, http, url):
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
        cmd = {'toggle': 'playpause', 'next': 'next track', 'prev': 'previous track'}[action]
        return await osascript(f'tell application "{self.player}" to {cmd}') is not None

    async def seek(self, http, ms):
        if not self.player:
            return False
        return await osascript(f'tell application "{self.player}" to set player position to {ms / 1000:.1f}') is not None
