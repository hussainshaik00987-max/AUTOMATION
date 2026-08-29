"""Build a browsable index of the rendered videos.

Two thousand MP4s in a folder is not reviewable. This emits a single
`out/index.html` that plays them inline, filters by vertical/trade/city and
searches by name, so picking the right clip to send — or spot-checking a batch —
is a scroll rather than a file-manager expedition.

    python tools/gallery.py            # -> out/index.html
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import ffmpeg_bin                                  # noqa: E402

HERE = Path(__file__).resolve().parent.parent

# Frame to grab as the card image. Six seconds in the conversation is underway —
# a couple of bubbles on screen — which is what makes a tile recognisable.
POSTER_AT = "6"
POSTER_W = 320


def build_posters(rows, mp4_dir: Path, poster_dir: Path, workers: int = 8) -> int:
    """Extract one still per video.

    A <video> with preload="metadata" paints nothing — not even with a #t=
    fragment, which needs media data the browser has not fetched. Without real
    posters every tile in a 2,000-card gallery is a black rectangle, so the page
    cannot be used to actually find a clip. These are ~20KB each.
    """
    poster_dir.mkdir(parents=True, exist_ok=True)
    todo = [(mp4_dir / f"{r['slug']}.mp4", poster_dir / f"{r['slug']}.jpg")
            for r in rows]
    todo = [(src, dst) for src, dst in todo if src.exists() and not dst.exists()]
    if not todo:
        return 0

    def one(pair):
        src, dst = pair
        subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
             "-ss", POSTER_AT, "-i", str(src), "-frames:v", "1",
             "-vf", f"scale={POSTER_W}:-2", "-q:v", "5", str(dst)],
            capture_output=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    return len(todo)


VERTICAL_LABEL = {
    "wedding_photo": "Wedding Photography",
    "bakery": "Bakery & Cake",
    "wedding_planner": "Wedding Planning",
    "fashion": "Fashion & Boutique",
    "interior_arch": "Interior & Architecture",
}

CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0d10;--card:#14181d;--line:#232a32;--ink:#e9edf2;--dim:#8d99a8;--accent:#25d366}
body{background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,-apple-system,sans-serif;padding:32px}
header{max-width:1500px;margin:0 auto 26px}
h1{font-size:30px;font-weight:650;letter-spacing:-.7px}
.sub{color:var(--dim);margin-top:6px}
.bar{max-width:1500px;margin:0 auto 22px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input,select{background:var(--card);color:var(--ink);border:1px solid var(--line);
  border-radius:10px;padding:10px 13px;font:inherit;outline:none}
input:focus,select:focus{border-color:var(--accent)}
input[type=search]{flex:1 1 280px;min-width:220px}
.count{color:var(--dim);margin-left:auto;font-variant-numeric:tabular-nums}
.grid{max-width:1500px;margin:0 auto;display:grid;gap:18px;
  grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;
  display:flex;flex-direction:column}
video{width:100%;aspect-ratio:9/16;background:#000;display:block;cursor:pointer}
.meta{padding:12px 14px 14px}
.nm{font-weight:600;letter-spacing:-.2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sm{color:var(--dim);font-size:12.5px;margin-top:4px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.row{display:flex;gap:8px;align-items:center;margin-top:9px}
.tag{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:999px;padding:2px 9px}
.score{margin-left:auto;font-size:11.5px;color:var(--accent);font-variant-numeric:tabular-nums}
button.copy{background:none;border:1px solid var(--line);color:var(--dim);border-radius:8px;
  padding:4px 9px;font:inherit;font-size:11.5px;cursor:pointer}
button.copy:hover{border-color:var(--accent);color:var(--accent)}
.missing{opacity:.42}
.none{grid-column:1/-1;color:var(--dim);padding:50px;text-align:center}
"""

