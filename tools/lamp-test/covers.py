"""Тестовые обложки (свои, абстрактные) с разными палитрами + цвет лампы для каждой."""
import base64
import io
import json
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter


HERE = os.path.dirname(os.path.abspath(__file__))
S = 480


def grad(c1, c2, vertical=True):
    im = Image.new('RGB', (1, 256))
    for y in range(256):
        t = y / 255
        im.putpixel((0, y), tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    im = im.resize((S, S))
    return im if vertical else im.rotate(90)


def noise(im, amt=10, seed=1):
    random.seed(seed)
    n = Image.effect_noise((S, S), amt).convert('RGB')
    return Image.blend(im, Image.composite(im, n, Image.new('L', (S, S), 200)), 0.25)


def c_sunset():
    im = grad((255, 120, 70), (120, 40, 110))
    d = ImageDraw.Draw(im)
    d.ellipse((140, 170, 340, 370), fill=(255, 200, 90))
    d.rectangle((0, 330, S, S), fill=(40, 20, 45))
    return im


def c_neon():
    im = Image.new('RGB', (S, S), (8, 8, 14))
    d = ImageDraw.Draw(im)
    for i in range(7):
        d.line((0, 90 + i * 48, S, 30 + i * 60), fill=(40, 110, 255) if i % 2 else (230, 40, 200), width=10)
    return im.filter(ImageFilter.GaussianBlur(2))


def c_bw():
    im = grad((230, 230, 230), (30, 30, 30))
    d = ImageDraw.Draw(im)
    d.ellipse((120, 80, 360, 320), fill=(20, 20, 20))
    d.rectangle((60, 380, 420, 400), fill=(240, 240, 240))
    return noise(im, 30)


def c_forest():
    im = grad((150, 190, 120), (20, 50, 30))
    d = ImageDraw.Draw(im)
    random.seed(3)
    for i in range(14):
        x = random.randint(-20, S)
        h = random.randint(180, 360)
        d.polygon([(x, S), (x + 40, S - h), (x + 80, S)], fill=(25 + i * 3, 70 + i * 4, 40))
    return im


def c_red_dot():
    im = Image.new('RGB', (S, S), (236, 228, 212))
    ImageDraw.Draw(im).ellipse((170, 170, 310, 310), fill=(214, 34, 40))
    return noise(im, 8)


def c_pastel():
    im = grad((214, 196, 236), (250, 216, 220), vertical=False)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((90, 90, 390, 390), 60, outline=(255, 255, 255), width=14)
    return im


def c_night_gold():
    im = Image.new('RGB', (S, S), (12, 12, 16))
    d = ImageDraw.Draw(im)
    d.text((0, 0), '', fill=0)
    d.ellipse((300, 60, 380, 140), fill=(250, 200, 70))
    for i in range(40):
        random.seed(i)
        x, y = random.randint(0, S), random.randint(0, S)
        d.ellipse((x, y, x + 3, y + 3), fill=(200, 200, 210))
    return im


def c_ocean():
    im = grad((20, 160, 180), (10, 40, 80))
    d = ImageDraw.Draw(im)
    for i in range(6):
        y = 240 + i * 40
        d.arc((-100, y - 60, S + 100, y + 60), 200, 340, fill=(140, 230, 230), width=5)
    return im


COVERS = [
    ('Закат на Фрунзенской', 'Пример 1', c_sunset),
    ('Неоновые провода', 'Пример 2', c_neon),
    ('Чёрно-белое кино', 'Пример 3', c_bw),
    ('Лес после дождя', 'Пример 4', c_forest),
    ('Красная точка', 'Пример 5', c_red_dot),
    ('Пастила', 'Пример 6', c_pastel),
    ('Ночь и луна', 'Пример 7', c_night_gold),
    ('Прибой', 'Пример 8', c_ocean),
]

