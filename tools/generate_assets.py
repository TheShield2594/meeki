#!/usr/bin/env python3
"""Generate the placeholder art and sound in assets/.

Draws a chibi Bichon Frise x Biewer Terrier pup (fluffy white Bichon body,
Biewer tricolour: blue-black saddle, gold ears/brows, top-knot with a bow)
for each animation state, plus the app icon and a chime.

    python tools/generate_assets.py            # writes into ./assets

Replace any of the generated PNGs with real art: each state is a folder of
frames (assets/sprites/<state>/NN.png) or a horizontal sprite sheet
(assets/sprites/<state>.png, square frames). Frames are 256x256 and are drawn
at ~112 logical px, so they stay crisp on HiDPI screens.
"""

from __future__ import annotations

import math
import os
import random
import struct
import sys
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

SIZE = 256

WHITE = QColor("#fdfaf5")
CREAM = QColor("#f1e8da")
OUTLINE = QColor("#a89a88")
GOLD = QColor("#cf9f5f")
GOLD_LIGHT = QColor("#e4c08a")
DARK = QColor("#414a5a")  # Biewer "blue" / black
DARK_LIGHT = QColor("#5b6578")
INK = QColor("#1d1d24")
TONGUE = QColor("#f08c9b")
BLUSH = QColor(255, 140, 150, 70)
BOW = QColor("#ec5486")
BOW_DARK = QColor("#c23a69")


# ---------------------------------------------------------------- primitives
def fluffy(p: QPainter, cx, cy, rx, ry, fill, seed, puff=None, outline=OUTLINE, n=None, stroke=2.4):
    """A scalloped, fur-like ellipse: outline pass then fill pass."""
    rnd = random.Random(seed)
    puff = puff or max(6.0, min(rx, ry) * 0.28)
    n = n or max(10, int(2 * math.pi * max(rx, ry) / (puff * 1.15)))
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n + rnd.uniform(-0.08, 0.08)
        r = puff * rnd.uniform(0.85, 1.15)
        pts.append((cx + (rx - puff * 0.55) * math.cos(a), cy + (ry - puff * 0.55) * math.sin(a), r))
    for color, grow in ((outline, stroke), (fill, 0.0)):
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), rx - puff * 0.55 + grow, ry - puff * 0.55 + grow)
        for x, y, r in pts:
            p.drawEllipse(QPointF(x, y), r + grow, r + grow)


def ellipse(p, cx, cy, rx, ry, brush, pen=Qt.NoPen):
    p.setPen(pen)
    p.setBrush(brush)
    p.drawEllipse(QPointF(cx, cy), rx, ry)


def rotated(p: QPainter, cx, cy, deg, fn):
    p.save()
    p.translate(cx, cy)
    p.rotate(deg)
    p.translate(-cx, -cy)
    fn()
    p.restore()


def shadow(p, cx, cy, rx):
    ellipse(p, cx, cy, rx, rx * 0.13, QColor(0, 0, 0, 40))


# ---------------------------------------------------------------- dog parts
def tail(p, base_x, base_y, angle, seed=11):
    def draw():
        fluffy(p, base_x + 26, base_y - 22, 18, 30, WHITE, seed, puff=8)
        fluffy(p, base_x + 30, base_y - 40, 12, 14, GOLD_LIGHT, seed + 1, puff=6)
    rotated(p, base_x, base_y, angle, draw)


def ear(p, cx, cy, side, flap=0.0, seed=0):
    """side=-1 left, +1 right. flap rotates the ear outward/up (celebrating)."""
    def draw():
        fluffy(p, cx, cy, 21, 38, GOLD, seed, puff=8)
        fluffy(p, cx + side * 4, cy - 22, 14, 13, DARK, seed + 5, puff=6, outline=DARK)
        fluffy(p, cx - side * 2, cy + 22, 13, 12, GOLD_LIGHT, seed + 9, puff=6, outline=GOLD)
    rotated(p, cx, cy - 30, side * (8 + flap), draw)


def bow(p, cx, cy):
    p.setPen(QPen(BOW_DARK, 2))
    p.setBrush(BOW)
    for s in (-1, 1):
        path = QPainterPath(QPointF(cx, cy))
        path.lineTo(cx + s * 18, cy - 10)
        path.quadTo(cx + s * 22, cy, cx + s * 18, cy + 10)
        path.closeSubpath()
        p.drawPath(path)
    ellipse(p, cx, cy, 6, 6, BOW, QPen(BOW_DARK, 2))


