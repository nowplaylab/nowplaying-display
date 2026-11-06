"""NowPlaying Server для Windows: сервер в фоне + значок в трее.

При первом запуске:
  * добавляет себя в автозапуск через Планировщик заданий (при входе в систему, без прав
    администратора; если задание создать нельзя — запасной вариант HKCU\\...\\Run);
  * если нет правил брандмауэра, просит разрешение (окно UAC) открыть
    TCP 47801 и UDP 47800 — только для локальной сети.
В меню значка: что играет, адрес сервера, окно настроек, автозапуск, журнал, обновление, выход.
"""
import os
import sys
import time
import traceback

# Самая первая запись — до тяжёлых импортов: так видно, запускала ли Windows программу вообще
# и на чём она упала, даже если основной журнал ещё не успел открыться.
_STARTUP_LOG = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'NowPlaying', 'startup.log')


def _startup_note(text):
    try:
        os.makedirs(os.path.dirname(_STARTUP_LOG), exist_ok=True)
        if os.path.exists(_STARTUP_LOG) and os.path.getsize(_STARTUP_LOG) > 200_000:
            os.replace(_STARTUP_LOG, _STARTUP_LOG + '.old')
        with open(_STARTUP_LOG, 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} pid={os.getpid()} {text}\n')
    except Exception:
        pass


_startup_note(f'launch cwd={os.getcwd()!r} exe={sys.executable!r}')


def clean_env():
    """Окружение для запуска новой копии exe. Без PYINSTALLER_RESET_ENVIRONMENT новая копия
    решит, что она «дочка» текущей, и возьмёт её временную папку с распакованными файлами —
    а та удаляется, когда текущая копия закрывается (так падала 1.6 после автообновления)."""
    env = dict(os.environ)
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    env['NP_CLEAN_START'] = '1'
    return env


# Запустили после обновления, но, возможно, старая версия (1.6) не сбросила окружение и мы
# работаем из её временной папки. Сразу перезапускаемся начисто, пока папка ещё на месте.
if getattr(sys, 'frozen', False) and '--after-update' in sys.argv and not os.environ.get('NP_CLEAN_START'):
    import subprocess
    _startup_note('after update: restarting with a clean environment')
    subprocess.Popen([sys.executable, *sys.argv[1:]], env=clean_env(), close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    sys.exit(0)

try:
    import asyncio
    import ctypes
    import socket
    import shutil
    import subprocess
    import threading
    import winreg
    import logging

    import pystray
    from aiohttp import web
    from PIL import Image

    import server
except Exception:
    _startup_note('import failed:\n' + traceback.format_exc())
    raise
_startup_note('imports ok')

APP_NAME = 'NowPlaying Server'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
DATA_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'NowPlaying')
FIRST_RUN_FLAG = os.path.join(DATA_DIR, 'first-run-done')
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
shell32 = ctypes.windll.shell32


def message(text, error=False):
    user32.MessageBoxW(None, text, APP_NAME, 0x10 if error else 0x40)


def resource(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------- автозапуск ----------

# Автозапуск — через Планировщик заданий («при входе в систему», перезапуск при сбое).
# Раньше был раздел реестра Run, но Windows иногда молча пропускает такие записи; Run остаётся
# только запасным вариантом, если задание создать не удалось.
TASK_NAME = APP_NAME
RUN_FALLBACK_FLAG = os.path.join(DATA_DIR, 'autostart-run-fallback')  # задание не создаётся — не пытаться каждый запуск
_autostart_cache = {}


def _launch_parts():
    """(программа, аргументы) — единый источник и для задания, и для записи Run."""
    if getattr(sys, 'frozen', False):
        return sys.executable, ''
    pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    return pythonw, f'"{os.path.abspath(__file__)}"'


def launch_command():
    exe, args = _launch_parts()
    return f'"{exe}" {args}'.strip()


def _q(text):
    """Строка в одинарных кавычках PowerShell (апостроф удваивается)."""
    return "'" + text.replace("'", "''") + "'"


def _ps(script, timeout=120):
    """PowerShell без окна; None, если не запустился или завис."""
    try:
        return subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                              capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        logging.getLogger('nowplaying').warning('powershell failed: %s', e)
        return None


def _run_key_present():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def _remove_run_key():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
            return True
    except OSError:
        return False


def _task_enabled():
    """Есть ли включённое задание. XML не зависит от языка Windows (в отличие от колонки «Состояние»)."""
    try:
        r = subprocess.run(['schtasks', '/query', '/tn', TASK_NAME, '/xml'],
                           capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and '<Enabled>false</Enabled>' not in r.stdout


def autostart_enabled(refresh=False):
    if refresh or 'on' not in _autostart_cache:
        _autostart_cache['on'] = _task_enabled() or _run_key_present()
    return _autostart_cache['on']


def set_autostart(on):
    if on:
        exe, args = _launch_parts()
        arg = f'-Argument {_q(args)}' if args else ''
        script = (
            f"$a = New-ScheduledTaskAction -Execute {_q(exe)} {arg}; "
            "$u = \"$env:USERDOMAIN\\$env:USERNAME\"; "
            "$t = New-ScheduledTaskTrigger -AtLogOn -User $u; "   # задержку делает сама программа (см. __main__)
            "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) "
            "-MultipleInstances IgnoreNew; "
            "$p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited; "
            f"Register-ScheduledTask -TaskName {_q(TASK_NAME)} -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null")
        r = _ps(script)
        if r is None or r.returncode != 0:
            logging.getLogger('nowplaying').warning('scheduled task failed, using Run key: %s',
                                                    r.stderr[-300:] if r else 'no powershell')
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_command())
                open(RUN_FALLBACK_FLAG, 'w').close()
            except OSError as e:
                logging.getLogger('nowplaying').warning('Run key failed too: %s', e)
        else:
            _remove_run_key()
            try:
                os.remove(RUN_FALLBACK_FLAG)
            except OSError:
                pass
    else:
        _ps(f"Unregister-ScheduledTask -TaskName {_q(TASK_NAME)} -Confirm:$false -ErrorAction SilentlyContinue")
        _remove_run_key()
    autostart_enabled(refresh=True)


# ---------- брандмауэр ----------

def firewall_ok():
    ps = ("$r = Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow -ErrorAction SilentlyContinue | "
          "Get-NetFirewallPortFilter | Where-Object { $_.LocalPort -in '47800','47801' }; "
          "($r | Select-Object -ExpandProperty LocalPort -Unique).Count")
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                             capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW).stdout
        return out.strip() == '2'
    except Exception:
        return True  # не смогли проверить — не дёргаем пользователя


