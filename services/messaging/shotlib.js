// ---------------------------------------------------------------------------
// Demo-site screenshots — the rendering itself.
//
// Shared by the bulk renderer that runs on the laptop (shotgen.js) and by the
// sidecar's on-demand fallback (shot.js), so the picture a prospect receives is
// the same picture either way.
//
// Three things decide whether the image sells the site, and none of them is
// resolution:
//
//  1. **The concept banner has to go.** Every demo carries a strip reading
//     "Concept site built for X · private link, not listed or indexed". That is
//     the right thing to show someone who OPENS the site. It is the wrong thing
//     to lead a WhatsApp message with, thirty seconds after being told on the
//     phone "मैंने आपके लिए website बना दी है".
//  2. **The photographs have to have loaded.** A hero with empty image slots is
//     a screenshot of nothing. Chrome's CLI screenshot mode gets this wrong
//     constantly, which is why this drives a real browser and waits for the
//     network to settle and the fonts to resolve.
//  3. **The frame has to be the hero, and the shape has to suit a chat
//     bubble.** WhatsApp shows a wide image nearly full width and center-crops
//     a tall one to a sliver, so this takes 1280x800 of the TOP of the document
//     — the part that reads unmistakably as "a website".
//
// The capture is clipped from the document origin rather than from whatever is
// currently on screen. That is not fussiness: the first version screenshotted
// the live viewport, a lazy-loading hero scrolled the page after the DOM edits,
// and the prospect would have been sent a picture of the "WHAT WE SHOOT"
// services grid instead of their own name.
// ---------------------------------------------------------------------------

// 1280x1400 rendered at 1.5x — 1920x2100 out, around 350KB.
//
// The shape first: nearly square is what WhatsApp gives the most room to in a
// chat bubble. A 16:9 strip renders small, and a tall portrait gets centre-
// cropped to a sliver in the preview. On this template it is also the height
// that reaches the photographs: a wedding photographer sent a picture of his own
// website with none of the photography in it has been shown a page of type.
//
// Then the scale. Rendering above the delivered size and letting the downscale
// do the anti-aliasing is what keeps the headline crisp; capturing at 1x gives
// WhatsApp something already soft to re-encode. 2x was tried and is simply
// wasted — 633KB that WhatsApp throws away on the way through, on a connection
// the recipient is probably paying for by the megabyte.
//
export const VIEWPORT = { width: 1280, height: 1400, deviceScaleFactor: 1.5 }
export const JPEG_QUALITY = 86

// What gets stripped before the shutter opens, by class, because the demo
// template has stable ones and text-matching does not.
//
//   .demo-bar  the "Concept site built for X · private link, not listed or
//              indexed" strip. Right for someone who OPENS the site; wrong as
//              the first thing in a WhatsApp message sent thirty seconds after
//              "मैंने आपके लिए website बना दी है".
//   .fab       the floating WhatsApp bubble, which sits over the hero.
//   .skip      the visually-hidden skip-to-content link.
//
// The site header is deliberately KEPT: their name and navigation are most of
// what makes the picture read as a real website rather than a mockup.
const STRIP = ['.demo-bar', '.fab', '.skip', '[class*="cookie"]', '[id*="cookie"]']

export function launchArgs() {
  return [
    '--hide-scrollbars',
    '--font-render-hinting=none', // match what a real display does
    '--force-color-profile=srgb',
    '--disable-dev-shm-usage',
  ]
}

// prepare strips the page of what should not appear in a marketing image and
// makes sure every photograph in the hero has actually arrived.
//
// It does NOT scroll. An earlier version scrolled to the bottom to trigger lazy
// images and back to the top again, and the screenshot came back showing the
// "WHAT WE SHOOT" section — the scroll had not settled when the shutter opened,
// so the prospect would have been sent a picture of a services grid instead of
// their own name. Lazy images are released by briefly making the viewport as
// tall as the document instead, which moves nothing.
async function prepare(page) {
  await page.evaluate((strip) => {
    for (const sel of strip) {
      for (const el of Array.from(document.querySelectorAll(sel))) {
        el.style.display = 'none'
      }
    }
    // Freeze animations so nothing is caught mid-transition, and force anything
    // that reveals on scroll to be fully revealed — at document top the hero is
    // often still below an IntersectionObserver's threshold.
    const css = document.createElement('style')
    css.textContent =
      '*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;' +
      'transition-duration:0s!important;transition-delay:0s!important;}' +
      '[data-aos],.fade-in,.animate-in,.reveal{opacity:1!important;transform:none!important;visibility:visible!important;}'
    document.head.appendChild(css)

    for (const img of Array.from(document.images)) img.loading = 'eager'
  }, STRIP)

  // Briefly grow the viewport so everything above the fold — and a little
  // below — counts as visible, then put it back.
  await page.setViewport({ ...VIEWPORT, height: 2000 })
  await page
    .evaluate(() =>
      Promise.all(
        Array.from(document.images)
          .filter((i) => !i.complete)
          .map((i) => new Promise((r) => { i.onload = i.onerror = r })),
      ),
    )
    .catch(() => {})
  await page.setViewport(VIEWPORT)

  await page.evaluate(() => document.fonts?.ready).catch(() => {})
  await page.evaluate(() => window.scrollTo(0, 0))
  await new Promise((r) => setTimeout(r, 400))
}

// renderHero loads one demo page and returns a JPEG buffer of its hero, or
// throws. Callers decide what a failure means; for the sidecar it means "send
// the link on its own", never "lose the lead".
export async function renderHero(browser, target, opts = {}) {
  const page = await browser.newPage()
  try {
    await page.setViewport(VIEWPORT)
    // networkidle2 rather than 'load': these are static pages whose hero
    // photographs are fetched from a separate asset host well after load fires.
    await page.goto(target, { waitUntil: 'networkidle2', timeout: 60_000 })
    await prepare(page)

    // The plain viewport, with the page known to be at the top. No clip and no
    // captureBeyondViewport: both interact with scroll position in ways that
    // produced screenshots of the middle of the page.
    return await page.screenshot({ type: 'jpeg', quality: opts.quality || JPEG_QUALITY })
  } finally {
    await page.close().catch(() => {})
  }
}

// shotKey is the filename a demo URL maps to. The bulk renderer on the laptop
// and the sidecar on the server both derive it the same way, which is what lets
// one pre-render a file the other simply finds.
export async function shotKey(demoUrl) {
  const { createHash } = await import('node:crypto')
  return createHash('sha1').update(String(demoUrl).trim()).digest('hex').slice(0, 16) + '.jpg'
}
