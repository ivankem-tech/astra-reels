"""Кадр Астры на нашем фоне: фон с колесом, стол, наши рубашки в перспективе.

Первый кадр ролика нового формата. Астру вырезаем из фотографии вместе с руками,
сзади ставим наш фон с колесом зодиака, на стол кладём рубашки трапециями.

Запуск из корня репозитория:

    python3 tools/astra_frame.py --day 2026-09-18 --palette day --cards 1 \\
            --title "САМАЯ ЖУТКАЯ КАРТА" --out "Астра на фоне"

Делает три файла:
    для_Flow_<дата>_<карт>.png   без единой буквы — этот отдаём во Flow
    <дата>_<карт>_подпись.jpg    с названием и ником, посмотреть
    <дата>_без_астры.png         фон, стол и карты без фигуры — под растворение

Почему три. Flow перерисовывает любой текст, попавший в кадр (проверено
12 сентября: ник превратился в «@astra_tarot_ai_ai_bot»), поэтому оживляем
кадр без букв, а название и ник накладываем уже на готовый клип. Кадр без
Астры нужен, чтобы она растворилась, а фон остался стоять.

Дата колеса задаётся ключом --day и по умолчанию НЕ равна сегодняшней:
колесо считается на дату, и кадр, собранный в другой день, не совпал бы
с уже сделанным клипом из Flow.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import reel  # noqa: E402
from app.services import cards as cards_image  # noqa: E402

PHOTO = ROOT / "assets" / "astra" / "photo.jpg"

W, H = reel.W, reel.H          # 1080 × 1920
SCALE = 1.25                   # фигура чуть меньше кадра
TABLE_EDGE = 1090              # строка фотографии, где стол уходит за руки
PATCH_TOP = 920                # откуда берём кусок фотографии с плащом и руками
PATCH_FADE_IN = 40             # верх куска вводим плавно, чтобы не было шва
PATCH_BOTTOM = 1160            # руки кончаются на 1158

# Силуэт для grabCut. Внизу расширен до x=20 и x=690: расклешённый рукав
# раньше в него не попадал, и на светлой палитре был виден ровный срез.
POLY = [(355, 158), (300, 175), (255, 215), (225, 265), (210, 330), (200, 420),
        (185, 500), (160, 560), (140, 620), (120, 700), (95, 780), (72, 860),
        (48, 940), (26, 1010), (20, 1088),
        (690, 1088), (686, 1010), (678, 940), (645, 860), (622, 780), (600, 700),
        (578, 620), (558, 560), (530, 500), (515, 420), (505, 330), (492, 265),
        (462, 215), (415, 175)]

# Обвод плаща, снятый с фотографии: строка -> (левая граница, правая граница).
# По нему возвращаем кусок фотографии не прямоугольником, а по фигуре.
EDGE = [(920, 34, 690), (960, 26, 688), (1000, 20, 685), (1040, 18, 683),
        (1090, 22, 680), (1120, 32, 678), (1160, 40, 676)]

CARD_SPOTS = {0: [], 1: [W / 2], 3: [W / 2 - 268, W / 2, W / 2 + 268]}


def cutout(photo: Path):
    """Астра с руками на прозрачном фоне + полоса стола из чистого дерева."""
    img = cv2.imread(str(photo))
    if img is None:
        raise SystemExit(f"не нашёл фотографию: {photo}")
    h, w = img.shape[:2]

    inside = np.zeros((h, w), np.uint8)
    cv2.fillPoly(inside, [np.array(POLY, np.int32)], 255)
    core = cv2.erode(inside, np.ones((71, 71), np.uint8))
    outer = cv2.dilate(inside, np.ones((9, 9), np.uint8))
    mask = np.full((h, w), cv2.GC_BGD, np.uint8)
    mask[outer > 0] = cv2.GC_PR_BGD
    mask[inside > 0] = cv2.GC_PR_FGD
    mask[core > 0] = cv2.GC_FGD
    cv2.grabCut(img, mask, None, np.zeros((1, 65)), np.zeros((1, 65)), 8,
                cv2.GC_INIT_WITH_MASK)
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8), iterations=2)
    _, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    m = np.where(lab == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA]), 255, 0).astype(np.uint8)
    ff = m.copy()
    cv2.floodFill(ff, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 255)
    m = cv2.bitwise_or(m, cv2.bitwise_not(ff))

    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    alpha = cv2.GaussianBlur(m, (0, 0), 2.2).astype(np.float32)
    lum = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    rim = cv2.dilate(m, np.ones((9, 9), np.uint8)) - cv2.erode(m, np.ones((9, 9), np.uint8))
    alpha[rim > 0] *= np.clip(lum[rim > 0] * 3.0, 0, 1)   # тёмный контур уходит в прозрачность

    # кусок фотографии по обводу плаща: плащ, руки и настоящее дерево
    xs = np.tile(np.arange(w, dtype=np.float32), (h, 1))
    ys = np.arange(h)
    xl = np.interp(ys, [e[0] for e in EDGE], [e[1] for e in EDGE])
    xr = np.interp(ys, [e[0] for e in EDGE], [e[2] for e in EDGE])
    ramp = 12.0
    patch = (np.clip((xs - xl[:, None]) / ramp, 0, 1)
             * np.clip((xr[:, None] - xs) / ramp, 0, 1))
    rows = np.zeros((h, 1), np.float32)
    rows[PATCH_TOP:PATCH_BOTTOM] = 1.0
    for i in range(PATCH_FADE_IN):
        rows[PATCH_TOP + i] = i / PATCH_FADE_IN
    for i in range(18):
        rows[PATCH_BOTTOM - 1 - i] *= i / 18
    alpha = np.maximum(alpha, patch * rows * 255.0)

    fig = Image.fromarray(np.dstack([rgb, np.clip(alpha, 0, 255).astype(np.uint8)]))

    # стол: чистое дерево из середины, зеркально размноженное на всю ширину
    clean = img[TABLE_EDGE:, 260:440]
    row = np.concatenate([clean if i % 2 == 0 else clean[:, ::-1] for i in range(6)], axis=1)
    table = Image.fromarray(cv2.cvtColor(row, cv2.COLOR_BGR2RGB))
    return fig, table


def quad(cx: float, band_top: float, wide=235.0, tall=122.0, near_gap=96):
    """Карта лежит на столе: дальний край короче ближнего, обе ближе пальцев."""
    far_w, near_w = wide * 0.74, wide
    y_far = band_top + near_gap
    y_near = y_far + tall
    sh = (cx - W / 2) * 0.07
    return [(cx - far_w / 2 - sh * 0.5, y_far), (cx + far_w / 2 - sh * 0.5, y_far),
            (cx + near_w / 2 + sh * 0.5, y_near), (cx - near_w / 2 + sh * 0.5, y_near)]


def warp_card(card: Image.Image, q, dim_far=0.55):
    """Перспективная укладка рубашки плюс затемнение к дальнему краю."""
    c = card.convert("RGBA")
    cw, ch = c.size
    grad = Image.new("L", (1, ch))
    gd = ImageDraw.Draw(grad)
    for y in range(ch):
        gd.point((0, y), fill=int(255 * (dim_far + (1 - dim_far) * (y / ch) ** 0.8)))
    shade = grad.resize((cw, ch))
    rgb = Image.composite(c.convert("RGB"), Image.new("RGB", (cw, ch), (0, 0, 0)), shade)
    c = Image.merge("RGBA", (*rgb.split(), c.getchannel("A")))
    mtx = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]]), np.float32(q))
    arr = cv2.warpPerspective(np.array(c), mtx, (W, H), flags=cv2.INTER_LANCZOS4,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    return Image.fromarray(arr)


def contact_shadow(q, blur=16, alpha=160):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon([(x, y + 7) for x, y in q], fill=(0, 0, 0, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def compose(day: dt.date, palette: str, cards: int, photo: Path,
            figure=True, text=False, title=None) -> Image.Image:
    original_mark, original_wheel = reel._watermark, reel.wheel_bg.cached
    reel.MARK = reel.BOT
    reel.wheel_bg.cached = lambda *a, **k: original_wheel(*a[:6], day)   # колесо на нужную дату
    if not text:
        reel._watermark = lambda *a, **k: None
    try:
        bg = reel._background(palette, reel.SEEDS[palette]).convert("RGBA")
    finally:
        reel._watermark, reel.wheel_bg.cached = original_mark, original_wheel

    fig, table = cutout(photo)
    band = table.resize((W, round(table.height * SCALE)), Image.LANCZOS).convert("RGBA")
    band_top = H - band.height
    bg.alpha_composite(band, (0, band_top))

    warm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(warm).ellipse((-200, band_top - 60, W + 200, H + 200), fill=(120, 74, 26, 55))
    bg.alpha_composite(warm.filter(ImageFilter.GaussianBlur(120)))

    if figure:
        figr = fig.resize((round(fig.width * SCALE), round(fig.height * SCALE)), Image.LANCZOS)
        bg.alpha_composite(figr, (round((W - figr.width) / 2), H - figr.height))

    for cx in CARD_SPOTS[cards]:
        q = quad(cx, band_top)
        bg.alpha_composite(contact_shadow(q))
        bg.alpha_composite(warp_card(cards_image._render_back(), q))

    if title and text:
        # ниже ника: на палитрах без наезда подпись стоит у самого верха
        reel._draw_block(ImageDraw.Draw(bg), title, reel._font(92), 300, reel.CREAM, spacing=6)
    return bg.convert("RGB")


def main():
    ap = argparse.ArgumentParser(description="Кадр Астры на нашем фоне")
    ap.add_argument("--day", default="2026-09-18", help="дата колеса, ГГГГ-ММ-ДД")
    ap.add_argument("--palette", default="day", choices=sorted(reel.PALETTES))
    ap.add_argument("--cards", type=int, default=1, choices=sorted(CARD_SPOTS))
    ap.add_argument("--title", default=None, help="название ролика сверху")
    ap.add_argument("--photo", default=str(PHOTO), help="исходная фотография")
    ap.add_argument("--out", default="Астра на фоне", help="куда складывать")
    a = ap.parse_args()

    day = dt.date.fromisoformat(a.day)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{day.day:02d}"
    photo = Path(a.photo)

    jobs = [
        (out / f"для_Flow_{tag}_{a.cards}карт.png", dict(figure=True, text=False)),
        (out / f"{tag}_{a.cards}карт_подпись.jpg", dict(figure=True, text=True, title=a.title)),
        (out / f"{tag}_без_астры_{a.cards}карт.png", dict(figure=False, text=False)),
    ]
    for path, kw in jobs:
        img = compose(day, a.palette, a.cards, photo, **kw)
        img.save(path, quality=96) if path.suffix == ".jpg" else img.save(path)
        print(path, f"{path.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
