"""SVG-экраны для Figma (перетащить файл на холст — получатся редактируемые слои и текст).

Запуск: pc/.venv/Scripts/python design/make_figma_svg.py  ->  design/figma-svg/*.svg
Размеры и цвета — те же, что в приложении (MainActivity, WidgetView). Шрифт в SVG — Inter
(в облачной Figma нет Inter Display; в приложении крупный текст — Inter Display Bold).
"""
import os
from xml.sax.saxutils import escape

from PIL import ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, 'figma-svg')
FONTS = os.path.join(ROOT, 'android', 'assets', 'fonts')
FONT_FILE = {500: 'Inter-Medium.ttf', 600: 'Inter-DisplaySemiBold.ttf', 700: 'Inter-DisplayBold.ttf'}
_cache = {}


def width(s, size, weight=500):
    key = (size, weight)
    if key not in _cache:
        _cache[key] = ImageFont.truetype(os.path.join(FONTS, FONT_FILE[weight]), int(round(size * 4)))
    return _cache[key].getlength(s) / 4


def text(s, x, y, size, weight=500, fill='#ffffff', opacity=1.0, anchor='start', name=None):
    """y — базовая линия. anchor: start / middle / end (считаем сами, без text-anchor)."""
    w = width(s, size, weight)
    if anchor == 'middle':
        x -= w / 2
    elif anchor == 'end':
        x -= w
    op = f' fill-opacity="{opacity:.2f}"' if opacity < 1 else ''
    nm = f' id="{escape(name)}"' if name else ''
    return (f'<text{nm} x="{x:.1f}" y="{y:.1f}" font-family="Inter" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}"{op}>{escape(s)}</text>')


def svg(w, h, body, defs=''):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<defs>{defs}</defs>{body}</svg>')


def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
        f.write(data)


CLOCK_BG = ('<linearGradient id="clockbg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#33383f"/><stop offset="0.5" stop-color="#525454"/>'
            '<stop offset="1" stop-color="#14171a"/></linearGradient>')
COVER = ('<linearGradient id="cover" x1="0" y1="0" x2="1" y2="1">'
         '<stop offset="0" stop-color="#3845a8"/><stop offset="0.5" stop-color="#99619e"/>'
         '<stop offset="1" stop-color="#f59e73"/></linearGradient>')
TRACK_BG = ('<linearGradient id="trackbg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#292b61"/><stop offset="0.55" stop-color="#332142"/>'
            '<stop offset="1" stop-color="#120f1a"/></linearGradient>')


def clock_block(y_top=96, color='#ffffff', date_op=0.67):
    base = y_top + 0.82 * 130
    return (f'<g id="Время и дата">{text("22:14", 512, base, 130, 500, color, anchor="middle", name="Время")}'
            f'{text("Среда, 4 ноября", 512, base + 8 + 26 + 18, 26, 500, color, date_op, anchor="middle", name="Дата")}</g>')


def plate(x, y, name):
    return (f'<rect id="{name}" x="{x}" y="{y}" width="296" height="140" rx="22" fill="#ffffff" fill-opacity="0.12" '
            f'stroke="#ffffff" stroke-opacity="0.1" stroke-width="1.5"/>')


def widget_forecast(x, y):
    out = [plate(x, y, 'Виджет — прогноз'), text('Прогноз', x + 22, y + 37, 17, 500, '#ffffff', 0.67)]
    for i, (d, t) in enumerate([('Чт', '+3°'), ('Пт', '−1°'), ('Сб', '+4°')]):
        cx = x + 22 + i * 98
        out += [text(d, cx, y + 88, 14, 500, '#ffffff', 0.67), text(t, cx, y + 120, 32, 600)]
    return f'<g id="Виджет: Прогноз">{"".join(out)}</g>'


def widget_resume(x, y):
    out = [plate(x, y, 'Виджет — продолжить'),
           f'<rect id="Обложка" x="{x + 22}" y="{y + 38}" width="64" height="64" rx="14" fill="url(#cover)"/>',
           text('Продолжить', x + 100, y + 58, 14, 500, '#ffffff', 0.67),
           text('Ночной маршрут', x + 100, y + 81, 17, 500),
           text('Кассета', x + 100, y + 101, 14, 500, '#ffffff', 0.67)]
    return f'<g id="Виджет: Продолжить">{"".join(out)}</g>'


