#!/usr/bin/env python
"""Harvest a curated stock-photo library from Pexels into `assets/photos/`.

Run once (or when you want to refresh the library):

    python tools/harvest_photos.py                 # everything
    python tools/harvest_photos.py --vertical bakery
    python tools/harvest_photos.py --manifest-only # re-scrape ids, no downloads

Why Pexels and not Unsplash: the Pexels license is free for commercial and
non-commercial use with no attribution required, and their site is reachable.
unsplash.com now sits behind a proof-of-work interstitial that a scripted
browser cannot clear.

The search pages are JS-rendered, so discovery runs through Playwright; the
actual image bytes come from `images.pexels.com` over plain HTTP, which is fast
and needs no browser.

Output:

    assets/photos/photos.json            the manifest the build reads
    assets/photos/<vertical>/<id>-lg.webp   hero / editorial / about
    assets/photos/<vertical>/<id>-sm.webp   gallery tiles
    assets/photos/<vertical>/<id>-og.jpg    1200x630 social crop

Everything is content-addressed by the Pexels photo id, so re-running skips
files that already exist and the library is incrementally extendable.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PHOTO_DIR = ROOT / "assets" / "photos"
MANIFEST = PHOTO_DIR / "photos.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# --------------------------------------------------------------------------
# what to look for
# --------------------------------------------------------------------------
# Each bucket is (topic, search query, how many to keep). `topic` is carried
# into the manifest so a single business's gallery can be spread across
# different subjects instead of six near-identical bride portraits.
BUCKETS: dict[str, list[tuple[str, str, int]]] = {
    "wedding_photo": [
        ("couple",     "indian wedding couple",            10),
        ("bride",      "indian bride portrait",            10),
        ("ceremony",   "indian wedding ceremony",          10),
        ("haldi",      "haldi ceremony indian",            10),
        ("mehendi",    "mehendi henna hands bride",        10),
        ("celebration","indian wedding celebration dance", 12),
        ("details",    "wedding rings detail flatlay",     10),
        ("candid",     "wedding guests candid laughing",   10),
        ("reception",  "wedding reception lights evening", 10),
        ("southindian","south indian wedding",             10),
        ("groom",      "indian groom sherwani portrait",   10),
        ("preweddin",  "couple pre wedding shoot outdoor", 10),
    ],
    "bakery": [
        ("cake",       "birthday cake decorated",          10),
        ("cupcake",    "cupcakes frosting",                10),
        ("bread",      "artisan bread bakery",             10),
        ("pastry",     "pastry dessert plated",            10),
        ("cookies",    "cookies baking tray",              10),
        ("wedcake",    "wedding cake tiered white",        10),
        ("shop",       "bakery shop interior counter",     10),
        ("chocolate",  "chocolate dessert closeup",        10),
        ("macaron",    "macarons colorful",                 10),
        ("baker",      "baker hands kneading dough",       10),
        ("donut",      "donuts glazed colorful",            10),
        ("brownie",    "brownie slice dessert",             10),
    ],
    "wedding_planner": [
        ("mandap",     "indian wedding mandap decoration", 10),
        ("stage",      "wedding stage decor flowers",      10),
        ("marigold",   "marigold flower decoration india", 10),
        ("table",      "wedding table setting elegant",    10),
        ("venue",      "outdoor wedding venue garden",     10),
        ("lights",     "fairy lights wedding decor night", 10),
        ("floral",     "floral arch wedding ceremony",     10),
        ("banquet",    "banquet hall event setup",         10),
        ("entrance",   "wedding entrance decor",           10),
        ("candles",    "event decoration candles centerpiece", 10),
        ("baraat",     "indian wedding baraat procession", 10),
        ("mehfil",     "sangeet night stage lighting",      10),
    ],
    "fashion": [
        ("lehenga",    "indian ethnic wear lehenga",       10),
        ("saree",      "woman saree traditional portrait", 10),
        ("boutique",   "boutique clothing store interior", 10),
        ("fabric",     "colorful textile fabric rolls",    10),
        ("tailor",     "tailor sewing machine studio",     10),
        ("jewellery",  "indian gold jewellery closeup",    10),
        ("model",      "fashion model studio portrait",    10),
        ("rack",       "clothing rack boutique display",   10),
        ("embroidery", "embroidery detail fabric handwork",10),
        ("silk",       "silk fabric texture folds",        10),
        ("menswear",   "indian menswear kurta sherwani",   10),
        ("accessory",  "fashion accessories flatlay",       10),
    ],
    "interior_arch": [
        ("living",     "modern living room interior",      10),
        ("kitchen",    "modern kitchen interior design",   10),
        ("bedroom",    "bedroom interior design modern",   10),
        ("office",     "modern office interior workspace", 10),
        ("facade",     "modern architecture building facade", 10),
        ("materials",  "interior design materials moodboard", 10),
        ("stair",      "modern staircase architecture",    10),
        ("bath",       "bathroom interior design modern",  10),
        ("minimal",    "minimalist interior white room",   10),
        ("plan",       "architectural drawing blueprint desk", 10),
        ("dining",     "dining room interior design",      10),
        ("retail",     "retail store interior design",     10),
    ],
}

# Renditions written for every photo.
#
# The large rendition is capped on its **long** edge rather than its width. A
# 2:3 portrait held to 1400px wide is 1400x2100 — 2.9 megapixels and 630KB of
# WebP for a slot that is never wider than half the viewport. Capping the long
# edge gives every photo the same pixel budget whichever way up it is.
LARGE_LONG_EDGE = 1100
TILE_WIDTH = 600
LARGE_QUALITY = 68
TILE_QUALITY = 70

OG_SIZE = (1200, 630)
OG_QUALITY = 60             # WhatsApp re-compresses anyway; ~110KB is plenty

MIN_SOURCE_W = 900          # reject thumbnails and avatars


# --------------------------------------------------------------------------
# discovery (Playwright)
# --------------------------------------------------------------------------
_EXTRACT = """() => {
  const seen = new Map();
  document.querySelectorAll('a[href*="/photo/"]').forEach(a => {
    const img = a.querySelector('img[src*="images.pexels.com/photos/"]');
    if (!img) return;
    const m = (img.getAttribute('src') || '').match(/photos\\/(\\d+)\\//);
    if (!m) return;
    const w = img.naturalWidth, h = img.naturalHeight;
    if (!w || !h) return;
    seen.set(m[1], {id: m[1], alt: (img.alt || '').replace(/^Free\\s+/, '').trim(),
                    w, h, page: a.getAttribute('href')});
  });
  return [...seen.values()];
}"""


async def discover(queries: list[tuple[str, str, str, int]]) -> list[dict]:
    """queries: (vertical, topic, search text, keep). Returns photo records.

    **Every search gets its own browser context.** Reusing one context is what
    a human browser does, but the second navigation reliably lands on
    Cloudflare's "Just a moment..." interstitial and every search after the
    first returns nothing — silently, with a 403 that still renders a page. A
    throwaway context per query keeps the whole run at HTTP 200.

    Nothing is scrolled: the first paint already carries ~24 results, which is
    more than any bucket asks for, and scrolling was what made the run long
    enough to look like a scraper.
    """
    from playwright.async_api import async_playwright

    found: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for vertical, topic, query, keep in queries:
                rows = await _one_search(browser, query)
                for r in rows[:keep]:
                    found.append({
                        "id": r["id"], "vertical": vertical, "topic": topic,
                        "alt": r["alt"][:140], "query": query,
                        "ratio": round(r["w"] / r["h"], 4),
                        "page": "https://www.pexels.com" + (r["page"] or ""),
                    })
                flag = "" if rows else "   <-- BLOCKED"
                print(f"  {vertical:16s} {topic:11s} {len(rows[:keep]):>3} / {keep}"
                      f"  {query}{flag}", flush=True)
                await asyncio.sleep(0.8)
        finally:
            await browser.close()
    return found


async def _one_search(browser, query: str, attempts: int = 3) -> list[dict]:
    ctx = None
    for attempt in range(attempts):
        try:
            ctx = await browser.new_context(
                viewport={"width": 1600, "height": 1200}, user_agent=UA,
                locale="en-US")
            page = await ctx.new_page()
            url = "https://www.pexels.com/search/" + urllib.parse.quote(query) + "/"
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            if resp and resp.status == 200:
                await page.wait_for_timeout(2600)
                rows = await page.evaluate(_EXTRACT)
                rows = [r for r in rows if r["w"] >= 200 and r["h"] >= 200]
                if rows:
                    return rows
            await asyncio.sleep(2.5 * (attempt + 1))
        except Exception as exc:                         # noqa: BLE001
            print(f"    ! {query} attempt {attempt + 1}: {type(exc).__name__}",
                  file=sys.stderr, flush=True)
            await asyncio.sleep(2.0)
        finally:
            if ctx is not None:
                await ctx.close()
                ctx = None
    return []


# --------------------------------------------------------------------------
# download + re-encode
# --------------------------------------------------------------------------
def _source_url(pid: str, width: int) -> str:
    return (f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg"
            f"?auto=compress&cs=tinysrgb&w={width}")


def _fetch(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _cover(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Centre-crop to exactly `size`, like CSS object-fit: cover."""
    tw, th = size
    scale = max(tw / im.width, th / im.height)
    nw, nh = max(tw, round(im.width * scale)), max(th, round(im.height * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def _fit_long(im: Image.Image, long_edge: int) -> Image.Image:
    scale = long_edge / max(im.size)
    if scale >= 1:
        return im
    return im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                     Image.LANCZOS)


def _fit_wide(im: Image.Image, width: int) -> Image.Image:
    if im.width <= width:
        return im
    return im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)


def process_one(rec: dict, *, force: bool = False) -> dict | None:
    """Download one photo and write every rendition. Returns updated record."""
    pid, vertical = rec["id"], rec["vertical"]
    out_dir = PHOTO_DIR / vertical
    out_dir.mkdir(parents=True, exist_ok=True)

    large_path = out_dir / f"{pid}-lg.webp"
    tile_path = out_dir / f"{pid}-sm.webp"
    og_path = out_dir / f"{pid}-og.jpg"

    if not force and rec.get("lqip") and all(
            q.exists() for q in (large_path, tile_path, og_path)):
        rec["files"] = {"lg": f"{vertical}/{pid}-lg.webp",
                        "sm": f"{vertical}/{pid}-sm.webp"}
        rec["og"] = f"{vertical}/{pid}-og.jpg"
        return rec

    try:
        raw = _fetch(_source_url(pid, 1600))
        src = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:                             # noqa: BLE001
        print(f"  ! {pid}: {type(exc).__name__} {exc}", file=sys.stderr)
        return None
    if src.width < MIN_SOURCE_W:
        print(f"  ! {pid}: source only {src.width}px wide, skipped", file=sys.stderr)
        return None

    rec["ratio"] = round(src.width / src.height, 4)
    _fit_long(src, LARGE_LONG_EDGE).save(
        large_path, "WEBP", quality=LARGE_QUALITY, method=6)
    _fit_wide(src, TILE_WIDTH).save(
        tile_path, "WEBP", quality=TILE_QUALITY, method=6)
    _cover(src, OG_SIZE).save(
        og_path, "JPEG", quality=OG_QUALITY, optimize=True, progressive=True)

    rec["files"] = {"lg": f"{vertical}/{pid}-lg.webp",
                    "sm": f"{vertical}/{pid}-sm.webp"}
    rec["og"] = f"{vertical}/{pid}-og.jpg"
    rec["lqip"] = _lqip(src)
    rec["bytes"] = (large_path.stat().st_size + tile_path.stat().st_size
                    + og_path.stat().st_size)
    return rec


def _lqip(src: Image.Image) -> str:
    """A 16px-wide WebP data URI used as the CSS background while loading."""
    import base64
    tiny = src.resize((16, max(1, round(16 * src.height / src.width))), Image.LANCZOS)
    buf = io.BytesIO()
    tiny.save(buf, "WEBP", quality=42, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vertical", default="", help="restrict to one vertical")
    ap.add_argument("--manifest-only", action="store_true",
                    help="re-scrape ids without downloading images")
    ap.add_argument("--download-only", action="store_true",
                    help="skip scraping, download whatever the manifest lists")
    ap.add_argument("--force", action="store_true", help="re-encode existing files")
    ap.add_argument("-j", "--jobs", type=int, default=12)
    args = ap.parse_args()

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    if MANIFEST.exists():
        existing = {r["id"]: r for r in json.loads(MANIFEST.read_text("utf-8"))["photos"]}

    if args.download_only:
        records = list(existing.values())
    else:
        queries = [(v, topic, q, keep)
                   for v, buckets in BUCKETS.items()
                   if not args.vertical or v == args.vertical
                   for topic, q, keep in buckets]
        print(f"discovering · {len(queries)} searches")
        scraped = asyncio.run(discover(queries))
        merged = dict(existing)
        for r in scraped:
            merged.setdefault(r["id"], {}).update(r)
        records = list(merged.values())
        print(f"manifest: {len(records)} photos "
              f"(+{len(records) - len(existing)} new)")

    if args.vertical:
        records = [r for r in records if r["vertical"] == args.vertical]

    if not args.manifest_only:
        print(f"downloading · {len(records)} photos · {args.jobs} threads")
        done: list[dict] = []
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for i, res in enumerate(
                    pool.map(lambda r: process_one(r, force=args.force), records), 1):
                if res:
                    done.append(res)
                if i % 50 == 0 or i == len(records):
                    print(f"  {i:>4}/{len(records)}", flush=True)
        records = done

    # Merge back so a per-vertical run never drops the other verticals.
    final = dict(existing)
    for r in records:
        final[r["id"]] = r
    final = {k: v for k, v in final.items()
             if (v.get("files") or {}).get("lg") and v.get("og")}

    by_vertical: dict[str, int] = {}
    for r in final.values():
        by_vertical[r["vertical"]] = by_vertical.get(r["vertical"], 0) + 1

    MANIFEST.write_text(json.dumps(
        {"source": "pexels", "license": "https://www.pexels.com/license/",
         "photos": sorted(final.values(), key=lambda r: (r["vertical"], r["topic"], r["id"]))},
        ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(f.stat().st_size for f in PHOTO_DIR.rglob("*") if f.is_file())
    print(f"\n{len(final)} photos · {total / 1e6:.0f} MB · {MANIFEST}")
    for v, n in sorted(by_vertical.items()):
        print(f"  {v:16s} {n:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
