"""The catalogue page — a working tool for the person making the calls.

Not a gallery of thumbnails. It is the lead list with the demo attached: search,
filter by vertical/segment/city, sort by score, and one tap to open the demo,
dial, or start a WhatsApp thread. The scraper's own `Pitch Angle` is shown on
each card so the opening line is on screen while the phone rings.

All rows ship inline as JSON and filter client-side, so it works from `file://`
with no server.
"""

from __future__ import annotations

import html
import json

from .data import Business, VERTICAL_LABEL
from .theme import PALETTES

SEGMENT_LABEL = {
    "A_hot": "A · Hot",
    "B_strong": "B · Strong",
    "C_worth_a_try": "C · Worth a try",
}


def _row(b: Business, template_key: str, template_name: str, palette_key: str) -> dict:
    return {
        "n": b.name,
        "s": b.slug,
        "v": b.vertical,
        "vl": VERTICAL_LABEL[b.vertical],
        "c": b.city,
        "a": b.area,
        "sc": b.score,
        "sg": b.segment,
        "r": round(b.rating, 1) if b.rating else None,
        "rv": b.reviews,
        "cat": b.category,
        "t": template_name,
        "tk": template_key,
        "p": PALETTES[palette_key].name,
        "ph": b.phone_display,
        "pe": b.phone_e164,
        "wa": b.whatsapp,
        "em": b.email,
        "ig": b.instagram,
        "pa": b.pitch_angle,
        "pr": b.price["display"] + (" " + b.price["unit"] if b.price["unit"] else "")
        if b.price else "",
    }


CSS = """
*,*::before,*::after{box-sizing:border-box}*{margin:0}
:root{--bg:#0e0f12;--card:#16181d;--card2:#1c1f26;--ink:#eceef2;--soft:#9aa1ad;
  --line:#282c35;--acc:#7cc4ff;--acc2:#ffd479;--good:#5ed69a;--warn:#ffb26b}
body{background:var(--bg);color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
.wrap{width:min(100% - 2.5rem,1500px);margin-inline:auto}
header{border-bottom:1px solid var(--line);background:linear-gradient(180deg,#15171d,#0e0f12);
  padding:2.4rem 0 1.8rem}
h1{font-size:clamp(1.5rem,3vw,2.1rem);letter-spacing:-.02em;font-weight:650}
.sub{color:var(--soft);margin-top:.5rem;max-width:75ch}
.kpis{display:flex;flex-wrap:wrap;gap:2.2rem;margin-top:1.6rem}
.kpi b{display:block;font-size:1.5rem;letter-spacing:-.02em;color:var(--acc2)}
.kpi span{font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;color:var(--soft)}
.controls{position:sticky;top:0;z-index:10;background:rgba(14,15,18,.93);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:.9rem 0}
.ctl-row{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center}
input[type=search],select{background:var(--card);border:1px solid var(--line);
  color:var(--ink);border-radius:9px;padding:.6rem .85rem;font:inherit;font-size:.9rem}
input[type=search]{flex:1;min-width:220px}
input:focus,select:focus,button:focus-visible{outline:2px solid var(--acc);outline-offset:1px}
.pill{background:var(--card);border:1px solid var(--line);color:var(--soft);
  border-radius:999px;padding:.46rem .9rem;font-size:.8rem;cursor:pointer;
  transition:background .15s,color .15s,border-color .15s}
.pill:hover{color:var(--ink)}
.pill[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:#06131f;
  font-weight:600}
.count{color:var(--soft);font-size:.85rem;margin-left:auto;white-space:nowrap}
.grid{display:grid;gap:.9rem;padding:1.6rem 0 4rem;
  grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:13px;
  padding:1.05rem 1.1rem;display:flex;flex-direction:column;gap:.7rem;
  transition:border-color .18s,transform .18s}
.card:hover{border-color:#3b4150;transform:translateY(-2px)}
.top{display:flex;gap:.7rem;align-items:flex-start}
.nm{font-weight:620;line-height:1.28;letter-spacing:-.01em}
.loc{color:var(--soft);font-size:.82rem;margin-top:.15rem}
.score{margin-left:auto;text-align:right;flex:none}
.score b{font-size:1.15rem;color:var(--acc2);letter-spacing:-.02em}
.score span{display:block;font-size:.6rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--soft)}
.tags{display:flex;flex-wrap:wrap;gap:.35rem}
.tag{font-size:.7rem;padding:.24rem .55rem;border-radius:6px;background:var(--card2);
  color:var(--soft);border:1px solid var(--line)}
.tag.seg-A_hot{color:#ffd479;border-color:#4a3c1c}
.tag.seg-B_strong{color:var(--good);border-color:#1e4535}
.tag.tpl{color:var(--acc);border-color:#1e3a4f}
.pitch{font-size:.83rem;color:var(--soft);line-height:1.5;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.acts{display:flex;gap:.45rem;margin-top:auto;padding-top:.25rem}
.btn{flex:1;text-align:center;padding:.55rem .5rem;border-radius:8px;font-size:.8rem;
  font-weight:600;border:1px solid var(--line);background:var(--card2);
  transition:background .15s,color .15s}
.btn:hover{background:#252a33}
.btn.main{background:var(--acc);color:#06131f;border-color:var(--acc)}
.btn.main:hover{background:#9ad2ff}
.btn.wa{color:var(--good)}
.btn.cp{cursor:pointer;font:inherit;font-size:.8rem}
.btn.cp.ok{color:var(--good);border-color:var(--good)}
.empty{padding:4rem 0;text-align:center;color:var(--soft)}
footer{border-top:1px solid var(--line);padding:1.8rem 0 3rem;color:var(--soft);
  font-size:.82rem}
"""

