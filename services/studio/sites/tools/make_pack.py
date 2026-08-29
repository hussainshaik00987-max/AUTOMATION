#!/usr/bin/env python
"""Bundle N leads with their demo site links and their WhatsApp demo videos.

    python tools/make_pack.py                          # top 100 -> ../delivery/100-leads
    python tools/make_pack.py --count 250 --out /tmp/pack
    python tools/make_pack.py --vertical bakery --count 50

Produces one folder:

    leads.xlsx     the N leads, every column, plus Demo Site and Demo Video
    videos.zip     the N matching mp4s, named 001-<slug>.mp4 … NNN-<slug>.mp4
    README.txt     what is in here and how the two line up

**How the correspondence is guaranteed.** `demovideos/engine/leads.py` builds
its lead list by calling `demosites/engine/data.load()` — the same function, on
the same CSV — so a video's slug and a demo site's slug are the same string by
construction, not by a lookup that could drift. This tool still asserts it
rather than trusting it, and refuses to write a pack with any video missing:
half a deliverable is worse than none, because the gap is invisible until a
prospect is already on the phone.

Videos are numbered in the sheet's own row order, so lead 7 in the spreadsheet
is `007-…` in the zip. That number is written into the sheet as well.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[0]
sys.path.insert(0, str(HERE))

EXPORTS = ROOT / "exports"
VIDEO_DIR = ROOT / "demovideos" / "out" / "mp4"

SITE_COL = "Demo Site"
VIDEO_COL = "Demo Video"
AFTER = "Business Name"

# An mp4 begins with a 4-byte size then the literal 'ftyp' box type. A truncated
# encode — a batch killed mid-write — still leaves a plausible-looking file, and
# this is the cheapest way to notice before it ships.
FTYP = b"ftyp"
MIN_BYTES = 200_000


def base_url() -> str:
    info = HERE / "out" / "build-info.json"
    if info.exists():
        return (json.loads(info.read_text("utf-8")).get("base_url") or "").rstrip("/")
    return ""


def pick(count: int, vertical: str) -> list[dict]:
    """Top `count` rows of the export, which is already sorted by score."""
    with (EXPORTS / "top_leads.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if vertical:
        rows = [r for r in rows if (r.get("Vertical") or "") == vertical]
    return rows[:count]


def resolve(rows: list[dict], base: str) -> tuple[list[dict], list[str]]:
    """Attach slug + video path to each row. Returns (entries, problems)."""
    from engine.data import load

    businesses = load(EXPORTS / "top_leads.csv")
    by_row = {b.row_id: b for b in businesses}
    with (EXPORTS / "top_leads.csv").open(encoding="utf-8", newline="") as fh:
        all_rows = list(csv.DictReader(fh))
    # Match on identity, not on the row dict, so a filtered subset still lines up.
    index = {id(r): i for i, r in enumerate(all_rows)}

    entries: list[dict] = []
    problems: list[str] = []
    for n, row in enumerate(rows, 1):
        i = index.get(id(row))
        if i is None:                                    # filtered copy, re-find it
            i = next((j for j, r in enumerate(all_rows) if r == row), None)
        b = by_row.get(i) if i is not None else None
        if b is None:
            problems.append(f"row {n} ({row.get('Business Name')!r}): no slug")
            continue
        mp4 = VIDEO_DIR / f"{b.slug}.mp4"
        if not mp4.exists():
            problems.append(f"{b.slug}: video missing")
            continue
        size = mp4.stat().st_size
        if size < MIN_BYTES or FTYP not in mp4.open("rb").read(64):
            problems.append(f"{b.slug}: video looks truncated ({size:,} bytes)")
            continue
        entries.append({
            "n": n, "row": row, "slug": b.slug, "mp4": mp4, "size": size,
            "arcname": f"{n:03d}-{b.slug}.mp4",
            "url": f"{base}/sites/{b.slug}" if base else "",
        })
    return entries, problems


def write_xlsx(entries: list[dict], path: Path) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font

    header = list(entries[0]["row"].keys())
    for col in (SITE_COL, VIDEO_COL):
        if col in header:
            header.remove(col)
    at = header.index(AFTER) + 1 if AFTER in header else len(header)
    header[at:at] = [SITE_COL, VIDEO_COL]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{len(entries)} leads"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(vertical="center")

    link_font = Font(color="0563C1", underline="single")
    for e in entries:
        row = dict(e["row"])
        row[SITE_COL] = e["url"]
        row[VIDEO_COL] = e["arcname"]
        ws.append([row.get(h, "") for h in header])
        r = ws.max_row
        if e["url"]:
            cell = ws.cell(row=r, column=header.index(SITE_COL) + 1)
            cell.hyperlink = e["url"]
            cell.font = link_font

    widths = {AFTER: 34, SITE_COL: 46, VIDEO_COL: 34, "Phone": 16, "City": 14,
              "Category": 24, "Segment": 14, "Vertical": 18}
    for name, w in widths.items():
        if name in header:
            ws.column_dimensions[
                ws.cell(row=1, column=header.index(name) + 1).column_letter].width = w
    # Freeze under the header and to the right of the video column, so scrolling
    # 40 columns of scraper detail never loses which lead you are looking at.
    ws.freeze_panes = ws.cell(row=2, column=header.index(VIDEO_COL) + 2)
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    wb.close()


def write_zip(entries: list[dict], path: Path) -> int:
    # STORED, not DEFLATED: H.264 does not compress again, so deflate spends
    # minutes of CPU to save a fraction of a percent.
    total = 0
    tmp = path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for i, e in enumerate(entries, 1):
            z.write(e["mp4"], e["arcname"])
            total += e["size"]
            if i % 25 == 0 or i == len(entries):
                print(f"    zipped {i}/{len(entries)}  ({total / 1e6:,.0f} MB)",
                      flush=True)
    tmp.replace(path)
    return total


README = """{count} leads — demo sites and WhatsApp demo videos
{rule}

