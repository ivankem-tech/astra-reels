"""Сборка вертикальных роликов для TikTok и YouTube Shorts.

Ролик собирается из статичных кадров: PIL рисует сцены, ffmpeg склеивает их
с плавным приближением. Снимать ничего не нужно — ни камеры, ни голоса,
ни лица.

Пять форматов, чтобы чередовать по дням и не приедаться:
    choose  «выбери одну из трёх карт»  — зритель ждёт свою карту
    day     «карта дня»                 — самый быстрый в производстве
    signs   «трём знакам повезёт»       — люди ищут свой знак
    yesno   «загадай вопрос»            — больше всего комментариев
    number  «число судьбы»              — сильнее всех тянет в бота

Фон везде тёмный с золотом — это узнаваемость канала. Но у каждого формата
свой оттенок и своя звёздная россыпь, поэтому лента не выглядит однообразной.

Звука нет намеренно: музыку добавляют в самом приложении из трендов,
так алгоритм охотнее показывает ролик.

Запуск:
    python3 tools/reel.py --format day --out reel.mp4
    python3 tools/reel.py --format choose --spec my.json --out reel.mp4
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.services import cards as cards_image  # noqa: E402
from app.services import profile  # noqa: E402
from app.services import tarot as tarot_service  # noqa: E402
from tools import wheel as wheel_bg  # noqa: E402

# Вертикаль под телефон.
W, H = 1080, 1920
FPS = 30

# Интерфейс TikTok закрывает низ и правый край — важное держим в середине.
SAFE_BOTTOM = 420
SAFE_TOP = 180

GOLD = cards_image.GOLD
GOLD_LIGHT = cards_image.GOLD_LIGHT
CREAM = cards_image.CREAM

# Оттенок фона под каждый формат. Все тёмные и все с золотым текстом —
# канал остаётся узнаваемым, но лента не выглядит одинаковой.
PALETTES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "choose": ((34, 21, 66), (10, 5, 24)),    # индиго — базовый
    "day":    ((48, 20, 64), (14, 5, 22)),    # сливовый
    "signs":  ((16, 32, 68), (5, 10, 26)),    # ночной синий
    "yesno":  ((58, 20, 46), (20, 6, 20)),    # винный
    "number": ((16, 46, 54), (5, 16, 24)),    # тёмная бирюза
}

# Звёздная россыпь у каждого формата своя, но внутри одного ролика —
# одна и та же. Если менять её от сцены к сцене, на каждом стыке звёзды
# перескакивают, и ролик выглядит дрожащим.
SEEDS = {"choose": 11, "choose_fast": 11, "choose_open": 11, "day": 31,
         "branch": 61, "count": 61,
         "signs": 41, "yesno": 61, "number": 81}

# Где оставить медленный наезд. В роликах с картами движения
# и так хватает, а вот текстовые форматы без него мертвеют.
ZOOM = {"choose": False, "choose_fast": False, "choose_open": False,
        "branch": False, "count": False, "day": True,
        "signs": False, "yesno": False, "number": True}

# Человек читает текст на экране примерно три слова в секунду. Держим кадр
# дольше — заскучает, короче — не успеет и уйдёт.
WORDS_PER_SECOND = 3.2
MIN_SECONDS = 2.2
MAX_SECONDS = 8.0


def _seconds(*texts: str, base: float = 1.8) -> float:
    """Сколько держать сцену, чтобы её успели прочитать без спешки."""
    words = sum(len(t.split()) for t in texts if t)
    return max(MIN_SECONDS, min(MAX_SECONDS, base + words / WORDS_PER_SECOND))


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _starfield(img: Image.Image, seed: int) -> None:
    """Мелкая звёздная россыпь: без неё фон выглядит плоской заливкой.

    Звёзды тусклые и мелкие намеренно — они оживляют кадр, но не спорят
    с текстом. Seed один на весь ролик: если менять его от сцены к сцене,
    звёзды перескакивают на каждом стыке и ролик выглядит дрожащим.
    """
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    for _ in range(150):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        r = rnd.uniform(0.8, 2.6)
        # Ближе к центру звёзды глуше, чтобы не мешали читать.
        dist = math.hypot(x - W / 2, y - H / 2) / math.hypot(W / 2, H / 2)
        alpha = rnd.uniform(0.10, 0.34) * (0.35 + 0.65 * dist)
        base = img.getpixel((int(x), int(y)))
        tint = GOLD_LIGHT if rnd.random() < 0.6 else (255, 255, 255)
        color = tuple(int(base[i] + (tint[i] - base[i]) * alpha) for i in range(3))
        d.ellipse((x - r, y - r, x + r, y + r), fill=color)


# Имя бота держим в кадре всё время, а не только в финальной заставке.
# До конца ролика досматривают от двух до четырнадцати процентов — значит
# призыв в конце видят единицы. Подпись в углу видят все.
# Подпись в кадре задаётся ниже как MARK — она зависит от того,
# куда ролик ведёт.


def _watermark(img: Image.Image, palette: str = "choose") -> None:
    """Ненавязчивая подпись в левом верхнем углу.

    Слева и сверху — потому что правый край и низ закрывает интерфейс
    приложения: там кнопки лайков и текст публикации.

    Отступ зависит от того, едет ли масштаб. Наезд ZOOM_MAX срезает
    103 пикселя сверху и 58 слева, и подпись у самого края уезжала за
    кадр целиком — в форматах с наездом опускаем её ниже SAFE_TOP.
    Там, где наезда нет, наоборот держим у края: ниже она налезает на
    заголовок, который в этих раскладках начинается высоко.
    """
    font = _font(34, False)
    x, y = (120, 210) if ZOOM.get(palette, False) else (54, 62)
    # Под подпись кладём тёмную полупрозрачную плашку: без неё значки
    # планет с колеса на фоне сливаются с буквами адреса.
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    label = f"Telegram · {MARK}"
    tb = od.textbbox((x, y), label, font=font)
    pad = 16
    od.rounded_rectangle([tb[0] - pad, tb[1] - 6, tb[2] + pad, tb[3] + 8],
                         radius=18, fill=(6, 3, 14, 150))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"),
              (0, 0))
    d = ImageDraw.Draw(img)
    # Приглушаем, чтобы подпись не спорила с содержанием кадра.
    faded = tuple(int(c * 0.62 + 20) for c in GOLD)
    d.text((x, y), label, font=font, fill=faded)


# Колесо натальной карты на фоне. Размер больше кадра намеренно: края
# уходят за границы, и колесо читается как продолжение за экраном,
# а не как значок посередине.
WHEEL_SIZE = 1320
WHEEL_CY = 694          # совпадает с _card_center(): карта в центре расчёта


def _background(palette: str = "choose", seed: int = 0) -> Image.Image:
    """Тёмный фон со свечением, колесом карты дня и звёздной россыпью."""
    top, bottom = PALETTES.get(palette, PALETTES["choose"])
    small = Image.new("RGB", (W // 8, H // 8))
    px = small.load()
    cx, cy = small.width / 2, small.height / 2.6
    maxd = math.hypot(cx, cy)
    for y in range(small.height):
        for x in range(small.width):
            t = min(1.0, (math.hypot(x - cx, y - cy) / maxd) ** 1.1)
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    img = small.resize((W, H), Image.LANCZOS).convert("RGBA")
    img.alpha_composite(wheel_bg.cached(
        W, H, WHEEL_SIZE, W // 2, WHEEL_CY, GOLD, dt.date.today()))
    img = img.convert("RGB")
    _starfield(img, seed)
    _watermark(img, palette)
    return img


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        probe = f"{cur} {word}".strip()
        if draw.textlength(probe, font=font) <= max_width:
            cur = probe
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _draw_block(
    draw, text: str, font, y: int, fill, max_width: int = W - 160, spacing: int = 14
) -> int:
    """Рисует абзац по центру. Возвращает y под последней строкой."""
    for line in _wrap(draw, text, font, max_width):
        w = draw.textlength(line, font=font)
        draw.text(((W - w) / 2, y), line, font=font, fill=fill)
        y += font.size + spacing
    return y


def _block_height(draw, text: str, font, max_width: int = W - 160,
                  spacing: int = 14) -> int:
    """Высота абзаца без отрисовки — нужна, чтобы центрировать сцену."""
    return len(_wrap(draw, text, font, max_width)) * (font.size + spacing)


def _start_y(total_height: int) -> int:
    """Верх блока, чтобы содержимое село по центру безопасной зоны.

    Низ экрана и правый край закрывает интерфейс приложения, поэтому
    рабочая область — не весь кадр.
    """
    avail = H - SAFE_TOP - SAFE_BOTTOM
    return SAFE_TOP + max(0, (avail - total_height) // 2)


def _backs_row(width: int, count: int = 3) -> Image.Image:
    """Рубашки в ряд на прозрачном фоне — чтобы легли на фон сцены.

    Ширина карты всегда как в ряду из трёх, сколько бы их ни было: иначе
    одна рубашка растягивается на весь кадр и вылезает за нижний край.
    """
    back = cards_image._render_back()
    gap = int(width * 0.04)
    card_w = (width - gap * 2) // 3
    card_h = int(card_w * back.height / back.width)
    back = back.resize((card_w, card_h), Image.LANCZOS)

    row_w = card_w * count + gap * (count - 1)
    row = Image.new("RGBA", (row_w, card_h), (0, 0, 0, 0))
    for i in range(count):
        row.paste(back, (i * (card_w + gap), 0), back)
    return row


def _card_art(name: str) -> Image.Image:
    card = {c.name: c for c in tarot_service.DECK}[name]
    return cards_image._card_image(card.name, card.is_major, card.suit)


def _card_scan(name: str) -> Image.Image:
    """Скан карты в исходном разрешении — для макро.

    `_card_art` вписывает карту в 400×660: для обычных сцен этого хватает
    с запасом, а для крупного плана нет — при увеличении втрое в кадр
    попадает сотня пикселей. Скан вдвое подробнее, поэтому для фрагмента
    берём его напрямую. Если скана нет, работаем с тем, что есть.
    """
    card = {c.name: c for c in tarot_service.DECK}[name]
    slug = cards_image._file_slug(card.name, card.is_major, card.suit)
    for ext in ("jpg", "jpeg", "png", "webp"):
        path = cards_image.DECK_DIR / f"{slug}.{ext}"
        if path.exists():
            try:
                return Image.open(path).convert("RGBA")
            except Exception:
                break
    return _card_art(name)


# ---------- сцены ----------


def scene_title(title: str, subtitle: str, palette: str, seed: int,
                backs: int = 3, align_cards: bool = False) -> Image.Image:
    """Заставка: крупный заголовок, подпись и рубашки под ними.

    С align_cards рубашки ставятся ровно туда, откуда начнётся разъезд
    в следующей сцене. Без этого на стыке карты подпрыгивают — заставка
    располагает их по своему усмотрению, а разъезд по своему.
    """
    img = _background(palette, seed)
    d = ImageDraw.Draw(img)
    f_title, f_sub = _font(96), _font(46, False)

    if align_cards and backs:
        back = cards_image._render_back()
        slots = _slots(-1, backs)
        y_center = _card_center()
        row_h = slots[0][1]

        for i in sorted(slots):
            x, h = slots[i]
            w = round(h * _ASPECT)
            piece = back.resize((w, round(h)), Image.LANCZOS)
            img.paste(piece, (round(x - w / 2), round(y_center - h / 2)), piece)

        block = (_block_height(d, title, f_title, spacing=6)
                 + 30 + _block_height(d, subtitle, f_sub))
        y = max(SAFE_TOP, int(y_center - row_h / 2) - 40 - block)
        y = _draw_block(d, title, f_title, y, CREAM, spacing=6)
        _draw_block(d, subtitle, f_sub, y + 30, GOLD)
        return img

    row = _backs_row(W - 160, backs) if backs else None
    total = (_block_height(d, title, f_title, spacing=6)
             + 30 + _block_height(d, subtitle, f_sub)
             + (60 + row.height if row else 0))

    y = _start_y(total)
    y = _draw_block(d, title, f_title, y, CREAM, spacing=6)
    y = _draw_block(d, subtitle, f_sub, y + 30, GOLD)
    if row:
        img.paste(row, ((W - row.width) // 2, int(y + 60)), row)
    return img


CARD_HEIGHT = 780


def _card_layout(d=None, label="", title="", text="", card_h=None) -> tuple[int, int]:
    """Где начинается подпись и где стоит карта.

    Координаты постоянные и от длины текста не зависят. Раньше блок
    центрировался целиком, и карта уезжала вверх-вниз в зависимости от
    того, в две строки трактовка или в три. На стыке сцен это читалось
    как рывок. Теперь карта стоит намертво, а текст просто течёт вниз.
    """
    y_label = SAFE_TOP + 20
    y_card = y_label + _font(64).size + 40
    return y_label, y_card


def _card_center() -> int:
    """Вертикальный центр крупной карты — общий для всех сцен формата."""
    return _card_layout()[1] + CARD_HEIGHT // 2


def _fit_card(art: Image.Image) -> Image.Image:
    scale = CARD_HEIGHT / art.height
    return art.resize((int(art.width * scale), CARD_HEIGHT), Image.LANCZOS)


def scene_card(label: str, card_name: str, text: str, palette: str, seed: int,
               heading: str | None = None) -> Image.Image:
    """Карта крупно: подпись сверху, картинка, название и трактовка."""
    img = _background(palette, seed)
    d = ImageDraw.Draw(img)
    f_label, f_name, f_text = _font(64), _font(58), _font(46, False)

    card = _fit_card(_card_art(card_name))
    title = heading or card_name
    y_label, y_card = _card_layout(d, label, title, text, card.height)

    _draw_block(d, label, f_label, y_label, GOLD, spacing=0)
    img.paste(card, ((W - card.width) // 2, y_card), card)

    y = y_card + card.height + 50
    y = _draw_block(d, title, f_name, y, CREAM, spacing=8)
    _draw_block(d, text, f_text, y + 24, GOLD_LIGHT, spacing=12)
    return img


# Маленькие карты по бокам: достаточно крупные, чтобы читалась рубашка,
# и достаточно мелкие, чтобы не спорить с открытой картой.
SIDE_HEIGHT = 370
SIDE_STEP = 88          # насколько соседние боковые карты налезают друг на друга
OVERLAP = 66            # насколько крупная карта заходит на ближнюю боковую

_ASPECT = cards_image.CARD_W / cards_image.CARD_H


def _half(height: float) -> float:
    return height * _ASPECT / 2


def _slots(active: int, count: int = 3) -> dict[int, tuple[float, float]]:
    """Где стоит каждая карта, когда открыта карта под номером active.

    Открытая — крупная по центру. Уже открытые уходят влево, ещё закрытые
    остаются справа. По расположению сразу видно, какая по счёту открыта.

    Боковые ставим относительно края крупной карты, а не края кадра:
    только так открытая гарантированно налезает на соседей независимо
    от того, сколько их с каждой стороны.
    """
    if active < 0:                                   # исходный ряд рубашек
        # Ширина карты всегда как в ряду из трёх, сколько бы их ни было:
        # одиночная рубашка иначе раздувается на весь кадр.
        gap = int((W - 160) * 0.04)
        card_w = (W - 160 - gap * 2) // 3
        row_h = card_w / _ASPECT
        row_w = card_w * count + gap * (count - 1)
        left = (W - row_w) / 2
        return {i: (left + card_w * (i + 0.5) + gap * i, row_h)
                for i in range(count)}

    big, small = _half(CARD_HEIGHT), _half(SIDE_HEIGHT)
    # Центр ближней боковой карты: её край заходит под крупную на OVERLAP.
    near = W / 2 + big - OVERLAP + small

    out: dict[int, tuple[float, float]] = {}
    opened = [i for i in range(count) if i < active]
    closed = [i for i in range(count) if i > active]

    # Слева ближе к центру та, что открыли последней; справа — та, что
    # откроется следующей. Порядок карт слева направо остаётся 1, 2, 3.
    for k, i in enumerate(reversed(opened)):
        out[i] = (W - near - SIDE_STEP * k, float(SIDE_HEIGHT))
    for k, i in enumerate(closed):
        out[i] = (near + SIDE_STEP * k, float(SIDE_HEIGHT))
    out[active] = (W / 2, float(CARD_HEIGHT))
    return out


def _compose(base: Image.Image, arts: list[Image.Image], slots: dict,
             active: int, y_center: int, width_scale: float = 1.0) -> Image.Image:
    """Собирает кадр: боковые карты сзади, открытая поверх них."""
    frame = base.copy()
    # Порядок наложения — по размеру: чем карта крупнее, тем она ближе
    # к зрителю. Класть наверх «текущую» нельзя: в начале разъезда она
    # ещё маленькая и на мгновение перекрывала бы крупную соседку.
    # При равном размере вперёд выходит та, что ближе к центру.
    order = sorted(range(len(arts)),
                   key=lambda i: (slots[i][1], -abs(slots[i][0] - W / 2)))
    for i in order:
        x, h = slots[i]
        art = arts[i]
        w = max(2, int(art.width * h / art.height))
        if i == active and width_scale < 1.0:
            w = max(2, int(w * width_scale))
        piece = art.resize((w, int(h)), Image.LANCZOS)
        frame.paste(piece, (int(x - w / 2), int(y_center - h / 2)), piece)
    return frame


def flip_card(label: str, cards: list[str], active: int, text: str,
              palette: str, seed: int, heading: str | None = None,
              move_frames: int = 12, flip_frames: int = 18) -> list[Image.Image]:
    """Карта выезжает вперёд и переворачивается.

    Две фазы. Сначала карты разъезжаются по местам: открываемая растёт
    к центру, остальные уменьшаются и уходят на задний план. Потом
    открываемая переворачивается — рубашка сжимается к вертикальной
    полоске, из неё раскрывается лицо. Настоящего трёхмерного поворота
    тут нет и не нужно: глаз читает сжатие как вращение.
    """
    base = _background(palette, seed)
    d = ImageDraw.Draw(base)
    f_label = _font(64)

    back = cards_image._render_back()
    faces = [_card_art(name) for name in cards]
    # На боковых местах и во время переворота карта показана рубашкой.
    backs = [back] * len(cards)

    title = heading or cards[active]
    y_label, y_card = _card_layout(d, label, title, text, CARD_HEIGHT)
    y_center = _card_center()
    _draw_block(d, label, f_label, y_label, GOLD, spacing=0)

    n = len(cards)
    start = _slots(active - 1 if active > 0 else -1, n)
    finish = _slots(active, n)
    shown = [faces[i] if i < active else backs[i] for i in range(len(cards))]

    out: list[Image.Image] = []
    for k in range(move_frames):
        t = k / max(1, move_frames - 1)
        eased = t * t * (3 - 2 * t)                  # мягкий разгон и торможение
        slots = {i: (start[i][0] + (finish[i][0] - start[i][0]) * eased,
                     start[i][1] + (finish[i][1] - start[i][1]) * eased)
                 for i in start}
        out.append(_compose(base, shown, slots, active, y_center))

    for k in range(flip_frames):
        t = k / (flip_frames - 1)
        scale = abs(math.cos(math.pi * t))
        stage = list(shown)
        stage[active] = backs[active] if t < 0.5 else faces[active]
        out.append(_compose(base, stage, finish, active, y_center,
                            width_scale=scale))
    return out


# ---------- открытие фрагментом ----------
#
# Первый кадр решает вход, и это измерено: «Выбери одну из трёх» дал 75%
# прошедших вход, «Карта дня» — 26%. Разница не в монтаже, а в том, что
# один кадр зовёт участвовать, а второй объявляет тему.
#
# Отсюда два изменения сразу. Титульной карточки больше нет: ролик
# начинается вопросом. И начинается он не с общего плана, а с куска
# старинной гравюры во весь экран, от которого камера отъезжает.
#
# Побочная выгода важнее основной: в колоде 78 разных рисунков, значит
# 78 разных первых кадров. Однообразие ленты лечится само, без новых
# выдумок — достаточно каждый раз брать другую карту.

FRAGMENT_START = 2.6      # во сколько раз карта крупнее кадра в начале
# Отъезд обрывается, не доходя до целой карты. Первая версия доезжала
# до общего плана — и в кадре читалось «QUEEN of CUPS», то есть ролик
# показывал карту раньше, чем зритель её выбрал. Фрагмент должен
# остаться фрагментом: он дразнит, а не рассказывает.
FRAGMENT_END = 1.25
FRAGMENT_FOCUS = 0.42     # точка, на которую смотрим: чуть выше центра


def scene_fragment(card_name: str, question: str, palette: str, seed: int,
                   seconds: float, sub: str = "") -> list[Image.Image]:
    """Макро по рисунку карты с отъездом. Поверх — вопрос зрителю."""
    art = _card_scan(card_name)
    base = _background(palette, seed)
    total = max(2, round(seconds * FPS))

    h_from, h_to = H * FRAGMENT_START, H * FRAGMENT_END
    frames: list[Image.Image] = []
    for k in range(total):
        t = k / (total - 1)
        eased = t * t * (3 - 2 * t)                  # мягко тронулась, мягко встала
        # Ход по геометрической прогрессии: на глаз скорость наезда
        # тогда ровная, а при линейном — в начале рывок, в конце ползёт.
        h = h_from * (h_to / h_from) ** eased
        scale = h / art.height
        w = art.width * scale

        if w >= W and h >= H:
            # Карта крупнее кадра — вырезаем кусок и растягиваем.
            # Так дешевле: увеличивать рисунок вчетверо на каждый кадр
            # долго, а вырезать из него маленький прямоугольник — нет.
            bw, bh = W / scale, H / scale
            cx, cy = art.width / 2, art.height * FRAGMENT_FOCUS
            cy = min(max(cy, bh / 2), art.height - bh / 2)
            box = (round(cx - bw / 2), round(cy - bh / 2),
                   round(cx + bw / 2), round(cy + bh / 2))
            frame = art.crop(box).resize((W, H), Image.LANCZOS).convert("RGB")
        else:
            frame = base.copy()
            piece = art.resize((max(1, round(w)), max(1, round(h))),
                               Image.LANCZOS)
            frame.paste(piece, (round((W - w) / 2), round((H - h) / 2)),
                        piece if piece.mode == "RGBA" else None)

        _scrim(frame)
        d = ImageDraw.Draw(frame)
        f_q = _fit_font(d, question, W - 150, 104)
        y = _draw_block(d, question, f_q, SAFE_TOP + 40, CREAM, spacing=8)
        if sub:
            _draw_block(d, sub, _font(46, False), y + 26, GOLD)
        _watermark(frame, palette)
        frames.append(frame)
    return frames


def _scrim(img: Image.Image) -> None:
    """Затемнение сверху и снизу: текст поверх гравюры иначе не читается.

    Рисунки Уэйта светлые и пёстрые, кремовые буквы на них теряются.
    Плотность подобрана так, чтобы картинка осталась видна.
    """
    band = H // 2
    dark = Image.new("L", (1, band))
    px = dark.load()
    for y in range(band):
        px[0, y] = int(190 * (1 - y / band) ** 1.4)
    top = dark.resize((W, band))
    layer = Image.new("L", (W, H), 0)
    layer.paste(top, (0, 0))
    layer.paste(top.transpose(Image.FLIP_TOP_BOTTOM), (0, H - band))
    img.paste(Image.new("RGB", (W, H), (6, 3, 14)), (0, 0), layer)


def intro_cards(title: str, subtitle: str, palette: str, seed: int,
                seconds: float, count: int = 3,
                rise: float = 0.75) -> list[Image.Image]:
    """Заставка, в которой карты влетают в кадр снизу.

    Первый кадр решает всё: в ленте, где вокруг движется каждое видео,
    неподвижная картинка читается как «тут пусто», и палец идёт дальше.
    Поэтому движение начинается сразу, а не после текста.
    """
    base = _background(palette, seed)
    d = ImageDraw.Draw(base)
    f_title, f_sub = _font(96), _font(46, False)

    back = cards_image._render_back()
    slots = _slots(-1, count)
    y_center = _card_center()
    row_h = slots[0][1]

    block = (_block_height(d, title, f_title, spacing=6)
             + 30 + _block_height(d, subtitle, f_sub))
    y = max(SAFE_TOP, int(y_center - row_h / 2) - 40 - block)
    y = _draw_block(d, title, f_title, y, CREAM, spacing=6)
    _draw_block(d, subtitle, f_sub, y + 30, GOLD)

    total = max(2, round(seconds * FPS))
    rising = max(2, round(rise * FPS))
    # Смещение маленькое намеренно. Раньше карты влетали из-за
    # нижнего края, и нулевой кадр оставался пустым — а именно
    # он решает в ленте. Проверка на живых цифрах: пустой первый
    # кадр срезал среднее время просмотра вдвое.
    travel = row_h * 0.42

    # Размер у всех рубашек одинаковый, поэтому масштабируем один раз.
    card_w = round(row_h * _ASPECT)
    piece = back.resize((card_w, round(row_h)), Image.LANCZOS)

    out: list[Image.Image] = []
    for k in range(total):
        t = min(1.0, k / (rising - 1))
        eased = 1 - (1 - t) ** 3                     # быстро влетают, мягко встают
        offset = round((1 - eased) * travel)
        frame = base.copy()
        for i in sorted(slots):
            x, _ = slots[i]
            frame.paste(piece, (round(x - card_w / 2),
                                round(y_center - row_h / 2) + offset), piece)
        out.append(frame)
        if offset == 0 and k >= rising:
            # Карты уже на месте: остаток сцены — тот же кадр.
            out.extend([frame] * (total - k - 1))
            break
    return out


def scene_stage(label: str, cards: list[str], active: int, text: str,
                palette: str, seed: int, heading: str | None = None) -> Image.Image:
    """Открытая карта крупно, соседи по бокам, под ней название и трактовка."""
    img = _background(palette, seed)
    d = ImageDraw.Draw(img)
    f_label, f_name, f_text = _font(64), _font(58), _font(46, False)

    back = cards_image._render_back()
    arts = [_card_art(name) if i <= active else back
            for i, name in enumerate(cards)]

    title = heading or cards[active]
    y_label, y_card = _card_layout(d, label, title, text, CARD_HEIGHT)
    y_center = _card_center()

    _draw_block(d, label, f_label, y_label, GOLD, spacing=0)
    img = _compose(img, arts, _slots(active, len(cards)), active, y_center)

    d = ImageDraw.Draw(img)
    y = y_card + CARD_HEIGHT + 50
    y = _draw_block(d, title, f_name, y, CREAM, spacing=8)
    _draw_block(d, text, f_text, y + 24, GOLD_LIGHT, spacing=12)
    return img


def _fit_font(d, text: str, max_width: int, size: int,
              floor: int = 96) -> ImageFont.FreeTypeFont:
    """Самый крупный кегль, при котором слово ещё влезает в строку.

    Нужно для знаков зодиака: «Скорпион» и «Близнецы» на 150-м кегле
    шире холста и обрезались краем кадра, а с наездом теряли ещё по
    58 пикселей с каждой стороны.
    """
    while size > floor:
        font = _font(size)
        bb = d.textbbox((0, 0), text, font=font)
        if bb[2] - bb[0] <= max_width:
            return font
        size -= 6
    return _font(floor)


def scene_word(word: str, text: str, palette: str, seed: int,
               caption: str = "") -> Image.Image:
    """Одно слово во весь кадр: знак зодиака, ДА/НЕТ, цифра судьбы."""
    img = _background(palette, seed)
    d = ImageDraw.Draw(img)
    f_cap, f_text = _font(44, False), _font(50, False)
    # В форматах с наездом до слова добирается не весь холст: на пике
    # масштаба по 58 пикселей с каждой стороны уходит за кадр.
    limit = int(W / ZOOM_MAX) if ZOOM.get(palette, False) else W
    f_word = _fit_font(d, word, limit - 160, 150)

    total = ((f_cap.size + 24 if caption else 0)
             + _block_height(d, word, f_word, spacing=0)
             + 40 + _block_height(d, text, f_text, spacing=14))

    y = _start_y(total)
    if caption:
        y = _draw_block(d, caption, f_cap, y, GOLD_LIGHT, spacing=0) + 24
    y = _draw_block(d, word, f_word, y, CREAM, spacing=0)
    _draw_block(d, text, f_text, y + 40, GOLD, spacing=14)
    return img


def scene_steps(title: str, steps: list[str], palette: str, seed: int,
                note: str = "") -> Image.Image:
    """Пошаговый расчёт — для нумерологии: как из даты получить число."""
    img = _background(palette, seed)
    d = ImageDraw.Draw(img)
    f_title, f_step = _font(60), _font(66)

    total = (_block_height(d, title, f_title, spacing=10) + 50
             + len(steps) * (f_step.size + 34))

    y = _start_y(total)
    y = _draw_block(d, title, f_title, y, CREAM, spacing=10) + 50
    for i, step in enumerate(steps):
        color = GOLD if i < len(steps) - 1 else GOLD_LIGHT
        y = _draw_block(d, step, f_step, y, color, spacing=34)
    if note:
        f_note = _font(38, False)
        _draw_block(d, note, f_note, y + 46, GOLD_LIGHT, spacing=10)
    return img

def scene_cta(bot: str, line: str, sub: str, palette: str, seed: int) -> Image.Image:
    img = _background(palette, seed)
    d = ImageDraw.Draw(img)
    f_line, f_sub, f_bot = _font(64), _font(44, False), _font(56)

    total = (_block_height(d, line, f_line) + 60
             + _block_height(d, sub, f_sub) + 40 + f_bot.size)

    y = _start_y(total)
    y = _draw_block(d, line, f_line, y, CREAM)
    y = _draw_block(d, sub, f_sub, y + 60, GOLD_LIGHT)
    _draw_block(d, bot, f_bot, y + 40, GOLD)
    return img


# ---------- сборка ----------


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Настоящая причина всегда в последних строках stderr.
        raise RuntimeError("ffmpeg не справился:\n" + result.stderr.strip()[-1200:])


ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
          "-pix_fmt", "yuv420p", "-r", str(FPS)]


ZOOM_MAX = 1.12


def _zoom_filter(z_from: float, z_to: float, total: int) -> str:
    """Наезд, заданный явно от и до, а не приращением на кадр.

    zoompan умеет копить масштаб сам, но тогда сцена всегда начинается
    с единицы, и на стыке картинка скачет. С явными границами соседние
    сцены стыкуются: одна заканчивает там, где вторая начинает.
    """
    if abs(z_from - 1.0) < 1e-6 and abs(z_to - 1.0) < 1e-6:
        return "setsar=1"
    span = max(1, total - 1)
    z = f"{z_from:.5f}+({z_to:.5f}-{z_from:.5f})*on/{span}"
    return (f"scale={W*2}:{H*2},"
            f"zoompan=z='{z}':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
            f"setsar=1")


def _segment(content: Image.Image | list[Image.Image], seconds: float,
             index: int, tmp: Path, z_from: float = 1.0,
             z_to: float = 1.0) -> Path:
    """Кусок ролика: неподвижная сцена или последовательность кадров.

    Наезд применяется одинаково к обеим, поэтому сцена с переворотом
    встраивается в общую цепочку масштаба наравне с остальными.
    """
    total = max(2, round(seconds * FPS))
    out = tmp / f"seg_{index:02d}.mp4"
    vf = _zoom_filter(z_from, z_to, total)

    if isinstance(content, list):
        folder = tmp / f"frames_{index:02d}"
        folder.mkdir()
        for i, frame in enumerate(content):
            # JPEG, а не PNG: сжатие без потерь тут ни к чему — впереди
            # ещё кодирование видео, — а пишется он в разы быстрее.
            frame.convert("RGB").save(folder / f"{i:04d}.jpg", quality=95)
        rate = len(content) / seconds
        _run(["ffmpeg", "-y", "-v", "error",
              "-framerate", f"{rate:.4f}", "-i", str(folder / "%04d.jpg"),
              "-vf", f"fps={FPS},{vf}", *ENCODE, str(out)])
        return out

    src = tmp / f"still_{index:02d}.png"
    content.save(src)
    _run(["ffmpeg", "-y", "-v", "error",
          "-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}",
          "-i", str(src), "-vf", vf, *ENCODE, str(out)])
    return out


# Своя музыка, синтезированная tools/music.py. Права наши целиком:
# трендовый звук из приложения снимает права на ролик и закрывает платное
# продвижение, а часть бесплатных библиотек зарегистрирована в YouTube
# Content ID — заявка прилетает даже на бесплатный трек.
MUSIC = (Path(__file__).resolve().parent.parent
         / "Астра ролики" / "музыка" / "астра_рассвет.wav")

# Поправка громкости — именно поправка, а не целевой уровень. Трек уже
# нормализован в tools/music.py на -20 дБ RMS; первая версия применяла
# здесь -19 дБ поверх, и в ролике получилось -39 дБ — звук был, но его
# не было слышно. Проверять надо готовый файл через volumedetect,
# а не считать, что задал уровень.
MUSIC_DB = 0.0
MUSIC_START = 0.0     # с какой секунды трека начинать
FADE = 1.2            # затухание в конце, иначе обрыв слышен как щелчок


def _add_music(video: Path, track: Path, seconds: float) -> None:
    """Подмешивает звук в готовый ролик.

    Трек длиннее ролика и зациклен, поэтому просто обрезаем по видео.
    Затухание в конце обязательно: без него последняя нота обрывается
    на полуслове, и это слышно даже в ленте.
    """
    tmp = video.with_suffix(".muxed.mp4")
    fade_start = max(0.0, seconds - FADE)
    trim0 = MUSIC_START
    trim1 = MUSIC_START + seconds
    # -stream_loop -1 повторяет трек столько раз, сколько нужно. Без
    # него «-shortest» резал видео по длине музыки: трек 24,5 секунды,
    # ролик 25,9 — и полторы секунды с призывом в канал молча пропадали.
    # Трек для того и собирается с бесшовной склейкой, чтобы повтор
    # не был слышен.
    _run(["ffmpeg", "-y", "-v", "error",
          "-i", str(video), "-stream_loop", "-1", "-i", str(track),
          "-filter_complex",
          f"[1:a]atrim={trim0:.3f}:{trim1:.3f},asetpts=PTS-STARTPTS,"
          f"afade=t=out:st={fade_start:.3f}:d={FADE},"
          f"volume={MUSIC_DB}dB[a]",
          "-map", "0:v", "-map", "[a]",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
          str(tmp)])
    tmp.replace(video)


def build(scenes: list[tuple[Image.Image | list[Image.Image], float]],
          out: Path, zoom: bool = False, music: Path | None = MUSIC) -> None:
    """Собирает ролик из сцен.

    Сцена — либо один кадр, либо список кадров с движением. Каждая
    кодируется отдельным куском, потом куски склеиваются: так проще, чем
    городить один огромный фильтр, и ошибку видно сразу в нужном месте.

    Наезд, если включён, идёт непрерывной цепочкой через весь ролик:
    каждая сцена начинает с того масштаба, которым закончила предыдущая,
    и направление чередуется. Без этого на стыках картинка скачет.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []
        z = 1.0

        for i, (content, seconds) in enumerate(scenes):
            z_to = z
            if zoom:
                z_to = ZOOM_MAX if z < (1.0 + ZOOM_MAX) / 2 else 1.0
            parts.append(_segment(content, seconds, i, tmp_path, z, z_to))
            z = z_to

        listing = tmp_path / "parts.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(listing), "-c", "copy", str(out)])

    if music and music.exists():
        _add_music(out, music, sum(sec for _, sec in scenes))
    elif music:
        print(f"музыки нет по пути {music} — собрал без звука")