def clock_dim():
    return '<rect id="Фон для часов" width="1024" height="600" fill="url(#clockbg)"/>' \
           '<rect id="Затемнение 35%" width="1024" height="600" fill="#000000" fill-opacity="0.35"/>'


def timer_screen():
    labels = ['3', '5', '10', '15', '20', '30', '45', '60', 'Помодоро']
    chip_h, gap, pad_x, pad_y = 49, 10, 22, 14
    title = 'Таймер, минут'
    ws = [width(l, 22) + 40 for l in labels]
    total = pad_x * 2 + width(title, 18) + gap + sum(ws) + gap * (len(ws) - 1)
    ph = chip_h + pad_y * 2
    px, py = (1024 - total) / 2, 600 - 40 - ph
    out = [f'<rect id="Панель таймера" x="{px:.1f}" y="{py}" width="{total:.1f}" height="{ph}" rx="36" fill="#161618" fill-opacity="0.92"/>',
           text(title, px + pad_x, py + ph / 2 + 6.5, 18, 500, '#ffffff', 0.67)]
    x = px + pad_x + width(title, 18) + gap
    for l, w in zip(labels, ws):
        sel = l == '10'
        stroke = ' stroke="#ffffff" stroke-width="3"' if sel else ''
        out.append(f'<g id="Кнопка {escape(l)}"><rect x="{x:.1f}" y="{py + pad_y}" width="{w:.1f}" height="{chip_h}" rx="24.5" '
                   f'fill="#ffffff" fill-opacity="0.16"{stroke}/>{text(l, x + 20, py + pad_y + 33, 22)}</g>')
        x += w + gap
    return svg(1024, 600, clock_dim() + clock_block() + f'<g id="Панель таймера">{"".join(out)}</g>', CLOCK_BG)


def alarm_screen():
    defs = ('<linearGradient id="sun" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1f1a38"/>'
            '<stop offset="0.45" stop-color="#c63f1c"/><stop offset="1" stop-color="#ffb066"/></linearGradient>')
    body = ('<rect id="Рассвет" width="1024" height="600" fill="url(#sun)"/>'
            f'<g id="Звонок">{text("Доброе утро", 512, 282, 64, 700, anchor="middle")}'
            f'{text("07:30", 512, 330, 34, 500, anchor="middle")}'
            f'{text("Коснись экрана или нажми ОК, чтобы выключить", 512, 372, 18, 500, "#6b300f", 0.85, anchor="middle")}</g>')
    return svg(1024, 600, body, defs)


def night_screen():
    return svg(1024, 600, '<rect id="Фон" width="1024" height="600" fill="#000000"/>'
               + clock_block(180, '#a3281e', 0.7))


def resume_screen():
    return svg(1024, 600, clock_dim() + clock_block() + widget_forecast(42, 420) + widget_resume(686, 420),
               CLOCK_BG + COVER)


