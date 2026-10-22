#!/usr/bin/env python3
"""Turn green-screen animation clips into sprite frames.

Each clip (an MP4 of the pup on a flat green background, made with the
prompts in docs/ai-sprites.md) becomes assets/sprites/<state>/NN.png:
frames sampled at --fps, the green keyed out to transparency, and every clip
cropped with the same box so the pup keeps one size and ground line across
states.

    python tools/key_sprites.py mood_happy=happy.mp4 mood_sad=sad.mp4 ...

The shared box is sized so that the clip named by --ref (default: the first
one) stands --ref-height px tall on a 256 px frame, matching the generated
art. Needs ffmpeg, Pillow and numpy (only for this tool, not the app).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

SIZE = 256
GROUND = 250  # y of the pup's feet in the generated art


def extract(clip: Path, fps: float, workdir: Path) -> list[Image.Image]:
    workdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vf", f"fps={fps}", str(workdir / "%03d.png")],
        check=True,
    )
    return [Image.open(p).convert("RGB") for p in sorted(workdir.glob("*.png"))]


def key(frame: Image.Image) -> Image.Image:
    """Green screen to alpha, with the green spill removed from the edges."""
    a = np.asarray(frame).astype(np.int32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    greenness = g - np.maximum(r, b)  # ~110 on the screen, <= 0 on the pup
    alpha = np.clip((90 - greenness) * 255 // 60, 0, 255).astype(np.uint8)
    mask = Image.fromarray(alpha).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    a[..., 1] = np.where(greenness > 0, np.maximum(r, b), g)
    rgba = np.dstack([np.clip(a, 0, 255).astype(np.uint8), np.asarray(mask)])
    return Image.fromarray(rgba, "RGBA")


def union_box(frames: list[Image.Image]) -> tuple[int, int, int, int]:
    boxes = [f.getbbox() for f in frames]
    boxes = [b for b in boxes if b]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+", metavar="STATE=CLIP.mp4")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "assets" / "sprites")
    ap.add_argument("--fps", type=float, default=8)
    ap.add_argument("--ref", help="state whose pup sets the scale (default: the first)")
    ap.add_argument("--ref-height", type=int, default=217, help="pup height in px on the 256 px frame")
    args = ap.parse_args()

    pairs = [c.split("=", 1) for c in args.clips]
    if any(len(p) != 2 for p in pairs):
        ap.error("clips are given as STATE=CLIP.mp4")
    keyed: dict[str, list[Image.Image]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for state, clip in pairs:
            keyed[state] = [key(f) for f in extract(Path(clip), args.fps, Path(tmp) / state)]

    ref = args.ref or pairs[0][0]
    rx0, ry0, rx1, ry1 = union_box(keyed[ref])
    scale = args.ref_height / (ry1 - ry0)
    side = round(SIZE / scale)
    left = (rx0 + rx1) // 2 - side // 2
    top = ry1 - round(GROUND / scale)
    box = (left, top, left + side, top + side)

    for state, frames in keyed.items():
        x0, y0, x1, y1 = union_box(frames)
        state_box = box
        if x0 < box[0] or x1 > box[2]:  # a wide pose (the nap): centre it on itself, same scale and ground
            left = (x0 + x1) // 2 - side // 2
            state_box = (left, box[1], left + side, box[3])
        if x0 < state_box[0] or y0 < state_box[1] or x1 > state_box[2] or y1 > state_box[3]:
            print(f"warning: {state} pokes out of the shared frame and will be clipped", file=sys.stderr)
        dest = args.out / state
        dest.mkdir(parents=True, exist_ok=True)
        for old in dest.glob("*.png"):
            old.unlink()
        for i, frame in enumerate(frames):
            sprite = frame.crop(state_box).resize((SIZE, SIZE), Image.LANCZOS)
            # flat cartoon art: a 256-colour palette is near-lossless and ~5x smaller
            sprite = sprite.quantize(256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
            sprite.save(dest / f"{i:02d}.png", optimize=True)
        (dest / "fps").write_text(f"{args.fps:g}\n")
        print(f"{state}: {len(frames)} frames -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