# ---------- форматы ----------


def _cta_scenes(spec: dict, palette: str) -> list[tuple[Image.Image, float]]:
    line, sub = spec["cta"], spec["cta_sub"]
    return [(scene_cta(spec["bot"], line, sub, palette, SEEDS[palette]),
             _seconds(line, sub, spec["bot"], base=1.6))]


FLIP_SECONDS = 1.1


def reel_choose(spec: dict) -> list[tuple[Image.Image, float]]:
    """Выбери одну из трёх карт: зритель ждёт свою и досматривает до конца."""
    p = "choose"
    scenes = [
        (scene_title(spec["hook"], spec["subtitle"], p, SEEDS[p], align_cards=True),
         _seconds(spec["hook"], spec["subtitle"], base=1.2)),
        (scene_title(spec["hook2"], spec["subtitle2"], p, SEEDS[p], align_cards=True),
         _seconds(spec["hook2"], spec["subtitle2"], base=1.0)),
    ]
    names = [item["card"] for item in spec["cards"]]
    for i, item in enumerate(spec["cards"]):
        label = f"Карта {i + 1}"
        # Сначала карты разъезжаются и открываемая переворачивается,
        # потом та же расстановка замирает с трактовкой. Между сценами
        # ничего не двигается, поэтому стык не виден.
        scenes.append((
            flip_card(label, names, i, item["text"], p, SEEDS[p]),
            FLIP_SECONDS,
        ))
        scenes.append((
            scene_stage(label, names, i, item["text"], p, SEEDS[p]),
            _seconds(item["card"], item["text"], base=2.0),
        ))
    return scenes + _cta_scenes(spec, p)


