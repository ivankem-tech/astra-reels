"""Чистка клипов, пришедших из Flow и CapCut.

Сервисы дорисовывают в кадр своё. Этот скрипт снимает лишнее, не трогая
остального, и всегда выкидывает звуковую дорожку сервиса: у Flow она наша
(речь Астры, её оставляем ключом --keep-audio), у CapCut подложена чужая
музыка, которую использовать нельзя.

Два режима:

  --mode flow    Flow подрисовывает знак зодиака сверху по центру. Закрываем
                 его нашей же подложкой: Flow воспроизводит фон почти
                 пиксель в пиксель (проверено 15 сентября — расхождение
                 10 из 255), поэтому заплатка не читается. Подложка
                 собирается тем же tools/astra_frame.py на ту же дату.

  --mode capcut  CapCut ставит «CapCut AI» в левом верхнем углу на каждом
                 кадре. Подложкой не закрыть — CapCut наезжает камерой, и
                 фон под знаком всё время разный. Штопаем inpaint: текст
                 уходит, остаётся лёгкое облачко и рвётся дуга колеса. В
                 тёмном углу малозаметно, а в ролике поверх ложится наш ник.

Примеры:

  python3 tools/clean_clip.py --mode flow --day 2026-09-18 \\
      --in "Материалы/1 Библиотека/клип.mp4" --out "чисто.mp4" --keep-audio

  python3 tools/clean_clip.py --mode capcut \\
      --in "Материалы/1 Библиотека/capcut.mp4" --out "чисто.mp4"
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Знак зодиака от Flow: рамка в кадре 720×1280, замерено 15 сентября
FLOW_GLYPH = (368, 146, 46, 50)          # центр x, центр y, полуось x, полуось y
# Водяной знак CapCut: рамка в кадре 720×1260
CAPCUT_MARK = (12, 6, 150, 52)           # x0, y0, x1, y1


def _probe(path: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h, rate = out.split(",")
    num, den = rate.split("/")
    return int(w), int(h), int(num) / int(den)


def _plate(day: dt.date, size: tuple[int, int]) -> Image.Image:
    """Наша подложка без Астры, под размер клипа."""
    from tools import astra_frame as af
    img = af.compose(day, "day", 1, af.PHOTO, figure=False, text=False)
    return img.resize(size, Image.LANCZOS)


def main() -> None:
    ap = argparse.ArgumentParser(description="Снять дорисованное сервисом")
    ap.add_argument("--mode", required=True, choices=("flow", "capcut"))
    ap.add_argument("--in", dest="src", required=True, type=Path)
    ap.add_argument("--out", dest="dst", required=True, type=Path)
    ap.add_argument("--day", default="2026-09-18", help="дата подложки для режима flow")
    ap.add_argument("--keep-audio", action="store_true",
                    help="оставить звук исходника — для Flow, где это речь Астры")
    a = ap.parse_args()

    if not shutil.which("ffmpeg"):
        raise SystemExit("нет ffmpeg")
    w, h, fps = _probe(a.src)
    print(f"исходник: {w}×{h}, {fps:g} к/с")

    tmp = Path(tempfile.mkdtemp())
    frames, clean = tmp / "in", tmp / "out"
    frames.mkdir(); clean.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(a.src),
                    str(frames / "%05d.png")], check=True)

    if a.mode == "flow":
        plate = _plate(dt.date.fromisoformat(a.day), (w, h))
        cx, cy, rx, ry = FLOW_GLYPH
        k = w / 720                                   # рамка замерена на 720 в ширину
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).ellipse(
            ((cx - rx) * k, (cy - ry) * k, (cx + rx) * k, (cy + ry) * k), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(14 * k))
        for p in sorted(frames.iterdir()):
            Image.composite(plate, Image.open(p).convert("RGB"), mask).save(clean / p.name)
    else:
        x0, y0, x1, y1 = (round(v * w / 720) for v in CAPCUT_MARK)
        pad = (0, 0, min(w, round(260 * w / 720)), min(h, round(130 * w / 720)))
        sub = np.zeros((pad[3] - pad[1], pad[2] - pad[0]), np.uint8)
        sub[y0:y1, x0:x1] = 255
        for p in sorted(frames.iterdir()):
            img = cv2.imread(str(p))
            crop = img[pad[1]:pad[3], pad[0]:pad[2]]
            img[pad[1]:pad[3], pad[0]:pad[2]] = cv2.inpaint(crop, sub, 7, cv2.INPAINT_TELEA)
            cv2.imwrite(str(clean / p.name), img)

    cmd = ["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps:g}",
           "-i", str(clean / "%05d.png")]
    if a.keep_audio:
        cmd += ["-i", str(a.src), "-map", "0:v", "-map", "1:a", "-c:a", "copy", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", str(a.dst)]
    subprocess.run(cmd, check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"готово: {a.dst}  ({a.dst.stat().st_size // 1024} КБ, звук "
          f"{'оставлен' if a.keep_audio else 'снят'})")


if __name__ == "__main__":
    main()
