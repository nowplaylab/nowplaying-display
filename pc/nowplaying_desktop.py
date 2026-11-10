"""NowPlaying Server для macOS и Linux: сервер в фоне + значок в строке меню (Mac) или в трее (Linux).

Точка входа собранных программ (.app в .dmg и .AppImage, см. pc/build_desktop.sh). Из исходников
по-прежнему можно запускать server.py (tools/NowPlaying-mac.command, tools/nowplaying-linux.sh).

При первом запуске собранной программы: добавляет себя в автозапуск при входе в систему
(macOS — ~/Library/LaunchAgents, Linux — ~/.config/autostart) и открывает окно настроек.
Если значок показать негде (например, GNOME без расширения для трея), сервер всё равно работает,
а повторный запуск программы просто открывает окно настроек.
"""
import asyncio
import logging
import os
import plistlib
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from aiohttp import web

import server

APP_NAME = 'NowPlaying Server'
DATA_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'NowPlaying')
FIRST_RUN_FLAG = os.path.join(DATA_DIR, 'first-run-done')
IS_MAC = sys.platform == 'darwin'
FROZEN = getattr(sys, 'frozen', False)
log = logging.getLogger('nowplaying')
_crash_file = None   # держим открытым для faulthandler


def resource(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def settings_url():
    return f'http://127.0.0.1:{server.HTTP_PORT}/settings'


def open_settings():
    webbrowser.open(settings_url())


def already_running():
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{server.HTTP_PORT}/health', timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def open_path(path):
    subprocess.Popen(['open' if IS_MAC else 'xdg-open', path], close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.168.0.1', 9))  # пакет не отправляется, нужен только выбор интерфейса
            return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'


def now_playing_text():
    st = server.state
    if not st.get('track_id'):
        return 'Ничего не играет'
    text = f"{st['artist']} — {st['title']}" if st.get('artist') else st['title']
    return ('▶ ' if st.get('playing') else '❚❚ ') + text[:90]


# ---------- автозапуск при входе ----------

LAUNCH_AGENT = os.path.expanduser('~/Library/LaunchAgents/com.nowplaylab.server.plist')
AUTOSTART_DESKTOP = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'),
                                 'autostart', 'nowplaying-server.desktop')


def launch_target():
    """Что запускать при входе: сам .app на маке, файл .AppImage на Linux. None — из исходников."""
    if not FROZEN:
        return None
    if IS_MAC:
        exe = os.path.realpath(sys.executable)          # …/NowPlaying.app/Contents/MacOS/NowPlaying
        app = exe.split('/Contents/MacOS/')[0]
        return app if app.endswith('.app') else exe
    return os.environ.get('APPIMAGE') or sys.executable


def autostart_enabled():
    return os.path.exists(LAUNCH_AGENT if IS_MAC else AUTOSTART_DESKTOP)


def set_autostart(on):
    path = LAUNCH_AGENT if IS_MAC else AUTOSTART_DESKTOP
    try:
        if not on:
            if os.path.exists(path):
                os.remove(path)
            return
        target = launch_target()
        if not target:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if IS_MAC:
            args = ['/usr/bin/open', '-g', '-a', target] if target.endswith('.app') else [target]
            with open(path, 'wb') as f:
                plistlib.dump({'Label': 'com.nowplaylab.server', 'ProgramArguments': args,
                               'RunAtLoad': True}, f)
        else:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('[Desktop Entry]\nType=Application\nName=NowPlaying Server\n'
                        f'Exec={shlex.quote(target)}\nIcon=nowplaying-server\n'
                        'X-GNOME-Autostart-enabled=true\nTerminal=false\n')
    except OSError as e:
        log.warning('autostart: %s', e)


# ---------- сервер в отдельном потоке ----------

class ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name='np-server')
        self.loop = None
        self.error = None
        self.started = threading.Event()

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        runner = web.AppRunner(server.create_app(), access_log=None)
        try:
            self.loop.run_until_complete(runner.setup())
            self.loop.run_until_complete(web.TCPSite(runner, '0.0.0.0', server.HTTP_PORT).start())
        except Exception as e:
            self.error = e
            self.started.set()
            return
        self.started.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.run_until_complete(runner.cleanup())

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


def tray_image():
    from PIL import Image
    if IS_MAC and os.path.exists(resource('menubar@2x.png')):
        return Image.open(resource('menubar@2x.png'))
    return Image.open(resource('icon.png'))