INTRO_SECONDS = 2.4
FAST_FLIP_SECONDS = 0.8


def reel_choose_fast(spec: dict) -> list[tuple[Image.Image, float]]:
    """«Выбери карту», перекроенный под то, как ролик смотрят на самом деле.

    В первой версии до первой карты проходило 6,6 секунды: две заставки
    подряд, и ни одного движения. Статистика показала, что средний зритель
    уходит ровно на первом перевороте — то есть честно ждёт и не дожидается.

    Здесь одна заставка вместо двух, карты влетают в кадр с первого кадра,
    первая открывается на третьей секунде. Ролик короче на треть: TikTok
    считает долю досмотра, и та же начинка в двадцати секундах даёт цифру
    заметно выше, чем в тридцати двух.
    """
    p = "choose"
    names = [item["card"] for item in spec["cards"]]
    scenes: list[tuple[Image.Image | list[Image.Image], float]] = [
        (intro_cards(spec["hook"], spec["subtitle"], p, SEEDS[p], INTRO_SECONDS),
         INTRO_SECONDS),
    ]
    for i, item in enumerate(spec["cards"]):
        label = f"Карта {i + 1}"
        scenes.append((
            flip_card(label, names, i, item["text"], p, SEEDS[p],
                      move_frames=8, flip_frames=14),
            FAST_FLIP_SECONDS,
        ))
        scenes.append((
            scene_stage(label, names, i, item["text"], p, SEEDS[p]),
            _seconds(item["text"], base=0.9),
        ))

    line, sub = spec["cta"], spec["cta_sub"]
    scenes.append((scene_cta(spec["bot"], line, sub, p, SEEDS[p]),
                   _seconds(line, sub, spec["bot"], base=0.9)))
    return scenes


