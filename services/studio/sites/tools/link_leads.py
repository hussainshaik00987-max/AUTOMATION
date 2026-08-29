#!/usr/bin/env python
"""Write each lead's live demo URL back into the exported lead sheets.

    python tools/link_leads.py                       # uses the last build's base url
    python tools/link_leads.py --base-url https://x.vercel.app
    python tools/link_leads.py --dry-run

Adds a **Demo Site** column to `exports/leads.xlsx` (every sheet, as a clickable
hyperlink), `exports/top_leads.csv` and `exports/all_leads.csv`.

Only the 2,000 leads in `top_leads.csv` have a demo built for them, so the
column is blank everywhere else — blank rather than a placeholder, so the sheet
can be filtered on it.

**The join.** `engine.data.load()` derives each slug from the business name and
de-duplicates collisions in row order, so the slug for a given lead exists only
as a by-product of reading `top_leads.csv` start to finish. That gives an exact
positional map there, but the workbook's other nine sheets are slices of the
full 15,603-row export and need a real key. Google Place ID covers only 84% of
the top leads, and name+city collides 366 times across the full export, so the
key is name + city + phone + address + score — measured over the real data as
0 collisions among the top 2,000, with every one of them matching exactly one
row in `all_leads.csv`.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from engine.data import load  # noqa: E402

EXPORTS = HERE.parents[0] / "exports"
COLUMN = "Demo Site"
# Sits immediately after the business name: the column you want on screen while
# the phone is ringing, not 40 columns to the right.
AFTER = "Business Name"

KEY_COLS = ("Business Name", "City", "Phone", "Address", "Score")


def fingerprint(row: dict) -> tuple[str, ...]:
    return tuple((row.get(c) or "").strip() for c in KEY_COLS)


def build_map(base_url: str) -> tuple[dict[tuple[str, ...], str], int]:
    """fingerprint -> demo URL, for every lead that has a demo."""
    csv_path = EXPORTS / "top_leads.csv"
    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    businesses = load(csv_path)

    mapping: dict[tuple[str, ...], str] = {}
    collisions = 0
    for b in businesses:
        # `row_id` is the index of the CSV row this business was read from, so
        # the slug lines up with the source row without re-deriving it.
        row = rows[b.row_id]
        key = fingerprint(row)
        url = f"{base_url}/sites/{b.slug}"
        if key in mapping and mapping[key] != url:
            collisions += 1
        mapping[key] = url
    return mapping, collisions


def patch_csv(path: Path, mapping: dict, dry: bool) -> tuple[int, int]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    if COLUMN in header:
        header.remove(COLUMN)
    idx = header.index(AFTER) + 1 if AFTER in header else len(header)
    header.insert(idx, COLUMN)

    hits = 0
    for row in rows:
        url = mapping.get(fingerprint(row), "")
        row[COLUMN] = url
        hits += bool(url)
    if not dry:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        tmp.replace(path)
    return hits, len(rows)


def patch_xlsx(path: Path, mapping: dict, dry: bool) -> dict[str, tuple[int, int]]:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.load_workbook(path)
    link_font = Font(color="0563C1", underline="single")
    report: dict[str, tuple[int, int]] = {}

    for ws in wb.worksheets:
        header = [c.value for c in ws[1]]
        if COLUMN in header:
            ws.delete_cols(header.index(COLUMN) + 1)
            header = [c.value for c in ws[1]]
        col = (header.index(AFTER) + 2) if AFTER in header else len(header) + 1
        ws.insert_cols(col)

        head_cell = ws.cell(row=1, column=col, value=COLUMN)
        head_cell.font = Font(bold=True)
        lookup = {name: i + 1 for i, name in enumerate([c.value for c in ws[1]])}
        need = [lookup.get(c) for c in KEY_COLS]

        hits = 0
        for r in range(2, ws.max_row + 1):
            key = tuple(str(ws.cell(row=r, column=c).value or "").strip()
                        if c else "" for c in need)
            url = mapping.get(key)
            if not url:
                continue
            cell = ws.cell(row=r, column=col, value=url)
            # A real hyperlink, not just text — the point of putting it in a
            # spreadsheet is that it is one click from the call.
            cell.hyperlink = url
            cell.font = link_font
            hits += 1
        ws.column_dimensions[head_cell.column_letter].width = 46
        report[ws.title] = (hits, ws.max_row - 1)

    if not dry:
        tmp = path.with_suffix(".tmp.xlsx")
        wb.save(tmp)
        tmp.replace(path)
    wb.close()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="",
                    help="defaults to the base url recorded by the last build")
    ap.add_argument("--exports", default=str(EXPORTS))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    if not base:
        info = HERE / "out" / "build-info.json"
        if info.exists():
            base = (json.loads(info.read_text("utf-8")).get("base_url") or "").rstrip("/")
    if not base:
        print("no --base-url and no out/build-info.json to read it from",
              file=sys.stderr)
        return 2
    if base.startswith("https://demo.test"):
        print(f"refusing to write the placeholder base url {base!r} into the "
              f"lead sheets — rebuild with the real --base-url first", file=sys.stderr)
        return 2

    exports = Path(args.exports)
    mapping, collisions = build_map(base)
    print(f"base url : {base}")
    print(f"demos    : {len(mapping)} leads mapped"
          + (f"  ({collisions} fingerprint collisions!)" if collisions else ""))
    if args.dry_run:
        print("(dry run — nothing written)")

    for name in ("top_leads.csv", "all_leads.csv"):
        p = exports / name
        if not p.exists():
            continue
        if not args.dry_run and not args.no_backup:
            shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
        hits, total = patch_csv(p, mapping, args.dry_run)
        print(f"  {name:20s} {hits:>6}/{total:<6} rows linked")

    xlsx = exports / "leads.xlsx"
    if xlsx.exists():
        if not args.dry_run and not args.no_backup:
            shutil.copy2(xlsx, xlsx.with_suffix(".xlsx.bak"))
        for sheet, (hits, total) in patch_xlsx(xlsx, mapping, args.dry_run).items():
            print(f"  leads.xlsx :: {sheet[:28]:30s} {hits:>6}/{total:<6} rows linked")

    unmatched = len(mapping)
    print(f"\n{COLUMN!r} inserted after {AFTER!r}. Blank means no demo was built "
          f"for that lead ({unmatched:,} of the export have one).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
