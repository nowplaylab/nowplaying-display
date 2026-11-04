"""Иконка и ТВ-баннер из макетов дизайнера (design/icon_v2/*_src_*.png) во все нужные места.

Запуск: pc/.venv/Scripts/python design/make_icon_final.py
  android/res/drawable-*/icon.png      — иконка приложения 48…192 px (скруглённые углы 22%:
                                         у Android 4.x маски нет, квадрат смотрелся бы плиткой)
  android/res/drawable-xhdpi/banner.png — баннер Android TV 320×180 (без скругления, лаунчер скругляет сам)
  pc/icon.png, pc/icon.ico, pc/settings_ui/icon.png, docs/screenshots/icon.png — значок на ПК и в README
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, 'icon_v2')


def rounded(img, size):
    S = size * 4
    big = img.convert('RGBA').resize((S, S), Image.LANCZOS)
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    big.putalpha(mask)
    return big.resize((size, size), Image.LANCZOS)


def banner(img, w=320, h=180):
    # вписываем по высоте и обрезаем лишнее по бокам поровну
    k = h / img.height
    tmp = img.convert('RGB').resize((round(img.width * k), h), Image.LANCZOS)
    x = (tmp.width - w) // 2
    return tmp.crop((x, 0, x + w, h))


def main():
    icon = Image.open(os.path.join(SRC, 'icon_src_512.png'))
    for dpi, px in (('mdpi', 48), ('hdpi', 72), ('xhdpi', 96), ('xxhdpi', 144), ('xxxhdpi', 192)):
        d = os.path.join(ROOT, 'android', 'res', 'drawable-' + dpi)
        os.makedirs(d, exist_ok=True)
        rounded(icon, px).save(os.path.join(d, 'icon.png'))
    banner(Image.open(os.path.join(SRC, 'banner_src_650x360.png'))).save(
        os.path.join(ROOT, 'android', 'res', 'drawable-xhdpi', 'banner.png'))
    big = rounded(icon, 256)
    for p in ('pc/icon.png', 'pc/settings_ui/icon.png', 'docs/screenshots/icon.png'):
        big.save(os.path.join(ROOT, p))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big.save(os.path.join(ROOT, 'pc', 'icon.ico'), sizes=[(s, s) for s in sizes],
             append_images=[rounded(icon, s) for s in sizes[:-1]])
    print('ok')


if __name__ == '__main__':
    main()
