"""The thirty templates.

A template is a *recipe*, not an HTML file: a `Skin` (structural knobs), a type
system, a set of candidate palettes, an ordered list of section blocks, and a
CSS layer of its own. That combination is what makes two templates on the same
vertical feel like they came from different studios — the corner language,
section rhythm, rule weight, nav treatment and heading case all move together.

Counts follow the lead distribution, so the biggest vertical gets the most looks:

    wedding_photo   8   (1,059 leads)
    bakery          6   (371)
    wedding_planner 6   (344)
    fashion         5   (127)
    interior_arch   5   (99)

`assign()` picks a template per business — deterministic, evenly spread, and
steered by real signals (price tier, Instagram reach, review volume) so a
₹2,50,000 studio lands on a luxury layout and a home baker lands on an ordering
one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import blocks as B
from .css import Skin
from .data import Business

Section = Callable[[Business, B.Ctx], str]


@dataclass(frozen=True, slots=True)
class Template:
    key: str
    name: str
    vertical: str
    blurb: str
    skin: Skin
    type_key: str
    palettes: tuple[str, ...]
    hero: str
    sections: tuple[Section, ...]
    css: str = ""
    tile_ratio: str = "4/5"
    dark_hero: bool = True
    # Businesses whose price is at or above this land here preferentially.
    luxury: bool = False


# --------------------------------------------------------------------------
# reusable section shorthands
# --------------------------------------------------------------------------
def gal(layout: str, count: int = 8, band: str = ""):
    return lambda b, c: B.gallery(b, c, layout=layout, count=count, band=band)


def svc(style: str = "cards", count: int = 6, band: str = "band-surface"):
    return lambda b, c: B.services(b, c, style=style, count=count, band=band)


def abt(band: str = "", art: bool = True):
    return lambda b, c: B.about(b, c, band=band, with_art=art)


def prc(band: str = ""):
    return lambda b, c: B.pricing(b, c, band=band)


def qts(band: str = "band-surface"):
    return lambda b, c: B.quotes(b, c, band=band)


def prz(band: str = "band-surface"):
    return lambda b, c: B.process(b, c, band=band)


def faq_(band: str = ""):
    return lambda b, c: B.faq(b, c, band=band)


def con(band: str = "band-surface"):
    return lambda b, c: B.contact(b, c, band=band)


STATS = B.stats_band
CTA = B.cta_band


# --------------------------------------------------------------------------
# per-template CSS layers
# --------------------------------------------------------------------------
CSS_HAIRLINE_HEAD = """
.sec-head{border-top:2px solid var(--ink);padding-top:1.4rem;display:grid;
  grid-template-columns:auto 1fr;gap:0 clamp(1.5rem,4vw,3.5rem);align-items:start}
.sec-head .eyebrow{margin:0;grid-row:1/3}
.sec-head h2{grid-column:2}
.sec-head .lede{grid-column:2;margin-top:.8rem}
@media (max-width:720px){.sec-head{grid-template-columns:1fr}
  .sec-head h2,.sec-head .lede{grid-column:1}
  .sec-head .eyebrow{grid-row:auto;margin-bottom:.9rem}}
"""

CSS_CENTERED = """
.sec-head{text-align:center}
.sec-head h2{margin-inline:auto}
.sec-head .lede{margin-inline:auto;text-align:center}
"""

CSS_BIG_TYPE = """
h1{font-size:clamp(2.8rem,9vw,7.4rem);line-height:.95}
h2{font-size:clamp(2.1rem,5.4vw,4.2rem)}
"""

# Caption sits below the frame rather than over it, so the figure flows in
# normal layout instead of being stretched to the tile.
CSS_TICKER = """
.brand-mark{border-radius:50%}
.tile figcaption{background:none;color:var(--ink);position:static;opacity:1;
  transform:none;padding:.85rem .1rem 0}
