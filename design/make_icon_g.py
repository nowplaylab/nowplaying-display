"""Иконка «Активная строка» (вариант Г): три строки текста песни, средняя — та, что поётся, —
яркая, с точкой слева; фон — рассвет из будильника.

Запуск: python design/make_icon_g.py  →  design/icon_g/
  icon_1024.png            — мастер, скруглённый квадрат (README, сайт, магазины)
  adaptive_fg_432.png      — передний слой адаптивной иконки Android 8+ (108 dp × 4), прозрачный
  adaptive_bg_432.png      — задний слой (градиент)
  android/drawable-*/icon.png  — обычная иконка для старых Android (48…192 px)
  tv/banner.png            — баннер Android TV 320×180 (xhdpi; лаунчер сам масштабирует)
  tray/icon.png, icon.ico  — значок программы на ПК (до 16 px — упрощённый, толще)
  preview.png              — всё на одном листе

Сетка — холст 108 как у адаптивных иконок Android: всё важное в центральном круге диаметром 66.
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'icon_g')
SS = 4                                   # суперсэмплинг для гладких краёв
TOP, BOTTOM = (255, 176, 102), (198, 63, 28)
WHITE = (255, 255, 255)
DIM = 115                                # прозрачность «соседних» строк (из 255)


def gradient(w, h):
    g = Image.new('RGB', (1, h))
    for y in range(h):
        k = y / max(1, h - 1)
        g.putpixel((0, y), tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * k) for i in range(3)))
    return g.resize((w, h))


def draw_lines(layer, scale, ox=0.0, oy=0.0, bold=False):
    """Рисунок в координатах сетки 108 (scale — пикселей на единицу сетки)."""
    d = ImageDraw.Draw(layer)

    def rr(x, y, w, h, a):
        box = [(ox + x) * scale, (oy + y) * scale, (ox + x + w) * scale, (oy + y + h) * scale]
        d.rounded_rectangle(box, radius=h * scale / 2, fill=WHITE + (a,))

    if bold:   # мелкие значки (16–32 px): строки толще, соседние короче — иначе превращаются в кашу
        rr(32, 33, 32, 9, DIM + 30)
        d.ellipse([(ox + 25) * scale, (oy + 49) * scale, (ox + 35) * scale, (oy + 59) * scale], fill=WHITE + (255,))
        rr(39, 48, 44, 12, 255)
        rr(32, 66, 22, 9, DIM + 30)
        return
    rr(34, 37, 30, 5, DIM)
    d.ellipse([(ox + 27.6) * scale, (oy + 50.6) * scale, (ox + 34.4) * scale, (oy + 57.4) * scale], fill=WHITE + (255,))
    rr(38, 50.5, 40, 7, 255)
    rr(34, 66, 24, 5, DIM)


def icon(size, rounded=True, crop=(14, 92), bold=None):
    """Готовая иконка size×size. crop — какой кусок сетки 108 видно (у старых Android
    маски нет, поэтому берём середину покрупнее); rounded — скруглённые углы 22%."""
    bold = size <= 32 if bold is None else bold
    S = size * SS
    lo, hi = crop
    scale = S / (hi - lo)
    img = gradient(S, S).convert('RGBA')
    fg = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    draw_lines(fg, scale, -lo, -lo, bold)
    img = Image.alpha_composite(img, fg)
    if rounded:
        mask = Image.new('L', (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
        img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


def banner(w=320, h=180):
    """Баннер Android TV: рисунок слева, название справа."""
    S = SS
    img = gradient(w * S, h * S).convert('RGBA')
    fg = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw_lines(fg, 1.6 * S, -14, -2.25)
    img = Image.alpha_composite(img, fg)
    d = ImageDraw.Draw(img)
    left, right = 150 * S, (w - 26) * S          # название — справа от рисунка, с отступом от края
    size = 30
    while True:
        font = None
        for name in ('Manrope-ExtraBold.ttf', 'seguibl.ttf', 'seguisb.ttf', 'arialbd.ttf'):
            try:
                font = ImageFont.truetype(name, size * S)
                break
            except OSError:
                continue
        if size <= 16 or d.textlength('NowPlaying', font=font) <= right - left:
            break
        size -= 1
    d.text((left, 90 * S), 'NowPlaying', font=font, fill=WHITE + (255,), anchor='lm')
    return img.resize((w, h), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    icon(1024).save(os.path.join(OUT, 'icon_1024.png'))
    # адаптивная иконка (Android 8+): слои отдельно, полный холст 108 → 432 px
    S = 432 * SS
    fg = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    draw_lines(fg, S / 108)
    fg.resize((432, 432), Image.LANCZOS).save(os.path.join(OUT, 'adaptive_fg_432.png'))
    gradient(432, 432).save(os.path.join(OUT, 'adaptive_bg_432.png'))
    for dpi, px in (('mdpi', 48), ('hdpi', 72), ('xhdpi', 96), ('xxhdpi', 144), ('xxxhdpi', 192)):
        d = os.path.join(OUT, 'android', 'drawable-' + dpi)
        os.makedirs(d, exist_ok=True)
        icon(px).save(os.path.join(d, 'icon.png'))
    os.makedirs(os.path.join(OUT, 'tv'), exist_ok=True)
    banner().save(os.path.join(OUT, 'tv', 'banner.png'))
    os.makedirs(os.path.join(OUT, 'tray'), exist_ok=True)
    icon(256).save(os.path.join(OUT, 'tray', 'icon.png'))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big = icon(256)
    big.save(os.path.join(OUT, 'tray', 'icon.ico'), sizes=[(s, s) for s in sizes],
             append_images=[icon(s) for s in sizes[:-1]])

    # лист-превью: крупно, на рабочем столе, мелкие размеры, баннер
    sheet = Image.new('RGB', (1000, 560), (24, 23, 28))
    sheet.paste(icon(256), (40, 40), icon(256))
    x = 340
    for s in (144, 96, 72, 48, 32, 24, 16):
        im = icon(s)
        sheet.paste(im, (x, 40 + (144 - s)), im)
        x += s + 24
    b = banner()
    sheet.paste(b, (340, 230))
    b2 = banner().resize((160, 90), Image.LANCZOS)
    sheet.paste(b2, (700, 230))
    # «рабочий стол»: светлый и тёмный фон, рядом серые соседи
    for i, bgc in enumerate(((236, 233, 228), (40, 39, 46))):
        y = 440 + 0 * i
        x0 = 40 + i * 300
        ImageDraw.Draw(sheet).rounded_rectangle([x0, y, x0 + 270, y + 90], radius=18, fill=bgc)
        for k in range(3):
            cx = x0 + 22 + k * 82
            if k == 1:
                im = icon(64)
                sheet.paste(im, (cx, y + 13), im)
            else:
                ImageDraw.Draw(sheet).rounded_rectangle([cx, y + 13, cx + 64, y + 77], radius=14,
                                                        fill=(190, 186, 180) if i == 0 else (70, 68, 78))
    sheet.save(os.path.join(OUT, 'preview.png'))
    print('ok', OUT)


if __name__ == '__main__':
    main()
