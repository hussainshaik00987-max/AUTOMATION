"""Scene HTML -> MP4.

The page never animates itself. This walks the timeline frame by frame, calling
`__seek(t)` and screenshotting, so output is a function of the scene data alone:
the same HTML always yields the same video, and a slow machine produces the same
file as a fast one rather than dropping frames.

Frames go straight into ffmpeg's stdin as MJPEG. Encoding a PNG inside Chromium
costs more than the whole rest of the frame — quality 96 JPEG is visually lossless
ahead of an x264 pass that is about to quantise everything anyway.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

STAGE_W, STAGE_H = 1080, 1920
# These JPEGs are an intermediate, not the deliverable: at q92 they sit around
# 45dB PSNR against the page, comfortably above the ~43.4dB the final H.264 pass
# lands at, so the encoder stays the limiting factor. Measured ~5% faster to
# capture than q96, which is worth having when capture is the whole bottleneck.
JPEG_QUALITY = 92
ENCODER_THREADS = 2

# --crf is expressed on x264's scale whichever encoder runs, so the same number
# means the same output. NVENC's CQ scale is not aligned with x264's CRF: on this
# footage, measured against the source frames, x264 CRF 21 lands at 7.26MB /
# 44.3dB PSNR and the matching NVENC point is CQ 28 (~7.5MB / ~43.4dB). At 2x
# zoom on the bubble text the two are indistinguishable.
NVENC_CQ_OFFSET = 7

# Subpixel (LCD) antialiasing paints coloured fringes on glyph edges. That is
# fine on a monitor and actively bad in video: 4:2:0 chroma subsampling smears
# the fringes into muddy haloes around every letter. Greyscale AA is correct here.
CHROME_ARGS = [
    "--force-color-profile=srgb",
    "--disable-lcd-text",
    "--font-render-hinting=none",
    "--disable-font-subpixel-positioning",
    # Headless paces captures to a fixed compositor cadence, which pins every
    # screenshot at exactly 50ms no matter how simple the page is.
    "--disable-frame-rate-limit",
    # Aggregate throughput across a dozen workers plateaus at ~34 shots/s on this
    # machine whatever the worker count, so the browsers are competing rather
    # than scaling. One raster thread each measured consistently (if slightly)
    # ahead of the default pool and keeps thread churn down.
    "--num-raster-threads=1",
    "--hide-scrollbars",
    "--mute-audio",
    "--disable-dev-shm-usage",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]


def ffmpeg_bin() -> str:
    for env in ("FFMPEG", "IMAGEIO_FFMPEG_EXE"):
        if os.environ.get(env):
            return os.environ[env]
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def has_nvenc() -> bool:
    """Is an NVIDIA encoder both compiled in and actually usable?

    Listing the encoder is not proof: ffmpeg advertises h264_nvenc on machines
    with no NVIDIA card at all. The only reliable check is to encode a frame.
    """
    try:
        probe = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=40)
        return probe.returncode == 0
    except Exception:
        return False


# Colour handling, and it is not optional.
#
# The frames arrive as MJPEG, which is *full range* (yuvj420p, 0-255). Encoding
# straight from that produced files tagged `yuvj420p(pc, bt709/unknown/unknown)`
# — full range, with primaries and transfer unset — even with `-pix_fmt yuv420p`
# on the command line. Any player that ignores the range flag, which is most of
# the Android pipeline, then treats 0-255 content as 16-235 and crushes the dark
# backdrop while blowing out the white bubbles.
#
# So the range conversion is done explicitly in a filter rather than left to the
# encoder, and `setparams` re-stamps the three colour tags afterwards because the
# scale filter drops them. Verified: black still lands on 0 and white on 255
# after a decode back to RGB, mean difference 0.7%.
_VF = ("scale=in_range=full:out_range=limited,format=yuv420p,"
       "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709")
_COLOUR = ["-vf", _VF, "-color_range", "tv",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def ffmpeg_cmd(out_path: Path, fps: int, crf: int, preset: str,
               threads: int = ENCODER_THREADS, encoder: str = "x264") -> list[str]:
    """H.264 High, yuv420p, faststart — the intersection of what WhatsApp,
    iOS Photos and every Android player will accept without re-encoding.

    Two backends, because the bottleneck moves depending on which is used:

    * **x264** — better compression, but it competes with Chromium for the very
      cores the capture loop needs. `-threads` matters a lot here: left to its
      default x264 sizes its pool from the core count, which on a 16-core box is
      ~24 threads and ~830MB *per encoder*. Six of those exhausted 8GB and drove
      the machine into swap while oversubscribing the CPU 9:1.
    * **nvenc** — encodes on the GPU's dedicated silicon, so it costs
      essentially no CPU and leaves every core for capture. GeForce parts cap
      concurrent NVENC sessions (8 on current drivers), so the worker count has
      to respect that ceiling.
    """
    base = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(fps), "-i", "-",
    ]
    tail = [
        *_COLOUR,
        "-g", str(fps * 3), "-keyint_min", str(fps),
        "-movflags", "+faststart",
        "-r", str(fps),
        str(out_path),
    ]

    if encoder == "nvenc":
        return base + [
            "-c:v", "h264_nvenc",
            # p7 is NVENC's slowest/highest-quality preset; on dedicated silicon
            # "slowest" still runs far ahead of the capture loop.
            "-preset", "p7", "-tune", "hq",
            "-rc", "vbr", "-cq", str(crf + NVENC_CQ_OFFSET), "-b:v", "0",
            "-maxrate", "14M", "-bufsize", "28M",
            # NVENC is tuned for camera footage and softens hard edges. This is
            # flat UI and type, so spatial AQ is pushed up to keep bits on the
            # sharp glyph edges instead of spending them on the smooth backdrop.
            "-spatial-aq", "1", "-aq-strength", "12",
            "-rc-lookahead", "20", "-bf", "3", "-b_ref_mode", "middle",
            "-profile:v", "high", "-level", "4.0",
        ] + tail

    return base[:1] + ["-threads", str(threads)] + base[1:] + [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-profile:v", "high", "-level", "4.0",
        "-x264-params", "colormatrix=bt709",
    ] + tail


class Renderer:
    """One browser, reused across many scenes — launch is ~400ms, so a batch that
    starts a browser per video spends more time booting Chromium than drawing."""

    def __init__(self, fps: int = 30, crf: int = 21, preset: str = "medium",
                 scale: float = 1.0, threads: int = ENCODER_THREADS,
                 encoder: str = "auto"):
        from playwright.sync_api import sync_playwright
        self.fps, self.crf, self.preset = fps, crf, preset
        self.scale, self.threads = scale, threads
        self.encoder = ("nvenc" if has_nvenc() else "x264") if encoder == "auto" \
            else encoder
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(args=CHROME_ARGS)
        self.ctx = self.browser.new_context(
            viewport={"width": STAGE_W, "height": STAGE_H},
            device_scale_factor=scale,
            reduced_motion="no-preference",
            color_scheme="light",
        )
        self.page = self.ctx.new_page()

    def close(self) -> None:
        try:
            self.ctx.close(); self.browser.close(); self._pw.stop()
        except Exception:
            pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def capture(self, html: Path, out: Path, on_frame=None) -> dict:
        page = self.page
        page.goto(html.resolve().as_uri(), wait_until="load")
        page.wait_for_function("document.documentElement.dataset.ready === '1'",
                               timeout=30_000)
        page.evaluate("document.fonts.ready")

        duration = page.evaluate("window.__duration()")
        n_frames = max(1, int(round(duration / 1000 * self.fps)))
        step = 1000.0 / self.fps

        out.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            ffmpeg_cmd(out, self.fps, self.crf, self.preset, self.threads,
                       self.encoder),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        t0 = time.time()
        prev_digest, prev_buf, reused = None, None, 0
        try:
            for i in range(n_frames):
                # seek() returns a hash of every style it wrote. Equal hash means
                # an identical frame, so the encoded JPEG is simply re-sent.
                digest = page.evaluate("t => window.__seek(t)", i * step)
                if digest == prev_digest and prev_buf is not None:
                    reused += 1
                else:
                    prev_buf = page.screenshot(type="jpeg", quality=JPEG_QUALITY,
                                               animations="disabled", caret="hide")
                    prev_digest = digest
                proc.stdin.write(prev_buf)
                if on_frame and i % 30 == 0:
                    on_frame(i, n_frames)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        err = proc.stderr.read().decode("utf-8", "replace")
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg failed ({rc}): {err[:600]}")

        elapsed = time.time() - t0
        return {
            "frames": n_frames,
            "shot": n_frames - reused,
            "reused": reused,
            "duration_ms": duration,
            "seconds": round(elapsed, 2),
            "fps_capture": round(n_frames / elapsed, 1) if elapsed else 0,
            "bytes": out.stat().st_size,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="Render scene HTML files to MP4.")
    ap.add_argument("scenes", nargs="+", help="scene .html files")
    ap.add_argument("--out", default="out/mp4", help="output directory")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=21)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--threads", type=int, default=ENCODER_THREADS,
                    help="x264 threads per encoder; keep low when running many workers")
    ap.add_argument("--encoder", default="auto", choices=("auto", "x264", "nvenc"),
                    help="auto probes for a usable NVIDIA encoder and falls back to x264")
    ap.add_argument("--force", action="store_true", help="re-render existing mp4s")
    args = ap.parse_args()

    out_dir = Path(args.out)
    paths = [Path(p) for p in args.scenes]
    todo = []
    for p in paths:
        dest = out_dir / (p.stem + ".mp4")
        if dest.exists() and not args.force:
            continue
        todo.append((p, dest))

    if not todo:
        print("nothing to do (all mp4s exist; use --force)")
        return 0

    with Renderer(fps=args.fps, crf=args.crf, preset=args.preset,
                  threads=args.threads, encoder=args.encoder) as r:
        print(f"encoder: {r.encoder}", flush=True)
        for i, (src, dest) in enumerate(todo, 1):
            try:
                st = r.capture(src, dest)
                print(f"[{i}/{len(todo)}] {dest.name}  "
                      f"{st['duration_ms']/1000:.1f}s  {st['frames']}f "
                      f"({st['shot']} shot, {st['reused']} reused)  "
                      f"{st['bytes']/1e6:.2f}MB  {st['seconds']}s "
                      f"({st['fps_capture']} fps)", flush=True)
            except Exception as exc:
                print(f"[{i}/{len(todo)}] FAIL {src.name}: {exc}", file=sys.stderr,
                      flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
