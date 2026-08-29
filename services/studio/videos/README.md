# studio/videos

A personalised 9:16 demo video for every lead in the discovery export.

Each video shows **the customer's phone**: an iPhone running WhatsApp, with *the
lead's own business name* in the chat header, at 11-something PM. A customer
asks the questions that trade actually gets asked; the business replies in
seconds — typing indicator, read receipts, the lot — and books the job. Then the
phone pulls back and the copy lands: nobody typed a word.

Putting the business's name in the header is the whole point of the
personalisation. An owner scrubbing a demo video recognises their own chat
before they have read a single caption.

```
out/scenes/<slug>.html   one self-contained scene per lead
out/bg/<vertical>-<n>.jpg  20 baked backdrops, shared across scenes
out/mp4/<slug>.mp4       1080x1920, H.264, 30fps, ~35s, ~7MB
out/manifest.json        slug -> name, trade, city, paths
```

## Run

```bash
python build.py --limit 8                    # scenes only, fast
python build.py --limit 8 --video            # ...and encode them
python build.py --slug meridian-studios --video
python build.py --vertical bakery --video --jobs 8
python build.py --all --video --jobs 8       # the full run, resumable

python tools/verify.py out/scenes --sample 160   # scene correctness checks
python tools/qa_video.py                        # rendered mp4s are deliverable
python tools/gallery.py                         # -> out/index.html, browsable
python tools/progress.py --every 1800           # live rate/ETA during a batch
python tools/shots.py out/scenes/foo.html /tmp/s --sheet /tmp/sheet.png
```

Measured on the reference machine (16 cores, RTX 3050): **~237 videos/hour** at
`--jobs 10 --encoder nvenc`, so a full 2,000-lead run takes roughly 8.5 hours and
about 15GB. Throughput plateaus around 34 screenshots/second no matter the worker
count — see Performance below.

Both stages skip work already on disk; `--force` rebuilds. Killing a batch
mid-run loses at most one video per worker.

### Requirements

`playwright` (+ `playwright install chromium`), `numpy`, `pillow`,
`imageio-ffmpeg`. Three fonts must be installed for the design to render as
intended — **Inter** (stands in for SF Pro), **Noto Sans Devanagari** (the Hindi
line in the outro) and **Noto Color Emoji**. They live in `assets/fonts/`; on
Windows they are registered per-user under
`HKCU\Software\Microsoft\Windows NT\CurrentVersion\Fonts`. Without them the page
silently falls back to Segoe UI and the whole thing looks like a different
product.

## How it renders

The page never animates itself. `runtime.js` exposes `seek(t)` which writes
every animated property as a pure function of the timeline position, and the
capture loop calls it frame by frame and screenshots. Nothing uses CSS
animations, transitions, `requestAnimationFrame` or `Date`. That means:

* the same HTML always produces the same video, and
* a slow machine produces the *same file* as a fast one rather than dropping
  frames, which is what would happen with any real-time recorder.

Timing lives in `storyboard.py`, in milliseconds, and is emitted as data
(`window.SCENE`). `runtime.js` only knows how to interpolate it.

### The bubble stack

Bubbles are absolutely positioned from the bottom of the thread and never
scrolled. Each carries a continuous *presence* in `[0,1]`; an item's distance
from the bottom is the sum of `(height + gap) * presence` over everything below
it. Because presence is continuous, the column slides smoothly as a bubble grows
in — and the typing indicator shrinking away while its reply grows in nets out
to one fluid motion instead of two competing ones.

Heights are measured once at init, with transforms cleared, because a scaled
element reports its scaled box.

## Content

