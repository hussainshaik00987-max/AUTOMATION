#!/usr/bin/env python3
"""Build a WhatsApp-AI demo video for every lead.

    python build.py --limit 8                 # scene HTML only, fast
    python build.py --limit 8 --video         # ...and encode them to MP4
    python build.py --slug meridian-studios --video
    python build.py --vertical bakery --video --jobs 6
    python build.py --all --video --jobs 6    # the full run

Scenes are written to out/scenes/<slug>.html and videos to out/mp4/<slug>.mp4.
Both stages are resumable: anything already on disk is skipped unless --force.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import backdrop                    # noqa: E402
from engine import leads as leads_mod          # noqa: E402
from engine import render as render_mod        # noqa: E402
from engine import storyboard                  # noqa: E402

# The lead list comes from services/discovery. Falls back to the synthetic
# sample so a fresh checkout runs end to end without a scrape.
_LEADS = HERE.parents[1] / "discovery" / "exports" / "top_leads.csv"
_SAMPLE = HERE.parents[1] / "discovery" / "data" / "sample_top_leads.csv"
DEFAULT_CSV = _LEADS if _LEADS.exists() else _SAMPLE

BRAND = {
    "kicker": "Set up in 24 hours",
    "sub": "Same number. Your prices, your tone.<br>It just never sleeps.",
    "cta": "Reply to this message",
}


def build_scenes(rows, out_dir: Path, bg_dir: Path, brand: dict,
                 force: bool) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    backdrop.ensure(bg_dir, force=force)

    written = []
    for lead in rows:
        dest = out_dir / f"{lead.slug}.html"
        if dest.exists() and not force:
            written.append(dest)
            continue
        variant = lead.seed % backdrop.VARIANTS
        bg = backdrop.path_for(bg_dir, lead.vertical, variant)
        # Relative so the scene tree stays movable, and so all 2,000 scenes in a
        # vertical share four decoded images rather than inlining one apiece.
        rel = os.path.relpath(bg, dest.parent).replace(os.sep, "/")
        scene = storyboard.build(lead, brand)
        dest.write_text(render_mod.render(scene, rel), encoding="utf-8")
        written.append(dest)
    return written


def run_videos(scenes: list[Path], out_dir: Path, jobs: int, fps: int, crf: int,
               preset: str, force: bool, log_dir: Path, threads: int,
               encoder: str) -> int:
    """Fan the scene list across N capture processes.

    Each worker owns one Chromium. Splitting round-robin rather than in blocks
    keeps the workers balanced when scene lengths differ."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    pending = [p for p in scenes
               if force or not (out_dir / f"{p.stem}.mp4").exists()]
    if not pending:
        print("all videos already rendered")
        return 0
    print(f"encoding {len(pending)} videos across {jobs} workers", flush=True)

    shards = [pending[i::jobs] for i in range(jobs)]
    procs = []
    for i, shard in enumerate(shards):
        if not shard:
            continue
        listing = log_dir / f"shard{i}.txt"
        listing.write_text("\n".join(str(p) for p in shard), encoding="utf-8")
        cmd = [sys.executable, str(HERE / "tools" / "capture.py"),
               "--out", str(out_dir), "--fps", str(fps), "--crf", str(crf),
               "--preset", preset, "--threads", str(threads),
               "--encoder", encoder]
        if force:
            cmd.append("--force")
        cmd += [str(p) for p in shard]
        log = open(log_dir / f"worker{i}.log", "w")
        procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log))

    rc = 0
    for p, log in procs:
        rc |= p.wait()
        log.close()
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--all", action="store_true", help="every lead in the CSV")
    ap.add_argument("--slug", action="append", help="build only these slugs")
    ap.add_argument("--vertical", action="append", help="filter by vertical")
    ap.add_argument("--trade", action="append", help="filter by trade key")
    ap.add_argument("--spread", action="store_true",
                    help="sample evenly across verticals instead of top-N")
    ap.add_argument("--video", action="store_true", help="also encode MP4")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 2 - 1))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=21)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--encoder", default="auto", choices=("auto", "x264", "nvenc"),
                    help="nvenc moves encoding to the GPU, freeing cores for capture")
    ap.add_argument("--threads", type=int, default=2,
                    help="x264 threads per worker (workers x threads should fit the CPU)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    rows = leads_mod.load(args.csv)
    total = len(rows)

    if args.vertical:
        keep = set(args.vertical)
        rows = [r for r in rows if r.vertical in keep]
    if args.trade:
        from engine.script import trade_for
        keep = set(args.trade)
        rows = [r for r in rows if trade_for(r.category, r.vertical) in keep]
    if args.slug:
        keep = set(args.slug)
        rows = [r for r in rows if r.slug in keep]

    if not args.all and not args.slug:
        if args.spread:
            by_v: dict[str, list] = {}
            for r in rows:
                by_v.setdefault(r.vertical, []).append(r)
            picked, i = [], 0
            while len(picked) < args.limit and any(by_v.values()):
                for v in list(by_v):
                    if by_v[v] and len(picked) < args.limit:
                        picked.append(by_v[v].pop(0))
                i += 1
            rows = picked
        else:
            rows = rows[:args.limit]

    out = Path(args.out)
    scenes_dir, mp4_dir, log_dir = out / "scenes", out / "mp4", out / "logs"
    bg_dir = out / "bg"

    scenes = build_scenes(rows, scenes_dir, bg_dir, BRAND, args.force)
    print(f"{len(scenes)} scenes  (of {total} leads)  in {time.time()-t0:.1f}s"
          f"  -> {scenes_dir}", flush=True)

    # A manifest makes the batch inspectable without re-parsing 2,000 HTML files.
    from engine.script import trade_for
    manifest = [{
        "slug": r.slug, "name": r.name, "display_name": r.display_name,
        "vertical": r.vertical, "trade": trade_for(r.category, r.vertical),
        "category": r.category, "city": r.city, "score": r.score,
        "html": str(scenes_dir / f"{r.slug}.html"),
        "mp4": str(mp4_dir / f"{r.slug}.mp4"),
    } for r in rows]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    if args.video:
        rc = run_videos(scenes, mp4_dir, args.jobs, args.fps, args.crf,
                        args.preset, args.force, log_dir, args.threads,
                        args.encoder)
        done = len(list(mp4_dir.glob("*.mp4")))
        print(f"{done} videos in {mp4_dir}  ({time.time()-t0:.1f}s total)")
        return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
