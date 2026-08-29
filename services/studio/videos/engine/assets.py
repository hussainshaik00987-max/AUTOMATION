"""Inline SVG assets.

Nothing is fetched at render time — every glyph and the doodle wallpaper are
built here and inlined, so a capture never waits on IO (which would also make
frames non-deterministic). The one non-inlined asset is the stage backdrop; see
backdrop.py for why that one is baked to a file instead.

The iOS status-bar and WhatsApp glyphs are drawn to the app's own proportions in
points, so they land correctly once #screen is zoomed to frame scale.
"""

from __future__ import annotations

import base64
import random

# ---------------------------------------------------------------------------
# iOS status bar
# ---------------------------------------------------------------------------

CELLULAR = """<svg width="18" height="12" viewBox="0 0 18 12" fill="currentColor">
<rect x="0" y="8.2" width="3.2" height="3.8" rx="1.1"/>
<rect x="4.9" y="6" width="3.2" height="6" rx="1.1"/>
<rect x="9.8" y="3.4" width="3.2" height="8.6" rx="1.1"/>
<rect x="14.7" y="0.6" width="3.2" height="11.4" rx="1.1"/></svg>"""

WIFI = """<svg width="17" height="12" viewBox="0 0 17 12" fill="currentColor">
<path d="M8.5 11.8 5.9 8.9a3.9 3.9 0 0 1 5.2 0z"/>
<path d="M8.5 3.6c2 0 3.9.75 5.35 2.1l1.6-1.8A10.1 10.1 0 0 0 8.5 1.2 10.1 10.1 0 0 0 1.55 3.9l1.6 1.8A7.85 7.85 0 0 1 8.5 3.6z" opacity=".95"/>
<path d="M8.5 6.6c1.2 0 2.3.45 3.15 1.2l1.5-1.7A7.1 7.1 0 0 0 8.5 4.35 7.1 7.1 0 0 0 3.85 6.1l1.5 1.7A4.75 4.75 0 0 1 8.5 6.6z" opacity=".95"/></svg>"""

BATTERY = """<svg width="27.4" height="13" viewBox="0 0 27.4 13" fill="none">
<rect x=".5" y=".5" width="24" height="12" rx="4.1" stroke="currentColor" stroke-opacity=".38"/>
<rect x="2.2" y="2.2" width="{fill}" height="8.6" rx="2.6" fill="currentColor"/>
<path d="M26.1 4.5v4c.85-.35 1.3-1.05 1.3-2s-.45-1.65-1.3-2z" fill="currentColor" fill-opacity=".4"/></svg>"""


def battery(pct: int = 78) -> str:
    """iOS draws the fill inset 1.7pt inside a 24pt shell -> 20.6pt of travel."""
    return BATTERY.format(fill=round(20.6 * max(0.06, min(1.0, pct / 100)), 2))


# ---------------------------------------------------------------------------
# WhatsApp chrome
# ---------------------------------------------------------------------------

CHEVRON = """<svg width="12" height="21" viewBox="0 0 12 21" fill="none">
<path d="M10.5 1.5 2 10.5l8.5 9" stroke="currentColor" stroke-width="2.6"
 stroke-linecap="round" stroke-linejoin="round"/></svg>"""

VIDEO_CALL = """<svg width="26" height="17" viewBox="0 0 26 17" fill="currentColor">
<rect x="0" y="0" width="17.4" height="17" rx="4.6"/>
<path d="M19.5 6.2 24.4 2.6c.7-.5 1.6 0 1.6.85v10.1c0 .85-.9 1.35-1.6.85L19.5 10.8z"/></svg>"""

VOICE_CALL = """<svg width="21" height="21" viewBox="0 0 21 21" fill="currentColor">
<path d="M4.6.9c.75-.5 1.7-.3 2.2.45l2.1 3.2c.5.75.3 1.75-.45 2.25l-1.35.9c-.3.2-.4.6-.25.95a13.4 13.4 0 0 0 5.6 5.6c.35.15.75.05.95-.25l.9-1.35c.5-.75 1.5-.95 2.25-.45l3.2 2.1c.75.5.95 1.45.45 2.2l-1.2 1.8c-.85 1.3-2.5 1.85-3.95 1.3A21.6 21.6 0 0 1 1.5 8.65C.95 7.2 1.5 5.55 2.8 4.7z"/></svg>"""

