"""Render a contact sheet of generated artwork and screenshot it.

A visual check on the art engine: every motif x several seeds x a few palettes,
so drift or a broken variant is obvious at a glance.

    python tools/contact_sheet.py [out.png]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.art import MOTIFS, artwork, grain_data_uri  # noqa: E402
from engine.theme import PALETTES  # noqa: E402

MOTIF_PALETTES = {
    "film": ("noir", "sepia", "blush", "slate"),
    "icing": ("butter", "berry", "pistachio", "cocoa"),
    "mandala": ("marigold", "emerald", "peony", "midnight"),
    "drape": ("runway", "atelier", "indigo", "rosewood"),
    "plan": ("concrete", "blueprint", "travertine", "brass"),
}


def build_html() -> str:
    rows = []
    for motif in MOTIFS:
        tiles = []
        for i, pal_key in enumerate(MOTIF_PALETTES[motif]):
            pal = PALETTES[pal_key]
            for j in range(3):
                seed = (hash((motif, pal_key, j)) & 0xFFFFFFFF) + j * 7919
                dark = j == 2
                svg = artwork(seed, motif, i * 3 + j, pal, width=520, height=650, dark=dark)
                tiles.append(
                    f'<figure class="t">{svg}<figcaption>{pal_key}'
                    f'{" · dark" if dark else ""}</figcaption></figure>'
                )
        rows.append(f'<section><h2>{motif}</h2><div class="grid">{"".join(tiles)}</div></section>')

    return f"""<!doctype html><meta charset="utf-8"><title>art contact sheet</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;padding:24px;background:#191919;color:#eee;
     font:14px system-ui,-apple-system,sans-serif}}
h2{{font-size:13px;letter-spacing:.22em;text-transform:uppercase;color:#9a9a9a;
    margin:28px 0 12px;font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:8px}}
.t{{margin:0;position:relative;aspect-ratio:4/5;overflow:hidden;border-radius:6px;
    background:#000}}
.t svg{{width:100%;height:100%;display:block}}
.t::after{{content:"";position:absolute;inset:0;background-image:url("{grain_data_uri(0.32)}");
    mix-blend-mode:overlay;pointer-events:none}}
figcaption{{position:absolute;left:0;right:0;bottom:0;font-size:9px;padding:3px 5px;
    background:rgba(0,0,0,.62);color:#fff;letter-spacing:.05em}}
</style>{"".join(rows)}"""


def main() -> None:
    out_png = Path(sys.argv[1] if len(sys.argv) > 1 else "art_sheet.png")
    html_path = out_png.with_suffix(".html")
    html_path.write_text(build_html(), encoding="utf-8")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1200},
                                device_scale_factor=1)
        page.goto(html_path.resolve().as_uri())
        page.wait_for_timeout(500)
        page.screenshot(path=str(out_png), full_page=True)
        browser.close()
    print(f"wrote {out_png} ({out_png.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
