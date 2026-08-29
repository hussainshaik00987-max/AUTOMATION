#!/usr/bin/env python
"""Generate a demo site for every lead in the CSV.

    python build.py                          # all leads -> out/
    python build.py --limit 50               # quick sample
    python build.py --vertical bakery        # one vertical
    python build.py --template wp-noir       # one template, for design work
    python build.py --out /tmp/demo -j 8

Rendering is CPU-bound string building, so it fans out across processes. Each
page is fully self-contained: inline CSS, inline SVG, inline JS, a data-URI
favicon, and no network requests at all.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import photos as photo_lib  # noqa: E402
from engine.data import Business, load  # noqa: E402
from engine.index_page import _row, build_index  # noqa: E402
from engine.blocks import SCRIPT  # noqa: E402
from engine.render import render, write_shared_asset  # noqa: E402
from engine.templates import BY_KEY, TEMPLATES, assign  # noqa: E402
from engine.theme import audit_palettes  # noqa: E402

HERE = Path(__file__).resolve().parent
# The lead list comes from services/discovery. Falls back to the synthetic
# sample so a fresh checkout runs end to end without a scrape.
_LEADS = HERE.parents[1] / "discovery" / "exports" / "top_leads.csv"
_SAMPLE = HERE.parents[1] / "discovery" / "data" / "sample_top_leads.csv"
DEFAULT_CSV = _LEADS if _LEADS.exists() else _SAMPLE
ASSET_SRC = HERE / "assets"


def _render_one(
    args: tuple[Business, str | None, str, str, bool, bool, str],
) -> tuple[str, int, str, str, str, str]:
    """Worker: render one business and write it. Returns index metadata."""
    b, force_template, out_dir, base_url, use_photos, inline, asset_url = args
    template, palette_key = assign(b)
    if force_template:
        template = BY_KEY[force_template]
        palette_key = template.palettes[(b.seed >> 17) % len(template.palettes)]
    out_root = None if inline else Path(out_dir)
    html_text = render(b, template, palette_key, base_url=base_url,
                       asset_base_url=asset_url, use_photos=use_photos,
                       out_root=out_root)
    path = Path(out_dir) / "sites" / f"{b.slug}.html"
    path.write_text(html_text, encoding="utf-8")
    css_name = ""
    if out_root is not None:
        m = re.search(r'assets/css/([0-9a-f]+\.css)', html_text)
        css_name = m.group(1) if m else ""
    return b.slug, len(html_text), template.key, template.name, palette_key, css_name


# --------------------------------------------------------------------------
# static assets + hosting config
# --------------------------------------------------------------------------
def mirror_assets(out: Path) -> tuple[int, int]:
    """Copy `assets/` into the output tree, skipping files already identical.

    The photo library is tens of megabytes and does not change between builds,
    so a size-and-mtime comparison keeps a rebuild at a second or two instead of
    re-copying everything each time.
    """
    if not ASSET_SRC.exists():
        return 0, 0
    copied = skipped = 0
    wanted: set[Path] = set()
    for src in ASSET_SRC.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(ASSET_SRC)
        dst = out / "assets" / rel
        wanted.add(dst)
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    # Drop renditions the library no longer produces. Without this a rename or
    # a re-encode leaves the superseded files behind and they ship with the
    # deploy — invisible locally, and paid for on every upload.
    live = out / "assets" / "photos"
    for stale in live.rglob("*"):
        if stale.is_file() and stale not in wanted:
            stale.unlink()
    return copied, skipped


# Demo sites carry someone else's business name and are not meant to compete
# with (or impersonate) that business in search — hence noindex everywhere.
# The social crawlers are allowed through explicitly, because a blocked
# crawler is exactly what turns a WhatsApp link into a bare grey rectangle.
ROBOTS = """User-agent: WhatsApp
Allow: /

User-agent: facebookexternalhit
Allow: /

User-agent: facebookcatalog
Allow: /

User-agent: Twitterbot
Allow: /

User-agent: LinkedInBot
Allow: /

User-agent: TelegramBot
Allow: /

User-agent: Slackbot-LinkExpanding
Allow: /

