"""Verify generated pages against the promises made about them.

    python tools/verify.py out/sites --sample 40

Checks, per sampled page:
  * network      no request leaves the document (the "zero external requests"
                 claim, measured in a real browser rather than by grepping)
  * js           no uncaught page errors
  * a11y         one h1, labelled form controls, alt/role on images, unique ids,
                 landmarks, discernible button text
  * images       every photo actually decoded (a wrong relative path is
                 invisible in the HTML and fatal on the page), alt text present,
                 intrinsic width/height set so nothing reflows on load
  * social       Open Graph tags a WhatsApp preview needs: absolute https
                 og:image, declared dimensions, title and description
  * structure    valid parse, JSON-LD parses, no unresolved template markers
  * typography   no headline word rendered wider than its own column (which
                 `overflow-wrap` would split mid-word)
  * layout       no horizontal overflow at 390px and 1440px
Exit code is non-zero if anything fails.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AUDIT_JS = r"""
() => {
  const out = {errors: [], warns: []};
  const push = (m) => out.errors.push(m);

  // headings
  const h1 = document.querySelectorAll('h1');
  if (h1.length !== 1) push(`h1 count = ${h1.length}`);

  // landmarks
  if (!document.querySelector('main')) push('no <main>');
  if (!document.querySelector('header')) push('no <header>');
  if (!document.querySelector('footer')) push('no <footer>');
  if (!document.querySelector('nav')) push('no <nav>');

  // duplicate ids break label/aria wiring
  const ids = [...document.querySelectorAll('[id]')].map(e => e.id);
  const dupes = ids.filter((v, i) => ids.indexOf(v) !== i);
  if (dupes.length) push('duplicate ids: ' + [...new Set(dupes)].join(','));

  // every form control needs a label
  document.querySelectorAll('input,textarea,select').forEach(el => {
    const labelled = (el.id && document.querySelector(`label[for="${el.id}"]`))
      || el.getAttribute('aria-label') || el.closest('label');
    if (!labelled) push('unlabelled control: ' + (el.id || el.name || el.type));
  });

  // buttons and links need discernible text
  document.querySelectorAll('a,button').forEach(el => {
    const text = (el.textContent || '').trim();
    const alt = el.getAttribute('aria-label') || el.getAttribute('title');
    if (!text && !alt) push('control with no accessible name: ' + el.tagName +
      ' ' + (el.className || ''));
  });

  // svg must be labelled or explicitly decorative
  document.querySelectorAll('svg').forEach(el => {
    const ok = el.getAttribute('aria-hidden') === 'true'
      || el.getAttribute('aria-label') || el.getAttribute('role') === 'presentation';
    if (!ok) out.warns.push('svg without aria-hidden/label');
  });

  // photographs: a relative path that is one directory off looks perfectly
  // fine in the HTML and renders as a blank slot, so decode is what gets
  // asserted, not the markup.
  const imgs = [...document.querySelectorAll('img')];
  out.imgCount = imgs.length;
  imgs.forEach(el => {
    const where = (el.getAttribute('src') || '').split('/').pop();
    if (!el.complete || el.naturalWidth === 0) push('image failed to load: ' + where);
    const decorative = el.getAttribute('role') === 'presentation'
      || el.getAttribute('aria-hidden') === 'true';
    if (!el.hasAttribute('alt')) push('image without alt attribute: ' + where);
    else if (!el.alt.trim() && !decorative) out.warns.push('image with empty alt');
    // Intrinsic dimensions keep the slot from collapsing and reflowing.
    if (!el.getAttribute('width') || !el.getAttribute('height'))
      out.warns.push('image without width/height');
    if (!el.getAttribute('srcset')) out.warns.push('image without srcset');
  });
  // Exactly one eager image — the hero. More and they compete for the
  // connection on mobile data; none and the LCP element waits for the scanner.
  const eager = imgs.filter(el => el.dataset.wasLazy !== undefined
    ? el.dataset.wasLazy === 'false' : el.getAttribute('loading') === 'eager');
  if (imgs.length && eager.length !== 1)
    out.warns.push('eager images = ' + eager.length);

  // A headline word wider than its own column gets split mid-word by
  // `overflow-wrap:break-word` ("PHOTOGRAPH S DON'T"). Measuring the source
  // text is not enough: a display face set in uppercase renders ~20% wider
  // than what `textContent` returns, so the probe has to carry the computed
  // text-transform too.
  document.querySelectorAll('h1,h2,.stat .v').forEach(h => {
    const cs = getComputedStyle(h);
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font:'
      + cs.font + ';letter-spacing:' + cs.letterSpacing
      + ';text-transform:' + cs.textTransform + ';font-family:' + cs.fontFamily;
    document.body.appendChild(probe);
    let widest = 0, word = '';
    for (const w of h.textContent.split(/\s+/)) {
      if (!w) continue;
      probe.textContent = w;
      const px = probe.getBoundingClientRect().width;
      if (px > widest) { widest = px; word = w; }
    }
    probe.remove();
    const box = h.getBoundingClientRect().width;
    if (widest > box + 1) {
      // A scraped business name genuinely can be longer than any column and
      // has to break; only editorial copy is a build bug.
      const isName = h.closest('#about') !== null
        && h.tagName === 'H2';
      const msg = `${h.tagName} word split mid-word: '${word}' `
        + `${Math.round(widest)}px in a ${Math.round(box)}px box`;
      if (isName) out.warns.push('long business name wraps mid-word');
      else push(msg);
    }
  });

  // Open Graph: what WhatsApp reads when the link is pasted into a chat.
  const meta = (sel) => (document.querySelector(sel) || {}).content || '';
  const og = meta('meta[property="og:image"]');
  out.og = {image: og, title: meta('meta[property="og:title"]'),
            url: meta('meta[property="og:url"]')};
  if (!meta('meta[property="og:title"]')) push('no og:title');
  if (!meta('meta[property="og:description"]')) push('no og:description');
  if (og) {
    if (!/^https:\/\//.test(og)) push('og:image is not an absolute https URL: ' + og);
    if (!meta('meta[property="og:image:width"]')) out.warns.push('og:image without width');
    if (meta('meta[name="twitter:card"]') !== 'summary_large_image')
      out.warns.push('og:image present but twitter:card is not summary_large_image');
  } else {
    out.warns.push('no og:image — link previews will be text only');
  }

  // structured data must parse
  const ld = document.querySelector('script[type="application/ld+json"]');
  if (!ld) push('no JSON-LD');
  else { try { JSON.parse(ld.textContent); } catch (e) { push('JSON-LD invalid: ' + e.message); } }

  // meta
  if (!document.querySelector('meta[name="description"]')) push('no meta description');
  if (!document.title.trim()) push('empty title');
  if (!document.querySelector('link[rel="icon"]')) push('no favicon');

  // unresolved template markers leaking into copy. Word-bounded: a substring
  // test flags "Anand Nagar" for containing "nan".
  const body = document.body.innerText;
  if (body.includes('{city}') || body.includes('{{')) out.warns.push('unrendered placeholder');
  [/\bNone\b/, /\bundefined\b/, /\bnan\b/i, /\bNaN\b/].forEach(re => {
    if (re.test(body)) out.warns.push('suspicious text: ' + re.source);
  });

  return out;
}
"""


# Below-the-fold images are `loading="lazy"`, so at a fixed viewport most of
# them have simply never been requested — asserting on `complete` without this
# reports a broken path for every tile past the fold. Flipping them to eager
# and awaiting settlement is what actually proves the relative paths resolve.
FORCE_IMAGES_JS = r"""
() => {
  const imgs = [...document.images];
  // Remember what the page actually asked for; the audit's eager-count check
  // would otherwise be measuring this function rather than the build.
  imgs.forEach(i => { i.dataset.wasLazy = String(i.loading === 'lazy'); i.loading = 'eager'; });
  return Promise.all(imgs.map(i => i.complete ? null : new Promise(res => {
    const done = () => res(null);
    i.addEventListener('load', done, {once: true});
    i.addEventListener('error', done, {once: true});
    setTimeout(done, 5000);
  })));
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    files = sorted(Path(args.src).glob("*.html"))
    if not files:
        print("no pages found", file=sys.stderr)
        return 2
    sample = random.Random(args.seed).sample(files, min(args.sample, len(files)))

    from playwright.sync_api import sync_playwright

    failures: list[str] = []
    warn_counts: dict[str, int] = {}
    external: list[str] = []
    img_total = 0
    og_images: set[str] = set()
    pages_without_og = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        js_errors: list[str] = []
        page.on("pageerror", lambda exc: js_errors.append(str(exc)))
        page.on("console", lambda m: js_errors.append(m.text) if m.type == "error" else None)
        # Anything that is not the document itself is an external request.
        page.on("request", lambda r: external.append(r.url)
                if not r.url.startswith(("file://", "data:", "about:")) else None)

        for f in sample:
            js_errors.clear()
            page.goto(f.resolve().as_uri(), wait_until="load")
            page.wait_for_timeout(180)
            page.evaluate(FORCE_IMAGES_JS)
            page.wait_for_timeout(120)

            result = page.evaluate(AUDIT_JS)
            img_total += result.get("imgCount", 0)
            og = (result.get("og") or {}).get("image") or ""
            if og:
                og_images.add(og)
            else:
                pages_without_og += 1
            for err in result["errors"]:
                failures.append(f"{f.name}: {err}")
            for w in result["warns"]:
                warn_counts[w] = warn_counts.get(w, 0) + 1
            for err in js_errors:
                failures.append(f"{f.name}: JS {err}")

            # Horizontal overflow at both ends of the range. The test is whether
            # the page can actually be scrolled sideways — scrollWidth alone
            # counts clipped SVG ink and reports phantom overflow.
            for width in (390, 768, 1024, 1440):
                page.set_viewport_size({"width": width, "height": 800})
                page.wait_for_timeout(60)
                # Column widths move with the viewport, so a headline that fits
                # at 1440px can still split at 1024px.
                for err in page.evaluate(AUDIT_JS)["errors"]:
                    if "word split mid-word" in err:
                        failures.append(f"{f.name} @{width}px: {err}")
                scrolled = page.evaluate(
                    "() => {window.scrollTo(900,0); const x=window.scrollX;"
                    " window.scrollTo(0,0); return x;}"
                )
                if scrolled > 0:
                    failures.append(
                        f"{f.name}: page scrolls {scrolled}px sideways at {width}px"
                    )
            page.set_viewport_size({"width": 1440, "height": 900})

        browser.close()

    print(f"checked {len(sample)} pages from {len(files)}")
    print(f"photographs: {img_total} across the sample "
          f"({img_total / max(len(sample), 1):.1f} per page)")
    # Distinct og:images across a random sample is the cheapest proxy for
    # "do two prospects who compare links see the same picture".
    print(f"og:image · {len(og_images)} distinct over {len(sample) - pages_without_og}"
          f" pages" + (f" · {pages_without_og} with none" if pages_without_og else ""))
    if external:
        uniq = sorted(set(external))
        print(f"\nEXTERNAL REQUESTS ({len(uniq)}):")
        for u in uniq[:20]:
            print("  -", u)
        failures.append(f"{len(uniq)} external requests")
    else:
        print("external requests: 0")

    if warn_counts:
        print("\nwarnings:")
        for w, n in sorted(warn_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}x {w}")

    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for f in failures[:40]:
            print("  -", f)
        if len(failures) > 40:
            print(f"  … and {len(failures) - 40} more")
        return 1

    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
