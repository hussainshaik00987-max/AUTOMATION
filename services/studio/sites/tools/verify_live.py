#!/usr/bin/env python
"""Check the deployed site over the real network, not the local files.

    python tools/verify_live.py https://demo.example.com --sample 12

`verify.py` proves the build is correct. This proves the *deployment* is —
which is a different set of failure modes, and every one of them is invisible
locally:

  * Deployment Protection left on, so every page is a Vercel login wall
  * `cleanUrls` not applied, so the extensionless links in `og:url` 404
  * the photo library on a second origin that is unreachable or CORS-blocked
  * `og:image` returning HTML (a login page) instead of a JPEG — WhatsApp then
    shows no preview at all, and nothing about the page looks wrong
  * cache headers missing, so every open re-downloads 60KB of stylesheet
  * the lead catalogue accidentally published at /

Exit code is non-zero if anything fails.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# WhatsApp identifies itself like this; a robots.txt or WAF rule that blocks it
# is the single most likely cause of a preview that renders as bare text.
WHATSAPP_UA = "WhatsApp/2.23.20.0 A"


def fetch(url: str, ua: str = UA, method: str = "GET", timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": ua}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(200_000)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(4096)
    except Exception as exc:                                 # noqa: BLE001
        return 0, {}, str(exc).encode()


def check_page(base: str, slug: str) -> list[str]:
    bad: list[str] = []
    # The extensionless form is what og:url advertises, so that is what gets
    # tested — not the .html the file is actually named.
    status, hdrs, body = fetch(f"{base}/sites/{slug}")
    if status != 200:
        return [f"{slug}: page returned {status}"]
    text = body.decode("utf-8", "replace")
    if "Authentication Required" in text or "vercel.com/sso" in text:
        return [f"{slug}: behind Deployment Protection"]

    cache = hdrs.get("Cache-Control", "")
    if "s-maxage" not in cache:
        bad.append(f"{slug}: no edge cache header ({cache!r})")

    m = re.search(r'property="og:image" content="([^"]+)"', text)
    if not m:
        bad.append(f"{slug}: no og:image")
    else:
        img_status, img_hdrs, _ = fetch(m.group(1), ua=WHATSAPP_UA)
        ctype = img_hdrs.get("Content-Type", "")
        if img_status != 200:
            bad.append(f"{slug}: og:image {img_status} {m.group(1)}")
        elif not ctype.startswith("image/"):
            bad.append(f"{slug}: og:image served {ctype!r}, not an image")

    # One real photo from the page body, on whichever origin it lives.
    src = re.search(r'<img[^>]+src="(https?://[^"]+)"', text)
    if src:
        p_status, p_hdrs, _ = fetch(src.group(1), method="HEAD")
        if p_status != 200:
            bad.append(f"{slug}: photo {p_status} {src.group(1)}")
        elif "immutable" not in p_hdrs.get("Cache-Control", ""):
            bad.append(f"{slug}: photo not immutably cached")

    css = re.search(r'href="\.\./(assets/css/[^"]+)"', text)
    if css:
        c_status, c_hdrs, _ = fetch(f"{base}/{css.group(1)}", method="HEAD")
        if c_status != 200:
            bad.append(f"{slug}: stylesheet {c_status}")
        elif "immutable" not in c_hdrs.get("Cache-Control", ""):
            bad.append(f"{slug}: stylesheet not immutably cached")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", help="production origin, e.g. https://x.vercel.app")
    ap.add_argument("--sites", default="out/sites")
    ap.add_argument("--sample", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    failures: list[str] = []
    print(f"checking {base}")

    status, hdrs, body = fetch(f"{base}/")
    text = body.decode("utf-8", "replace")
    print(f"  /                     {status}")
    if status != 200:
        failures.append(f"root returned {status}")
    if "score" in text and "Open demo" in text:
        failures.append("THE LEAD CATALOGUE IS PUBLISHED AT / — take it down")
    if "Authentication Required" in text or "vercel.com/sso" in text:
        failures.append("site is behind Deployment Protection")

    r_status, _, r_body = fetch(f"{base}/robots.txt")
    robots = r_body.decode("utf-8", "replace")
    print(f"  /robots.txt           {r_status}")
    if r_status != 200:
        failures.append("no robots.txt")
    elif "WhatsApp" not in robots:
        failures.append("robots.txt does not allow the WhatsApp crawler")

    nf_status, _, _ = fetch(f"{base}/sites/definitely-not-a-real-slug")
    print(f"  unknown slug          {nf_status}")
    if nf_status != 404:
        failures.append(f"unknown slug returned {nf_status}, expected 404")

    slugs = [p.stem for p in sorted(Path(args.sites).glob("*.html"))]
    if not slugs:
        print("no local pages to sample from", file=sys.stderr)
        return 2
    sample = random.Random(args.seed).sample(slugs, min(args.sample, len(slugs)))
    print(f"  sampling {len(sample)} pages …")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for bad in pool.map(lambda s: check_page(base, s), sample):
            failures.extend(bad)

    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for f in failures[:25]:
            print("  -", f)
        return 1
    print(f"\n{len(sample)} live pages OK · og:image fetches as an image for the "
          f"WhatsApp crawler · assets immutably cached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