def add_firewall_rules():
    """Окно UAC: правила только для локальной подсети."""
    ps = ("New-NetFirewallRule -DisplayName 'NowPlaying (TCP 47801)' -Direction Inbound -Action Allow "
          "-Protocol TCP -LocalPort 47801 -RemoteAddress LocalSubnet; "
          "New-NetFirewallRule -DisplayName 'NowPlaying поиск (UDP 47800)' -Direction Inbound -Action Allow "
          "-Protocol UDP -LocalPort 47800 -RemoteAddress LocalSubnet")
    shell32.ShellExecuteW(None, 'runas', 'powershell.exe',
                          f'-NoProfile -WindowStyle Hidden -Command "{ps}"', None, 0)


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


# ---------- окно настроек ----------

def open_settings():
    """Страница настроек в отдельном окне Edge без адресной строки (как обычная программа);
    если Edge нет — в браузере по умолчанию."""
    url = f'http://127.0.0.1:{server.HTTP_PORT}/settings'
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        edge = os.path.join(base or '', 'Microsoft', 'Edge', 'Application', 'msedge.exe')
        if base and os.path.exists(edge):
            subprocess.Popen([edge, f'--app={url}', '--window-size=1000,780'], close_fds=True)
            return
    import webbrowser
    webbrowser.open(url)


# ---------- обновление (новый exe скачивает updater.py) ----------

AFTER_UPDATE = '--after-update'