PLUS = """<svg width="17" height="17" viewBox="0 0 17 17" fill="none">
<path d="M8.5 1.6v13.8M1.6 8.5h13.8" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"/></svg>"""

STICKER = """<svg width="19" height="19" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5">
<path d="M11.2 1.4A8.6 8.6 0 1 0 18.6 8.8h-4.1a3.3 3.3 0 0 1-3.3-3.3z" stroke-linejoin="round"/>
<circle cx="6.6" cy="7.4" r="1.15" fill="currentColor" stroke="none"/>
<circle cx="6.9" cy="12.6" r="1.15" fill="currentColor" stroke="none"/>
<circle cx="11.6" cy="12.9" r="1.15" fill="currentColor" stroke="none"/></svg>"""

CAMERA = """<svg width="21" height="18" viewBox="0 0 22 19" fill="none" stroke="currentColor" stroke-width="1.5">
<path d="M1.75 6.2c0-1.1.9-2 2-2h1.9c.65 0 1.25-.32 1.6-.86l.75-1.13c.37-.55.99-.88 1.65-.88h2.7c.66 0 1.28.33 1.65.88l.75 1.13c.35.54.95.86 1.6.86h1.9c1.1 0 2 .9 2 2v8.9c0 1.1-.9 2-2 2H3.75c-1.1 0-2-.9-2-2z" stroke-linejoin="round"/>
<circle cx="11" cy="10.4" r="3.7"/></svg>"""

MIC = """<svg width="16" height="21" viewBox="0 0 16 22" fill="currentColor">
<rect x="4.6" y="0" width="6.8" height="12.6" rx="3.4"/>
<path d="M1 9.6v1.6a7 7 0 0 0 14 0V9.6" stroke="currentColor" stroke-width="1.9" fill="none" stroke-linecap="round"/>
<path d="M8 18.6V22" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>"""

LOCK = """<svg width="9" height="11" viewBox="0 0 9 11" fill="currentColor">
<path d="M4.5 0a2.6 2.6 0 0 0-2.6 2.6v1.3h1.5V2.6a1.1 1.1 0 0 1 2.2 0v1.3h1.5V2.6A2.6 2.6 0 0 0 4.5 0z"/>
<rect x=".6" y="4.2" width="7.8" height="6.4" rx="1.5"/></svg>"""

# The app's own tail geometry, 8x13. Drawn twice: a soft shadow copy under the
# solid fill, which is how the bubble's box-shadow appears to continue into the nub.
TAIL_IN = """<svg class="tailsvg" viewBox="0 0 8 13" width="8" height="13">
<path opacity=".13" d="M1.533 3.568 8 12.193V1H2.812C1.042 1 .474 2.156 1.533 3.568z" fill="#0b141a"/>
<path fill="currentColor" d="M1.533 2.568 8 11.193V0H2.812C1.042 0 .474 1.156 1.533 2.568z"/></svg>"""

TAIL_OUT = """<svg class="tailsvg" viewBox="0 0 8 13" width="8" height="13">
<path opacity=".13" d="M5.188 1H0v11.193l6.467-8.625C7.526 2.156 6.958 1 5.188 1z" fill="#0b141a"/>
<path fill="currentColor" d="M5.188 0H0v11.193l6.467-8.625C7.526 1.156 6.958 0 5.188 0z"/></svg>"""

TICK_READ = """<svg class="tick read" width="16" height="11" viewBox="0 0 16 11" fill="none"
 stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">
<path d="M1 6.1 3.9 9 9.6 1.6"/><path d="M6.5 6.1 9.4 9 15.1 1.6"/></svg>"""

TICK_SENT = TICK_READ.replace("tick read", "tick sent")


# ---------------------------------------------------------------------------
# Value-card icons  (74pt tile, drawn on a 32 grid)
# ---------------------------------------------------------------------------

_ICO = ('<svg width="38" height="38" viewBox="0 0 32 32" fill="none" stroke="currentColor" '
        'stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">{}</svg>')

