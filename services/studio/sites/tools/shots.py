"""Screenshot rendered demo pages for visual review.

    python tools/shots.py <html-dir> <out-dir> [--width 1440] [--mobile] [--full]

Defaults to above-the-fold desktop captures, which is what actually needs
judging; `--full` captures the whole page.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=980)
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*.html"))
    if args.limit:
        files = files[: args.limit]

    width, height = (390, 844) if args.mobile else (args.width, args.height)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            is_mobile=args.mobile,
            has_touch=args.mobile,
        )
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        for f in files:
            page.goto(f.resolve().as_uri())
            page.wait_for_timeout(320)
            if args.full:
                # Trigger every scroll-reveal before capturing.
                page.evaluate(
                    "()=>{document.querySelectorAll('.rv').forEach(e=>e.classList.add('in'))}"
                )
                page.wait_for_timeout(150)
            page.screenshot(path=str(dst / f"{f.stem}.png"), full_page=args.full)
        browser.close()

    print(f"{len(files)} shots -> {dst}")
    if errors:
        print(f"JS errors ({len(errors)}):")
        for err in dict.fromkeys(errors):
            print("  -", err)
    else:
        print("no JS errors")


if __name__ == "__main__":
    main()