def eyes(p, lx, rx_, y, mode):
    if mode == "closed":
        pen = QPen(INK, 3.2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for x in (lx, rx_):
            p.drawArc(QRectF(x - 9, y - 6, 18, 12), 200 * 16, 140 * 16)
        return
    if mode == "happy":  # ^ ^
        pen = QPen(INK, 3.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for x in (lx, rx_):
            p.drawArc(QRectF(x - 9, y - 4, 18, 14), 20 * 16, 140 * 16)
        return
    dy = 3 if mode in ("down", "side") else 0
    dx = -6 if mode == "side" else 0  # "side": looking away, unimpressed
    for x in (lx, rx_):
        ellipse(p, x, y, 9.5, 10.5, INK)
        ellipse(p, x + 3 + dx, y - 4 + dy, 3.4, 3.4, QColor("white"))
        ellipse(p, x - 3, y + 4 + dy, 1.6, 1.6, QColor(255, 255, 255, 180))
    if mode == "down":  # sleepy-focused lids
        for x in (lx, rx_):
            p.setPen(Qt.NoPen)
            p.setBrush(WHITE)
            p.drawChord(QRectF(x - 11, y - 12, 22, 22), 0, 180 * 16)
            pen = QPen(INK, 2.4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x - 10, y - 1), QPointF(x + 10, y - 1))
    if mode == "side":  # heavy lids slanting down to the middle: a grumpy look
        for x, inner in ((lx, 1), (rx_, -1)):
            lid = QPainterPath(QPointF(x - 12, y - 13))
            lid.lineTo(x + 12, y - 13)
            lid.lineTo(x + 12, y - 2 + inner * 3)
            lid.lineTo(x - 12, y - 2 - inner * 3)
            lid.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(WHITE)
            p.drawPath(lid)
            pen = QPen(INK, 2.4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x - 10, y - 2 - inner * 2.5), QPointF(x + 10, y - 2 + inner * 2.5))


def face(p, cx, cy, eye_mode="open", mouth="smile", glasses=False):
    # gold Biewer "eyebrows"
    ellipse(p, cx - 22, cy - 20, 9, 5, GOLD_LIGHT)
    ellipse(p, cx + 22, cy - 20, 9, 5, GOLD_LIGHT)
    eyes(p, cx - 22, cx + 22, cy - 4, eye_mode)
    if glasses:
        p.setPen(QPen(INK, 2.6))
        p.setBrush(QColor(255, 255, 255, 40))
        for x in (cx - 22, cx + 22):
            p.drawEllipse(QPointF(x, cy - 3), 15, 14)
        p.drawLine(QPointF(cx - 7, cy - 5), QPointF(cx + 7, cy - 5))
    # muzzle
    fluffy(p, cx, cy + 22, 25, 18, CREAM, 77, puff=7, outline=QColor(0, 0, 0, 0), stroke=0)
    ellipse(p, cx - 36, cy + 18, 9, 6, BLUSH)
    ellipse(p, cx + 36, cy + 18, 9, 6, BLUSH)
    # nose
    ellipse(p, cx, cy + 14, 10.5, 7.5, INK)
    ellipse(p, cx - 3, cy + 11, 3.2, 2.2, QColor(255, 255, 255, 170))
    pen = QPen(INK, 2.4)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if mouth == "open":
        path = QPainterPath(QPointF(cx - 12, cy + 26))
        path.quadTo(cx, cy + 46, cx + 12, cy + 26)
        path.closeSubpath()
        p.setBrush(QColor("#7a2d3a"))
        p.drawPath(path)
        ellipse(p, cx, cy + 35, 6.5, 5.5, TONGUE)
    elif mouth == "smile":
        p.drawLine(QPointF(cx, cy + 21), QPointF(cx, cy + 25))
        p.drawArc(QRectF(cx - 10, cy + 19, 10, 10), 200 * 16, 140 * 16)
        p.drawArc(QRectF(cx, cy + 19, 10, 10), 200 * 16, 140 * 16)
    elif mouth == "frown":
        p.drawLine(QPointF(cx, cy + 21), QPointF(cx, cy + 25))
        p.drawArc(QRectF(cx - 9, cy + 25, 18, 10), 30 * 16, 120 * 16)
    elif mouth == "flat":
        p.drawLine(QPointF(cx, cy + 21), QPointF(cx, cy + 25))
        p.drawLine(QPointF(cx - 6, cy + 27), QPointF(cx + 6, cy + 27))