ICONS = {
    "bolt":     _ICO.format('<path d="M17.5 2 6 18h8l-1.5 12L26 14h-8z" fill="currentColor" stroke-width="1.6"/>'),
    "clock":    _ICO.format('<circle cx="16" cy="16" r="13"/><path d="M16 8v8.4l5.6 3.4"/>'),
    "chat":     _ICO.format('<path d="M28 15.3c0 6.2-5.4 11.2-12 11.2a13 13 0 0 1-3.6-.5L5 28.5l2.2-5.6A10.8 10.8 0 0 1 4 15.3C4 9.1 9.4 4 16 4s12 5.1 12 11.3z"/><path d="M11.5 15.2h9M11.5 19.6h5.5"/>'),
    "calendar": _ICO.format('<rect x="3.5" y="6" width="25" height="23" rx="4"/><path d="M3.5 13h25M10.5 3v6M21.5 3v6"/><path d="m11.5 20.5 3 3 6-6.5"/>'),
    "globe":    _ICO.format('<circle cx="16" cy="16" r="13"/><path d="M3.2 16h25.6M16 3a21 21 0 0 1 0 26 21 21 0 0 1 0-26z"/>'),
    "user":     _ICO.format('<circle cx="16" cy="11" r="6.2"/><path d="M4.6 28.6c1.4-5.7 6-9.2 11.4-9.2s10 3.5 11.4 9.2"/>'),
    "shield":   _ICO.format('<path d="M16 3 5 7.4v8.2c0 6.6 4.5 12 11 13.9 6.5-1.9 11-7.3 11-13.9V7.4z"/><path d="m11.2 16.2 3.2 3.3 6.4-6.8"/>'),
    "rupee":    _ICO.format('<path d="M9 5h14M9 11.5h14M9 5c6.5 0 8.5 2.2 8.5 5.4S15.5 17 10 17h-1l11 11"/>'),
    "moon":     _ICO.format('<path d="M27 19.4A12 12 0 1 1 13.4 5 9.6 9.6 0 0 0 27 19.4z"/>'),
    "phone":    _ICO.format('<rect x="8" y="2.5" width="16" height="27" rx="3.6"/><path d="M13.6 25.6h4.8"/>'),
    "spark":    _ICO.format('<path d="M16 3.5 18.9 12l8.6 3-8.6 3-2.9 8.5-2.9-8.5L4.5 15l8.6-3z" fill="currentColor" stroke-width="1.6"/><path d="M25.5 4v5M28 6.5h-5" stroke-width="1.8"/>'),
    "inbox":    _ICO.format('<path d="M3.5 17.5 7 6.2A3 3 0 0 1 9.9 4h12.2a3 3 0 0 1 2.9 2.2l3.5 11.3"/><path d="M3.5 17.5h7l1.8 3.6h7.4l1.8-3.6h7v6.9a3.6 3.6 0 0 1-3.6 3.6H7.1a3.6 3.6 0 0 1-3.6-3.6z"/>'),
}


def icon(name: str) -> str:
    return ICONS.get(name, ICONS["spark"])


# ---------------------------------------------------------------------------
# Doodle wallpaper
# ---------------------------------------------------------------------------

