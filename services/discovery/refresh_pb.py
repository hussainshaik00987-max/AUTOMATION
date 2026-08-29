"""Re-capture the Google Maps `pb` template.

Maps search results arrive via an XHR whose `pb` protobuf parameter encodes the field
selection. Google rotates that format periodically; when it does, the scraper's replayed
requests start returning ~5KB query echoes instead of ~210KB of results.

Run this to capture a fresh template into pb_template.txt:

    python refresh_pb.py

Requires: python -m playwright install chromium
"""
import re, sys, urllib.parse
from playwright.sync_api import sync_playwright
from config import UA, PB_TEMPLATE

PROBE = ("https://www.google.com/maps/search/wedding+photographer+mumbai/"
         "@19.0760,72.8777,12z?hl=en&gl=in")


def capture():
    urls = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True,
                              args=["--no-sandbox",
                                    "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(user_agent=UA, locale="en-IN",
                            viewport={"width": 1366, "height": 900})
        pg = ctx.new_page()
        pg.on("request", lambda r: urls.append(r.url)
              if ("/search?" in r.url and "tbm=map" in r.url) else None)
        pg.goto(PROBE, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(7000)
        try:
            if pg.query_selector('div[role="feed"]'):
                for _ in range(3):
                    pg.eval_on_selector('div[role="feed"]', "e => e.scrollBy(0, 2000)")
                    pg.wait_for_timeout(2500)
        except Exception as e:
            print("scroll warning:", e, file=sys.stderr)
        b.close()

    # Prefer a paginated request (it carries the !7i..!8i.. offset markers we rewrite)
    best = None
    for u in urls:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        pb = (q.get("pb") or [None])[0]
        if not pb:
            continue
        if re.search(r"!7i\d+!8i\d+", pb):
            best = pb
            if "8i20" in pb:
                break
    if not best:
        print("FAILED: no pb parameter with pagination markers captured", file=sys.stderr)
        return 1
    PB_TEMPLATE.write_text(best)
    print(f"captured pb template ({len(best)} chars) -> {PB_TEMPLATE}")
    print("pagination markers:", re.findall(r"!7i\d+!8i\d+", best))
    print("coords markers    :", re.findall(r"!1d[\d.]+!2d[\d.-]+!3d[\d.-]+", best))
    return 0


if __name__ == "__main__":
    sys.exit(capture())