def head(p, cx, cy, tilt=0.0, eye_mode="open", mouth="smile", ear_flap=0.0, glasses=False):
    def draw():
        fluffy(p, cx, cy - 48, 22, 16, WHITE, 31, puff=8)  # top-knot
        fluffy(p, cx, cy, 58, 52, WHITE, 21, puff=12)
        ear(p, cx - 54, cy + 4, -1, ear_flap, seed=41)
        ear(p, cx + 54, cy + 4, 1, ear_flap, seed=51)
        bow(p, cx, cy - 56)
        face(p, cx, cy + 2, eye_mode, mouth, glasses)
    rotated(p, cx, cy + 40, tilt, draw)


def paw(p, cx, cy, seed, rx=17, ry=12):
    fluffy(p, cx, cy, rx, ry, WHITE, seed, puff=6)
    pen = QPen(OUTLINE, 1.6)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for dx in (-5, 5):
        p.drawLine(QPointF(cx + dx, cy + ry - 8), QPointF(cx + dx, cy + ry - 3))


# ---------------------------------------------------------------- poses
def sitting(p, *, lift=0.0, breathe=0.0, tail_angle=0.0, tilt=0.0, eye_mode="open", mouth="smile",
            ear_flap=0.0, paws="down", glasses=False, laptop=None, shadow_scale=1.0):
    y = -lift
    shadow(p, 128, 240, 72 * shadow_scale)
    tail(p, 168, 190 + y, -25 + tail_angle)
    fluffy(p, 128, 182 + y, 64 + breathe, 52 + breathe, WHITE, 1, puff=12)  # body
    fluffy(p, 128, 140 + y, 66, 24, DARK, 2, puff=10, outline=QColor("#2e3440"))  # Biewer saddle/shoulders
    fluffy(p, 128, 190 + y, 36, 34, CREAM, 3, puff=9, outline=QColor(0, 0, 0, 0), stroke=0)  # chest fluff
    if paws == "down":
        paw(p, 104, 226 + y, 61)
        paw(p, 152, 226 + y, 62)
    if laptop is not None:
        draw_laptop(p, laptop)
    head(p, 128, 104 + y, tilt, eye_mode, mouth, ear_flap, glasses)
    if paws == "up":  # cheering, paws in the air beside the head
        paw(p, 52, 112 + y, 61, 14, 14)
        paw(p, 204, 112 + y, 62, 14, 14)


def draw_laptop(p, typing_frame):
    # lid seen from behind, with a little tomato sticker
    lid = QRectF(76, 184, 104, 54)
    grad = QLinearGradient(lid.topLeft(), lid.bottomLeft())
    grad.setColorAt(0, QColor("#c9ced6"))
    grad.setColorAt(1, QColor("#9aa1ad"))
    p.setPen(QPen(QColor("#6b7280"), 2))
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(lid, 8, 8)
    ellipse(p, 128, 212, 9, 8.5, QColor("#e2574c"))
    p.setPen(QPen(QColor("#3f8f3a"), 2.6))
    p.drawLine(QPointF(128, 204), QPointF(133, 200))
    # paws resting on top of the lid, alternating like typing
    left_up = typing_frame % 2 == 0
    paw(p, 104, 184 - (6 if left_up else 0), 61, 15, 10)
    paw(p, 152, 184 - (0 if left_up else 6), 62, 15, 10)