User-agent: *
Disallow: /
"""

VERCEL_CONFIG = {
    "$schema": "https://openapi.vercel.sh/vercel.json",
    "cleanUrls": True,
    "trailingSlash": False,
    "headers": [
        {
            # Stylesheets are named after a digest of their contents and photos
            # after the source photo id, so neither is ever edited in place —
            # a year of immutable caching is safe and makes a repeat open free.
            "source": "/assets/(css|photos)/(.*)",
            "headers": [
                {"key": "Cache-Control",
                 "value": "public, max-age=31536000, immutable"},
            ],
        },
        {
            # site.js is NOT content-addressed, so it must revalidate or a
            # behaviour fix would never reach anyone holding a cached copy.
            "source": "/assets/js/(.*)",
            "headers": [
                {"key": "Cache-Control",
                 "value": "public, max-age=0, s-maxage=86400, "
                          "stale-while-revalidate=604800"},
            ],
        },
        {
            # Pages are regenerated whenever the CSV changes, so they revalidate
            # while still being served instantly from the edge cache.
            "source": "/sites/(.*)",
            "headers": [
                {"key": "Cache-Control",
                 "value": "public, max-age=0, s-maxage=86400, "
                          "stale-while-revalidate=604800"},
                {"key": "X-Content-Type-Options", "value": "nosniff"},
                {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
            ],
        },
    ],
}


def write_hosting_files(out: Path, base_url: str, pages: int) -> None:
    (out / "robots.txt").write_text(ROBOTS, encoding="utf-8")
    (out / "vercel.json").write_text(
        json.dumps(VERCEL_CONFIG, indent=2) + "\n", encoding="utf-8")
    (out / "404.html").write_text(NOT_FOUND, encoding="utf-8")
    (out / "build-info.json").write_text(json.dumps({
        "pages": pages,
        "base_url": base_url,
        "photos": photo_lib.stats(),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=1) + "\n", encoding="utf-8")


ROOT_INDEX = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Concept sites</title><meta name="robots" content="noindex,nofollow">
<style>
body{margin:0;min-height:100svh;display:grid;place-items:center;
  font:16px/1.65 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  background:#0e1211;color:#f2f5f4;text-align:center;padding:2rem}
h1{font-size:clamp(1.6rem,4vw,2.4rem);margin:0 0 .7rem;letter-spacing:-.02em}
p{opacity:.7;margin:0;max-width:46ch}
</style></head><body><div>
<h1>Nothing to see here</h1>
<p>This host serves individual concept sites by direct link. If you were sent
one, use the link you were given.</p>
</div></body></html>
"""

