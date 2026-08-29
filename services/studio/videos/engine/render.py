"""Assemble one scene into a single self-contained HTML file.

CSS, JS, the doodle wallpaper and every glyph are inlined; the only external
reference is the baked backdrop JPEG, which is a sibling file on disk and is
shared by every scene in a vertical. Nothing is fetched over the network, so a
capture worker never blocks on IO — which would make frame timing, and therefore
the video, non-deterministic.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from . import assets
from .storyboard import FPS, STAGE_H, STAGE_W, Scene

_HERE = Path(__file__).resolve().parent
_CSS = (_HERE / "styles.css").read_text(encoding="utf-8")
_JS = (_HERE / "runtime.js").read_text(encoding="utf-8")

# The backdrop carries the vertical; the accent stays WhatsApp green everywhere,
# because the product being sold is a WhatsApp bot and the colour is the fastest
# way to say so.
ACCENT = "#25d366"

# Vertical gaps between bubbles, in points. WhatsApp tightens a run from the same
# sender and opens up when the sender changes — copying that is most of what
# makes a mocked thread read as real.
GAP_RUN, GAP_TURN, GAP_PILL = 2.0, 8.0, 12.0


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _gaps(items) -> list[float]:
    """Space above each item, derived from what precedes it."""
    out = []
    prev_side = None
    for it in items:
        if it.kind == "pill":
            out.append(GAP_PILL if prev_side is not None else 0.0)
        elif prev_side is None or it.side != prev_side:
            out.append(GAP_TURN)
        else:
            out.append(GAP_RUN)
        prev_side = it.side if it.kind != "pill" else None
    return out


def _bubble_html(it) -> str:
    """One thread row. Timestamps sit inside the bubble over a reserved spacer,
    the way the app lays them out, so a short last line does not push them onto
    a line of their own."""
    if it.kind == "pill":
        lock = assets.LOCK if "enc" in it.cls else ""
        return f'<div class="{it.cls}">{lock}{_esc(it.text)}</div>'

    if it.kind == "typing":
        return ('<div class="tbubble">' + assets.TAIL_IN
                + '<span class="tdot"></span><span class="tdot"></span>'
                  '<span class="tdot"></span></div>')

    tail = assets.TAIL_OUT if it.side == "out" else assets.TAIL_IN
    tick = ""
    meta_w = 46
    if it.tick == "read":
        tick, meta_w = assets.TICK_READ, 64
    elif it.tick == "sent":
        tick, meta_w = assets.TICK_SENT, 64
    return (f'<div class="bubble" style="--meta-w:{meta_w}px">{tail}'
            f'{_esc(it.text)}<span class="spacer"></span>'
            f'<span class="meta">{_esc(it.stamp)}{tick}</span></div>')


def _thread_html(scene: Scene) -> str:
    rows = []
    for i, it in enumerate(scene.items):
        cls = f"item {it.side}" + (" tail" if it.tail else "")
        rows.append(f'<div class="{cls}" data-i="{i}">{_bubble_html(it)}</div>')
    return "\n".join(rows)


def _layers_html(scene: Scene) -> str:
    out = []
    for L in scene.layers:
        out.append(f'<div class="layer" id="L-{L.id}" style="top:{L.top}px;'
                   f'visibility:hidden;{L.extra}">{L.html}</div>')
    return "\n".join(out)


def _scene_json(scene: Scene) -> str:
    gaps = _gaps(scene.items)
    return json.dumps({
        "duration": scene.duration,
        "fps": FPS,
        "chat": {
            "items": [
                {"at": it.at, "until": it.until, "gap": gaps[i]}
                for i, it in enumerate(scene.items)
            ],
            "typingWindows": [list(w) for w in scene.typing_windows],
        },
        "camera": scene.camera,
        "layers": [
            {"id": L.id, "in": L.t_in, "out": L.t_out, "rise": L.rise, "fall": L.fall,
             "stagger": L.stagger, "travel": L.travel, "blur": L.blur,
             "barDelay": L.bar_delay}
            for L in scene.layers
        ],
    }, separators=(",", ":"))


def render(scene: Scene, backdrop_url: str) -> str:
    """`backdrop_url` is resolved by the caller — a relative path from the scene
    file, so 2,000 scenes share 20 baked images instead of inlining one each."""
    lead = scene.lead
    rng = random.Random(lead.seed ^ 0x5EED)

    doodle = assets.doodle_wallpaper(lead.seed, ink=scene.trade.doodle_ink)
    avatar_hue = rng.randrange(360)

    css_vars = f"""
    :root {{
      --backdrop-uri: url("{backdrop_url}");
      --accent: {ACCENT};
      --accent-soft: rgba(37,211,102,.74);
      --accent-shadow: rgba(37,211,102,.26);
      --doodle-uri: {doodle};
      --avatar-bg: linear-gradient(140deg, hsl({avatar_hue},46%,58%), hsl({(avatar_hue + 26) % 360},52%,42%));
    }}"""

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>{_esc(lead.display_name)} — WhatsApp AI demo</title>
<style>{_CSS}{css_vars}</style>
</head>
<body>
<div id="stage">
  <div id="phone">
    <div id="screen-clip">
      <div id="screen">
        <div id="wallpaper"></div>

        <div id="thread-wrap"><div id="thread">
{_thread_html(scene)}
        </div></div>

        <div id="navbar">
          <div id="nav-back">{assets.CHEVRON}</div>
          <div id="nav-avatar">{_esc(lead.initials)}</div>
          <div id="nav-id">
            <div id="nav-name">{_esc(lead.display_name)}</div>
            <div id="nav-status">
              <span id="st-online">online</span>
              <span id="st-typing">typing…</span>
            </div>
          </div>
          <div id="nav-actions">{assets.VIDEO_CALL}{assets.VOICE_CALL}</div>
        </div>

        <div id="statusbar">
          <div id="sb-time">{scene.clock}</div>
          <div id="island"></div>
          <div id="sb-right">{assets.CELLULAR}{assets.WIFI}{assets.battery(78)}</div>
        </div>

        <div id="composer">
          <div id="comp-plus">{assets.PLUS}</div>
          <div id="comp-field">
            <span class="ph">Message</span>
            <span class="ic">{assets.STICKER}</span>
            <span class="ic">{assets.CAMERA}</span>
          </div>
          <div id="comp-mic">{assets.MIC}</div>
        </div>

        <div id="home-indicator"></div>
      </div>
      <div id="gloss"></div>
    </div>
  </div>

{_layers_html(scene)}
</div>
<script>window.SCENE={_scene_json(scene)};</script>
<script>{_JS}</script>
</body></html>"""