`script.py` holds **32 conversation scripts across 14 trades**. A "trade" is
finer-grained than the scraper's vertical: a wedding photographer, a rental
studio and a video production house are all `wedding_photo` but get asked
completely different questions. Google's `Category` column picks the trade,
which picks both the script pool and the outro vocabulary ("Holds the date,
books the call" vs "Takes the order" vs "Books the free site visit").

Copy rules, because these go to real business owners:

* Every claim about *this* business comes from the CSV — price, city, area — or
  is phrased as a range true of the trade generally. A prospect who reads a
  fabricated price in their own demo is lost.
* Every script ends on the thing the owner cares about: a held date, a confirmed
  order, a booked visit.

Openers and reveal lines are pooled separately from the scripts, so two leads on
the same script still do not open on the same sentence. Across 2,000 leads that
yields **1,554 distinct (script, opener, reveal, backdrop) combinations**, the
largest identical group being 6 — before the business name, city, area, price,
avatar colour and wallpaper seed are substituted.

## Performance

Frames go into ffmpeg's stdin as MJPEG. The interesting work was getting from
2.2 fps to a batch that finishes in hours rather than days:

| Change | Effect |
|---|---|
| `filter: blur(190px)` on the glow blobs → multi-stop radial gradients | **308ms → ~0 per frame.** The blur was re-rasterised every time the ambient drift moved it: 74% of the entire frame cost, for two soft light pools a gradient renders identically. |
| Background (glows, key light, vignette, grain) baked to a JPEG by `backdrop.py` | ~44ms/frame → one blit. It is a *static* image; compositing it live re-evaluated it on every capture. |
| `backdrop-filter` removed from the nav bar, composer and cards | ~17ms/frame. Nothing sits behind them worth blurring — the thread ends 18pt above the composer. |
| `--disable-frame-rate-limit` | Headless pins every screenshot at exactly 50ms otherwise, regardless of page complexity. |
| Duplicate-frame detection | **~36% of frames never get screenshotted.** `seek()` returns an FNV-1a hash of every style it wrote; an equal hash means identical pixels, so the encoded JPEG is re-sent. This is why the camera holds dead still through the chat — a slow push-in is imperceptible at ~2px/sec and would change the phone's transform on every frame, defeating the whole optimisation. |
| `-threads` capped on x264 | Left to its default, x264 sizes its pool from the core count: ~24 threads and **~830MB per encoder**. Six workers exhausted 8GB and drove the machine into swap. |
| `h264_nvenc` when an NVIDIA GPU is present | Encoding moves to dedicated silicon, freeing every core for capture — which is the actual bottleneck. Measured 12+ concurrent sessions available. |

`--crf` is always on x264's scale whichever encoder runs; `NVENC_CQ_OFFSET`
translates it. Measured against the source frames on this footage, x264 CRF 21
gives 7.26MB / 44.3dB PSNR and the matching NVENC point is CQ 28 (~7.5MB /
~43.4dB). At 2x zoom on the bubble text they are indistinguishable.

## Output format

1080x1920, H.264 High @ 4.0, **`yuv420p(tv, bt709)`**, 30fps, `+faststart`.
~7.6MB for ~35s, comfortably inside WhatsApp's limit.

### Colour range — the one that nearly shipped broken

Frames arrive from Chromium as MJPEG, which is **full range** (`yuvj420p`,
0-255). Encoding straight from that produced files tagged
`yuvj420p(pc, bt709/unknown/unknown)` — full range, with primaries and transfer
unset — *even with `-pix_fmt yuv420p` on the command line*. Any player that
ignores the range flag then reads 0-255 content as 16-235 and crushes the dark
backdrop while blowing out the white bubbles, which is most of what this video
is made of.

The fix does the range conversion in an explicit filter instead of trusting the
encoder, then re-stamps the colour tags because `scale` drops them:

```
-vf scale=in_range=full:out_range=limited,format=yuv420p,
    setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709
-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709
```

Verified by decoding back to RGB: black still lands on 0, white on 255, mean
difference 0.7%. `tools/qa_video.py` asserts the pixel format on every file so
this cannot come back silently.

30fps rather than 60 because the motion is eased UI, and rather than 24 because
the reveal's slow moves judder below 30. Grain is baked into the backdrop: large
flat gradients posterise into visible bands once x264 quantises them, and a
couple of levels of noise dithers the ramp away for far less bitrate than the
quality bump that would otherwise be needed.

Antialiasing is forced to greyscale (`--disable-lcd-text`). Subpixel AA paints
coloured fringes on glyph edges, and 4:2:0 chroma subsampling smears those into
muddy haloes around every letter.

## Checking the output

`tools/verify.py` drives a real browser over a sample and fails on the things a
contact sheet cannot show: copy that ran outside the frame (the stage's
`overflow:hidden` crops it silently), a card title that wrapped to two lines, a
bubble wider than the screen, a timestamp sitting on top of the message text, an
unsubstituted `{slot}`, any network request, any console error.

`tools/qa_video.py` checks the rendered files rather than the scenes: codec,
resolution, pixel format *and colour range*, fps, duration, size, and that `moov`
precedes `mdat` (without faststart a phone on mobile data must read the whole
file before playing). It skips anything modified in the last 90 seconds, which is
a worker still writing rather than a broken file.

`tools/shots.py` renders chosen timeline positions to PNG and tiles them into a
contact sheet — encoding a 35-second video to look at one badly-placed bubble is
40 seconds of waiting; a six-up sheet is two.

`tools/gallery.py` writes `out/index.html`: every video playable inline,
filterable by vertical, trade and city, searchable by name, sorted by lead score.
Two thousand MP4s in a folder is not reviewable.

It also extracts a poster still per video into `out/posters/` (~27MB for 2,000).
That is not decoration: a `<video>` with `preload="metadata"` paints nothing —
not even with a `#t=` fragment, which needs media data the browser has not
fetched — so without real posters every tile is a black rectangle. With them the
video file itself is only attached on first click, so scrolling the grid does not
open thousands of files.

## Layout notes

The device is deliberately large in frame. The video is watched *on a phone*, so
a phone-within-the-frame is already ~65% scale; shrink it further and the chat
text stops being readable at the size this actually gets viewed at.

Everything inside `#screen` is authored in iPhone points (1px = 1pt) and scaled
with `zoom`, which re-runs layout rather than resampling, so type stays crisp at
video resolution. The screen is 402x874pt — iPhone 17 Pro, 2622x1206 at 3x.

Chat chrome follows WhatsApp's current iOS build: translucent nav bar the thread
scrolls under, a floating composer rather than a docked one, noticeably rounder
bubbles, and the app's own 8x13 tail geometry on the first bubble of each run.
WhatsApp tightens the gap within a run from the same sender and opens it up when
the sender changes — copying that is most of what makes a mocked thread read as
real.
