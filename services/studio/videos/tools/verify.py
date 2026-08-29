"""Drive a real browser over a sample of scenes and fail on anything broken.

Screenshots only prove the frame you looked at. These are the failures that are
invisible in a contact sheet and real on every affected video:

* a card title that wrapped to two lines, so the card is a different height to
  the four around it
* copy that ran outside the 1080x1920 frame, which the stage's overflow:hidden
  silently crops rather than reporting
* a bubble wider than the phone screen
* an unsubstituted {slot} that shipped as literal braces
* a network request, which would make frame timing depend on the network

    python tools/verify.py out/scenes --sample 80
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import CHROME_ARGS, STAGE_H, STAGE_W          # noqa: E402

SLOT = re.compile(r"\{[a-z_]{2,20}\}")

PROBE = """
() => {
  const W = %d, H = %d, bad = [];
  const push = (kind, detail) => bad.push({kind, detail});

  // copy layers must stay inside the frame
  for (const el of document.querySelectorAll('.layer')) {
    if (getComputedStyle(el).visibility === 'hidden') continue;
    if (parseFloat(el.style.opacity || '1') < 0.02) continue;
    const r = el.getBoundingClientRect();
    if (r.left < -1 || r.right > W + 1)
      push('layer-x', `${el.id} ${r.left.toFixed(0)}..${r.right.toFixed(0)}`);
    if (r.top < -2 || r.bottom > H + 2)
      push('layer-y', `${el.id} ${r.top.toFixed(0)}..${r.bottom.toFixed(0)}`);
  }

  // card titles are sized to sit on one line; two lines unbalances the stack
  for (const t of document.querySelectorAll('.card .t1')) {
    const lh = parseFloat(getComputedStyle(t).lineHeight) ||
               parseFloat(getComputedStyle(t).fontSize) * 1.2;
    if (t.offsetHeight > lh * 1.6)
      push('card-title-wrap', t.textContent.trim().slice(0, 46));
  }
  for (const t of document.querySelectorAll('.card .t2')) {
    const lh = parseFloat(getComputedStyle(t).lineHeight) ||
               parseFloat(getComputedStyle(t).fontSize) * 1.3;
    if (t.offsetHeight > lh * 2.4)
      push('card-sub-wrap', t.textContent.trim().slice(0, 46));
  }

  // bubbles must fit the screen, and the timestamp must not sit on the text
  const screen = document.getElementById('screen');
  const sw = screen.getBoundingClientRect().width;
  for (const b of document.querySelectorAll('.bubble')) {
    const r = b.getBoundingClientRect();
    if (r.width > sw - 4) push('bubble-wide', b.textContent.trim().slice(0, 40));
    const meta = b.querySelector('.meta'), sp = b.querySelector('.spacer');
    if (meta && sp) {
      const m = meta.getBoundingClientRect(), s = sp.getBoundingClientRect();
      if (m.left < s.left - 1.5)
        push('stamp-overlap', b.textContent.trim().slice(0, 40));
    }
  }

  // the header name is the personalisation; it must not be empty or blank
  const nm = document.getElementById('nav-name');
  if (!nm || !nm.textContent.trim()) push('nav-name-empty', '');

  return bad;
}
""" % (STAGE_W, STAGE_H)


def probe_times(duration: int, layers: list[dict]) -> list[int]:
    """Every layer at its most-visible moment, plus the end of the chat."""
    ts = {int(duration * 0.05)}
    for L in layers:
        ts.add(int(L["in"] + min(900, (L["out"] - L["in"]) * 0.5)))
        ts.add(int(max(L["in"], L["out"] - 400)))
    ts.add(duration - 300)
    return sorted(t for t in ts if 0 <= t < duration)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenes", help="directory of scene .html files")
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    files = sorted(Path(args.scenes).glob("*.html"))
    if len(files) > args.sample:
        files = random.Random(args.seed).sample(files, args.sample)

    from playwright.sync_api import sync_playwright

    failures: dict[str, list[str]] = {}
    net, jserr = [], []
    checked = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROME_ARGS)
        page = browser.new_context(
            viewport={"width": STAGE_W, "height": STAGE_H}).new_page()

        page.on("console", lambda m: jserr.append(f"{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: jserr.append(f"pageerror: {e}"))
        page.on("request", lambda r: net.append(r.url)
                if not r.url.startswith("file:") else None)

        for f in files:
            page.goto(f.resolve().as_uri(), wait_until="load")
            page.wait_for_function("document.documentElement.dataset.ready === '1'")
            duration = page.evaluate("window.__duration()")
            layers = page.evaluate("window.SCENE.layers")

            if not 25_000 <= duration <= 55_000:
                failures.setdefault("duration", []).append(f"{f.stem}: {duration}ms")

            text = page.evaluate("document.getElementById('stage').innerText")
            for hit in SLOT.findall(text):
                failures.setdefault("unfilled-slot", []).append(f"{f.stem}: {hit}")

            for t in probe_times(duration, layers):
                page.evaluate("t => window.__seek(t)", t)
                for bad in page.evaluate(PROBE):
                    failures.setdefault(bad["kind"], []).append(
                        f"{f.stem}@{t}: {bad['detail']}")
            checked += 1
        browser.close()

    if net:
        failures["network"] = sorted(set(net))[:10]
    if jserr:
        failures["js"] = sorted(set(jserr))[:10]

    print(f"{checked} scenes checked")
    if not failures:
        print("all checks passed")
        return 0
    for kind, items in sorted(failures.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{kind}  ({len(items)})")
        for line in items[:6]:
            print(f"    {line}")
        if len(items) > 6:
            print(f"    ... {len(items) - 6} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
