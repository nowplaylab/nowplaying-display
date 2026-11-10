"""Прячет секрет приложения Яндекса в копии yandex_secret.py внутри папки сборки:
открытый текст заменяется на 'x:<base64 xor>' (pc/lamp.py умеет это читать).
    python tools/hide_secret.py <путь к yandex_secret.py в папке сборки>
Сам pc/yandex_secret.py не трогать — он не в git и нужен для запуска из исходников."""
import base64
import re
import sys

p = sys.argv[1]
s = re.search(r"SECRET\s*=\s*'([^']*)'", open(p, encoding='utf-8').read()).group(1)
if not s.startswith('x:'):
    x = bytes(c ^ ((0x5A + i * 29) & 0xFF) for i, c in enumerate(s.encode()))
    s = 'x:' + base64.b64encode(x).decode()
open(p, 'w', encoding='utf-8').write(f"SECRET = '{s}'\n")
