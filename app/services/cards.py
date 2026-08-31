"""Отрисовка карт Таро и сборка картинки расклада.

Почему рисуем сами, а не берём готовые сканы:
  • колода Райдера-Уэйта общественное достояние, но это 78 файлов в репозитории
    и чужая графика, никак не связанная с оформлением бота;
  • свои карты в том же стиле, что аватарка, работают на узнаваемость;
  • ничего не надо скачивать и хранить — рисуем на лету.

Если ты всё же захочешь настоящие сканы: положи их в assets/deck/ с именами
вида `mag.jpg`, `wands_03.jpg` — код подхватит их вместо нарисованных.
Соответствие имён смотри в функции `_file_slug`.

Названия карт на картинку не пишем: во-первых, они и так есть в подписи
к фото, во-вторых, кириллический шрифт есть не в каждом окружении, а римские
цифры и масти рисуются линиями и не зависят от шрифтов вовсе.
"""

from __future__ import annotations

import io
import logging
import math
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.tarot import MAJORS, RANKS, DrawnCard

log = logging.getLogger(__name__)

# ---------- палитра, та же что у аватарки ----------
GOLD = (232, 195, 106)
GOLD_LIGHT = (247, 228, 170)
GOLD_DEEP = (176, 136, 58)
CREAM = (252, 243, 219)
BG_TOP = (46, 29, 86)
BG_BOTTOM = (17, 9, 38)
PAGE_BG = (13, 7, 30)

# ---------- размеры ----------
CARD_W, CARD_H = 200, 330
GAP = 18
PADDING = 26
SS = 2                      # во сколько раз рисуем крупнее для сглаживания
MAX_CANVAS_PIXELS = 6_000_000

DECK_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "deck"

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

ROMAN = [
    "0", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI",
]


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)   # Pillow 10.1+
    except TypeError:
        return ImageFont.load_default()


# =====================================================================
# Элементы масти
# =====================================================================


def _star_points(
    cx: float, cy: float, r_out: float, r_in: float, rays: int, rot: float = 0.0
) -> list[tuple[float, float]]:
    """Точки многолучевой звезды — для медальона на рубашке и мелких звёзд."""
    return [
        (
            cx + (r_out if i % 2 == 0 else r_in) * math.sin(rot + i * math.pi / rays),
            cy - (r_out if i % 2 == 0 else r_in) * math.cos(rot + i * math.pi / rays),
        )
        for i in range(rays * 2)
    ]


