"""Колесо натальной карты — фон для роликов.

Зачем. Фоном во всех роликах была звёздная россыпь: красиво, но ничего
не значит. Астрологических каналов с россыпью — тысячи. А расчёт есть
только у нас: `app/services/sky.py` считает положения планет, асцендент
и дома с нуля, по алгоритмам Мееуса.

Поэтому фон теперь — настоящая карта на сегодняшний день. Не украшение:
если открыть эфемериды на дату ролика, точки сойдутся. Это единственное
наше отличие, которое видно с первого кадра и которое нельзя подделать
чужой библиотекой.

Рисуется один раз за сборку и кладётся в кэш: колесо обязано быть
одинаковым во всех сценах ролика. Если пересчитывать его на каждую
сцену, планеты будут дёргаться на стыках — той же болезнью мы уже
болели со звёздной россыпью.
"""
from __future__ import annotations

import datetime as dt
import math
from functools import lru_cache

from PIL import Image, ImageDraw

from app.services import sky

# Алматы: часовой пояс проекта, по нему же живёт канал.
LAT, LON = 43.238, 76.889

BODIES = ("Меркурий", "Венера", "Марс", "Юпитер", "Сатурн")

# Насколько ярко. Подобрано глазами на телефоне: колесо должно читаться
# как чертёж под картинкой, но не спорить с картой за внимание.
RING, LINE, DOT = 130, 80, 190

# Сглаживание: рисуем крупнее и уменьшаем. Прямая отрисовка тонких
# линий по кругу даёт рваный край, который на видео заметен сильнее,
# чем на картинке.
SUPERSAMPLE = 3

# Чем помечать планеты на круге:
#   "dot"   — точка с ореолом, как было
#   "glyph" — астрологический знак: ☉ ☽ ☿ ♀ ♂ ♃ ♄
#   "draw"  — рисунок золотой линией: кольца Сатурна, полосы Юпитера
#   "label" — точка и русская подпись мелким шрифтом
#
# Цветных планет намеренно нет. Красный Марс и полосатый Юпитер выбьются
# из золотой гаммы, по которой узнают канал, и потянут внимание на себя —
# а фон обязан оставаться фоном.
STYLE = "draw"

# Относительный размер кружка планеты. Юпитер крупнее всех, Меркурий
# мельче — как на школьной картинке Солнечной системы.
SCALE = {"Солнце": 1.25, "Луна": 1.0, "Меркурий": 0.62, "Венера": 0.85,
         "Марс": 0.72, "Юпитер": 1.35, "Сатурн": 1.1}

GLYPHS = {"Солнце": "\u2609", "Луна": "\u263D", "Меркурий": "\u263F",
          "Венера": "\u2640", "Марс": "\u2642", "Юпитер": "\u2643",
          "Сатурн": "\u2644"}


def positions(moment: dt.datetime) -> tuple[dict[str, float], list[float]]:
    """Долготы светил и куспиды домов на момент."""
    jd = sky.julian_day(moment)
    t = sky.centuries(jd)
    bodies = {"Солнце": sky.sun_longitude(t), "Луна": sky.moon_longitude(t)}
    for name in BODIES:
        bodies[name] = sky.planet_longitude(name, t)
    return bodies, sky.houses(jd, LAT, LON)


