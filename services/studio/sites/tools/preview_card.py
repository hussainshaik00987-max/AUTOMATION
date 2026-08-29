#!/usr/bin/env python
"""Render what a page's link looks like when pasted into WhatsApp.

    python tools/preview_card.py out/sites/kettle-petal.html preview.png
    python tools/preview_card.py out/sites --sample 6 sheet.png

Reads the page's real Open Graph tags and its real `og:image` file off disk and
composes them the way WhatsApp lays a link preview out, so the thing that gets
checked is the actual output rather than a guess about it. Useful before a
build goes out: a title that clips badly or an image whose subject is centred
out of the 1200x630 crop is obvious here and invisible in the HTML.

Layout follows WhatsApp's outgoing-message bubble: full-bleed image on top,
then bold title, then two lines of description, then the host.
"""

from __future__ import annotations

import argparse
import html
import random
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

CARD_W = 620
IMG_H = 325
PAD = 18
BUBBLE = "#d9fdd3"          # outgoing message green
INK = "#111b21"
SUBTLE = "#667781"
CANVAS = "#0b141a"

FONT_DIRS = [
    Path(r"C:\Windows\Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    Path(__file__).resolve().parents[2] / "demovideos" / "assets" / "fonts",
]


def _font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        for n in names:
            f = d / n
            if f.exists():
                try:
                    return ImageFont.truetype(str(f), size)
                except OSError:
                    pass
    return ImageFont.load_default(size)


def _meta(doc: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', doc):
        out.setdefault(m.group(1), html.unescape(m.group(2)))
    return out


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int,
          max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and words:
        # Ellipsise only if something was actually dropped.
        joined = " ".join(lines)
        if len(joined) < len(text):
            while (draw.textlength(lines[-1] + "…", font=font) > width
                   and " " in lines[-1]):
                lines[-1] = lines[-1].rsplit(" ", 1)[0]
            lines[-1] += "…"
    return lines


# The photo library can live on a second origin (see the README on why the
# deploy is split), in which case there is no local copy to read. Falling back
# to an actual HTTP fetch is not a workaround — it is the more faithful test,
# because fetching that URL over the network is exactly what WhatsApp does.
_OG_CACHE: dict[str, Image.Image | None] = {}


def _load_og(url: str, root: Path) -> Image.Image | None:
    if url in _OG_CACHE:
        return _OG_CACHE[url]
    parsed = urlparse(url)
    for candidate in (root / parsed.path.lstrip("/"),
                      Path(__file__).resolve().parents[1] / "assets" / "photos"
                      / parsed.path.lstrip("/")):
        if candidate.exists():
            im = Image.open(candidate).convert("RGB")
            _OG_CACHE[url] = im
            return im
    im = None
    if parsed.scheme in ("http", "https"):
        try:
            import io
            import urllib.request
            req = urllib.request.Request(
                url, headers={"User-Agent": "WhatsApp/2.23.20.0 A"})
            with urllib.request.urlopen(req, timeout=25) as r:
                if r.headers.get("Content-Type", "").startswith("image/"):
                    im = Image.open(io.BytesIO(r.read())).convert("RGB")
        except Exception:                                    # noqa: BLE001
            im = None
    _OG_CACHE[url] = im
    return im


def card(page: Path, root: Path) -> Image.Image:
    meta = _meta(page.read_text(encoding="utf-8"))
    title = meta.get("og:title", "(no og:title)")
    desc = meta.get("og:description", "(no og:description)")
    url = meta.get("og:url", "")
    img_url = meta.get("og:image", "")

    f_title = _font(["seguisb.ttf", "segoeuib.ttf", "Inter-SemiBold.ttf", "arialbd.ttf"], 17)
    f_body = _font(["segoeui.ttf", "Inter-Regular.ttf", "arial.ttf"], 15)
    f_host = _font(["segoeui.ttf", "Inter-Regular.ttf", "arial.ttf"], 13)

    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    inner = CARD_W - 2 * PAD
    t_lines = _wrap(scratch, title, f_title, inner, 2)
    d_lines = _wrap(scratch, desc, f_body, inner, 2)

    text_h = PAD + len(t_lines) * 23 + 4 + len(d_lines) * 20 + 6 + 18 + PAD
    has_img = bool(img_url)
    card_h = (IMG_H if has_img else 0) + text_h
    im = Image.new("RGB", (CARD_W + 40, card_h + 40), CANVAS)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((20, 20, 20 + CARD_W, 20 + card_h), 12, fill=BUBBLE)

    y = 20
    if has_img:
        photo = _load_og(img_url, root)
        if photo is not None:
            scale = max(CARD_W / photo.width, IMG_H / photo.height)
            photo = photo.resize((round(photo.width * scale), round(photo.height * scale)),
                                 Image.LANCZOS)
            l, t = (photo.width - CARD_W) // 2, (photo.height - IMG_H) // 2
            photo = photo.crop((l, t, l + CARD_W, t + IMG_H))
            mask = Image.new("L", (CARD_W, IMG_H), 255)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, CARD_W - 1, IMG_H - 1), 12, fill=255)
            ImageDraw.Draw(mask).rectangle((0, IMG_H - 14, CARD_W, IMG_H), fill=255)
            im.paste(photo, (20, y), mask)
        else:
            d.rectangle((20, y, 20 + CARD_W, y + IMG_H), fill="#c8d3cd")
            d.text((20 + PAD, y + IMG_H // 2),
                   f"UNREACHABLE: {img_url[:64]}", font=f_body, fill="#8a3324")
        y += IMG_H

    y += PAD
    for line in t_lines:
        d.text((20 + PAD, y), line, font=f_title, fill=INK)
        y += 23
    y += 4
    for line in d_lines:
        d.text((20 + PAD, y), line, font=f_body, fill=SUBTLE)
        y += 20
    y += 6
    d.text((20 + PAD, y), urlparse(url).netloc or "(no og:url)", font=f_host, fill=SUBTLE)
    return im


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="one .html page, or a directory of them")
    ap.add_argument("out")
    ap.add_argument("--sample", type=int, default=6)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args()

    src = Path(args.src)
    if src.is_file():
        pages, root = [src], src.parent.parent
    else:
        every = sorted(src.glob("*.html"))
        if not every:
            print("no pages found", file=sys.stderr)
            return 2
        pages = random.Random(args.seed).sample(every, min(args.sample, len(every)))
        root = src.parent

    cards = [card(p, root) for p in pages]
    if len(cards) == 1:
        cards[0].save(args.out)
    else:
        cols = min(args.cols, len(cards))
        rows = (len(cards) + cols - 1) // cols
        cw = max(c.width for c in cards)
        ch = max(c.height for c in cards)
        sheet = Image.new("RGB", (cols * cw, rows * ch), CANVAS)
        for i, c in enumerate(cards):
            sheet.paste(c, ((i % cols) * cw, (i // cols) * ch))
        sheet.save(args.out)
    print(f"{len(cards)} preview card(s) -> {args.out}")
    for p in pages:
        print("  ", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