def _draw_wand(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    """Жезл: посох с листом и почкой на конце."""
    w = max(2, int(r * 0.24))
    d.line([(cx, cy - r), (cx, cy + r)], fill=GOLD, width=w)
    # лист сбоку
    d.polygon([(cx, cy - r * 0.1), (cx + r * 0.55, cy - r * 0.5),
               (cx + r * 0.2, cy + r * 0.05)], fill=GOLD)
    # почка на верхушке
    d.ellipse([cx - r * 0.24, cy - r * 1.28, cx + r * 0.24, cy - r * 0.8],
              fill=GOLD)


def _draw_cup(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    """Кубок: чаша, ножка, основание."""
    w = max(3, int(r * 0.22))
    d.chord([cx - r * 0.85, cy - r * 0.95, cx + r * 0.85, cy + r * 0.45],
            start=0, end=180, outline=GOLD, width=w)
    d.line([(cx, cy + r * 0.42), (cx, cy + r * 0.9)], fill=GOLD, width=w)
    d.line([(cx - r * 0.55, cy + r * 0.95), (cx + r * 0.55, cy + r * 0.95)],
           fill=GOLD, width=max(3, int(r * 0.26)))


def _draw_sword(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    """Меч: клинок остриём вверх, гарда, рукоять."""
    w = max(3, int(r * 0.24))
    d.polygon([(cx, cy - r * 1.15), (cx - r * 0.3, cy - r * 0.55),
               (cx + r * 0.3, cy - r * 0.55)], fill=GOLD)
    d.line([(cx, cy - r * 0.6), (cx, cy + r * 0.95)], fill=GOLD, width=w)
    d.line([(cx - r * 0.7, cy + r * 0.5), (cx + r * 0.7, cy + r * 0.5)],
           fill=GOLD, width=w)
    d.ellipse([cx - r * 0.2, cy + r * 0.95, cx + r * 0.2, cy + r * 1.3],
              fill=GOLD)


def _draw_pentacle(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    """Пентакль: пятиконечная звезда в круге."""
    w = max(2, int(r * 0.16))
    d.ellipse([cx - r * 0.9, cy - r * 0.9, cx + r * 0.9, cy + r * 0.9],
              outline=GOLD, width=w)
    pts = []
    for i in range(5):
        a = -math.pi / 2 + i * 4 * math.pi / 5
        pts.append((cx + r * 0.7 * math.cos(a), cy + r * 0.7 * math.sin(a)))
    for i in range(5):
        d.line([pts[i], pts[(i + 1) % 5]], fill=GOLD, width=w)


_SUIT_DRAW = {
    "Жезлов": _draw_wand,
    "Кубков": _draw_cup,
    "Мечей": _draw_sword,
    "Пентаклей": _draw_pentacle,
}


def _draw_crown(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    """Корона для фигурных карт."""
    base = cy + r * 0.4
    pts = [(cx - r, base)]
    for i in range(3):
        x = cx - r + (i + 0.5) * (2 * r / 3)
        pts.append((x, cy - r * 0.6))
        pts.append((cx - r + (i + 1) * (2 * r / 3), base - r * 0.15))
    pts.append((cx + r, base))
    d.polygon(pts, fill=GOLD)


# =====================================================================
# Одна карта
# =====================================================================


def _gradient(size: tuple[int, int]) -> Image.Image:
    w, h = size
    grad = Image.new("RGB", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)
        )
    return grad.resize((w, h))


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255
    )
    return mask


def _file_slug(card_name: str, is_major: bool, suit: str | None) -> str:
    """Имя файла, если захочешь подложить настоящие сканы."""
    if is_major:
        return f"major_{MAJORS.index(card_name):02d}"
    rank = card_name.rsplit(" ", 1)[0]
    suit_key = {"Жезлов": "wands", "Кубков": "cups",
                "Мечей": "swords", "Пентаклей": "pentacles"}[suit or ""]
    return f"{suit_key}_{RANKS.index(rank) + 1:02d}"


def _load_real(slug: str, size: tuple[int, int]) -> Image.Image | None:
    """Настоящий скан карты, если он подложен в assets/deck.

    Возвращаем в том же виде, что и рисованные карты: RGBA со скруглёнными
    углами. Иначе сканы ведут себя иначе там, где картинку используют как
    собственную маску прозрачности, и раскладка падает.
    """
    for ext in ("jpg", "jpeg", "png", "webp"):
        path = DECK_DIR / f"{slug}.{ext}"
        if not path.exists():
            continue
        try:
            src = Image.open(path).convert("RGBA")
        except Exception:
            log.warning("не смог открыть %s", path)
            continue

        # Пропорции скана не трогаем: настоящие карты уже, чем наши
        # рисованные, и растягивание под общий размер сразу заметно.
        # Вписываем целиком и кладём по центру прозрачного холста.
        scale = min(size[0] / src.width, size[1] / src.height)
        fitted = src.resize((max(1, round(src.width * scale)),
                            max(1, round(src.height * scale))), Image.LANCZOS)
        # Углы только смягчаем. Рисованным картам скругление в 12 пунктов
        # идёт, а у скана оно въедается в напечатанную рамку и выглядит
        # неровным обрезом.
        fitted.putalpha(_rounded_mask(fitted.size, 5 * SS))

        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.paste(fitted, ((size[0] - fitted.width) // 2,
                              (size[1] - fitted.height) // 2), fitted)
        return canvas
    return None


def _render_card(card_name: str, is_major: bool, suit: str | None) -> Image.Image:
    """Рисует одну карту в увеличенном масштабе (без учёта переворота)."""
    w, h = CARD_W * SS, CARD_H * SS

    real = _load_real(_file_slug(card_name, is_major, suit), (w, h))
    if real is not None:
        return real

    img = _gradient((w, h))
    d = ImageDraw.Draw(img)

    pad = 9 * SS
    d.rounded_rectangle([pad, pad, w - pad, h - pad],
                        radius=12 * SS, outline=GOLD_DEEP, width=3 * SS)
    d.rounded_rectangle([pad + 5 * SS, pad + 5 * SS, w - pad - 5 * SS, h - pad - 5 * SS],
                        radius=9 * SS, outline=GOLD, width=1 * SS)

    cx, cy = w / 2, h / 2

    if is_major:
        # Старший аркан: сверху лучистая звезда, под ней римская цифра.
        # Композиция намеренно несимметричная по вертикали — тогда
        # перевёрнутая карта читается как перевёрнутая, а не как сбой вёрстки.
        star_y = cy - 58 * SS
        for i in range(16):
            a = i * math.pi / 8
            long_ray = i % 2 == 0
            r_out = (72 if long_ray else 52) * SS
            d.line([(cx + 20 * SS * math.sin(a), star_y - 20 * SS * math.cos(a)),
                    (cx + r_out * math.sin(a), star_y - r_out * math.cos(a))],
                   fill=GOLD_DEEP if long_ray else GOLD, width=2 * SS)
        pts = []
        for i in range(16):
            a = i * math.pi / 8
            rr = (30 if i % 2 == 0 else 11) * SS
            pts.append((cx + rr * math.sin(a), star_y - rr * math.cos(a)))
        d.polygon(pts, fill=GOLD_LIGHT)

        numeral = ROMAN[MAJORS.index(card_name)]
        font = _font(int(46 * SS if len(numeral) <= 4 else 34 * SS))
        box = d.textbbox((0, 0), numeral, font=font)
        d.text((cx - (box[2] - box[0]) / 2, cy + 58 * SS - box[1]),
               numeral, font=font, fill=CREAM)
        d.line([(cx - 46 * SS, cy + 42 * SS), (cx + 46 * SS, cy + 42 * SS)],
               fill=GOLD_DEEP, width=2 * SS)
    else:
        rank = card_name.rsplit(" ", 1)[0]
        index = RANKS.index(rank) + 1
        draw_suit = _SUIT_DRAW[suit or "Жезлов"]

        if index <= 10:
            # Числовые карты: столько знаков масти, сколько номинал.
            cols = 1 if index == 1 else (2 if index <= 6 else 3)
            rows = math.ceil(index / cols)
            r = 52 * SS if index == 1 else (26 * SS if index <= 6 else 19 * SS)
            step_x = (w - 66 * SS) / max(1, cols)
            step_y = min(66 * SS, (h - 110 * SS) / max(1, rows))
            drawn = 0
            for row in range(rows):
                in_row = min(cols, index - drawn)
                for col in range(in_row):
                    x = cx + (col - (in_row - 1) / 2) * step_x
                    y = cy + (row - (rows - 1) / 2) * step_y
                    draw_suit(d, x, y, r)
                    drawn += 1
        else:
            # Фигурные карты: корона и крупный знак масти.
            _draw_crown(d, cx, cy - 62 * SS, 30 * SS)
            draw_suit(d, cx, cy + 26 * SS, 44 * SS)
            marks = index - 10          # Паж 1 … Король 4
            for i in range(marks):
                x = cx + (i - (marks - 1) / 2) * 20 * SS
                d.ellipse([x - 4 * SS, h - 44 * SS, x + 4 * SS, h - 36 * SS],
                          fill=GOLD)

    img.putalpha(_rounded_mask((w, h), 12 * SS))
    return img


@lru_cache(maxsize=48)
def _card_cached(card_name: str, is_major: bool, suit: str | None) -> bytes:
    """Кэшируем готовую карту в сыром виде: перерисовывать одно и то же незачем."""
    img = _render_card(card_name, is_major, suit)
    return img.tobytes(), img.size, img.mode  # type: ignore[return-value]


def _card_image(card_name: str, is_major: bool, suit: str | None) -> Image.Image:
    raw, size, mode = _card_cached(card_name, is_major, suit)  # type: ignore[misc]
    return Image.frombytes(mode, size, raw)


# =====================================================================
# Раскладка
# =====================================================================


def _columns(count: int) -> int:
    if count <= 3:
        return count
    if count <= 6:
        return 3
    if count <= 8:
        return 4
    if count <= 10:
        return 5
    return 4          # 13 карт: 4+4+4+1


def _render_back() -> Image.Image:
    """Рубашка карты: симметричный узор, чтобы не было видно верха и низа."""
    w, h = CARD_W * SS, CARD_H * SS
    img = _gradient((w, h))
    d = ImageDraw.Draw(img)
    cx, cy = w / 2, h / 2

    pad = 9 * SS
    d.rounded_rectangle([pad, pad, w - pad, h - pad],
                        radius=12 * SS, outline=GOLD_DEEP, width=3 * SS)
    d.rounded_rectangle([pad + 5 * SS, pad + 5 * SS, w - pad - 5 * SS, h - pad - 5 * SS],
                        radius=9 * SS, outline=GOLD, width=1 * SS)

    # Ромбовидная сетка мелких звёзд по всему полю.
    step = 26 * SS
    for i in range(-6, 7):
        for j in range(-9, 10):
            x = cx + i * step + (step / 2 if j % 2 else 0)
            y = cy + j * step
            if not (pad + 14 * SS < x < w - pad - 14 * SS
                    and pad + 14 * SS < y < h - pad - 14 * SS):
                continue
            d.polygon(_star_points(x, y, 5 * SS, 1.6 * SS, 4, rot=0.4), fill=GOLD_DEEP)

    # Центральный медальон: круги и восьмиконечная звезда.
    for r, width in ((66 * SS, 3 * SS), (54 * SS, 1 * SS)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD_DEEP, width=width)
    for i in range(16):
        a = i * math.pi / 8
        d.line([(cx + 20 * SS * math.sin(a), cy - 20 * SS * math.cos(a)),
                (cx + 48 * SS * math.sin(a), cy - 48 * SS * math.cos(a))],
               fill=GOLD_DEEP, width=2 * SS)
    d.polygon(_star_points(cx, cy, 30 * SS, 9 * SS, 8, rot=math.pi / 8), fill=GOLD)
    d.polygon(_star_points(cx, cy, 14 * SS, 4 * SS, 4), fill=CREAM)

    img.putalpha(_rounded_mask((w, h), 12 * SS))
    return img


def render_backs(count: int = 3, numbered: bool = True) -> bytes | None:
    """Несколько карт рубашкой вверх — для постов «выбери карту»."""
    try:
        cell_w = (CARD_W + GAP) * SS
        cell_h = (CARD_H + GAP) * SS
        width = count * cell_w + PADDING * 2 * SS
        height = cell_h + PADDING * 2 * SS

        page = Image.new("RGB", (width, height), PAGE_BG)
        draw = ImageDraw.Draw(page)
        font = _font(20 * SS)
        back = _render_back()

        for index in range(count):
            x = PADDING * SS + index * cell_w + GAP * SS / 2
            y = PADDING * SS + GAP * SS / 2
            page.paste(back, (int(x), int(y)), back)

            if numbered:
                label = str(index + 1)
                bx = x + CARD_W * SS / 2 - 17 * SS
                by = y + CARD_H * SS - 52 * SS
                draw.ellipse([bx, by, bx + 34 * SS, by + 34 * SS],
                             fill=PAGE_BG, outline=GOLD, width=2 * SS)
                box = draw.textbbox((0, 0), label, font=font)
                draw.text(
                    (bx + 17 * SS - (box[2] - box[0]) / 2,
                     by + 17 * SS - (box[3] - box[1]) / 2 - box[1]),
                    label, font=font, fill=GOLD_LIGHT,
                )

        page = page.resize((width // SS, height // SS), Image.LANCZOS)
        buffer = io.BytesIO()
        page.save(buffer, format="JPEG", quality=90, optimize=True)
        return buffer.getvalue()
    except Exception:
        log.exception("не смог нарисовать рубашки")
        return None


def render_spread(cards: list[DrawnCard]) -> bytes | None:
    """Собирает одну картинку расклада. Возвращает JPEG или None при сбое."""
    if not cards:
        return None

    try:
        cols = _columns(len(cards))
        rows = math.ceil(len(cards) / cols)

        cell_w = (CARD_W + GAP) * SS
        cell_h = (CARD_H + GAP + 14) * SS
        width = cols * cell_w + PADDING * 2 * SS
        height = rows * cell_h + PADDING * 2 * SS

        if width * height > MAX_CANVAS_PIXELS * SS * SS:
            log.warning("расклад слишком большой для картинки: %s карт", len(cards))
            return None

        page = Image.new("RGB", (width, height), PAGE_BG)
        draw = ImageDraw.Draw(page)
        badge_font = _font(15 * SS)

        for index, item in enumerate(cards):
            row, col = divmod(index, cols)
            in_row = min(cols, len(cards) - row * cols)
            # Последний неполный ряд центрируем.
            offset = (cols - in_row) * cell_w / 2
            x = PADDING * SS + offset + col * cell_w + GAP * SS / 2
            y = PADDING * SS + row * cell_h + GAP * SS / 2

            card = _card_image(item.card.name, item.card.is_major, item.card.suit)
            if item.reversed:
                card = card.rotate(180)

            page.paste(card, (int(x), int(y)), card)

            # Номер позиции — по нему подпись под фото сходится с картинкой.
            label = str(index + 1)
            bx, by = x + 12 * SS, y + 12 * SS
            draw.ellipse([bx, by, bx + 26 * SS, by + 26 * SS],
                         fill=PAGE_BG, outline=GOLD, width=2 * SS)
            box = draw.textbbox((0, 0), label, font=badge_font)
            draw.text(
                (bx + 13 * SS - (box[2] - box[0]) / 2,
                 by + 13 * SS - (box[3] - box[1]) / 2 - box[1]),
                label, font=badge_font, fill=GOLD_LIGHT,
            )

            if item.reversed:
                draw.text((x + CARD_W * SS - 30 * SS, y + 10 * SS),
                          "R", font=badge_font, fill=GOLD_LIGHT)

        page = page.resize((width // SS, height // SS), Image.LANCZOS)

        buffer = io.BytesIO()
        page.save(buffer, format="JPEG", quality=88, optimize=True)
        return buffer.getvalue()
    except Exception:
        log.exception("не смог собрать картинку расклада")
        return None