# WhatsApp's default light wallpaper is a scatter of thin line-art glyphs on warm
# beige. These are that vocabulary — camera, heart, cup, gift, music, plane —
# drawn as open paths on a 40x40 cell so any of them can be dropped into a slot.
_DOODLES = [
    "M6 14h6l2-3h12l2 3h6v18H6zM20 16.5a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11z",           # camera
    "M20 33S6 24.5 6 15.7A6.7 6.7 0 0 1 20 12a6.7 6.7 0 0 1 14 3.7C34 24.5 20 33 20 33z",   # heart
    "M9 11h20v10a10 10 0 0 1-20 0zM29 13.5h3.5a3.5 3.5 0 0 1 0 7H29M8 34h22",               # cup
    "M8 17h24v15H8zM8 11h24v6H8zM20 11v21M20 11c-3-6-9-5-9-1s6 1 9 1zm0 0c3-6 9-5 9-1s-6 1-9 1z",  # gift
    "M15 27V9l16-3v18M15 27a4 4 0 1 1-8 0 4 4 0 0 1 8 0zm16-3a4 4 0 1 1-8 0 4 4 0 0 1 8 0z",  # music
    "M6 20 34 8l-6 26-8-8-3 7-1-9z",                                                          # plane
    "M20 6l4.2 9.4 10.3 1.1-7.7 6.9 2.2 10.1L20 28.3l-9 5.2 2.2-10.1L5.5 16.5l10.3-1.1z",     # star
    "M20 5a15 15 0 1 1 0 30 15 15 0 0 1 0-30zM14 17.5v.1M26 17.5v.1M13.5 25a8 8 0 0 0 13 0",  # smiley
    "M10 30V16a10 10 0 0 1 20 0v14zM20 30v5M14 35h12",                                        # balloon
    "M8 32c0-11 8-20 20-22 1 12-8 22-20 22zM8 32c4-7 9-11 14-13",                             # leaf
    "M20 8a7 7 0 1 1 0 14 7 7 0 0 1 0-14zM11 26h18l2 8H9z",                                   # ring/cake
    "M6 12h28v18H6zM6 13l14 10 14-10",                                                        # envelope
    "M20 9v-4M20 35v-4M31 20h4M5 20h4M27.8 12.2l2.8-2.8M9.4 30.6l2.8-2.8M27.8 27.8l2.8 2.8M9.4 9.4l2.8 2.8M20 12.5a7.5 7.5 0 1 1 0 15 7.5 7.5 0 0 1 0-15z",  # sun
    "M12 27a7 7 0 0 1 0-14 9.5 9.5 0 0 1 18 2.5 6 6 0 0 1-1 11.5z",                           # cloud
    "M13 6h14v6a7 7 0 0 1-14 0zM11 34h18M20 19v10M15 12h10",                                  # trophy/glass
    "M8 33V11l12-5 12 5v22M17 33V23h6v10",                                                    # home
]


def doodle_wallpaper(seed: int, ink: str = "#d3c8ba", opacity: float = 0.72,
                     tile: int = 366, cells: int = 6) -> str:
    """Seeded scatter of line-art glyphs -> a repeating background-image data URI.

    Glyphs are placed one per grid cell with jitter and rotation. A cell grid
    (rather than free scatter) is what keeps density even; free placement clumps
    and leaves bald patches that read as a texture bug at video scale.
    """
    rng = random.Random(seed)
    step = tile / cells
    parts = []
    for row in range(cells):
        for col in range(cells):
            if rng.random() < 0.12:          # a few deliberate gaps
                continue
            path = rng.choice(_DOODLES)
            scale = rng.uniform(0.42, 0.62)
            cx = col * step + step / 2 + rng.uniform(-step * 0.22, step * 0.22)
            cy = row * step + step / 2 + rng.uniform(-step * 0.22, step * 0.22)
            rot = rng.uniform(-26, 26)
            off = 20 * scale               # glyphs are authored on a 40x40 box
            parts.append(
                f'<g transform="translate({cx:.1f},{cy:.1f}) rotate({rot:.1f}) '
                f'scale({scale:.3f}) translate({-off / scale:.1f},{-off / scale:.1f})">'
                f'<path d="{path}"/></g>'
            )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{tile}" height="{tile}" '
        f'viewBox="0 0 {tile} {tile}">'
        f'<g fill="none" stroke="{ink}" stroke-width="2.1" stroke-linecap="round" '
        f'stroke-linejoin="round" opacity="{opacity}">' + "".join(parts) + "</g></svg>"
    )
    return _svg_uri(svg)


def _svg_uri(svg: str) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"url(data:image/svg+xml;base64,{b64})"


# ---------------------------------------------------------------------------
# Avatar
# ---------------------------------------------------------------------------

def avatar_svg(seed: int, initials: str) -> str:
    """Deterministic monogram tile for the chat header.

    Real profile photos are not in the dataset, so nothing is invented — this is
    the same monogram WhatsApp itself falls back to, coloured from the slug hash.
    """
    rng = random.Random(seed)
    hue = rng.randrange(360)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 34 34" width="34" height="34">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="hsl({hue},46%,58%)"/>'
            f'<stop offset="1" stop-color="hsl({(hue + 26) % 360},52%,42%)"/></linearGradient></defs>'
            f'<rect width="34" height="34" fill="url(#g)"/></svg>')