def run_tray(srv, log_path, first_run):
    """Значок с меню. Возвращает False, если показать его негде."""
    try:
        import pystray
    except Exception as e:
        log.warning('tray unavailable: %s', e)
        return False

    def quit_app(icon, item):
        srv.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: now_playing_text(), None, enabled=False),
        pystray.MenuItem(lambda item: f'Адрес для планшета: {local_ip()}:{server.HTTP_PORT}', None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Настройки…', lambda icon, item: open_settings(), default=True),
        pystray.MenuItem('Запускать при входе в систему', lambda icon, item: set_autostart(not autostart_enabled()),
                         checked=lambda item: autostart_enabled(), visible=FROZEN),
        pystray.MenuItem('Открыть журнал', lambda icon, item: log_path and open_path(log_path)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f'Версия {server.VERSION}', None, enabled=False),
        pystray.MenuItem('Выход', quit_app),
    )
    try:
        icon = pystray.Icon('NowPlaying', tray_image(), APP_NAME, menu)
        if IS_MAC:
            _mark_template(icon)
    except Exception as e:
        log.warning('tray unavailable: %s', e)
        return False

    # macOS разрешает трогать строку меню только из главного потока (иначе программа падает
    # без следа в журнале), а setup у pystray работает в отдельном — передаём действия в главный
    if IS_MAC:
        from PyObjCTools import AppHelper
        on_main = AppHelper.callAfter
    else:
        def on_main(fn, *args):
            fn(*args)

    def show(icon):
        icon.visible = True
        log.info('tray icon shown')

    def refresher(icon):
        on_main(show, icon)
        while srv.is_alive():
            time.sleep(3)
            try:
                on_main(icon.update_menu)
            except Exception:
                log.exception('tray refresh')
        on_main(icon.stop)

    try:
        log.info('starting tray')
        icon.run(setup=refresher)
    except Exception as e:
        log.warning('tray failed: %s', e)
        return False
    return True


MENUBAR_GLYPH = 'menubar-glyph@2x.png'   # 32×22 px = 16×11 pt на Retina
MENUBAR_SIZE = (16, 11)


def _mark_template(icon):
    """Значок строки меню берём сами: pystray растягивает картинку на всю высоту строки меню
    (выходит крупно и мыльно). Здесь — чёткий @2x-рисунок нужного размера в пунктах, в режиме
    «шаблон»: macOS сама красит его под светлую и тёмную строку."""
    path = resource(MENUBAR_GLYPH)
    if not os.path.exists(path):
        return

    def assert_image():
        if icon._icon_image is not None:
            return
        import AppKit
        import Foundation
        with open(path, 'rb') as f:
            img = AppKit.NSImage.alloc().initWithData_(Foundation.NSData.dataWithBytes_length_(f.read(), os.path.getsize(path)))
        img.setSize_(MENUBAR_SIZE)
        img.setTemplate_(True)
        icon._icon_image = img
        icon._status_item.button().setImage_(img)
    icon._assert_image = assert_image


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    log_path = server.setup_logging(DATA_DIR)
    log.info('NowPlaying Server %s started on %s (pid %d)', server.VERSION, sys.platform, os.getpid())
    # без консоли ошибки иначе пропадают: Python-ошибки — в журнал, падения глубже — в crash.log
    sys.excepthook = lambda t, v, tb: log.critical('crash', exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.critical('crash in thread %s', a.thread and a.thread.name,
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    import faulthandler
    global _crash_file
    _crash_file = open(os.path.join(DATA_DIR, 'crash.log'), 'a', encoding='utf-8')
    faulthandler.enable(_crash_file)

    if already_running():
        log.info('already running, opening settings')
        open_settings()
        return

    srv = ServerThread()
    srv.start()
    srv.started.wait(15)
    if srv.error:
        log.error('cannot start server on port %s: %s', server.HTTP_PORT, srv.error)
        return
    log.info('server listening on %s', server.HTTP_PORT)

    first_run = not os.path.exists(FIRST_RUN_FLAG)
    if first_run and FROZEN:
        set_autostart(True)
        open(FIRST_RUN_FLAG, 'w').close()
    if first_run:
        threading.Timer(1.5, open_settings).start()

    if not run_tray(srv, log_path, first_run):
        # значок показать негде — работаем без него, окно настроек открывается повторным запуском
        if not first_run:
            open_settings()
        srv.join()
    log.info('exiting')


if __name__ == '__main__':
    main()