FRAGMENT_SECONDS = 2.6


def reel_choose_open(spec: dict) -> list[tuple[Image.Image, float]]:
    """«Выбери карту», открытый фрагментом вместо титульной карточки.

    Отличается от choose_fast ровно одним: вместо заставки с названием
    ролик начинается макро по рисунку одной из трёх карт и вопросом
    зрителю. Остальное — та же начинка, чтобы разницу можно было
    приписать входу, а не переделке целиком.
    """
    p = "choose"
    names = [item["card"] for item in spec["cards"]]
    # Фрагмент берём с последней карты: первая откроется через пару
    # секунд, и показать её кусок заранее — значит проговориться.
    scenes: list[tuple[Image.Image | list[Image.Image], float]] = [
        (scene_fragment(names[-1], spec["hook"], p, SEEDS[p],
                        FRAGMENT_SECONDS, spec.get("subtitle", "")),
         FRAGMENT_SECONDS),
    ]
    for i, item in enumerate(spec["cards"]):
        label = f"Карта {i + 1}"
        scenes.append((
            flip_card(label, names, i, item["text"], p, SEEDS[p],
                      move_frames=8, flip_frames=14),
            FAST_FLIP_SECONDS,
        ))
        scenes.append((
            scene_stage(label, names, i, item["text"], p, SEEDS[p]),
            _seconds(item["text"], base=0.9),
        ))
    line, sub = spec["cta"], spec["cta_sub"]
    scenes.append((scene_cta(spec["bot"], line, sub, p, SEEDS[p]),
                   _seconds(line, sub, spec["bot"], base=0.9)))
    return scenes