def draw(width: int, height: int, size: int, cx: int, cy: int,
         colour: tuple[int, int, int], day: dt.date | None = None,
         ring: int = RING, line: int = LINE, dot: int = DOT,
         style: str = STYLE) -> Image.Image:
    """Прозрачный слой с колесом. Края намеренно уходят за кадр."""
    day = day or dt.date.today()
    # Полдень: карта суточная, привязываться к минуте сборки незачем —
    # иначе два ролика одного дня получат чуть разные фоны.
    moment = dt.datetime.combine(day, dt.time(12), dt.timezone.utc)
    bodies, cusps = positions(moment)
    asc = cusps[0]

    ss = SUPERSAMPLE
    layer = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    CX, CY, R = cx * ss, cy * ss, size * ss // 2

    def circle(r: int, w: int, alpha: int) -> None:
        d.ellipse([CX - r, CY - r, CX + r, CY + r],
                  outline=colour + (alpha,), width=w * ss)

    def point(deg: float, r: float) -> tuple[float, float]:
        # Асцендент слева и счёт против часовой — как на настоящей карте.
        a = math.radians(180 - (deg - asc))
        return CX + r * math.cos(a), CY - r * math.sin(a)

    circle(R, 2, ring)
    circle(int(R * 0.86), 1, ring)
    circle(int(R * 0.60), 1, ring // 2)

    # Границы знаков.
    for i in range(12):
        deg = asc - (asc % 30) + i * 30
        d.line([point(deg, int(R * 0.86)), point(deg, R)],
               fill=colour + (line,), width=1 * ss)

    # Куспиды домов — внутрь, до центра.
    for cusp in cusps:
        d.line([point(cusp, 0), point(cusp, int(R * 0.86))],
               fill=colour + (line // 2,), width=1 * ss)

    # Деления по пять градусов: от них вид чертежа, а не орнамента.
    for k in range(72):
        deg = asc + k * 5
        inner = int(R * 0.93) if k % 6 else int(R * 0.88)
        d.line([point(deg, inner), point(deg, R)],
               fill=colour + (line,), width=1 * ss)

    # Планеты.
    for name, lon in bodies.items():
        x, y = point(lon, int(R * 0.73))
        _marker(d, style, name, x, y, ss, colour, dot, line)

    # Аспекты внутри малого круга.
    longitudes = list(bodies.values())
    for i in range(len(longitudes)):
        for j in range(i + 1, len(longitudes)):
            gap = abs(longitudes[i] - longitudes[j]) % 360
            gap = min(gap, 360 - gap)
            if any(abs(gap - exact) <= 6 for exact in (0, 60, 90, 120, 180)):
                d.line([point(longitudes[i], int(R * 0.60)),
                        point(longitudes[j], int(R * 0.60))],
                       fill=colour + (line,), width=1 * ss)

    return layer.resize((width, height), Image.LANCZOS)


@lru_cache(maxsize=8)
def cached(width: int, height: int, size: int, cx: int, cy: int,
           colour: tuple[int, int, int], day: dt.date,
           style: str = STYLE) -> Image.Image:
    """То же колесо, но считается один раз за сборку."""
    return draw(width, height, size, cx, cy, colour, day, style=style)


# ---------- метки планет ----------
#
# Планеты на фоне должны читаться, но не спорить с основным текстом.
# Отсюда монохром: всё тем же золотом, что и остальное колесо, разной
# только толщина линии и размер.

BASE_RADIUS = 11          # радиус кружка «средней» планеты, до масштаба


def _font(size: int):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _marker(d, style: str, name: str, x: float, y: float, ss: int,
            colour: tuple[int, int, int], dot: int, line: int) -> None:
    r = BASE_RADIUS * SCALE.get(name, 1.0) * ss

    if style == "glyph":
        f = _font(round(34 * ss))
        glyph = GLYPHS.get(name, "•")
        w = d.textlength(glyph, font=f)
        d.text((x - w / 2, y - 24 * ss), glyph, font=f, fill=colour + (dot,))
        return

    if style == "label":
        _disc(d, x, y, r, ss, colour, dot)
        f = _font(round(24 * ss))
        w = d.textlength(name, font=f)
        d.text((x - w / 2, y + r + 6 * ss), name, font=f,
               fill=colour + (max(60, dot - 70),))
        return

    if style == "draw":
        _drawn(d, name, x, y, r, ss, colour, dot, line)
        return

    _disc(d, x, y, r, ss, colour, dot)


def _disc(d, x, y, r, ss, colour, dot) -> None:
    """Точка с мягким ореолом — читается на любом фоне."""
    for radius, alpha in ((r * 1.8, dot // 4), (r, dot)):
        d.ellipse([x - radius, y - radius, x + radius, y + radius],
                  fill=colour + (alpha,))


def _drawn(d, name, x, y, r, ss, colour, dot, line) -> None:
    """Планета золотой линией: узнаётся по приметам, а не по цвету."""
    w = max(1, round(1.6 * ss))
    ring_a = colour + (dot,)
    faint = colour + (max(50, dot // 2),)

    def circle(rr, fill=None, outline=ring_a, width=w):
        d.ellipse([x - rr, y - rr, x + rr, y + rr],
                  fill=fill, outline=outline, width=width)

    if name == "Солнце":
        circle(r, fill=colour + (dot // 3,))
        d.ellipse([x - r * 0.22, y - r * 0.22, x + r * 0.22, y + r * 0.22],
                  fill=ring_a)
        for k in range(8):                       # лучи
            a = math.radians(k * 45)
            d.line([x + r * 1.35 * math.cos(a), y + r * 1.35 * math.sin(a),
                    x + r * 1.95 * math.cos(a), y + r * 1.95 * math.sin(a)],
                   fill=faint, width=w)
        return

    if name == "Луна":
        # Серп: круг, из которого вычтен сдвинутый круг.
        circle(r, fill=colour + (dot // 2,), outline=ring_a)
        d.ellipse([x - r * 0.55, y - r * 1.05, x + r * 1.45, y + r * 1.05],
                  fill=(0, 0, 0, 0), outline=None)
        d.pieslice([x - r, y - r, x + r, y + r], 300, 60,
                   fill=(0, 0, 0, 0), outline=None)
        return

    if name == "Юпитер":
        circle(r, fill=colour + (dot // 4,))
        for k in (-0.45, -0.12, 0.22, 0.55):     # полосы
            dy = r * k
            half = math.sqrt(max(0.0, r * r - dy * dy)) * 0.92
            d.line([x - half, y + dy, x + half, y + dy], fill=faint, width=w)
        # Большое красное пятно — рисуем как маленький овал.
        d.ellipse([x - r * 0.52, y + r * 0.02, x - r * 0.08, y + r * 0.34],
                  fill=ring_a)
        return

    if name == "Сатурн":
        circle(r, fill=colour + (dot // 4,))
        d.ellipse([x - r * 2.0, y - r * 0.62, x + r * 2.0, y + r * 0.62],
                  outline=ring_a, width=w)
        d.ellipse([x - r * 1.55, y - r * 0.46, x + r * 1.55, y + r * 0.46],
                  outline=faint, width=w)
        return

    if name == "Марс":
        circle(r, fill=colour + (dot // 3,))
        # Полярная шапка — единственная примета, читаемая в этом размере.
        d.pieslice([x - r, y - r, x + r, y + r], 205, 335, fill=ring_a)
        return

    if name == "Венера":
        circle(r, fill=colour + (dot // 3,))
        for k in (-0.3, 0.15):                   # облачные полосы
            dy = r * k
            half = math.sqrt(max(0.0, r * r - dy * dy)) * 0.9
            d.line([x - half, y + dy, x + half, y + dy], fill=faint, width=w)
        return

    circle(r, fill=colour + (dot // 3,))         # Меркурий и всё прочее
