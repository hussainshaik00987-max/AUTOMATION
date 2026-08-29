"""Tile PNG screenshots into one labelled contact sheet for review.

    python tools/sheet.py <shots-dir> <out.png> [--cols 3] [--tile 560]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--tile", type=int, default=560)
    ap.add_argument("--maxh", type=int, default=0, help="crop tall shots to this height")
    args = ap.parse_args()

    files = sorted(Path(args.src).glob("*.png"))
    if not files:
        raise SystemExit("no screenshots found")

    thumbs = []
    for f in files:
        im = Image.open(f).convert("RGB")
        w, h = im.size
        th = round(args.tile * h / w)
        if args.maxh and th > args.maxh:
            im = im.crop((0, 0, w, round(w * args.maxh / args.tile)))
            th = args.maxh
        thumbs.append((f.stem, im.resize((args.tile, th))))

    label_h, pad = 24, 6
    row_h = [
        max(t.size[1] for _, t in thumbs[i : i + args.cols]) + label_h + pad
        for i in range(0, len(thumbs), args.cols)
    ]
    sheet = Image.new("RGB", (args.cols * (args.tile + pad), sum(row_h)), "#1b1b1d")
    draw = ImageDraw.Draw(sheet)

    y = 0
    for r, start in enumerate(range(0, len(thumbs), args.cols)):
        for c, (name, im) in enumerate(thumbs[start : start + args.cols]):
            x = c * (args.tile + pad)
            draw.text((x + 4, y + 6), name, fill="#e8e8e8")
            sheet.paste(im, (x, y + label_h))
        y += row_h[r]

    sheet.save(args.out)
    print(f"{len(thumbs)} tiles -> {args.out} {sheet.size}")


if __name__ == "__main__":
    main()