.tile{aspect-ratio:auto;cursor:zoom-in;background:none}
.tile>figure{position:static;display:grid}
.tile figure>svg{height:auto;aspect-ratio:var(--tile-ratio);border-radius:var(--radius)}
"""

CSS_SIDE_RULE = """
section>.wrap{position:relative}
.sec-head{padding-left:clamp(0px,3vw,2.6rem);border-left:3px solid var(--accent)}
"""

CSS_SOFT = """
.card,.quote,.tile,.price-card,.map-frame{box-shadow:0 1px 2px rgba(0,0,0,.04),
  0 8px 24px -16px rgba(0,0,0,.18)}
.btn{border-radius:999px}
"""

CSS_PLATE = """
.card{background:var(--surface);border-color:transparent}
.band-surface .card{background:var(--bg)}
.card:hover{border-color:transparent;box-shadow:0 20px 42px -22px rgba(0,0,0,.3)}
"""

CSS_MONO_LABEL = """
.eyebrow{font-variant-numeric:tabular-nums}
.stat .v{font-family:var(--font-eyebrow);font-weight:600}
.card-num,.svc-row .n,.step::before{font-variant-numeric:tabular-nums}
"""

CSS_WIDE_GUTTER = """
.wrap{width:min(100% - 4rem,var(--container))}
@media (max-width:700px){.wrap{width:min(100% - 2.2rem,var(--container))}}
"""

CSS_UNDERLINE_H = """
h2{padding-bottom:.5rem;border-bottom:var(--rule) solid var(--accent);display:inline-block}
.sec-head .lede{margin-top:1.2rem}
"""


# --------------------------------------------------------------------------
# the catalogue
# --------------------------------------------------------------------------
TEMPLATES: list[Template] = [
    # ============================ WEDDING PHOTOGRAPHY (8) ============================
    Template(
        "wp-noir", "Noir", "wedding_photo",
        "Full-bleed dark hero, masonry gallery, film grain. For studios whose work is the whole pitch.",
        Skin(radius="0px", density=1.05, rule="1px", shadow="none", grain=0.3,
             container="1240px", hero_min="94svh"),
        "editorial", ("noir", "sepia", "slate"), "full",
        (gal("masonry", 9), abt("band-surface"), svc("list", 6, ""), STATS,
         qts("band-surface"), prc(""), faq_("band-surface"), con(""), CTA),
        css=CSS_HAIRLINE_HEAD + CSS_WIDE_GUTTER, luxury=True,
    ),
    Template(
        "wp-atelier", "Atelier", "wedding_photo",
        "Split hero and mosaic grid with generous whitespace. Quiet, gallery-like.",
        Skin(radius="0px", density=1.25, rule="1px", shadow="none", container="1160px",
             measure="62ch"),
        "quiet", ("sage", "slate", "noir"), "split",
        (gal("mosaic", 7, "band-surface"), abt(""), svc("cards", 6, "band-surface"),
         prz(""), qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_CENTERED,
    ),
    Template(
        "wp-filmstrip", "Filmstrip", "wedding_photo",
        "Stacked hero over a wide art band, with a horizontal scrolling reel.",
        Skin(radius="0px", density=.95, rule="2px", shadow="offset", container="1280px",
             uppercase_nav=True),
        "poster", ("noir", "sepia", "blush"), "stack",
        (gal("strip", 10, "band-surface"), svc("numbered", 6, ""), abt("band-surface"),
         STATS, prz(""), qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_BIG_TYPE + CSS_TICKER, tile_ratio="3/4",
    ),
    Template(
        "wp-journal", "Journal", "wedding_photo",
        "Editorial cover layout with long-form story copy. Warm and personal.",
        Skin(radius="2px", density=1.15, rule="1px", shadow="soft", container="1120px",
             measure="66ch", grain=0.18),
        "warm", ("sepia", "blush", "noir"), "editorial",
        (abt(""), gal("grid", 8, "band-surface"), svc("list", 6, ""), prz("band-surface"),
         qts(""), prc("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_SIDE_RULE,
    ),
    Template(
        "wp-vow", "Vow", "wedding_photo",
        "Framed hero with soft corners and a mosaic wall. Romantic without being fussy.",
        Skin(radius="14px", density=1.1, rule="1px", shadow="soft", container="1180px"),
        "editorial", ("blush", "sepia", "sage"), "frame",
        (gal("mosaic", 7, "band-surface"), svc("cards", 6, ""), abt("band-surface"),
         prz(""), qts("band-surface"), prc(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SOFT + CSS_CENTERED,
    ),
    Template(
        "wp-archive", "Archive", "wedding_photo",
        "Uniform grid, mono labels, tabular figures. Systematic and calm.",
        Skin(radius="0px", density=1.0, rule="1px", shadow="none", container="1300px",
             uppercase_nav=True, letter_body=".003em"),
        "drafting", ("slate", "noir", "sage"), "split",
        (gal("grid", 9, ""), svc("list", 7, "band-surface"), abt(""), STATS,
         prz("band-surface"), faq_(""), qts("band-surface"), con(""), CTA),
        css=CSS_MONO_LABEL + CSS_HAIRLINE_HEAD, tile_ratio="1/1",
    ),
    Template(
        "wp-canvas", "Canvas", "wedding_photo",
        "Big display type with offset shadows and a masonry wall. Confident and graphic.",
        Skin(radius="0px", density=1.0, rule="2px", shadow="offset", container="1260px"),
        "swiss", ("noir", "slate", "blush"), "full",
        (gal("masonry", 10, ""), svc("numbered", 6, "band-surface"), abt(""),
         qts("band-surface"), prc(""), prz("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_BIG_TYPE + CSS_UNDERLINE_H,
    ),
    Template(
        "wp-lumen", "Lumen", "wedding_photo",
        "Airy split hero and a scrolling reel. Light, modern, mobile-first.",
        Skin(radius="10px", density=1.3, rule="1px", shadow="soft", container="1150px",
             measure="60ch"),
        "quiet", ("sage", "slate", "sepia"), "split",
        (gal("strip", 10, ""), abt("band-surface"), svc("cards", 6, ""), prz("band-surface"),
         STATS, qts(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SOFT, tile_ratio="4/5",
    ),

    # ================================== BAKERY (6) ==================================
    Template(
        "bk-counter", "Counter", "bakery",
        "Split hero with a menu-style grid. Built to take orders, not to be admired.",
        Skin(radius="12px", density=1.0, rule="1px", shadow="soft", container="1160px"),
        "warm", ("butter", "cocoa", "citrus"), "split",
        (svc("cards", 8, "band-surface"), gal("grid", 8, ""), prc("band-surface"),
         abt(""), prz("band-surface"), qts(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SOFT + CSS_PLATE, tile_ratio="1/1",
    ),
    Template(
        "bk-parlour", "Parlour", "bakery",
        "Full-bleed hero and a mosaic display case. Warm, indulgent, dessert-forward.",
        Skin(radius="18px", density=1.1, rule="1px", shadow="soft", container="1180px",
             hero_min="86svh"),
        "warm", ("berry", "butter", "cocoa"), "full",
        (gal("mosaic", 7, ""), svc("cards", 6, "band-surface"), abt(""), STATS,
         qts("band-surface"), prz(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SOFT + CSS_CENTERED,
    ),
    Template(
        "bk-daily", "Daily", "bakery",
        "Compact stacked hero and a scrolling counter reel. Fast, everyday ordering.",
        Skin(radius="8px", density=.88, rule="1px", shadow="none", container="1220px"),
        "swiss", ("butter", "pistachio", "citrus"), "stack",
        (svc("list", 8, "band-surface"), gal("strip", 10, ""), prz("band-surface"),
         abt(""), prc("band-surface"), qts(""), faq_("band-surface"), con(""), CTA),
        css=CSS_TICKER, tile_ratio="1/1",
    ),
    Template(
        "bk-patisserie", "Patisserie", "bakery",
        "Editorial hero with a masonry wall. For studios selling craft, not convenience.",
        Skin(radius="2px", density=1.2, rule="1px", shadow="soft", container="1140px",
             measure="64ch", grain=0.16),
        "editorial", ("cocoa", "berry", "butter"), "editorial",
        (gal("masonry", 9, "band-surface"), svc("list", 6, ""), abt("band-surface"),
         prc(""), qts("band-surface"), prz(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SIDE_RULE, luxury=True,
    ),
    Template(
        "bk-order", "Order", "bakery",
        "Framed hero with ordering pushed to the top. Least friction to a WhatsApp message.",
        Skin(radius="14px", density=.95, rule="1px", shadow="soft", container="1130px"),
        "warm", ("pistachio", "butter", "berry"), "frame",
        (svc("numbered", 6, "band-surface"), prz(""), gal("grid", 8, "band-surface"),
         prc(""), abt("band-surface"), qts(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SOFT + CSS_CENTERED, tile_ratio="1/1",
    ),
    Template(
        "bk-crumb", "Crumb", "bakery",
        "Playful full hero, rounded everything, uniform grid. Friendly and approachable.",
        Skin(radius="20px", density=1.0, rule="1px", shadow="soft", container="1200px",
             uppercase_nav=False),
        "warm", ("citrus", "berry", "pistachio"), "full",
        (gal("grid", 9, ""), svc("cards", 6, "band-surface"), prz(""), abt("band-surface"),
         STATS, qts(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SOFT + CSS_PLATE + CSS_CENTERED, tile_ratio="1/1",
    ),

    # ============================== WEDDING PLANNING (6) ==============================
    Template(
        "pl-mandap", "Mandap", "wedding_planner",
        "Full-bleed hero and mosaic decor wall. Ornate, celebratory, unmistakably Indian.",
        Skin(radius="0px", density=1.1, rule="1px", shadow="none", container="1220px",
             hero_min="92svh", grain=0.14),
        "editorial", ("marigold", "haldi", "emerald"), "full",
        (gal("mosaic", 7, ""), svc("cards", 6, "band-surface"), prz(""), STATS,
         abt("band-surface"), qts(""), prc("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_CENTERED,
    ),
    Template(
        "pl-soiree", "Soirée", "wedding_planner",
        "Editorial hero, masonry, deep jewel palette. Luxury and destination weddings.",
        Skin(radius="2px", density=1.25, rule="1px", shadow="soft", container="1160px",
             measure="62ch"),
        "couture", ("midnight", "emerald", "peony"), "editorial",
        (gal("masonry", 9, "band-surface"), svc("list", 7, ""), prz("band-surface"),
         abt(""), qts("band-surface"), prc(""), faq_("band-surface"), con(""), CTA),
        css=CSS_HAIRLINE_HEAD, luxury=True,
    ),
    Template(
        "pl-shubh", "Shubh", "wedding_planner",
        "Split hero and a clean grid. Process-led — for planners who sell reliability.",
        Skin(radius="6px", density=1.0, rule="1px", shadow="soft", container="1180px"),
        "warm", ("emerald", "marigold", "midnight"), "split",
        (prz("band-surface"), gal("grid", 8, ""), svc("cards", 6, "band-surface"),
         abt(""), STATS, qts("band-surface"), prc(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SOFT,
    ),
    Template(
        "pl-saanjh", "Saanjh", "wedding_planner",
        "Framed hero and a scrolling reel with airy spacing. Soft and contemporary.",
        Skin(radius="16px", density=1.3, rule="1px", shadow="soft", container="1140px"),
        "quiet", ("peony", "haldi", "marigold"), "frame",
        (gal("strip", 10, "band-surface"), abt(""), svc("cards", 6, "band-surface"),
         prz(""), qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_SOFT + CSS_CENTERED,
    ),
    Template(
        "pl-utsav", "Utsav", "wedding_planner",
        "Stacked hero with a wide decor band and mosaic. Big, festive, high-energy.",
        Skin(radius="0px", density=1.0, rule="2px", shadow="offset", container="1260px"),
        "poster", ("haldi", "marigold", "emerald"), "stack",
        (gal("mosaic", 7, "band-surface"), svc("numbered", 6, ""), prz("band-surface"),
         abt(""), STATS, qts("band-surface"), prc(""), faq_("band-surface"), con(""), CTA),
        css=CSS_BIG_TYPE + CSS_UNDERLINE_H,
    ),
    Template(
        "pl-bandhan", "Bandhan", "wedding_planner",
        "Restrained full hero and masonry. Minimal luxury, no ornament.",
        Skin(radius="0px", density=1.2, rule="1px", shadow="none", container="1200px",
             measure="60ch"),
        "quiet", ("midnight", "peony", "emerald"), "full",
        (gal("masonry", 9, "band-surface"), prz(""), svc("list", 7, "band-surface"),
         abt(""), qts("band-surface"), prc(""), faq_("band-surface"), con(""), CTA),
        css=CSS_HAIRLINE_HEAD, luxury=True,
    ),

    # ================================= FASHION (5) =================================
    Template(
        "fs-runway", "Runway", "fashion",
        "Monochrome full-bleed hero and mosaic lookbook. Pure editorial.",
        Skin(radius="0px", density=1.15, rule="1px", shadow="none", container="1300px",
             hero_min="95svh"),
        "couture", ("runway", "atelier", "rosewood"), "full",
        (gal("mosaic", 7, ""), svc("list", 6, "band-surface"), abt(""), STATS,
         qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_BIG_TYPE + CSS_WIDE_GUTTER, tile_ratio="3/4", luxury=True,
    ),
    Template(
        "fs-bolt", "Bolt", "fashion",
        "Split hero with a hard grid and poster type. Loud, current, retail-forward.",
        Skin(radius="0px", density=.92, rule="2px", shadow="offset", container="1240px"),
        "poster", ("indigo", "neon", "runway"), "split",
        (gal("grid", 9, "band-surface"), svc("numbered", 6, ""), prz("band-surface"),
         abt(""), qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_BIG_TYPE + CSS_UNDERLINE_H, tile_ratio="3/4",
    ),
    Template(
        "fs-atelier", "Atelier", "fashion",
        "Editorial hero, masonry lookbook, couture serif. Made-to-measure and bridal.",
        Skin(radius="0px", density=1.3, rule="1px", shadow="none", container="1150px",
             measure="60ch", grain=0.14),
        "couture", ("atelier", "rosewood", "runway"), "editorial",
        (gal("masonry", 9, "band-surface"), svc("list", 6, ""), abt("band-surface"),
         prz(""), qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_HAIRLINE_HEAD, luxury=True,
    ),
    Template(
        "fs-drape", "Drape", "fashion",
        "Framed hero and a scrolling rail. Soft, tactile, boutique-scale.",
        Skin(radius="10px", density=1.15, rule="1px", shadow="soft", container="1160px"),
        "quiet", ("rosewood", "atelier", "indigo"), "frame",
        (gal("strip", 10, ""), svc("cards", 6, "band-surface"), abt(""), prz("band-surface"),
         qts(""), faq_("band-surface"), con(""), CTA),
        css=CSS_SOFT + CSS_CENTERED, tile_ratio="3/4",
    ),
    Template(
        "fs-label", "Label", "fashion",
        "Stacked hero, uniform grid, swiss type. Product-first for a growing label.",
        Skin(radius="0px", density=1.0, rule="1px", shadow="none", container="1280px",
             uppercase_nav=True),
        "swiss", ("neon", "runway", "indigo"), "stack",
        (gal("grid", 10, "band-surface"), svc("cards", 6, ""), abt("band-surface"),
         STATS, prz(""), qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_MONO_LABEL + CSS_HAIRLINE_HEAD, tile_ratio="1/1",
    ),

    # ============================ INTERIOR & ARCHITECTURE (5) ============================
    Template(
        "ia-blueprint", "Blueprint", "interior_arch",
        "Split hero, drafting type, tabular figures. Reads like a drawing set.",
        Skin(radius="0px", density=1.0, rule="1px", shadow="none", container="1280px",
             uppercase_nav=True, letter_body=".004em"),
        "drafting", ("blueprint", "concrete", "brass"), "split",
        (gal("grid", 9, "band-surface"), svc("list", 7, ""), prz("band-surface"),
         abt(""), STATS, qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_MONO_LABEL + CSS_HAIRLINE_HEAD, tile_ratio="4/3",
    ),
    Template(
        "ia-studio", "Studio", "interior_arch",
        "Editorial hero with a mosaic project wall. Swiss and confident.",
        Skin(radius="0px", density=1.2, rule="2px", shadow="none", container="1240px",
             measure="64ch"),
        "swiss", ("concrete", "travertine", "studio"), "editorial",
        (gal("mosaic", 7, ""), svc("numbered", 6, "band-surface"), prz(""), abt("band-surface"),
         qts(""), prc("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_BIG_TYPE + CSS_UNDERLINE_H, tile_ratio="4/3", luxury=True,
    ),
    Template(
        "ia-plot", "Plot", "interior_arch",
        "Stacked hero over a wide plan band, then a masonry wall of work.",
        Skin(radius="0px", density=1.05, rule="1px", shadow="offset", container="1260px"),
        "drafting", ("travertine", "concrete", "blueprint"), "stack",
        (gal("masonry", 9, "band-surface"), prz(""), svc("list", 7, "band-surface"),
         abt(""), STATS, qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_MONO_LABEL + CSS_SIDE_RULE,
    ),
    Template(
        "ia-frame", "Frame", "interior_arch",
        "Framed hero and a calm uniform grid. Quiet, material, residential.",
        Skin(radius="4px", density=1.25, rule="1px", shadow="soft", container="1160px",
             measure="62ch"),
        "quiet", ("brass", "travertine", "studio"), "frame",
        (gal("grid", 8, ""), abt("band-surface"), svc("cards", 6, ""), prz("band-surface"),
         qts(""), prc("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_SOFT + CSS_CENTERED, tile_ratio="4/3",
    ),
    Template(
        "ia-terra", "Terra", "interior_arch",
        "Full-bleed hero and a scrolling project rail. Warm and material-led.",
        Skin(radius="8px", density=1.1, rule="1px", shadow="soft", container="1200px",
             hero_min="88svh", grain=0.12),
        "warm", ("studio", "travertine", "brass"), "full",
        (gal("strip", 10, "band-surface"), svc("cards", 6, ""), abt("band-surface"),
         prz(""), STATS, qts("band-surface"), faq_(""), con("band-surface"), CTA),
        css=CSS_SOFT, tile_ratio="4/3",
    ),
]

BY_KEY = {t.key: t for t in TEMPLATES}
BY_VERTICAL: dict[str, list[Template]] = {}
for _t in TEMPLATES:
    BY_VERTICAL.setdefault(_t.vertical, []).append(_t)


# --------------------------------------------------------------------------
# assignment
# --------------------------------------------------------------------------
def _is_premium(b: Business) -> bool:
    """Does this lead read as a high-ticket operator?"""
    if b.price and b.price["amount"] >= 150000:
        return True
    if b.wmg_listing in ("Handpicked", "Pro Plus"):
        return True
    if b.ig_followers and b.ig_followers >= 40000:
        return True
    return False


def assign(b: Business) -> tuple[Template, str]:
    """Pick a template and a palette for one business.

    Deterministic (same lead -> same site on every rebuild) and evenly spread
    across the vertical's templates, but nudged: premium leads get first refusal
    on the luxury layouts.
    """
    pool = BY_VERTICAL[b.vertical]
    luxe = [t for t in pool if t.luxury]

    if _is_premium(b) and luxe and (b.seed >> 5) % 100 < 62:
        chosen = luxe[(b.seed >> 11) % len(luxe)]
    else:
        chosen = pool[b.seed % len(pool)]

    palette_key = chosen.palettes[(b.seed >> 17) % len(chosen.palettes)]
    return chosen, palette_key
