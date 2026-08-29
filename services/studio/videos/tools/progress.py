"""Emit one progress line per interval while a batch renders.

Designed to be watched: each line is a complete status, and anything that looks
like a failure in a worker log is surfaced immediately rather than discovered at
the end of a multi-hour run.

    python tools/progress.py --every 180 --target 2000
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FAIL = re.compile(r"(FAIL|Traceback|Error|ffmpeg failed|Timeout)", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--every", type=int, default=180, help="seconds between lines")
    ap.add_argument("--target", type=int, default=0, help="0 = read from manifest")
    args = ap.parse_args()

    out = Path(args.out)
    mp4_dir, log_dir = out / "mp4", out / "logs"

    target = args.target
    if not target:
        import json
        try:
            target = len(json.loads((out / "manifest.json").read_text(encoding="utf-8")))
        except Exception:
            target = 0

    seen_fail: set[str] = set()
    t0 = time.time()
    start_n = len(list(mp4_dir.glob("*.mp4"))) if mp4_dir.exists() else 0
    last_n = start_n

    while True:
        time.sleep(args.every)
        files = list(mp4_dir.glob("*.mp4")) if mp4_dir.exists() else []
        n = len(files)
        elapsed = time.time() - t0
        made = n - start_n
        rate = made / elapsed * 3600 if elapsed > 0 else 0
        left = max(0, target - n)
        eta = f"{left / rate:.1f}h" if rate > 0.5 else "?"
        mb = sum(f.stat().st_size for f in files) / 1e6 if files else 0
        avg = mb / n if n else 0

        print(f"{n}/{target} videos  (+{n - last_n} this tick)  "
              f"{rate:.0f}/hr  ETA {eta}  avg {avg:.1f}MB  total {mb/1000:.1f}GB",
              flush=True)
        last_n = n

        # Surface failures as they happen; a silent multi-hour run that produced
        # 400 broken files is the outcome this exists to prevent.
        for log in sorted(log_dir.glob("worker*.log")) if log_dir.exists() else []:
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if FAIL.search(line):
                    key = f"{log.name}:{line[:120]}"
                    if key not in seen_fail:
                        seen_fail.add(key)
                        print(f"  !! {log.name}: {line.strip()[:200]}", flush=True)

        if target and n >= target:
            print(f"BATCH COMPLETE: {n} videos in {elapsed/3600:.2f}h", flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