leads.xlsx   {count} leads, highest score first, every column from the export
             plus two:
               Demo Site   clickable link to that business's concept website
               Demo Video  the matching file inside videos.zip

videos.zip   {count} videos, 1080x1920 H.264, ~35 seconds each.
             Named 001-<slug>.mp4 … {last}, in the same order as the
             spreadsheet — row 7 of the sheet is 007-… in the zip.

Each video shows that business's own name in the WhatsApp chat header, and
each demo site is built from that business's public Google listing. Both are
generated from the same lead record, so they always refer to the same business.

The websites are live but unlisted: they carry noindex and are not linked from
anywhere. Send a link only to the business it was built for.

total: {mb:,.0f} MB of video
built: {when}
site : {base}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--vertical", default="", help="restrict to one vertical key")
    ap.add_argument("--out", default=str(ROOT / "delivery" / "100-leads"))
    ap.add_argument("--base-url", default="")
    args = ap.parse_args()

    base = args.base_url.rstrip("/") or base_url()
    if not base:
        print("no base url — build the sites first, or pass --base-url",
              file=sys.stderr)
        return 2

    rows = pick(args.count, args.vertical)
    if len(rows) < args.count:
        print(f"only {len(rows)} leads available", file=sys.stderr)
    entries, problems = resolve(rows, base)

    if problems:
        print(f"{len(problems)} problems — refusing to write a partial pack:",
              file=sys.stderr)
        for p in problems[:20]:
            print("  -", p, file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(entries)} leads -> {out}")

    xlsx = out / "leads.xlsx"
    write_xlsx(entries, xlsx)
    print(f"  leads.xlsx   {xlsx.stat().st_size / 1024:,.0f} KB  "
          f"({len(entries)} rows)")

    print("  videos.zip   packing …")
    total = write_zip(entries, out / "videos.zip")
    zsize = (out / "videos.zip").stat().st_size
    print(f"  videos.zip   {zsize / 1e6:,.0f} MB  ({len(entries)} videos)")

    import time
    (out / "README.txt").write_text(README.format(
        count=len(entries), rule="=" * 52, last=entries[-1]["arcname"],
        mb=total / 1e6, when=time.strftime("%Y-%m-%d %H:%M"), base=base),
        encoding="utf-8")
    print(f"  README.txt   written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
