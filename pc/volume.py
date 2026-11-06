"""Громкость Windows (основное устройство вывода) через Core Audio (pycaw).

comtypes при импорте включает COM в однопоточном режиме (STA) в том потоке, где его
импортировали. Если это случится в основном потоке сервера, перестаёт работать опрос
панели «Сейчас играет» (winrt). Поэтому pycaw импортируем только в рабочем потоке —
функции отсюда вызываются через run_in_executor.

На Linux — pactl (PulseAudio и PipeWire), на macOS — osascript.
"""
import re
import subprocess
import sys


def _run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=True).stdout


def _other_get():
    if sys.platform == 'darwin':
        vol, mute = _run('osascript', '-e', 'set s to get volume settings',
                         '-e', 'return (output volume of s as text) & "," & (output muted of s as text)').strip().split(',')
        return int(vol), mute.strip() == 'true'
    vol = re.search(r'(\d+)%', _run('pactl', 'get-sink-volume', '@DEFAULT_SINK@'))
    mute = 'yes' in _run('pactl', 'get-sink-mute', '@DEFAULT_SINK@')
    return (int(vol.group(1)) if vol else None), mute


def _other_set(level):
    level = max(0, min(100, level))
    if sys.platform == 'darwin':
        _run('osascript', '-e', f'set volume output volume {level}' + (' without output muted' if level else ''))
    else:
        _run('pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{level}%')
        if level:
            _run('pactl', 'set-sink-mute', '@DEFAULT_SINK@', '0')
    return _other_get()[0]


def _endpoint():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    return AudioUtilities.GetSpeakers().EndpointVolume


def _done():
    import comtypes
    comtypes.CoUninitialize()


def get():
    """(громкость 0–100, выключен ли звук). Только из рабочего потока."""
    if sys.platform != 'win32':
        return _other_get()
    v = _endpoint()
    try:
        return round(v.GetMasterVolumeLevelScalar() * 100), bool(v.GetMute())
    finally:
        del v
        _done()


def set_level(level):
    if sys.platform != 'win32':
        return _other_set(level)
    v = _endpoint()
    try:
        v.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100, None)
        if level > 0 and v.GetMute():
            v.SetMute(0, None)   # прибавили громкость — значит, звук нужен
        return round(v.GetMasterVolumeLevelScalar() * 100)
    finally:
        del v
        _done()