def tv_track():
    k = 2   # ТВ 1920×1080 при плотности 320 dpi = 960×540 dp, всё ×2
    lyr = [('Город спит, а я опять не сплю', 0.29), ('Фонари рисуют нам маршрут', 0.49),
           ('Сделай громче, это та самая', 1.0), ('Что играла в машине до утра', 0.49),
           ('Окна настежь, ветер в проводах', 0.29)]
    col_x, col_w = 472 * k, 452 * k
    title_h, artist_h, lyr_line = 30 * 1.15 * k, 22 * 1.2 * k, 26 * 1.05 * k
    block = title_h + artist_h + 18 * k + len(lyr) * lyr_line + 4 * 14 * k + 26 * k + 4 * k + 6 * k + 14 * 1.2 * k
    y = (1080 - block) / 2
    out = ['<rect id="Фон" width="1920" height="1080" fill="url(#trackbg)"/>',
           '<circle id="Свечение" cx="472" cy="540" r="920" fill="url(#glow)"/>',
           f'<rect id="Тень обложки" x="{36 * k}" y="{(540 - 400) * k / 2 + 16}" width="{400 * k}" height="{400 * k}" fill="#000000" fill-opacity="0.35" filter="url(#blur)"/>',
           f'<rect id="Обложка" x="{36 * k}" y="{(540 - 400) * k / 2}" width="{400 * k}" height="{400 * k}" fill="url(#cover)"/>',
           text('Ночной маршрут', col_x, y + 30 * k * 0.95, 30 * k, 700, name='Название')]
    y += title_h
    out.append(text('Кассета', col_x, y + 22 * k * 0.95, 22 * k, 500, '#ffffff', 0.78, name='Исполнитель'))
    y += artist_h + 18 * k
    for s, op in lyr:
        out.append(text(s, col_x, y + 26 * k * 0.9, 26 * k, 700, '#ffffff', op))
        y += lyr_line + 14 * k
    y += 26 * k - 14 * k
    out += [f'<rect id="Полоса" x="{col_x}" y="{y:.1f}" width="{col_w}" height="{4 * k}" rx="{2 * k}" fill="#ffffff" fill-opacity="0.25"/>',
            f'<rect id="Прошло" x="{col_x}" y="{y:.1f}" width="{col_w * 0.35:.1f}" height="{4 * k}" rx="{2 * k}" fill="#ffffff"/>']
    y += 4 * k + 6 * k + 14 * k
    out += [text('1:12', col_x, y, 14 * k, 500, '#ffffff', 0.67), text('3:28', col_x + col_w, y, 14 * k, 500, '#ffffff', 0.67, anchor='end')]
    defs = (TRACK_BG + COVER + '<radialGradient id="glow"><stop offset="0" stop-color="#8c66cc" stop-opacity="0.35"/>'
            '<stop offset="1" stop-color="#8c66cc" stop-opacity="0"/></radialGradient>'
            '<filter id="blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="30"/></filter>')
    return svg(1920, 1080, ''.join(out), defs)


LAV = ('<linearGradient id="lav" x1="0.15" y1="0" x2="0.05" y2="1"><stop offset="0" stop-color="#7a689b"/>'
       '<stop offset="1" stop-color="#343759"/></linearGradient>'
       '<radialGradient id="lavR" cx="1" cy="0.7" r="1.05"><stop offset="0" stop-color="#55507f"/><stop offset="0.45" stop-color="#55507f" stop-opacity="0.9"/>'
       '<stop offset="1" stop-color="#55507f" stop-opacity="0"/></radialGradient>')


def tv_banner():
    # мастер 640×360 (в приложении — 320×180, xhdpi); строки как в макете: 29 / 49 / 100 / 49 / 29 %
    rows = [('Cover', 0.29, 40), ('Lyrics', 0.49, 40), ('NowPlaying', 1.0, 46), ('Clock', 0.49, 40), ('Widgets', 0.29, 40)]
    out = ['<rect id="Фон" width="640" height="360" fill="url(#lav)"/>',
           '<rect id="Фон — справа" width="640" height="360" fill="url(#lavR)"/>']
    for i, (s, op, size) in enumerate(rows):
        cy = 38 + i * 71
        out.append(text(s, 320, cy + size * 0.36, size, 700, '#ffffff', op, anchor='middle', name=s))
    return svg(640, 360, ''.join(out), LAV)


def icon():
    body = ('<rect id="Фон" width="512" height="512" fill="url(#lav)"/>'
            '<rect id="Фон — справа" width="512" height="512" fill="url(#lavR)"/>'
            '<rect id="Строка выше" x="160" y="175" width="143" height="24" rx="12" fill="#848484"/>'
            '<circle id="Точка" cx="144" cy="256" r="16.5" fill="#ffffff"/>'
            '<rect id="Текущая строка" x="179" y="239" width="191" height="34" rx="17" fill="#ffffff"/>'
            '<rect id="Строка ниже" x="160" y="313" width="114" height="24" rx="12" fill="#848484"/>')
    return svg(512, 512, body, LAV)


def main():
    save('01 Часы — панель таймера.svg', timer_screen())
    save('02 Будильник — Доброе утро.svg', alarm_screen())
    save('03 Часы — ночной режим.svg', night_screen())
    save('04 Часы — Продолжить.svg', resume_screen())
    save('05 ТВ — экран трека 1920x1080.svg', tv_track())
    save('06 ТВ — баннер 640x360.svg', tv_banner())
    save('07 Иконка 512.svg', icon())
    print('ok', OUT)


if __name__ == '__main__':
    main()
