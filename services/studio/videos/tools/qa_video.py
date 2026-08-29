"""Check rendered MP4s are actually deliverable.

A file existing on disk proves nothing: a killed worker leaves a truncated MP4,
and a container without its index at the front stalls before playing on a phone.
This checks the properties that decide whether the video plays for a prospect.

    python tools/qa_video.py                 # every mp4 in out/mp4
    python tools/qa_video.py --sample 120
"""

from __future__ import annotations

import argparse
import random
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import ffmpeg_bin                                  # noqa: E402

HERE = Path(__file__).resolve().parent.parent

EXPECT_W, EXPECT_H = 1080, 1920
DURATION_RANGE = (25.0, 55.0)
SIZE_RANGE_MB = (1.5, 15.5)      # WhatsApp rejects video over ~16MB
FPS_EXPECT = 30

_STREAM = re.compile(r"Stream #\d+:\d+.*?Video:\s*(\w+)\s*\(([^)]*)\).*?(\d{3,5})x(\d{3,5})",
                     re.S)
_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_PIXFMT = re.compile(r"Video:\s*\w+\s*\([^)]*\)[^,]*,\s*([a-z0-9]+)")


def faststart(path: Path) -> bool | None:
    """True when `moov` precedes `mdat`.

    Without it a player must read to the end of the file before it can start,
    which on a phone over mobile data is the difference between a video that
    plays and one the prospect scrolls past.
    """
    try:
        with path.open("rb") as fh:
            pos = 0
            for _ in range(24):
                fh.seek(pos)
                head = fh.read(8)
                if len(head) < 8:
                    return None
                size, tag = struct.unpack(">I4s", head)
                if tag == b"moov":
                    return True
                if tag == b"mdat":
                    return False
                if size == 1:                       # 64-bit extended size
                    size = struct.unpack(">Q", fh.read(8))[0]
                if size < 8:
                    return None
                pos += size
    except OSError:
        return None
    return None


def probe(path: Path) -> dict:
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True)
    txt = out.stderr
    info: dict = {"path": path, "size_mb": path.stat().st_size / 1e6}

    m = _STREAM.search(txt)
    if m:
        info["codec"], info["profile"] = m.group(1), m.group(2)
        info["w"], info["h"] = int(m.group(3)), int(m.group(4))
    p = _PIXFMT.search(txt)
    if p:
        info["pix_fmt"] = p.group(1)
    f = _FPS.search(txt)
    if f:
        info["fps"] = float(f.group(1))
    d = _DUR.search(txt)
    if d:
        info["duration"] = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    info["faststart"] = faststart(path)
    return info


def issues(i: dict) -> list[str]:
    bad = []
    if i.get("codec") != "h264":
        bad.append(f"codec={i.get('codec')}")
    if (i.get("w"), i.get("h")) != (EXPECT_W, EXPECT_H):
        bad.append(f"size={i.get('w')}x{i.get('h')}")
    if i.get("pix_fmt") != "yuv420p":
        bad.append(f"pix_fmt={i.get('pix_fmt')}")
    if i.get("fps") and abs(i["fps"] - FPS_EXPECT) > 0.6:
        bad.append(f"fps={i['fps']}")
    dur = i.get("duration")
    if dur is None:
        bad.append("no duration (truncated?)")
    elif not DURATION_RANGE[0] <= dur <= DURATION_RANGE[1]:
        bad.append(f"duration={dur:.1f}s")
    if not SIZE_RANGE_MB[0] <= i["size_mb"] <= SIZE_RANGE_MB[1]:
        bad.append(f"size={i['size_mb']:.1f}MB")
    if i.get("faststart") is not True:
        bad.append("no faststart")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(HERE / "out" / "mp4"))
    ap.add_argument("--sample", type=int, default=0, help="0 = all")
    args = ap.parse_args()

    files = sorted(Path(args.dir).glob("*.mp4"))
    # A file a worker is still writing has no moov atom yet and would be reported
    # as truncated. Anything touched in the last 90s is in flight, not broken.
    now = time.time()
    in_flight = [f for f in files if now - f.stat().st_mtime < 90]
    files = [f for f in files if f not in set(in_flight)]
    if in_flight:
        print(f"skipping {len(in_flight)} still being written")
    if not files:
        print("no completed mp4s yet")
        return 0
    if args.sample and len(files) > args.sample:
        files = random.Random(11).sample(files, args.sample)

    bad_files, durs, sizes = [], [], []
    for f in files:
        info = probe(f)
        problems = issues(info)
        if info.get("duration"):
            durs.append(info["duration"])
        sizes.append(info["size_mb"])
        if problems:
            bad_files.append((f.name, problems))

    print(f"checked {len(files)} videos")
    if durs:
        durs.sort()
        print(f"  duration  min {durs[0]:.1f}s  median {durs[len(durs)//2]:.1f}s  "
              f"max {durs[-1]:.1f}s")
    if sizes:
        sizes.sort()
        print(f"  size      min {sizes[0]:.1f}MB  median {sizes[len(sizes)//2]:.1f}MB  "
              f"max {sizes[-1]:.1f}MB  total {sum(sizes)/1000:.1f}GB")

    if not bad_files:
        print("  all pass (h264 / 1080x1920 / yuv420p / 30fps / faststart)")
        return 0
    print(f"\n{len(bad_files)} FAILED:")
    for name, problems in bad_files[:25]:
        print(f"  {name}: {', '.join(problems)}")
    if len(bad_files) > 25:
        print(f"  ... {len(bad_files) - 25} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
