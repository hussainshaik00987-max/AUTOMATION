"""Render specific timeline positions to PNG, and tile them into a contact sheet.

This is the review loop: encoding a 38-second video to look at one badly-placed
bubble is 40 seconds of waiting, while a six-up sheet of the moments that matter
lands in about two.

    python tools/shots.py out/scenes/foo.html /tmp/shots           # key beats
    python tools/shots.py out/scenes/foo.html /tmp/s --at 0,4200,9000
    python tools/shots.py out/scenes/*.html /tmp/s --sheet /tmp/sheet.png
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import CHROME_ARGS, STAGE_H, STAGE_W          # noqa: E402


def key_times(duration: int, layers: list[dict], n: int = 6) -> list[int]:
    """Beats worth looking at: the middle of each copy layer, plus a couple of
    points inside the chat, which is the longest stretch with no layer of its own."""
    pts = {int(duration * 0.06)}
    for L in layers:
        pts.add(int((L["in"] + min(L["out"], L["in"] + 2600)) / 2))
    chat = [L for L in layers if L["id"].startswith("ann")]
    if chat:
        last = max(L["out"] for L in chat)
        first = min(L["in"] for L in chat)
        pts.add(int(first + (last - first) * 0.55))
    pts.add(duration - 400)
    out = sorted(p for p in pts if 0 <= p < duration)
    if len(out) > n:
        stride = len(out) / n
        out = [out[int(i * stride)] for i in range(n)]
    return out


def shoot(html_paths: list[Path], out_dir: Path, at: list[int] | None,
          n: int, scale: float) -> list[Path]:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROME_ARGS)
        page = browser.new_context(
            viewport={"width": STAGE_W, "height": STAGE_H},
            device_scale_factor=scale, color_scheme="light").new_page()
        for html in html_paths:
            page.goto(html.resolve().as_uri(), wait_until="load")
            page.wait_for_function("document.documentElement.dataset.ready === '1'")
            duration = page.evaluate("window.__duration()")
            layers = page.evaluate("window.SCENE.layers")
            times = at or key_times(duration, layers, n)
            for t in times:
                page.evaluate("t => window.__seek(t)", t)
                dest = out_dir / f"{html.stem}__{t:06d}.png"
                page.screenshot(path=str(dest), animations="disabled")
                written.append(dest)
        browser.close()
    return written


# --- minimal PNG tiler (no PIL dependency for the review path) -------------

def _read_png(path: Path) -> tuple[int, int, bytearray]:
    data = path.read_bytes()
    pos, idat, w, h = 8, bytearray(), 0, 0
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h, bit, ctype = struct.unpack(">IIBB", body[:10])
            if bit != 8 or ctype not in (2, 6):
                raise ValueError(f"unsupported PNG {bit}/{ctype}")
            channels = 3 if ctype == 2 else 4
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        pos += 12 + ln

    raw = zlib.decompress(bytes(idat))
    stride = w * channels
    out = bytearray(w * h * 3)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        # PNG per-scanline filters, unwound in place
        if ft == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        for x in range(w):
            si, di = x * channels, (y * w + x) * 3
            out[di:di + 3] = line[si:si + 3]
        prev = line
    return w, h, out


def _write_png(path: Path, w: int, h: int, rgb: bytearray) -> None:
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * w * 3:(y + 1) * w * 3]

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
                     + chunk(b"IEND", b""))


def sheet(shots: list[Path], dest: Path, cols: int = 6, tile_w: int = 300) -> None:
    """Nearest-neighbour downscale into a grid. Good enough to judge composition,
    spacing and colour, which is all a contact sheet is for."""
    if not shots:
        return
    tiles = []
    for p in shots:
        w, h, rgb = _read_png(p)
        tw = tile_w
        th = max(1, round(h * tw / w))
        small = bytearray(tw * th * 3)
        for y in range(th):
            sy = min(h - 1, y * h // th)
            for x in range(tw):
                sx = min(w - 1, x * w // tw)
                si, di = (sy * w + sx) * 3, (y * tw + x) * 3
                small[di:di + 3] = rgb[si:si + 3]
        tiles.append((tw, th, small))

    pad = 8
    rows = (len(tiles) + cols - 1) // cols
    cw, ch = tiles[0][0], tiles[0][1]
    W = cols * cw + pad * (cols + 1)
    H = rows * ch + pad * (rows + 1)
    canvas = bytearray(b"\x16\x16\x1a" * (W * H))
    for i, (tw, th, buf) in enumerate(tiles):
        cx = pad + (i % cols) * (cw + pad)
        cy = pad + (i // cols) * (ch + pad)
        for y in range(min(th, ch)):
            di = ((cy + y) * W + cx) * 3
            canvas[di:di + tw * 3] = buf[y * tw * 3:(y + 1) * tw * 3]
    _write_png(dest, W, H, canvas)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenes", nargs="+")
    ap.add_argument("out")
    ap.add_argument("--at", help="comma-separated milliseconds")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--sheet")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--tile", type=int, default=300)
    args = ap.parse_args()

    at = [int(x) for x in args.at.split(",")] if args.at else None
    shots = shoot([Path(p) for p in args.scenes], Path(args.out), at, args.n, args.scale)
    print(f"{len(shots)} frames -> {args.out}")
    if args.sheet:
        sheet(shots, Path(args.sheet), cols=args.cols, tile_w=args.tile)
        print(f"sheet -> {args.sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