def reel_branch(spec: dict) -> list[tuple[Image.Image, float]]:
    """«Если да / если нет / что делать» — расклад на развилку.

    Отличается от «выбери карту» по устройству: зритель ничего не
    выбирает. Он держит в голове свой вопрос и смотрит все три карты
    подряд — что будет, если согласиться; что будет, если отказаться;
    и что с этим делать. Участие заменяется узнаванием.

    Открытие сделано как в choose_fast, а не как в старом yesno: там
    было две статичные заставки подряд, и по цифрам yesno оказался
    худшим форматом из всех — вход 34% против 75% у «выбери карту».
    Карты влетают с первого кадра, вопрос стоит поверх них.
    """
    p = "yesno"
    names = [item["card"] for item in spec["cards"]]
    scenes: list[tuple[Image.Image | list[Image.Image], float]] = [
        (intro_cards(spec["hook"], spec["subtitle"], p, SEEDS[p],
                     INTRO_SECONDS),
         INTRO_SECONDS),
    ]
    for i, item in enumerate(spec["cards"]):
        label = item["label"]
        scenes.append((
            flip_card(label, names, i, item["text"], p, SEEDS[p],
                      heading=item["heading"], move_frames=8, flip_frames=14),
            FAST_FLIP_SECONDS,
        ))
        scenes.append((
            scene_stage(label, names, i, item["text"], p, SEEDS[p],
                        heading=item["heading"]),
            _seconds(item["text"], base=1.6),
        ))
    line, sub = spec["cta"], spec["cta_sub"]
    scenes.append((scene_cta(spec["bot"], line, sub, p, SEEDS[p]),
                   _seconds(line, sub, spec["bot"], base=1.4)))
    return scenes


