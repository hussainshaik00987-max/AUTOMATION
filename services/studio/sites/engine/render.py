"""Page assembly — Business + Template -> one HTML file.

Two asset modes:

**shared** (default) writes the stylesheet to `assets/css/<hash>.css` and links
it. Thirty templates crossed with twenty-five palettes collapse to about ninety
distinct stylesheets, so 2,000 pages carry ~90 copies of the CSS instead of
2,000 — the difference between a 171MB deploy and a ~55MB one, which is what
puts the whole catalogue under Vercel's 100MB upload ceiling. The pages still
open straight off disk, because the link is relative.

**inline** keeps everything in the document, as it was: one file, no
dependencies, correct for a USB stick or an email attachment.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import blocks as B
from .css import build_css
from .data import Business
from .templates import Template, assign
from .theme import PALETTES, TYPE_SYSTEMS

# Collapse the whitespace the CSS constants are written with. Only touches the
# stylesheet, never page text, so no visible content is altered.
_CSS_WS = re.compile(r"\s*\n\s*")


def _minify_css(css: str) -> str:
    css = _CSS_WS.sub("", css)
    return re.sub(r";\}", "}", css)


def css_digest(css: str) -> str:
    return hashlib.sha256(css.encode()).hexdigest()[:12]


def write_shared_asset(root: Path, kind: str, name: str, body: str) -> None:
    """Write `assets/<kind>/<name>` unless an identical file is already there.

    Workers race on the same stylesheet — every process rendering leads that
    share a template derives the same digest. The content is identical, so the
    write is idempotent; writing to a temp name and replacing keeps a reader
    from ever seeing a half-written file.
    """
    path = root / "assets" / kind / name
    encoded = body.encode()
    if path.exists() and path.stat().st_size == len(encoded):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{id(body):x}.tmp")
    tmp.write_bytes(encoded)
    try:
        tmp.replace(path)
    except OSError:
        # Windows refuses the rename while another worker holds the same
        # destination open. The name is a digest of the content, so whoever
        # won the race wrote exactly these bytes — dropping ours is correct.
        tmp.unlink(missing_ok=True)


def render(b: Business, template: Template | None = None,
           palette_key: str | None = None, *, base_url: str = "",
           asset_base_url: str = "", use_photos: bool = True,
           out_root: Path | None = None) -> str:
    """Render one page. Pass `out_root` for shared assets, omit to inline them."""
    if template is None or palette_key is None:
        auto_t, auto_p = assign(b)
        template = template or auto_t
        palette_key = palette_key or auto_p

    palette = PALETTES[palette_key]
    type_system = TYPE_SYSTEMS[template.type_key]

    skin = template.skin
    css = build_css(palette, type_system, skin, extra=template.css)
    if template.tile_ratio != "4/5":
        css += f":root{{--tile-ratio:{template.tile_ratio}}}"
    css = _minify_css(css)

    ctx = B.make_ctx(b, palette, template.key, template.name,
                     tile_ratio=template.tile_ratio, dark_hero=template.dark_hero,
                     hero=template.hero, use_photos=use_photos, base_url=base_url,
                     asset_base_url=asset_base_url)

    if out_root is not None:
        name = f"{css_digest(css)}.css"
        write_shared_asset(out_root, "css", name, css)
        head = B.head(b, ctx, css_href=f"../assets/css/{name}")
        tail = B.tail(js_src="../assets/js/site.js")
    else:
        head = B.head(b, ctx, css=css)
        tail = B.tail()

    parts = [
        head,
        B.demo_bar(b, ctx),
        B.header(b, ctx),
        '<main id="main">',
        B.HEROES[template.hero](b, ctx),
    ]
    parts.extend(section(b, ctx) for section in template.sections)
    parts.append("</main>")
    parts.append(B.footer(b, ctx))
    parts.append(B.fab(b, ctx))
    parts.append(tail)
    page = "".join(parts)
    # The sprite is built from the finished document so it carries only the
    # icons this template actually referenced.
    return page.replace("<!--sprite-->", B.sprite(page), 1)
