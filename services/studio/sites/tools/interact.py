#!/usr/bin/env python
"""Behaviour test: drive the things a prospect actually touches.

    python tools/interact.py out/sites --sample 8

`verify.py` audits the document — structure, contrast of concerns, whether
anything overflows. It cannot tell you that the WhatsApp button composes the
right message, because nothing about that is visible in the DOM until someone
clicks. This drives five interactions per page and asserts the outcome:

  sticky header   the shared stylesheet and script actually loaded and ran
  mobile nav      opens, and is `visibility:visible` rather than merely moved
  lightbox        opens on a tile AND swaps in the wide rendition, not the
                  700px tile blown up
  enquiry form    composes a `wa.me` deep link with the typed fields in it
  validation      an empty form does not open anything

The enquiry form is the single most demonstrable thing on the page during a
call, and it is pure client-side JS with no backend to fail loudly — so it is
exactly the kind of thing that breaks silently and stays broken.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import urllib.parse
from pathlib import Path


async def check(page, ctx, path: Path) -> list[str]:
    fails: list[str] = []
    await page.goto(path.resolve().as_uri(), wait_until="load")
    await page.wait_for_timeout(350)

    await page.evaluate("window.scrollTo(0,400)")
    await page.wait_for_timeout(220)
    if await page.get_attribute("#siteHead", "data-stuck") != "1":
        fails.append("sticky header never engaged (script did not run?)")
    await page.evaluate("window.scrollTo(0,0)")

    await page.set_viewport_size({"width": 390, "height": 800})
    await page.click("#navToggle")
    await page.wait_for_timeout(340)
    state = await page.evaluate(
        "() => {const n = document.getElementById('navLinks');"
        " return [n.dataset.open, getComputedStyle(n).visibility];}")
    if state != ["1", "visible"]:
        fails.append(f"mobile nav did not open: {state}")
    await page.click("#navToggle")
    await page.set_viewport_size({"width": 1440, "height": 900})
    await page.wait_for_timeout(200)

    if await page.query_selector(".tile"):
        await page.click(".tile")
        await page.wait_for_timeout(420)
        lb = await page.evaluate(
            "() => {const s = document.getElementById('lbStage');"
            " const i = s && s.querySelector('img,svg');"
            " return {open: document.getElementById('lb').open,"
            "  tag: i ? i.tagName : null,"
            "  src: i && i.tagName === 'IMG' ? i.getAttribute('src') : ''};}")
        if not lb["open"]:
            fails.append("lightbox did not open")
        elif lb["tag"] == "IMG" and not lb["src"].endswith("-lg.webp"):
            fails.append(f"lightbox showed the tile rendition: {lb['src']}")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

    form = await page.query_selector("#enq")
    if form:
        await page.evaluate(
            "() => {window.__opened = null;"
            " window.open = (u) => {window.__opened = u; return null;};}")
        await page.click("#enq button[type=submit]")
        await page.wait_for_timeout(220)
        if await page.evaluate("() => window.__opened") is not None:
            fails.append("empty enquiry form still opened WhatsApp")

        await page.fill("#f-name", "Priya Sharma")
        if await page.query_selector("#f-phone"):
            await page.fill("#f-phone", "+91 98765 43210")
        await page.evaluate("() => {window.__opened = null;}")
        await page.click("#enq button[type=submit]")
        await page.wait_for_timeout(320)
        url = await page.evaluate("() => window.__opened")
        wa = await page.get_attribute("#enq", "data-wa")
        if wa and not (url or "").startswith("https://wa.me/"):
            fails.append(f"enquiry form did not open a wa.me link: {url!r}")
        elif url:
            text = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("text", [""])[0]
            if "Priya Sharma" not in text:
                fails.append("typed name missing from the composed message")
    return fails


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--sample", type=int, default=8)
    ap.add_argument("--seed", type=int, default=4)
    args = ap.parse_args()

    files = sorted(Path(args.src).glob("*.html"))
    if not files:
        print("no pages found", file=sys.stderr)
        return 2
    sample = random.Random(args.seed).sample(files, min(args.sample, len(files)))

    from playwright.async_api import async_playwright

    failures: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: failures.append(f"JS error: {e}"))
        for f in sample:
            for msg in await check(page, ctx, f):
                failures.append(f"{f.name}: {msg}")
            print(".", end="", flush=True)
        await browser.close()

    print(f"\n{len(sample)} pages driven")
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for msg in failures[:30]:
            print("  -", msg)
        return 1
    print("nav, lightbox and enquiry form all behave")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