SCRIPT = """
const $=s=>document.querySelector(s);
const grid=$('#grid'),count=$('#count'),q=$('#q'),sortSel=$('#sort'),citySel=$('#city');
let fV='',fS='';
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function card(r){
  const stars=r.r?`<span class="tag">${r.r}\\u2605 \\u00b7 ${r.rv}</span>`:'';
  const price=r.pr?`<span class="tag">${esc(r.pr)}</span>`:'';
  const wa=r.wa?`<a class="btn wa" href="${esc(r.wa)}" target="_blank" rel="noopener">WhatsApp</a>`:'';
  const tel=r.pe?`<a class="btn" href="tel:${esc(r.pe)}">Call</a>`:'';
  // The link the prospect receives, not the local file — one tap to paste into
  // the WhatsApp thread that is already open on the other screen.
  const copy=PREFIX.startsWith('http')
    ?`<button class="btn cp" data-u="${PREFIX}${esc(r.s)}${SUFFIX}">Copy link</button>`:'';
  return `<article class="card">
    <div class="top"><div><div class="nm">${esc(r.n)}</div>
      <div class="loc">${esc([r.a,r.c].filter(Boolean).join(' \\u00b7 '))}</div></div>
      <div class="score"><b>${r.sc}</b><span>score</span></div></div>
    <div class="tags"><span class="tag seg-${esc(r.sg)}">${esc(SEG[r.sg]||r.sg)}</span>
      <span class="tag">${esc(r.vl)}</span>${stars}${price}
      <span class="tag tpl">${esc(r.t)} \\u00b7 ${esc(r.p)}</span></div>
    ${r.pa?`<p class="pitch">${esc(r.pa)}</p>`:''}
    <div class="acts"><a class="btn main" href="${PREFIX}${esc(r.s)}${SUFFIX}" target="_blank">Open demo</a>${copy}${wa}${tel}</div>
  </article>`;
}

grid.addEventListener('click',function(ev){
  const b=ev.target.closest('.cp'); if(!b) return;
  navigator.clipboard.writeText(b.dataset.u).then(function(){
    const was=b.textContent; b.textContent='Copied'; b.classList.add('ok');
    setTimeout(function(){b.textContent=was;b.classList.remove('ok');},1400);
  });
});

function apply(){
  const term=q.value.trim().toLowerCase();
  const city=citySel.value;
  let out=ROWS.filter(r=>{
    if(fV&&r.v!==fV) return false;
    if(fS&&r.sg!==fS) return false;
    if(city&&r.c!==city) return false;
    if(!term) return true;
    return (r.n+' '+r.c+' '+r.a+' '+r.cat+' '+r.t).toLowerCase().includes(term);
  });
  const s=sortSel.value;
  out.sort((a,b)=> s==='score'?b.sc-a.sc : s==='reviews'?b.rv-a.rv :
                   s==='city'?a.c.localeCompare(b.c)||b.sc-a.sc : a.n.localeCompare(b.n));
  count.textContent=out.length.toLocaleString('en-IN')+' of '+ROWS.length.toLocaleString('en-IN');
  // Cap the DOM: 2,000 cards at once is a visible stall on a laptop.
  grid.innerHTML = out.length? out.slice(0,300).map(card).join('')
    : '<p class="empty">Nothing matches those filters.</p>';
  if(out.length>300){
    grid.insertAdjacentHTML('beforeend',
      `<p class="empty">Showing the first 300 of ${out.length}. Narrow the filters to see the rest.</p>`);
  }
}

document.querySelectorAll('[data-v]').forEach(el=>el.addEventListener('click',()=>{
  fV = el.dataset.v===fV ? '' : el.dataset.v;
  document.querySelectorAll('[data-v]').forEach(o=>o.setAttribute('aria-pressed',String(o.dataset.v===fV)));
  apply();
}));
document.querySelectorAll('[data-s]').forEach(el=>el.addEventListener('click',()=>{
  fS = el.dataset.s===fS ? '' : el.dataset.s;
  document.querySelectorAll('[data-s]').forEach(o=>o.setAttribute('aria-pressed',String(o.dataset.s===fS)));
  apply();
}));
q.addEventListener('input',apply); sortSel.addEventListener('change',apply);
citySel.addEventListener('change',apply);
apply();
"""