def reel_count(spec: dict) -> list[tuple[Image.Image, float]]:
    """Да или нет по счёту карт — так это делают на самом деле.

    Почему не «если да / если нет». Тот расклад мы попробовали и убрали:
    он вообще не отвечает на вопрос, а разворачивает обе ветки развилки,
    работает только когда решение в руках спрашивающего, и вдобавок
    оказался выдумкой XXI века — ни у Уэйта, ни у Золотой Зари, ни
    у Иден Грей его нет.

    А подсчёт — настоящая практика. У каждой карты своя полярность,
    большинство решает. Единого списка карт в мире нет (у разных школ
    Смерть то «нет», то «может быть»), но устойчивое ядро сходится
    везде: Солнце, Звезда, Мир, тузы — да; Башня, Дьявол, мечи — нет.
    Берём только из ядра.

    Драматургия в бегущем счёте: 1:0, потом 1:1, и третья карта решает.
    Впервые у ролика появляется причина досмотреть — не «где моя карта»,
    а «чем кончится». И мы ничего не обещаем: показываем метод, а не
    предсказываем событие человеку, которого не знаем.
    """
    p = "yesno"
    names = [item["card"] for item in spec["cards"]]
    scenes: list[tuple[Image.Image | list[Image.Image], float]] = [
        (intro_cards(spec["hook"], spec["subtitle"], p, SEEDS[p],
                     INTRO_SECONDS),
         INTRO_SECONDS),
    ]
    # Счёт обязан догонять карту, а не бежать впереди. Пока карта
    # переворачивается, наверху ещё прежний счёт; новый появляется
    # вместе с открытой картой. Иначе ролик проговаривается: зритель
    # видит «2 : 1» раньше, чем узнал третью карту.
    yes = no = 0
    for i, item in enumerate(spec["cards"]):
        before = f"{yes} : {no}" if i else ""
        if item["verdict"] == "ДА":
            yes += 1
        else:
            no += 1
        after = f"{yes} : {no}"
        scenes.append((
            flip_card(before, names, i, item["text"], p, SEEDS[p],
                      move_frames=8, flip_frames=14),
            FAST_FLIP_SECONDS,
        ))
        scenes.append((
            scene_stage(after, names, i, item["text"], p, SEEDS[p],
                        heading=item["verdict"]),
            _seconds(item["text"], base=1.5),
        ))

    scenes.append((
        scene_word(spec["answer"], spec["answer_text"], p, SEEDS[p],
                   caption=spec["answer_caption"]),
        _seconds(spec["answer_text"], base=1.8),
    ))
    line, sub = spec["cta"], spec["cta_sub"]
    scenes.append((scene_cta(spec["bot"], line, sub, p, SEEDS[p]),
                   _seconds(line, sub, spec["bot"], base=1.4)))
    return scenes