def apply_update():
    """Подменить свой exe скачанным и запустить новый. Запущенный exe Windows удалить не даёт,
    но переименовать даёт: старый становится .old и удаляется при следующем запуске."""
    log = logging.getLogger('nowplaying')
    new, cur = server.UPDATER.exe, sys.executable
    old = cur + '.old'
    try:
        if os.path.exists(old):
            os.remove(old)
        os.replace(cur, old)
        try:
            shutil.copy2(new, cur)
        except OSError:
            os.replace(old, cur)  # не вышло — возвращаем как было
            raise
    except OSError as e:
        log.warning('update: cannot replace exe: %s', e)
        server.UPDATER.exe = None
        return False
    log.info('update: replaced exe with %s, restarting', server.UPDATER.latest)
    subprocess.Popen([cur, AFTER_UPDATE], close_fds=True, env=clean_env(),
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return True


def remove_old_exe():
    old = sys.executable + '.old'
    for _ in range(20):   # старый процесс может ещё закрываться
        if not os.path.exists(old):
            return
        try:
            os.remove(old)
            return
        except OSError:
            time.sleep(0.5)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    log_path = server.setup_logging(DATA_DIR)
    after_update = AFTER_UPDATE in sys.argv
    # любые необработанные ошибки — в журнал, иначе без консоли программа «молча» пропадает
    import logging
    log = logging.getLogger('nowplaying')
    sys.excepthook = lambda t, v, tb: log.critical('crash', exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.critical('crash in thread %s', a.thread and a.thread.name,
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    log.info('NowPlaying Server started (pid %d)', os.getpid())

    # один экземпляр (после обновления ждём, пока старая копия закроется)
    for attempt in range(40 if after_update else 1):
        # тестовая копия на другом порту (NP_HTTP_PORT) не мешает рабочей
        port = os.environ.get('NP_HTTP_PORT', '')
        mutex = kernel32.CreateMutexW(None, False, 'Local\\NowPlayingServer' + (f'-{port}' if port and port != '47801' else ''))
        if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            break
        kernel32.CloseHandle(mutex)
        time.sleep(0.5)
    else:
        _startup_note('already running, exit')
        if not after_update:
            message('NowPlaying Server уже запущен — значок в трее рядом с часами.')
        return
    if getattr(sys, 'frozen', False):
        threading.Thread(target=remove_old_exe, daemon=True).start()

    srv = ServerThread()
    srv.start()
    srv.started.wait(15)
    if srv.error:
        message(f'Не удалось запустить сервер на порту {server.HTTP_PORT}:\n{srv.error}\n\n'
                'Возможно, он уже запущен другим способом.', error=True)
        return

    first_run = not os.path.exists(FIRST_RUN_FLAG)
    if first_run:
        set_autostart(True)
        open(FIRST_RUN_FLAG, 'w').close()
    elif _run_key_present() and not os.path.exists(RUN_FALLBACK_FLAG) and not _task_enabled():
        # переезд со старой версии: автозапуск был в реестре Run — переносим в Планировщик заданий
        # (Run удаляется только если задание создалось; если не создалось — больше не пытаемся)
        _startup_note('migrating autostart from Run key to Task Scheduler')
        set_autostart(True)
    fw = {'ok': firewall_ok()}
    if first_run and not fw['ok']:
        add_firewall_rules()

    def toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())

    def fix_firewall(icon, item):
        add_firewall_rules()

    def open_log(icon, item):
        if log_path and os.path.exists(log_path):
            os.startfile(log_path)

    def show_settings(icon, item):
        open_settings()

    def update_now(icon, item):
        if apply_update():
            srv.stop()
            icon.stop()

    def version_text():
        u = server.UPDATER
        if u.exe:
            return f'Версия {server.VERSION}, скачана {u.latest}'
        return f'Версия {server.VERSION}'

    def quit_app(icon, item):
        srv.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: now_playing_text(), None, enabled=False),
        pystray.MenuItem(lambda item: f'Адрес для планшета: {local_ip()}:{server.HTTP_PORT}', None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Настройки…', show_settings, default=True),   # и по клику на значок
        pystray.MenuItem('Запускать вместе с Windows', toggle_autostart, checked=lambda item: autostart_enabled()),
        pystray.MenuItem('Разрешить в брандмауэре…', fix_firewall, visible=lambda item: not fw['ok']),
        pystray.MenuItem('Открыть журнал', open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: version_text(), None, enabled=False),
        pystray.MenuItem(lambda item: f'Обновить до {server.UPDATER.latest}', update_now,
                         visible=lambda item: bool(server.UPDATER.exe)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Выход', quit_app),
    )
    icon = pystray.Icon('NowPlaying', Image.open(resource('icon.png')), APP_NAME, menu)

    def refresher(icon):
        icon.visible = True
        if first_run:
            icon.notify('Сервер запущен и добавлен в автозагрузку. '
                        'Откройте «Сейчас играет» на планшете — он найдёт компьютер сам.', APP_NAME)
        elif after_update:
            icon.notify(f'Обновлено до версии {server.VERSION}.', APP_NAME)
        last_fw_check = time.time()
        quiet_since = time.time()
        while srv.is_alive():
            try:
                icon.title = f'{APP_NAME}\n{now_playing_text()}'[:127]
                # новая версия скачана — ставим, когда музыка 2 минуты не играет
                if server.state.get('playing'):
                    quiet_since = time.time()
                elif server.UPDATER.exe and server.UPDATER.enabled and time.time() - quiet_since > 120:
                    if apply_update():
                        srv.stop()
                        icon.stop()
                        return
                if not fw['ok'] and time.time() - last_fw_check > 20:  # после окна UAC
                    fw['ok'] = firewall_ok()
                    last_fw_check = time.time()
                icon.update_menu()
            except Exception:
                logging.getLogger('nowplaying').exception('tray refresh')
            time.sleep(3)
        logging.getLogger('nowplaying').warning('server thread stopped')

    icon.run(setup=refresher)
    logging.getLogger('nowplaying').info('tray closed, exiting')


if __name__ == '__main__':
    try:
        # сразу после включения ПК даём Windows дособрать панель задач, иначе значок в трее может не встать
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        uptime = ctypes.windll.kernel32.GetTickCount64() / 1000
        if uptime < 180 and '--after-update' not in sys.argv:   # после обновления ждать незачем
            _startup_note(f'early start (uptime {uptime:.0f}s), waiting 20s')
            time.sleep(20)
        main()
        _startup_note('exit normally')
    except SystemExit:
        _startup_note('exit (SystemExit)')
        raise
    except Exception:
        _startup_note('crash: ' + traceback.format_exc())
        try:
            ctypes.windll.user32.MessageBoxW(None, 'NowPlaying Server упал при запуске. Подробности: ' + _STARTUP_LOG,
                                             'NowPlaying Server', 0x10)
        except Exception:
            pass
        raise