NOT_FOUND = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Not found</title><meta name="robots" content="noindex">
<style>
:root{color-scheme:light dark}
body{margin:0;min-height:100svh;display:grid;place-items:center;
  font:16px/1.6 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  background:#0e1211;color:#f2f5f4;text-align:center;padding:2rem}
h1{font-size:clamp(2rem,6vw,3.2rem);margin:0 0 .6rem;letter-spacing:-.02em}
p{opacity:.72;margin:0 0 1.8rem}
a{color:#8fd6b8}
</style></head><body><div>
<h1>That page has moved on</h1>
<p>The concept site you are looking for is not here.</p>
<a href="/">Back to the index</a>
</div></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--vertical", default="")
    ap.add_argument("--template", default="", help="force one template for every lead")
    ap.add_argument("--base-url", default=os.environ.get("DEMO_BASE_URL", ""),
                    help="public origin the pages will be served from, e.g. "
                         "https://demos.example.com. Required for WhatsApp / "
                         "Open Graph link previews, which need absolute image "
                         "URLs. Falls back to $DEMO_BASE_URL.")
    ap.add_argument("--inline-assets", action="store_true",
                    help="inline CSS and JS into every page instead of linking "
                         "shared files. Fully self-contained pages, but ~3x the "
                         "total bytes — use for a USB stick, not for hosting.")
    ap.add_argument("--asset-base-url", default=os.environ.get("DEMO_ASSET_URL", ""),
                    help="serve the photo library from a separate origin, e.g. "
                         "https://x-assets.vercel.app. Pages then reference it "
                         "absolutely and the library is NOT copied into --out, "
                         "which is how a 153MB build fits a 100MB upload cap. "
                         "Omit it and photos stay local and relative.")
    ap.add_argument("--publish-catalogue", action="store_true",
                    help="also write the lead catalogue to out/index.html. It "
                         "lists every lead with score, pitch angle and phone "
                         "number, so only do this behind access control.")
    ap.add_argument("--no-photos", action="store_true",
                    help="skip the stock photo library and use procedural SVG only")
    ap.add_argument("-j", "--jobs", type=int, default=min(16, (os.cpu_count() or 4)))
    args = ap.parse_args()

    base_url = args.base_url.rstrip("/")
    if base_url and not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    use_photos = not args.no_photos and bool(photo_lib.LIBRARY)
    asset_base = args.asset_base_url.rstrip("/")
    if asset_base and not asset_base.startswith(("http://", "https://")):
        asset_base = "https://" + asset_base

    problems = audit_palettes()
    if problems:
        print(f"palette contrast failures ({len(problems)}):", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 1

    if args.template and args.template not in BY_KEY:
        print(f"unknown template {args.template!r}. Known: {', '.join(sorted(BY_KEY))}",
              file=sys.stderr)
        return 2

    leads = load(args.csv, limit=args.limit or None)
    if args.vertical:
        leads = [b for b in leads if b.vertical == args.vertical]
    if not leads:
        print("no leads matched", file=sys.stderr)
        return 3

    out = Path(args.out)
    (out / "sites").mkdir(parents=True, exist_ok=True)

    lib = photo_lib.stats()
    print(f"{len(leads):,} leads · {len(TEMPLATES)} templates · {args.jobs} workers")
    if use_photos:
        print(f"photos: {sum(lib.values())} in library "
              f"({', '.join(f'{k} {v}' for k, v in lib.items())})")
    else:
        print("photos: off — procedural artwork only")
    if base_url:
        print(f"base url: {base_url}")
    if asset_base:
        print(f"asset origin: {asset_base}  (photos not copied into --out)")
    else:
        print("base url: none — link previews will have NO image. "
              "Pass --base-url https://your-domain to fix.", file=sys.stderr)
    started = time.time()

    if not args.inline_assets:
        write_shared_asset(out, "js", "site.js", SCRIPT)
    payload = [(b, args.template or None, str(out), base_url, use_photos,
                args.inline_assets, asset_base) for b in leads]
    results: list[tuple[str, int, str, str, str, str]] = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for i, res in enumerate(pool.map(_render_one, payload, chunksize=16), 1):
            results.append(res)
            if i % 250 == 0 or i == len(leads):
                rate = i / max(time.time() - started, 1e-9)
                print(f"  {i:>5}/{len(leads)}  {rate:5.0f} pages/sec", flush=True)

    by_slug = {r[0]: r for r in results}
    rows = [
        _row(b, by_slug[b.slug][2], by_slug[b.slug][3], by_slug[b.slug][4])
        for b in leads if b.slug in by_slug
    ]

    total_bytes = sum(r[1] for r in results)
    used_templates = {r[2] for r in results}
    stats = {
        "demo sites": f"{len(results):,}",
        "templates used": f"{len(used_templates)}/{len(TEMPLATES)}",
        "cities": f"{len({b.city for b in leads if b.city}):,}",
        "avg page size": f"{total_bytes // max(len(results), 1) // 1024} KB",
        "photographs": f"{sum(lib.values())}" if use_photos else "0",
    }
    # The catalogue is the sales pipeline: 2,000 leads with scores, pitch
    # angles, phone numbers and WhatsApp deep links. It is deliberately written
    # OUTSIDE `out/` so that pushing the deployable tree to a repo cannot put it
    # on the public internet. The demos themselves only ever restate what is
    # already on a business's public Google listing.
    # Once the site is live the catalogue should hand over the URL the prospect
    # will actually receive, not a file:// path — the whole point of the page is
    # to open a demo on one screen and paste its link into WhatsApp on the other.
    if base_url:
        catalogue = build_index(rows, stats, site_prefix=f"{base_url}/sites/",
                                site_suffix="")
    else:
        catalogue = build_index(rows, stats, site_prefix="out/sites/")
    (HERE / "catalogue.html").write_text(catalogue, encoding="utf-8")
    if args.publish_catalogue:
        (out / "index.html").write_text(
            build_index(rows, stats, site_prefix="sites/"), encoding="utf-8")
    else:
        (out / "index.html").write_text(ROOT_INDEX, encoding="utf-8")

    # A stylesheet is named after a digest of its contents, so editing css.py
    # mints new names and orphans the old ones. Without this the directory grows
    # by ~2.7MB of dead CSS on every design change and ships them all.
    live_css = {r[5] for r in results if r[5]}
    css_dir = out / "assets" / "css"
    dropped = 0
    if live_css and css_dir.exists():
        for stale in css_dir.iterdir():
            if stale.is_file() and stale.name not in live_css:
                stale.unlink()
                dropped += 1

    # With a separate asset origin the pages never look for a local copy, so
    # mirroring 93MB into the output would be 93MB of dead weight in the upload.
    copied, skipped = (0, 0) if asset_base else mirror_assets(out)
    if asset_base:
        shutil.rmtree(out / "assets" / "photos", ignore_errors=True)
    write_hosting_files(out, base_url, len(results))

    elapsed = time.time() - started
    print(f"{chr(10)}{len(results):,} pages in {elapsed:.1f}s "
          f"({len(results) / elapsed:.0f}/sec, {total_bytes / 1e6:.0f} MB html)")
    css_files = sorted((out / "assets" / "css").glob("*.css"))
    if css_files:
        css_bytes = sum(f.stat().st_size for f in css_files)
        print(f"stylesheets: {len(css_files)} shared "
              f"({css_bytes / 1024:.0f} KB total, "
              f"{css_bytes / len(css_files) / 1024:.0f} KB each)"
              + (f", {dropped} stale removed" if dropped else ""))
    print(f"assets: {copied} copied, {skipped} already current")
    print(f"catalogue: {HERE / 'catalogue.html'}  (local only)")
    if args.publish_catalogue:
        print("  ! --publish-catalogue: the lead list will be public at /",
              file=sys.stderr)
    if not base_url:
        print(f"{chr(10)}WARNING: built without --base-url, so og:image is omitted "
              "and WhatsApp will show a text-only preview.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