def reel_day(spec: dict) -> list[tuple[Image.Image, float]]:
    """Карта дня: одна карта, короткая трактовка. Быстрее всех в сборке."""
    p = "day"
    card, label, text = spec["card"], spec["label"], spec["text"]
    return [
        (scene_title(spec["hook"], spec["subtitle"], p, SEEDS[p], backs=0),
         _seconds(spec["hook"], spec["subtitle"], base=1.2)),
        (flip_card(label, [card], 0, text, p, SEEDS[p]), FLIP_SECONDS),
        (scene_stage(label, [card], 0, text, p, SEEDS[p]),
         _seconds(card, text, base=2.2)),
        (scene_word(spec["advice_word"], spec["advice"], p, SEEDS[p],
                    caption="Совет дня"),
         _seconds(spec["advice"], base=1.8)),
    ] + _cta_scenes(spec, p)


def reel_signs(spec: dict) -> list[tuple[Image.Image, float]]:
    """Три знака зодиака: люди ищут свой и досматривают ради него."""
    p = "signs"
    scenes = [
        (scene_title(spec["hook"], spec["subtitle"], p, SEEDS[p], backs=0),
         _seconds(spec["hook"], spec["subtitle"], base=1.4)),
    ]
    for i, item in enumerate(spec["signs"], 1):
        scenes.append((
            scene_word(item["sign"], item["text"], p, SEEDS[p], caption=f"{i} из 3"),
            _seconds(item["text"], base=1.8),
        ))
    return scenes + _cta_scenes(spec, p)


def reel_yesno(spec: dict) -> list[tuple[Image.Image, float]]:
    """Загадай вопрос: под такими роликами больше всего комментариев.

    Устроен как «выбери карту»: три рубашки разъезжаются, открываемая
    выходит вперёд и переворачивается. Отличается только тем, что вместо
    названия карты крупно стоит ответ — да, нет или почти.
    """
    p = "yesno"
    scenes = [
        (scene_title(spec["hook"], spec["subtitle"], p, SEEDS[p],
                     align_cards=True),
         _seconds(spec["hook"], spec["subtitle"], base=1.2)),
        (scene_title(spec["hook2"], spec["subtitle2"], p, SEEDS[p],
                     align_cards=True),
         _seconds(spec["hook2"], spec["subtitle2"], base=1.0)),
    ]
    names = [item["card"] for item in spec["cards"]]
    for i, item in enumerate(spec["cards"]):
        label = f"Карта {i + 1}"
        scenes.append((
            flip_card(label, names, i, item["text"], p, SEEDS[p],
                      heading=item["answer"]),
            FLIP_SECONDS,
        ))
        scenes.append((
            scene_stage(label, names, i, item["text"], p, SEEDS[p],
                        heading=item["answer"]),
            _seconds(item["answer"], item["text"], base=2.0),
        ))
    return scenes + _cta_scenes(spec, p)


def reel_number(spec: dict) -> list[tuple[Image.Image, float]]:
    """Число судьбы: сильнее прочих тянет в бота — там полный разбор."""
    p = "number"
    return [
        (scene_title(spec["hook"], spec["subtitle"], p, SEEDS[p], backs=0),
         _seconds(spec["hook"], spec["subtitle"], base=1.4)),
        (scene_steps(spec["example_title"], spec["steps"], p, SEEDS[p],
                     spec.get("note", "")),
         _seconds(*spec["steps"], base=2.4)),
        (scene_word(spec["number"], spec["text"], p, SEEDS[p],
                    caption="Получилось столько?"),
         _seconds(spec["text"], base=2.0)),
    ] + _cta_scenes(spec, p)


FORMATS = {
    "choose": reel_choose,
    "choose_fast": reel_choose_fast,
    "choose_open": reel_choose_open,
    "branch": reel_branch,
    "count": reel_count,
    "day": reel_day,
    "signs": reel_signs,
    "yesno": reel_yesno,
    "number": reel_number,
}


# ---------- заготовки текстов ----------

BOT = "@astra_tarot_ai_bot"
CHANNEL = "@astra_tarot_daily"

# Куда ролик ведёт, тем и подписан. Два разных адреса в одном ролике —
# подпись в углу одна, призыв в конце другая — заставляют выбирать,
# а выбор на этом шаге мы проигрываем. Одно имя, одна дверь.
MARK_BY_FORMAT = {"branch": CHANNEL, "count": CHANNEL}
MARK = BOT

DEFAULTS: dict[str, dict] = {
    "choose": {
        "hook": "ВЫБЕРИ КАРТУ",
        "subtitle": "Не думай. Первая, к которой потянуло.",
        "hook2": "ЗАПОМНИЛ НОМЕР?",
        "subtitle2": "Смотрим, что она говорит на эту неделю",
        "cards": [
            {"card": "Звезда",
             "text": "Станет легче, чем было. Не хватайся за новое."},
            {"card": "Семёрка Пентаклей",
             "text": "Результат будет, но не сейчас. Проверь, туда ли вкладываешься."},
            {"card": "Рыцарь Мечей",
             "text": "Скажешь то, что давно держал в себе. Выбери слова."},
        ],
        "bot": BOT,
        "cta": "Это общий расклад",
        "cta_sub": "Разбор по твоей дате рождения — в боте",
    },
    "day": {
        "hook": "КАРТА ДНЯ",
        "subtitle": "Что сегодня важнее всего",
        "label": "Сегодня",
        "card": "Колесо Фортуны",
        "text": "День поворота. То, что вчера не двигалось, сдвинется само.",
        "advice_word": "НЕ СПЕШИ",
        "advice": "Дай событиям день. Решение придёт к вечеру.",
        "bot": BOT,
        "cta": "Карта дня — каждое утро",
        "cta_sub": "Твоя личная, по дате рождения — в боте",
    },
    "signs": {
        "hook": "ТРЁМ ЗНАКАМ ПОВЕЗЁТ",
        "subtitle": "На этой неделе",
        "signs": [
            {"sign": "ТЕЛЕЦ",
             "text": "Деньги придут оттуда, откуда не ждал. Не отказывайся."},
            {"sign": "ДЕВА",
             "text": "Разговор, который ты откладывал, пройдёт легче, чем думал."},
            {"sign": "РЫБЫ",
             "text": "Старая история наконец закроется. Отпусти без сожалений."},
        ],
        "bot": BOT,
        "cta": "А что у твоего знака?",
        "cta_sub": "Прогноз по дате и месту рождения — в боте",
    },
    "yesno": {
        "hook": "ЗАГАДАЙ ВОПРОС",
        "subtitle": "Такой, где ответ — да или нет",
        "hook2": "ДЕРЖИ ЕГО В ГОЛОВЕ",
        "subtitle2": "И выбери одну из трёх карт",
        "cards": [
            {"card": "Солнце", "answer": "ДА",
             "text": "Ответ прямой. Сомнения не от ситуации, а от тебя."},
            {"card": "Восьмёрка Мечей", "answer": "НЕТ",
             "text": "Не сейчас. Условия изменятся — вернись к вопросу позже."},
            {"card": "Двойка Жезлов", "answer": "ПОЧТИ",
             "text": "Всё зависит от одного разговора. Начни его первым."},
        ],
        "bot": BOT,
        "cta": "Совпало?",
        "cta_sub": "Задай свой вопрос картам — в боте",
    },
    "number": {
        "hook": "ЧИСЛО СУДЬБЫ",
        "subtitle": "Сложи все цифры своей даты рождения",
        "example_title": "Например, 15 марта 1990:",
        "steps": [
            "1+5+0+3+1+9+9+0 = 28",
            "2+8 = 10",
            "1+0 = 1",
        ],
        "note": "Если вышло 11, 22 или 33 — дальше не складывай.\nЭто мастер-числа.",
        "number": "1",
        "text": "Ты идёшь первым и не умеешь ждать. Это и сила, и вся твоя усталость.",
        "bot": BOT,
        "cta": "Своё число посчитал?",
        "cta_sub": "Полный разбор по нему — в боте",
    },
}