def lying(p, *, breathe=0.0, zs=0.0, tail_angle=0.0):
    shadow(p, 128, 238, 92)
    tail(p, 184, 208, 75 + tail_angle, seed=12)
    fluffy(p, 132, 204, 80, 34 + breathe, WHITE, 4, puff=12)  # body
    fluffy(p, 146, 182 + breathe * 0.5, 56, 16, DARK, 5, puff=9, outline=QColor("#2e3440"))  # saddle
    head(p, 96, 150, tilt=-8, eye_mode="closed", mouth="flat")
    paw(p, 70, 226, 63)
    paw(p, 112, 228, 64)
    # floating Zzz
    pen = QPen(DARK_LIGHT, 3.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    for i, size in enumerate((10, 14, 18)):
        t = (zs + i / 3) % 1.0
        x = 168 + i * 20 + 6 * math.sin(t * math.pi * 2)
        yb = 120 - i * 26 - t * 18
        c = QColor(DARK_LIGHT)
        c.setAlphaF(max(0.0, min(1.0, 1.2 - t)))
        pen.setColor(c)
        p.setPen(pen)
        path = QPainterPath(QPointF(x, yb))
        path.lineTo(x + size, yb)
        path.lineTo(x, yb + size)
        path.lineTo(x + size, yb + size)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


def confetti(p, frame):
    rnd = random.Random(1000 + frame)
    colors = ["#e2574c", "#ffca28", "#43a047", "#1e88e5", "#ec5486", "#8e24aa"]
    for i in range(22):
        x = rnd.uniform(14, 242)
        y = (rnd.uniform(-20, 140) + frame * 18 + i * 7) % 170
        if 70 < x < 186 and y > 40:  # keep the pup's face clear
            continue
        c = QColor(colors[i % len(colors)])
        p.save()
        p.translate(x, y)
        p.rotate(rnd.uniform(0, 360) + frame * 30)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        if i % 3 == 0:
            p.drawEllipse(QPointF(0, 0), 3.5, 3.5)
        else:
            p.drawRect(QRectF(-4, -2, 8, 4))
        p.restore()


# ---------------------------------------------------------------- frames
def frame_idle(p, i):
    tails = [-18, 0, 18, 0, -18, 0, 18, 0]
    sitting(p, tail_angle=tails[i], breathe=(1.5 if i % 4 < 2 else 0), tilt=(0 if i < 4 else 5),
            eye_mode="closed" if i == 6 else "open")


def frame_focusing(p, i):
    sitting(p, tail_angle=[-6, 0, 6, 0][i], tilt=[0, 1, 0, -1][i], eye_mode="down", mouth="flat",
            paws="none", glasses=True, laptop=i)


def frame_break(p, i):
    lying(p, breathe=[0, 1.5, 3, 1.5][i], zs=i / 4, tail_angle=[0, 4, 8, 4][i])


def frame_celebrating(p, i):
    lifts = [0, 12, 24, 30, 24, 12]
    confetti(p, i)
    sitting(p, lift=lifts[i], tail_angle=[-25, 25, -25, 25, -25, 25][i], eye_mode="happy", mouth="open",
            ear_flap=[0, 10, 22, 28, 22, 10][i], paws="up", shadow_scale=1 - lifts[i] / 80)


def rain_cloud(p, i):
    """Little grey cloud with rain for the sulky pup."""
    cx, cy = 200 + [0, 2, 4, 2][i], 34
    pen = QPen(QColor("#7d8aa0"), 2.6)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    for k in range(3):
        y = cy + 18 + ((i * 7 + k * 9) % 22)
        p.drawLine(QPointF(cx - 12 + k * 12, y), QPointF(cx - 14 + k * 12, y + 6))
    for ox, oy, r in ((-14, 4, 11), (0, -4, 14), (14, 4, 11), (0, 8, 12)):
        ellipse(p, cx + ox, cy + oy, r, r * 0.85, QColor("#b3bccb"))


def frame_sulky(p, i):
    sitting(p, tail_angle=34, breathe=(1 if i % 2 else 0), tilt=-7, eye_mode="side", mouth="frown",
            ear_flap=-12)
    rain_cloud(p, i)


STATES = {
    "idle": (frame_idle, 8),
    "focusing": (frame_focusing, 4),
    "break": (frame_break, 4),
    "celebrating": (frame_celebrating, 6),
    "sulky": (frame_sulky, 4),  # idle pose when the pup's mood is low
}


def render(fn, i, size=SIZE) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / SIZE, size / SIZE)
    fn(p, i)
    p.end()
    return img


def write_chime(path: Path) -> None:
    rate, dur = 44100, 1.1
    notes = [(0.0, 880.0), (0.16, 1318.5)]  # A5 then E6, soft bell-ish
    frames = bytearray()
    for n in range(int(rate * dur)):
        t = n / rate
        v = 0.0
        for start, freq in notes:
            if t >= start:
                tt = t - start
                env = math.exp(-tt * 4.0) * min(1.0, tt * 200)
                v += env * (math.sin(2 * math.pi * freq * tt) + 0.3 * math.sin(2 * math.pi * freq * 2 * tt))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v * 0.33)) * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets"
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    for state, (fn, count) in STATES.items():
        d = out / "sprites" / state
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.png"):
            old.unlink()
        for i in range(count):
            render(fn, i).save(str(d / f"{i:02d}.png"))
    (out / "icons").mkdir(parents=True, exist_ok=True)
    for size in (256, 128, 64, 48):
        render(frame_idle, 0, size).save(str(out / "icons" / f"pomodoro-pet-{size}.png"))
    render(frame_idle, 0, 256).save(str(out / "icons" / "pomodoro-pet.png"))
    (out / "sounds").mkdir(parents=True, exist_ok=True)
    write_chime(out / "sounds" / "chime.wav")
    print(f"Assets written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