def build_index(rows: list[dict], stats: dict, *,
                site_prefix: str = "sites/", site_suffix: str = ".html") -> str:
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731

    verticals = sorted({r["v"] for r in rows})
    v_pills = "".join(
        f'<button class="pill" data-v="{e(v)}" aria-pressed="false">'
        f'{e(VERTICAL_LABEL[v])} <span style="opacity:.6">'
        f'{sum(1 for r in rows if r["v"] == v)}</span></button>'
        for v in verticals
    )
    segments = ["A_hot", "B_strong", "C_worth_a_try"]
    s_pills = "".join(
        f'<button class="pill" data-s="{e(s)}" aria-pressed="false">{e(SEGMENT_LABEL[s])}'
        f' <span style="opacity:.6">{sum(1 for r in rows if r["sg"] == s)}</span></button>'
        for s in segments if any(r["sg"] == s for r in rows)
    )
    cities = sorted({r["c"] for r in rows if r["c"]})
    city_opts = "".join(f'<option value="{e(c)}">{e(c)}</option>' for c in cities)

    kpis = "".join(
        f'<div class="kpi"><b>{e(v)}</b><span>{e(k)}</span></div>'
        for k, v in stats.items()
    )

    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    seg_map = json.dumps(SEGMENT_LABEL, ensure_ascii=False, separators=(",", ":"))
    prefix = json.dumps(site_prefix)
    suffix = json.dumps(site_suffix)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Demo sites — {len(rows)} leads</title>
<style>{CSS}</style></head><body>
<header><div class="wrap">
<h1>Concept sites for {len(rows):,} leads</h1>
<p class="sub">One demo per business, generated from the scraped listing — real name,
city, rating, review count and pricing. Filter to a segment, open the demo on one
screen and dial from the other.</p>
<div class="kpis">{kpis}</div>
</div></header>

<div class="controls"><div class="wrap">
<div class="ctl-row" style="margin-bottom:.6rem">
<input type="search" id="q" placeholder="Search name, city, area, category or template…"
  aria-label="Search leads">
<select id="city" aria-label="Filter by city"><option value="">All cities</option>{city_opts}</select>
<select id="sort" aria-label="Sort">
<option value="score">Sort: score</option>
<option value="reviews">Sort: reviews</option>
<option value="name">Sort: name</option>
<option value="city">Sort: city</option>
</select>
<span class="count" id="count"></span>
</div>
<div class="ctl-row">{v_pills}{s_pills}</div>
</div></div>

<div class="wrap"><div class="grid" id="grid"></div></div>
<footer><div class="wrap">Generated demos · photography is licensed stock
placeholder · open any file directly from disk.<br>
<b>This page is the lead list — keep it off the public site.</b></div></footer>

<script>const ROWS={data};const SEG={seg_map};const PREFIX={prefix};const SUFFIX={suffix};{SCRIPT}</script>
</body></html>"""


__all__ = ["build_index", "_row"]