JS = """
// The poster image carries the tile, so the video file itself is only attached
// on first interaction. Otherwise scrolling the grid opens thousands of files.
document.addEventListener('play', e => {
  const v = e.target;
  if (!v.src && v.dataset.src) v.src = v.dataset.src;
}, true);
document.addEventListener('click', e => {
  const v = e.target.closest('video');
  if (v && !v.src && v.dataset.src) { v.src = v.dataset.src; v.play(); }
}, true);

const q=document.getElementById('q'), fv=document.getElementById('fv'),
      ft=document.getElementById('ft'), fc=document.getElementById('fc'),
      cnt=document.getElementById('cnt'), cards=[...document.querySelectorAll('.card')];
function apply(){
  const s=q.value.trim().toLowerCase(), v=fv.value, t=ft.value, c=fc.value;
  let n=0;
  for(const el of cards){
    const ok=(!s||el.dataset.search.includes(s))&&(!v||el.dataset.vertical===v)
           &&(!t||el.dataset.trade===t)&&(!c||el.dataset.city===c);
    el.style.display=ok?'':'none'; if(ok)n++;
  }
  cnt.textContent=n+' of '+cards.length;
  document.querySelector('.none').style.display=n?'none':'';
}
[q,fv,ft,fc].forEach(e=>e.addEventListener('input',apply));
// Only one clip plays at a time, otherwise a scroll turns into a wall of noise.
document.addEventListener('play',e=>{
  for(const v of document.querySelectorAll('video')) if(v!==e.target) v.pause();
},true);
document.addEventListener('click',e=>{
  const b=e.target.closest('button.copy'); if(!b)return;
  navigator.clipboard.writeText(b.dataset.path);
  const o=b.textContent; b.textContent='copied'; setTimeout(()=>b.textContent=o,1100);
});
apply();
"""


def build(out_dir: Path) -> Path:
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    mp4_dir = out_dir / "mp4"
    poster_dir = out_dir / "posters"
    index = out_dir / "index.html"

    made = build_posters(manifest, mp4_dir, poster_dir)
    if made:
        print(f"generated {made} posters")

    rows = []
    for m in manifest:
        mp4 = mp4_dir / f"{m['slug']}.mp4"
        m["_exists"] = mp4.exists()
        m["_size"] = mp4.stat().st_size if m["_exists"] else 0
        rows.append(m)
    rows.sort(key=lambda r: (-r["_exists"], -r.get("score", 0)))

    verticals = sorted({r["vertical"] for r in rows})
    trades = sorted({r["trade"] for r in rows})
    cities = sorted({r["city"] for r in rows if r["city"]})
    done = sum(1 for r in rows if r["_exists"])
    total_mb = sum(r["_size"] for r in rows) / 1e6

    def opt(values, labels=None):
        return "".join(
            f'<option value="{html.escape(v)}">'
            f'{html.escape((labels or {}).get(v, v).replace("_", " ").title())}</option>'
            for v in values)

    cards = []
    for r in rows:
        name = html.escape(r["name"])
        disp = html.escape(r["display_name"])
        city = html.escape(r["city"])
        trade = html.escape(r["trade"].replace("_", " "))
        search = html.escape(f"{r['name']} {r['display_name']} {r['city']} "
                             f"{r['trade']} {r['category']}".lower())
        rel = f"mp4/{r['slug']}.mp4"
        poster = f"posters/{r['slug']}.jpg"
        has_poster = (poster_dir / f"{r['slug']}.jpg").exists()
        media = (f'<video data-src="{rel}" preload="none" muted playsinline controls'
                 + (f' poster="{poster}"' if has_poster else "")
                 + '></video>' if r["_exists"] else
                 '<div style="aspect-ratio:9/16;display:grid;place-items:center;'
                 'color:#5a6572;font-size:12px">not rendered yet</div>')
        size = f'{r["_size"]/1e6:.1f}MB' if r["_exists"] else "—"
        cards.append(
            f'<div class="card{"" if r["_exists"] else " missing"}" '
            f'data-search="{search}" data-vertical="{html.escape(r["vertical"])}" '
            f'data-trade="{html.escape(r["trade"])}" data-city="{city}">'
            f'{media}'
            f'<div class="meta"><div class="nm" title="{name}">{disp}</div>'
            f'<div class="sm">{city or "—"} · {trade}</div>'
            f'<div class="row"><span class="tag">{size}</span>'
            f'<button class="copy" data-path="{html.escape(str((mp4_dir / (r["slug"] + ".mp4")).resolve()))}">path</button>'
            f'<span class="score">{r.get("score", 0)}</span></div></div></div>')

    index.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp AI demo videos</title><style>{CSS}</style></head><body>
<header><h1>WhatsApp AI demo videos</h1>
<div class="sub">{done:,} of {len(rows):,} rendered · {total_mb/1000:.1f} GB · sorted by lead score</div>
</header>
<div class="bar">
  <input type="search" id="q" placeholder="Search business, city, category…">
  <select id="fv"><option value="">All verticals</option>{opt(verticals, VERTICAL_LABEL)}</select>
  <select id="ft"><option value="">All trades</option>{opt(trades)}</select>
  <select id="fc"><option value="">All cities</option>{opt(cities)}</select>
  <span class="count" id="cnt"></span>
</div>
<div class="grid">{"".join(cards)}<div class="none">Nothing matches.</div></div>
<script>{JS}</script></body></html>""", encoding="utf-8")
    return index


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "out"))
    args = ap.parse_args()
    path = build(Path(args.out))
    print(f"gallery -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