def _fill_today(spec: dict, fmt: str) -> dict:
    """Карту дня берём ту же, что уходит в канал: ролик и пост совпадают."""
    if fmt != "day":
        return spec
    today = dt.date.today()
    drawn = tarot_service.draw(tarot_service.SPREADS["day"],
                               seed=today.toordinal())[0]
    spec = dict(spec)
    spec["card"] = drawn.card.name
    spec["label"] = profile.format_today_ru(today)
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", default="choose", choices=sorted(FORMATS),
                        help="сценарий ролика")
    parser.add_argument("--spec", type=Path, help="JSON с текстами")
    parser.add_argument("--out", type=Path, default=Path("reel.mp4"))
    parser.add_argument("--today", action="store_true",
                        help="для карты дня подставить сегодняшнюю карту")
    parser.add_argument("--mark", choices=["bot", "channel"],
                        help="что в углу и куда ведём; по умолчанию — как у формата")
    parser.add_argument("--music", type=Path,
                        help="файл музыки; по умолчанию — синтез астра_рассвет")
    parser.add_argument("--music-db", type=float,
                        help="поправка громкости музыки в дБ")
    parser.add_argument("--music-start", type=float,
                        help="с какой секунды трека начинать")
    args = parser.parse_args()

    spec = dict(DEFAULTS[args.format])
    if args.spec:
        spec.update(json.loads(args.spec.read_text()))
    if args.today:
        spec = _fill_today(spec, args.format)

    # Подпись выставляем до сборки сцен: она рисуется на каждом кадре
    # в момент отрисовки, и менять её потом уже поздно.
    global MARK
    MARK = MARK_BY_FORMAT.get(args.format, BOT)
    if args.mark:
        MARK = CHANNEL if args.mark == "channel" else BOT

    if args.music_db is not None:
        global MUSIC_DB
        MUSIC_DB = args.music_db
    if args.music_start is not None:
        global MUSIC_START
        MUSIC_START = args.music_start
    track = args.music if args.music else MUSIC

    scenes = FORMATS[args.format](spec)
    build(scenes, args.out, zoom=ZOOM[args.format], music=track)

    total = sum(s for _, s in scenes)
    print(f"готово: {args.out}  ({total:.1f} с, {len(scenes)} сцен)")


# Быстрый вариант «выбери карту» берёт те же тексты, что обычный:
# отличается только раскладка сцен, а не содержание.
DEFAULTS["choose_fast"] = DEFAULTS["choose"]
DEFAULTS["choose_open"] = DEFAULTS["choose"]

DEFAULTS["count"] = {
    "hook": "Загадай вопрос: да или нет?",
    "subtitle": "Считаем по трём картам",
    "cards": [
        {"card": "Солнце", "verdict": "ДА",
         "text": "Ясность и открытая радость. Всё названо своими именами."},
        {"card": "Пятёрка Кубков", "verdict": "НЕТ",
         "text": "Сожаление о потерянном, за которым не видно оставшегося."},
        {"card": "Девятка Кубков", "verdict": "ДА",
         "text": "Довольство и исполненное желание. Хорошо здесь и сейчас."},
    ],
    "answer": "ДА",
    "answer_caption": "Ответ",
    "answer_text": "Два «да» против одного «нет». Так и считают: решает большинство.",
    "bot": CHANNEL,
    "cta": "Разбираем по карте каждый день",
    "cta_sub": "Канал в Телеграме",
}

DEFAULTS["branch"] = {
    "hook": "Загадай вопрос: да или нет?",
    "subtitle": "Три карты покажут обе дороги",
    "cards": [
        {"label": "", "heading": "ЕСЛИ ДА",
         "card": "Восьмёрка Кубков",
         "text": "Придётся уйти из привычного. Сначала это похоже на потерю."},
        {"label": "", "heading": "ЕСЛИ НЕТ",
         "card": "Четвёрка Кубков",
         "text": "Останется как есть. И через месяц это надоест ещё сильнее."},
        {"label": "", "heading": "ЧТО ДЕЛАТЬ",
         "card": "Ас Мечей",
         "text": "Назови вслух, чего боишься. Решение станет очевидным."},
    ],
    "bot": CHANNEL,
    "cta": "Разбираем по карте каждый день",
    "cta_sub": "Канал в Телеграме",
}



def reel_meaning(spec: dict) -> list:
    """Разбор одной карты: чего от неё ждут и что она значит на самом деле.

    Ведёт в канал, где по вторникам и субботам выходят разборы всех 78.
    Устроен как «карта дня», но подпись под финальным словом берётся из
    spec, а не прибита к «Совету дня»."""
    p = "day"
    card, label, text = spec["card"], spec["label"], spec["text"]
    return [
        (scene_title(spec["hook"], spec["subtitle"], p, SEEDS[p], backs=0),
         _seconds(spec["hook"], spec["subtitle"], base=1.2)),
        (flip_card(label, [card], 0, text, p, SEEDS[p]), FLIP_SECONDS),
        (scene_stage(label, [card], 0, text, p, SEEDS[p]),
         _seconds(card, text, base=2.4)),
        (scene_word(spec["word"], spec["word_text"], p, SEEDS[p],
                    caption=spec["word_caption"]),
         _seconds(spec["word_text"], base=2.0)),
    ] + _cta_scenes(spec, p)


FORMATS["meaning"] = reel_meaning
ZOOM["meaning"] = False
MARK_BY_FORMAT["meaning"] = CHANNEL
DEFAULTS["meaning"] = {
    "hook": "КАРТА, КОТОРОЙ БОЯТСЯ",
    "subtitle": "Её вытягивают — и бледнеют",
    "label": "",
    "card": "Смерть",
    "text": "Почти никогда не про смерть. Это конец того, что отжило: "
            "работа или связь, что давно держатся на одной привычке.",
    "word": "ПЕРЕМЕНА",
    "word_caption": "На самом деле",
    "word_text": "Дверь закрывается, чтобы открылась другая. Бойся не карты — "
                 "бойся стоять на месте.",
    "bot": CHANNEL,
    "cta": "Разбираю все 78 карт",
    "cta_sub": "По одной, каждую неделю — в канале",
}


if __name__ == "__main__":
    main()
