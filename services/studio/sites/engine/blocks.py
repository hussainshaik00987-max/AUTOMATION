"""Section components — the parts a template arranges into a page.

Every block takes `(Business, Ctx)` and returns an HTML string. Templates in
`templates.py` pick a recipe of blocks plus a `Skin`; that combination, not a
per-template HTML file, is what produces thirty distinct layouts.

Two blocks earn their keep beyond looking good:

* `contact()` renders an enquiry form that **composes a prefilled WhatsApp
  message** on submit. No backend, no form service, works the moment the file is
  opened — and it maps directly onto how these businesses already take orders.
* `head()` emits JSON-LD `LocalBusiness` structured data built from real Google
  fields (rating, review count, geo, address), so the demo is genuinely better
  for search than the Instagram page most of these leads use as a website.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field

from . import content as C
from . import photos as P
from .art import VERTICAL_MOTIF, artwork, favicon, street_plate
from .data import Business, VERTICAL_LABEL
from .theme import Palette


def e(value: str) -> str:
    """Escape for HTML text and attribute contexts."""
    return html.escape(str(value or ""), quote=True)


@dataclass(slots=True)
class Ctx:
    """Everything a block needs beyond the business record."""

    palette: Palette
    template_key: str
    template_name: str
    motif: str
    tile_ratio: str = "4/5"
    art_index: int = field(default=0)
    dark_hero: bool = True
    photos: P.PhotoSet = P.EMPTY
    asset_prefix: str = P.ASSET_PREFIX
    business_name: str = ""
    base_url: str = ""
    # Absolute origin the photo files are served from. Equals
    # `{base_url}/assets/photos` for a single-project deploy, or a separate
    # origin when the library is hosted as its own Vercel project — which is
    # how a 153MB build fits a plan that caps CLI uploads at 100MB.
    asset_origin: str = ""

    def art(self, index: int, w: int = 1200, h: int = 900, *, dark: bool | None = None,
            label: str = "", css_class: str = "art") -> str:
        return artwork(
            self._seed, self.motif, index, self.palette, width=w, height=h,
            dark=self.dark_hero if dark is None else dark, label=label,
            css_class=css_class,
        )

    def shot(self, photo: P.Photo | None, *, fallback: int, w: int, h: int,
             sizes: str, rendition: str = "large", context: str = "",
             eager: bool = False, dark: bool | None = None, label: str = "",
             css_class: str = "ph", decorative: bool = False) -> str:
        """A photograph if the library has one for this slot, else the SVG.

        Keeping the procedural fallback live means `--photos none` still
        produces a complete page, and a partially-harvested library degrades
        one slot at a time instead of leaving a hole.
        """
        if photo is not None:
            return P.img(photo, rendition=rendition, sizes=sizes, css_class=css_class,
                         business=self.business_name, context=context, eager=eager,
                         prefix=self.asset_prefix, decorative=decorative)
        return self.art(fallback, w, h, dark=dark, label=label)

    _seed: int = 0


# A hero slot's crop decides which photographs suit it: the split and editorial
# heroes stand a picture on its end, the banner heroes lie it flat.
# `full` is a full-bleed cover crop and reads fine from either orientation, so
# it takes the whole vertical and gets the widest choice. The banner heroes are
# 21:8 and 16:8 and would slice a portrait into a ribbon; the split and
# editorial heroes stand the picture on its end.
HERO_SHAPE = {"full": "any", "split": "portrait", "editorial": "portrait",
              "frame": "landscape", "stack": "landscape"}


def make_ctx(b: Business, palette: Palette, template_key: str, template_name: str,
             *, hero: str = "full", gallery_count: int = 12, use_photos: bool = True,
             base_url: str = "", asset_base_url: str = "", **kwargs) -> Ctx:
    ctx = Ctx(palette=palette, template_key=template_key, template_name=template_name,
              motif=VERTICAL_MOTIF[b.vertical], **kwargs)
    ctx._seed = b.seed
    ctx.business_name = b.name
    ctx.base_url = base_url.rstrip("/")
    if asset_base_url:
        ctx.asset_prefix = asset_base_url.rstrip("/") + "/"
        ctx.asset_origin = asset_base_url.rstrip("/")
    elif ctx.base_url:
        ctx.asset_origin = ctx.base_url + "/assets/photos"
    if use_photos:
        ctx.photos = P.select(
            b.vertical, b.seed, gallery=gallery_count,
            hero_shape=HERO_SHAPE.get(hero, "landscape"), cohort=b.cohort,
            city_seed=int(hashlib.sha1((b.city or "in").encode()).hexdigest()[:8], 16),
        )
    return ctx


# ==========================================================================
# icons — inline, currentColor, 24px grid
# ==========================================================================
# Icon geometry, stored once and emitted as <symbol> defs. Ten icons
# inlined per page cost 6.9KB of duplicated path data; referenced from a
# sprite they cost about 50 bytes each, which is ~6MB across the catalogue.
ICON_DEFS: dict[str, tuple[str, str]] = {
    "whatsapp": (
        'viewBox="0 0 24 24" fill="currentColor"',
        '<path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.46 1.32 4.96L2 22l5.25-1.38a9.9 9.9 0 0 0 4.79 1.22h.01c5.46 0 9.91-4.45 9.91-9.91S17.5 2 12.04 2Zm0 18.15c-1.48 0-2.93-.4-4.2-1.15l-.3-.18-3.12.82.83-3.04-.2-.31a8.22 8.22 0 0 1-1.26-4.38c0-4.54 3.7-8.23 8.25-8.23 2.2 0 4.27.86 5.83 2.42a8.19 8.19 0 0 1 2.41 5.82c0 4.54-3.7 8.23-8.24 8.23Zm4.52-6.16c-.25-.12-1.47-.72-1.69-.81-.23-.08-.39-.12-.56.13-.16.25-.64.8-.79.97-.14.16-.29.19-.54.06-.25-.12-1.05-.39-1.99-1.23-.74-.66-1.23-1.47-1.38-1.72-.14-.25-.01-.38.11-.51.11-.11.25-.29.37-.43.13-.15.17-.25.25-.41.08-.17.04-.31-.02-.43-.06-.12-.56-1.34-.76-1.84-.2-.48-.4-.42-.56-.43h-.47c-.17 0-.43.06-.66.31-.22.25-.86.85-.86 2.07 0 1.22.89 2.4 1.01 2.56.12.17 1.75 2.67 4.23 3.74.59.26 1.05.41 1.41.52.59.19 1.13.16 1.56.1.47-.07 1.47-.6 1.68-1.18.21-.58.21-1.08.14-1.18-.06-.11-.22-.17-.47-.29Z"/>'),
    "phone": (
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"',
        '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2Z"/>'),
    "mail": (
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"',
        '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>'),
    "instagram": (
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"',
        '<rect x="2" y="2" width="20" height="20" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1.1" fill="currentColor" stroke="none"/>'),
    "facebook": (
        'viewBox="0 0 24 24" fill="currentColor"',
        '<path d="M22 12a10 10 0 1 0-11.6 9.9v-7H7.9V12h2.5V9.8c0-2.5 1.5-3.9 3.8-3.9 1.1 0 2.2.2 2.2.2v2.5h-1.3c-1.2 0-1.6.8-1.6 1.6V12h2.8l-.4 2.9h-2.4v7A10 10 0 0 0 22 12Z"/>'),
    "pin": (
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"',
        '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>'),
    "arrow": (
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"',
        '<path d="M5 12h14M13 6l6 6-6 6"/>'),
    "star": (
        'viewBox="0 0 24 24" fill="currentColor"',
        '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1Z"/>'),
    "globe": (
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"',
        '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20Z"/>'),
}

# Short ids keep every reference in the document down to ~48 bytes.
ICON_ID = {'whatsapp': 'wa', 'phone': 'ph', 'mail': 'ml', 'instagram': 'ig', 'facebook': 'fb', 'pin': 'pn', 'arrow': 'ar', 'star': 'st', 'globe': 'gl'}


def icon(name: str) -> str:
    """A reference into the page's sprite. `sprite()` emits the matching defs."""
    ref = ICON_ID.get(name)
    return f'<svg class="ic" aria-hidden="true"><use href="#i-{ref}"/></svg>' if ref else ""


def sprite(html: str) -> str:
    """The <symbol> defs for exactly the icons `html` references.

    Emitting all nine on every page would put ~3.4KB of unused geometry on a
    page that uses four of them, so the sprite is built from the finished
    document rather than declared up front.
    """
    used = set(re.findall(r'href="#i-(\w+)"', html))
    if not used:
        return ""
    symbols = []
    for name, (attrs, body) in ICON_DEFS.items():
        ref = ICON_ID[name]
        if ref in used:
            symbols.append(f'<symbol id="i-{ref}" {attrs}>{body}</symbol>')
    return ('<svg style="display:none" aria-hidden="true" '
            'xmlns="http://www.w3.org/2000/svg"><defs>'
            + "".join(symbols) + "</defs></svg>")


# ==========================================================================
# document head
# ==========================================================================
def _json_ld(b: Business) -> str:
    """LocalBusiness schema from real Google fields only."""
    schema_type = {
        "wedding_photo": "ProfessionalService",
        "bakery": "Bakery",
        "wedding_planner": "ProfessionalService",
        "fashion": "ClothingStore",
        "interior_arch": "ProfessionalService",
    }[b.vertical]

    data: dict = {
        "@context": "https://schema.org",
        "@type": schema_type,
        "name": b.name,
        "description": C.meta_description(b),
    }
    if b.category:
        data["additionalType"] = b.category
    if b.phone_e164:
        data["telephone"] = b.phone_e164
    if b.email:
        data["email"] = b.email
    if b.address or b.city:
        addr: dict = {"@type": "PostalAddress", "addressCountry": "IN"}
        if b.city:
            addr["addressLocality"] = b.city
        if b.state:
            addr["addressRegion"] = b.state
        if b.address:
            addr["streetAddress"] = b.address
        data["address"] = addr
    if b.lat and b.lng:
        data["geo"] = {"@type": "GeoCoordinates", "latitude": b.lat, "longitude": b.lng}
    if b.has_rating and b.rating:
        data["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": round(b.rating, 1),
            "reviewCount": b.reviews,
            "bestRating": 5,
        }
    if b.price:
        data["priceRange"] = b.price["display"] + "+"
    sameas = [u for u in (b.instagram, b.facebook, b.wmg_profile) if u]
    if sameas:
        data["sameAs"] = sameas
    if b.city:
        data["areaServed"] = b.city
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _social_meta(b: Business, ctx: Ctx, title: str, description: str) -> str:
    """Open Graph + Twitter card.

    Tuned for WhatsApp, which is where these links actually get sent. WhatsApp
    only renders a preview image when `og:image` is an **absolute** https URL,
    so this is empty unless the build was given a base URL — a relative path
    would silently produce a text-only preview, which is worse than none.
    It also wants a real bitmap: the SVG favicon that works everywhere else is
    ignored by its crawler, hence the 1200x630 JPEG crop of the hero photo.
    """
    # Extensionless: vercel.json sets cleanUrls, so /sites/<slug>.html answers
    # with a 308 to /sites/<slug>. Advertising the redirect target directly
    # keeps crawlers (and anyone who copies the address bar) on one URL.
    page_url = f"{ctx.base_url}/sites/{b.slug}" if ctx.base_url else ""
    tags = [
        f'<meta property="og:title" content="{e(title)}">',
        f'<meta property="og:description" content="{e(description)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{e(b.name)}">',
        '<meta property="og:locale" content="en_IN">',
    ]
    if page_url:
        tags.append(f'<meta property="og:url" content="{e(page_url)}">')
        tags.append(f'<link rel="canonical" href="{e(page_url)}">')

    photo = ctx.photos.hero or ctx.photos.about
    if ctx.asset_origin and photo is not None:
        img = photo.og_url(ctx.asset_origin)
        tags += [
            f'<meta property="og:image" content="{e(img)}">',
            f'<meta property="og:image:secure_url" content="{e(img)}">',
            '<meta property="og:image:type" content="image/jpeg">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            f'<meta property="og:image:alt" content="{e(title)}">',
            '<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:image" content="{e(img)}">',
        ]
    else:
        tags.append('<meta name="twitter:card" content="summary">')
    tags += [
        f'<meta name="twitter:title" content="{e(title)}">',
        f'<meta name="twitter:description" content="{e(description)}">',
    ]
    return "".join(tags)


def head(b: Business, ctx: Ctx, css: str = "", *, css_href: str = "") -> str:
    title = f"{b.name} — {C.tagline(b)}"
    description = C.meta_description(b)
    social_title, social_desc = C.social_title(b), C.social_description(b)
    # A crawler will not run JS, so the preview title has to be short enough to
    # survive WhatsApp's ~65-character clamp with the business name intact.
    return (
        '<!doctype html><html lang="en-IN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(title)}</title>"
        f'<meta name="description" content="{e(description)}">'
        f'<meta name="theme-color" content="{ctx.palette.accent}">'
        '<meta name="robots" content="noindex,nofollow">'
        '<meta name="format-detection" content="telephone=no">'
        f'<meta name="author" content="{e(b.name)}">'
        f"{_social_meta(b, ctx, social_title, social_desc)}"
        f'<link rel="icon" href="{favicon(b.seed, ctx.palette, b.initials)}">'
        f'<script type="application/ld+json">{_json_ld(b)}</script>'
        + (f'<link rel="stylesheet" href="{e(css_href)}">' if css_href
                 else f"<style>{css}</style>")
        + "</head><body><!--sprite-->"
        '<a class="skip" href="#main">Skip to content</a>'
    )


# ==========================================================================
# demo ribbon + header + nav
# ==========================================================================
def demo_bar(b: Business, ctx: Ctx) -> str:
    return (
        # "not published anywhere" was true while these opened off a laptop. Once
        # the catalogue is hosted it is not, and a page whose whole premise is
        # that nothing on it is invented cannot open with a false sentence.
        '<div class="demo-bar">Concept site built for <b>' + e(b.name) + "</b> · "
        "every detail below comes from your public listing · "
        "<b>private link, not listed or indexed</b></div>"
    )


NAV_ITEMS = {
    "wedding_photo": [("work", "Work"), ("services", "Services"), ("about", "About"),
                      ("faq", "FAQ"), ("contact", "Contact")],
    "bakery": [("menu", "Menu"), ("gallery", "Gallery"), ("about", "About"),
               ("faq", "FAQ"), ("contact", "Order")],
    "wedding_planner": [("work", "Weddings"), ("services", "Services"), ("process", "Process"),
                        ("faq", "FAQ"), ("contact", "Enquire")],
    "fashion": [("collection", "Collection"), ("services", "Services"), ("about", "Studio"),
                ("faq", "FAQ"), ("contact", "Visit")],
    "interior_arch": [("work", "Projects"), ("services", "Services"), ("process", "Process"),
                      ("faq", "FAQ"), ("contact", "Enquire")],
}


def header(b: Business, ctx: Ctx) -> str:
    items = "".join(
        f'<li><a href="#{key}">{e(label)}</a></li>' for key, label in NAV_ITEMS[b.vertical]
    )
    cta = ""
    if b.whatsapp:
        cta = (f'<a class="btn btn-primary" href="{e(b.whatsapp)}" target="_blank" '
               f'rel="noopener">{icon("whatsapp")}WhatsApp</a>')
    elif b.phone_e164:
        cta = (f'<a class="btn btn-primary" href="tel:{e(b.phone_e164)}">'
               f'{icon("phone")}Call</a>')
    return (
        '<header class="site-head" id="siteHead"><div class="wrap"><nav class="nav" '
        'aria-label="Primary">'
        f'<a class="brand" href="#top" aria-label="{e(b.name)} — home">'
        f'<span class="brand-mark" aria-hidden="true"><span>{e(b.initials)}</span></span>'
        f'<span class="brand-text">'
        f'<span class="brand-name">{e(b.name)}</span>'
        f'<span class="brand-sub">{e(C.tagline(b))}</span></span></a>'
        f'<ul class="nav-links" id="navLinks">{items}</ul>'
        f'<div class="nav-cta">{cta}'
        '<button class="nav-toggle" id="navToggle" aria-expanded="false" '
        'aria-controls="navLinks" aria-label="Menu">'
        "<span></span><span></span><span></span></button></div>"
        "</nav></div></header>"
    )


# ==========================================================================
# heroes
# ==========================================================================
def _hero_chips(b: Business, limit: int = 3) -> str:
    chips = C.badges(b)[:limit]
    if not chips:
        return ""
    out = []
    for chip in chips:
        star = '<span class="star" aria-hidden="true">★</span>' if "★" in chip else ""
        out.append(f'<span class="chip">{star}{e(chip.replace("★", "").strip())}</span>')
    return f'<div class="chips hero-meta">{"".join(out)}</div>'


def _hero_actions(b: Business, ctx: Ctx, ghost_label: str = "See the work") -> str:
    primary_label, _ = C.cta(b)
    btns = []
    if b.whatsapp:
        btns.append(f'<a class="btn btn-primary btn-lg" href="{e(b.whatsapp)}" '
                    f'target="_blank" rel="noopener">{icon("whatsapp")}{e(primary_label)}</a>')
    else:
        btns.append(f'<a class="btn btn-primary btn-lg" href="#contact">'
                    f'{e(primary_label)}{icon("arrow")}</a>')
    btns.append(f'<a class="btn btn-ghost btn-lg" href="#work">{e(ghost_label)}</a>')
    return f'<div class="hero-actions">{"".join(btns)}</div>'


def hero_full(b: Business, ctx: Ctx) -> str:
    """Full-bleed art with the copy centred over it."""
    headline, subhead = C.hero(b)
    # A photograph needs a heavier scrim than the procedural art does; see the
    # `.hero.on-photo` rules in css.py.
    photo_class = " on-photo" if ctx.photos.hero else ""
    return (
        f'<section class="hero on-art{photo_class}" id="top">'
        f'<div class="hero-art">{ctx.shot(ctx.photos.hero, fallback=0, w=1600, h=1100, sizes="100vw", context=C.hero_alt(b), eager=True, dark=True, decorative=True)}</div>'
        '<div class="hero-scrim"></div>'
        '<div class="wrap hero-inner" style="text-align:center">'
        f'<span class="eyebrow">{e(VERTICAL_LABEL[b.vertical])}'
        f'{" · " + e(b.city) if b.city else ""}</span>'
        f"<h1 style=\"margin-inline:auto\">{e(headline)}</h1>"
        f'<p class="lede" style="margin-inline:auto">{e(subhead)}</p>'
        f'<div style="display:flex;justify-content:center">{_hero_actions(b, ctx)}</div>'
        f'<div style="display:flex;justify-content:center">{_hero_chips(b)}</div>'
        "</div></section>"
    )


def hero_split(b: Business, ctx: Ctx) -> str:
    """Copy on one side, art on the other — reads fast, works well on mobile."""
    headline, subhead = C.hero(b)
    return (
        '<section class="hero-split" id="top">'
        '<div class="hero-copy">'
        f'<span class="eyebrow">{e(C.tagline(b))}</span>'
        f"<h1>{e(headline)}</h1>"
        f'<p class="lede">{e(subhead)}</p>'
        f"{_hero_actions(b, ctx)}{_hero_chips(b)}"
        "</div>"
        # Dark panel against light copy: the light variants render pale beside
        # body text and read as an empty placeholder rather than a photograph.
        f'<div class="hero-visual">{ctx.shot(ctx.photos.hero, fallback=0, w=900, h=1100, sizes="(max-width:900px) 100vw, 50vw", context=C.hero_alt(b), eager=True, dark=True)}</div>'
        "</section>"
    )


def hero_editorial(b: Business, ctx: Ctx) -> str:
    """Magazine cover: oversized type, art offset below-right."""
    headline, subhead = C.hero(b)
    return (
        '<section class="hero hero-ed" id="top">'
        '<div class="wrap hero-inner">'
        '<div class="ed-grid">'
        "<div class=\"ed-copy\">"
        f'<span class="eyebrow">{e(VERTICAL_LABEL[b.vertical])}</span>'
        f"<h1>{e(headline)}</h1>"
        f'<p class="lede">{e(subhead)}</p>'
        f"{_hero_actions(b, ctx)}{_hero_chips(b)}"
        "</div>"
        f'<div class="ed-art">{ctx.shot(ctx.photos.hero, fallback=0, w=760, h=1000, sizes="(max-width:900px) 100vw, 46vw", context=C.hero_alt(b), eager=True, dark=True)}</div>'
        "</div></div></section>"
    )


def hero_frame(b: Business, ctx: Ctx) -> str:
    """Art inset within a generous margin — gallery-wall feel."""
    headline, subhead = C.hero(b)
    return (
        '<section class="hero hero-fr" id="top">'
        '<div class="wrap hero-inner">'
        f'<span class="eyebrow">{e(C.tagline(b))}</span>'
        f"<h1>{e(headline)}</h1>"
        f'<p class="lede">{e(subhead)}</p>'
        f"{_hero_actions(b, ctx)}"
        f'<div class="fr-art">{ctx.shot(ctx.photos.hero, fallback=0, w=1500, h=780, sizes="(max-width:1240px) 100vw, 1180px", context=C.hero_alt(b), eager=True, dark=True)}</div>'
        f"{_hero_chips(b, 4)}"
        "</div></section>"
    )


def hero_stack(b: Business, ctx: Ctx) -> str:
    """Type block first, then a full-width art band beneath it."""
    headline, subhead = C.hero(b)
    return (
        '<section class="hero hero-st" id="top">'
        '<div class="wrap hero-inner">'
        f'<span class="eyebrow">{e(VERTICAL_LABEL[b.vertical])}'
        f'{" · " + e(b.city) if b.city else ""}</span>'
        f"<h1>{e(headline)}</h1>"
        '<div class="st-row">'
        f'<p class="lede">{e(subhead)}</p>{_hero_actions(b, ctx)}'
        "</div></div>"
        f'<div class="st-art">{ctx.shot(ctx.photos.hero, fallback=0, w=1700, h=760, sizes="100vw", context=C.hero_alt(b), eager=True, dark=True, decorative=True)}</div>'
        "</section>"
    )


HEROES = {
    "full": hero_full, "split": hero_split, "editorial": hero_editorial,
    "frame": hero_frame, "stack": hero_stack,
}


# ==========================================================================
# gallery
# ==========================================================================
GALLERY_TITLE = {
    "wedding_photo": ("Selected work", "A few frames from recent weddings."),
    "bakery": ("From the counter", "A sample of what comes out of our kitchen."),
    "wedding_planner": ("Weddings we've made", "Decor and production from recent celebrations."),
    "fashion": ("The collection", "Current pieces and recent commissions."),
    "interior_arch": ("Selected projects", "Recent residential and commercial work."),
}


def gallery(b: Business, ctx: Ctx, layout: str = "grid", count: int = 8,
            band: str = "") -> str:
    heading, sub = GALLERY_TITLE[b.vertical]
    captions = C.gallery_captions(b, count)
    tiles = []
    for i, cap in enumerate(captions):
        photo = ctx.photos.tile(i)
        # Masonry spans follow each photograph's own aspect ratio, so nothing is
        # cropped in this layout at all — the wall is uneven because the pictures
        # are, which is what a real portfolio wall looks like. Without a photo it
        # falls back to a fixed cycle so the rhythm still looks composed.
        span = ""
        if layout == "masonry":
            if photo is not None:
                # ~272px is the settled column width at the default container.
                rows = min(46, max(18, round(272 / max(photo.ratio, 0.4) / 11)))
            else:
                rows = (26, 34, 30, 40, 28, 36, 32, 30)[i % 8]
            span = f' style="grid-row:span {rows}"'
        art = ctx.shot(photo, fallback=i + 1, w=700, h=880, rendition="tile",
                       sizes="(max-width:560px) 92vw, (max-width:960px) 46vw, 30vw",
                       context=cap, dark=False,
                       label=f"{cap} — artwork placeholder for {b.name}")
        # The lightbox upgrades to the wide rendition; without this it would
        # blow a 700px tile up to 1080px and show the softness.
        full = (f' data-full="{e(ctx.asset_prefix + photo.large)}"' if photo else "")
        tiles.append(
            f'<button class="tile" data-i="{i}" data-cap="{e(cap)}"{span}{full} '
            f'aria-label="View {e(cap)}"><figure style="margin:0">{art}'
            f"<figcaption>{e(cap)}</figcaption></figure></button>"
        )
    # These pages go to real business owners, so the placeholder has to announce
    # itself rather than imply we already hold their portfolio.
    note = (
        '<p class="form-note" style="margin-top:1.6rem">'
        + ("Stock photography, shown to size the frames — your own work drops "
           "straight in." if ctx.photos.gallery else
           "Artwork is generated placeholder — your own photographs drop straight "
           "into these frames.")
        + "</p>"
    )
    return (
        f'<section id="work" class="{band}">'
        '<div class="wrap"><div class="sec-head rv">'
        f'<span class="eyebrow">{e(GALLERY_TITLE[b.vertical][0])}</span>'
        f"<h2>{e(heading)}</h2><p class=\"lede\">{e(sub)}</p></div>"
        f'<div class="gal gal-{layout} rv" id="gallery">{"".join(tiles)}</div>'
        f"{note}</div></section>"
    )


# ==========================================================================
# services
# ==========================================================================
SERVICE_HEADING = {
    "wedding_photo": ("What we shoot", "Coverage built around your schedule, not a fixed package sheet."),
    "bakery": ("What we bake", "Order ahead for custom work, or pick up what's on the counter today."),
    "wedding_planner": ("What we handle", "Take all of it, or just the parts you'd rather not do."),
    "fashion": ("What we make", "Ready to wear, made to measure, or designed from scratch."),
    "interior_arch": ("What we do", "Design, drawings and delivery — as much or as little as you need."),
}


def services(b: Business, ctx: Ctx, style: str = "cards", count: int = 6,
             band: str = "band-surface") -> str:
    heading, sub = SERVICE_HEADING[b.vertical]
    items = C.services(b, count)

    if style == "list":
        rows = "".join(
            f'<div class="svc-row rv"><div class="n">{i + 1:02d}</div>'
            f"<div><h3>{e(title)}</h3><p>{e(body)}</p></div></div>"
            for i, (title, body) in enumerate(items)
        )
        inner = f'<div class="svc-list">{rows}</div>'
    elif style == "numbered":
        cards = "".join(
            f'<article class="card rv"><span class="card-num">{i + 1:02d}</span>'
            f"<h3>{e(title)}</h3><p>{e(body)}</p></article>"
            for i, (title, body) in enumerate(items)
        )
        inner = f'<div class="cards">{cards}</div>'
    else:
        cards = "".join(
            f'<article class="card rv"><div class="card-rule"></div>'
            f"<h3>{e(title)}</h3><p>{e(body)}</p></article>"
            for title, body in items
        )
        inner = f'<div class="cards">{cards}</div>'

    return (
        f'<section id="services" class="{band} edge-top"><div class="wrap">'
        '<div class="sec-head rv"><span class="eyebrow">Services</span>'
        f"<h2>{e(heading)}</h2><p class=\"lede\">{e(sub)}</p></div>"
        f"{inner}</div></section>"
    )


# ==========================================================================
# about
# ==========================================================================
def about(b: Business, ctx: Ctx, band: str = "", with_art: bool = True) -> str:
    paras = "".join(f"<p>{e(p)}</p>" for p in C.about(b))
    art = ""
    if with_art:
        art = ('<div class="about-art">' + ctx.shot(ctx.photos.about, fallback=20, w=800, h=980,
                       sizes="(max-width:880px) 100vw, 40vw",
                       context=C.about_alt(b), dark=False) + '</div>')
    stats_html = _stats_inline(b)
    return (
        f'<section id="about" class="{band} edge-top"><div class="wrap">'
        f'<div class="about-grid rv">{art}'
        '<div class="about-copy"><span class="eyebrow">About</span>'
        f"<h2>{e(b.name)}</h2>"
        f'<div class="prose">{paras}</div>{stats_html}</div>'
        "</div></div></section>"
    )


def _stat_size(value: str) -> str:
    """Class that keeps a stat value inside its ~140px grid track.

    A stat is one unbreakable token: "5.0" is three characters, "Visakhapatnam"
    is thirteen. At the display size the second is twice its track wide, and the
    only thing stopping it from pushing the whole about grid sideways is
    `overflow-wrap`, which splits it mid-word.
    """
    longest = max((len(w) for w in value.split()), default=0)
    if longest >= 10:
        return " v-long"
    return " v-mid" if longest >= 7 else ""


def _stats_inline(b: Business) -> str:
    items = C.stats(b)
    if not items:
        return ""
    cells = "".join(
        f'<div class="stat"><span class="v{_stat_size(s.value)}">{e(s.value)}</span>'
        f'<span class="l">{e(s.label)}</span></div>'
        for s in items
    )
    return f'<div class="stats" style="margin-top:2.6rem">{cells}</div>'


def stats_band(b: Business, ctx: Ctx) -> str:
    items = C.stats(b)
    if not items:
        return ""
    cells = "".join(
        f'<div class="stat rv"><span class="v{_stat_size(s.value)}">{e(s.value)}</span>'
        f'<span class="l">{e(s.label)}</span></div>'
        for s in items
    )
    return (
        '<section class="band-deep"><div class="wrap">'
        f'<div class="stats">{cells}</div></div></section>'
    )


# ==========================================================================
# process
# ==========================================================================
PROCESS_HEADING = {
    "wedding_photo": "How booking works",
    "bakery": "How ordering works",
    "wedding_planner": "How we work",
    "fashion": "How it works",
    "interior_arch": "How a project runs",
}


def process(b: Business, ctx: Ctx, band: str = "band-surface") -> str:
    steps = "".join(
        f'<div class="step rv"><h3>{e(title)}</h3><p>{e(body)}</p></div>'
        for title, body in C.process(b)
    )
    return (
        f'<section id="process" class="{band} edge-top"><div class="wrap">'
        '<div class="sec-head rv"><span class="eyebrow">Process</span>'
        f"<h2>{e(PROCESS_HEADING[b.vertical])}</h2>"
        '<p class="lede">No mystery, no surprises — here is exactly what happens '
        "from your first message.</p></div>"
        f'<div class="steps">{steps}</div></div></section>'
    )


# ==========================================================================
# pricing (only rendered when real price data exists)
# ==========================================================================
def pricing(b: Business, ctx: Ctx, band: str = "") -> str:
    if not b.price:
        return ""
    unit = b.price["unit"] or ""
    note = {
        "wedding_photo": "Coverage is quoted per day. Multi-day functions, second shooters, "
                         "films and albums are priced on top — you'll get it in writing before you commit.",
        "bakery": "Pricing varies with size, design and flavour. Send your reference and "
                  "serving count and we'll quote back the same day.",
        "wedding_planner": "Every wedding is scoped differently. This is where packages start; "
                           "the real number comes after we understand your guest count and venue.",
        "fashion": "Pricing depends on fabric, work and finishing. We confirm before anything is cut.",
        "interior_arch": "Indicative only — the real figure comes after a site visit and an "
                         "agreed scope. We'd rather quote accurately than quote fast.",
    }[b.vertical]
    return (
        f'<section id="pricing" class="{band} edge-top"><div class="wrap">'
        '<div class="price-card rv">'
        '<div><span class="eyebrow">Investment</span>'
        f'<h2>Starting at {e(b.price["display"])}'
        f'{" " + e(unit) if unit else ""}</h2>'
        f'<p class="lede">{e(note)}</p></div>'
        f'<div class="price-cta">{_quote_button(b)}</div>'
        "</div></div></section>"
    )


def _quote_button(b: Business) -> str:
    if b.whatsapp:
        return (f'<a class="btn btn-primary btn-lg" href="{e(b.whatsapp)}" target="_blank" '
                f'rel="noopener">{icon("whatsapp")}Get a quote</a>')
    return f'<a class="btn btn-primary btn-lg" href="#contact">Get a quote{icon("arrow")}</a>'


# ==========================================================================
# testimonials
# ==========================================================================
def quotes(b: Business, ctx: Ctx, band: str = "band-surface") -> str:
    cards = []
    for q in C.testimonials(b):
        tag = '<span class="sample-tag">Sample</span>' if q.is_placeholder else ""
        cls = "quote is-sample" if q.is_placeholder else "quote"
        cards.append(
            f'<figure class="{cls} rv">{tag}<p>{e(q.text)}</p>'
            f"<cite>{e(q.attribution)}</cite></figure>"
        )
    sub = (
        "The cards marked <em>sample</em> are placeholders — your real reviews go "
        "straight in here."
    )
    return (
        f'<section id="reviews" class="{band} edge-top"><div class="wrap">'
        '<div class="sec-head rv"><span class="eyebrow">Reviews</span>'
        "<h2>What clients say</h2>"
        f'<p class="lede">{sub}</p></div>'
        f'<div class="quotes">{"".join(cards)}</div></div></section>'
    )


# ==========================================================================
# FAQ
# ==========================================================================
def faq(b: Business, ctx: Ctx, band: str = "") -> str:
    items = "".join(
        f"<details{' open' if i == 0 else ''}><summary>{e(q)}</summary>"
        f'<div class="a">{e(a)}</div></details>'
        for i, (q, a) in enumerate(C.faqs(b))
    )
    return (
        f'<section id="faq" class="{band} edge-top"><div class="wrap">'
        '<div class="sec-head rv"><span class="eyebrow">Questions</span>'
        "<h2>Before you get in touch</h2></div>"
        f'<div class="faq rv">{items}</div></div></section>'
    )


# ==========================================================================
# contact — the WhatsApp-composing enquiry form
# ==========================================================================
FORM_FIELDS = {
    "wedding_photo": [("Wedding date", "date", "date"), ("Venue or city", "text", "venue")],
    "bakery": [("Needed by", "date", "date"), ("Servings / size", "text", "size")],
    "wedding_planner": [("Wedding date", "date", "date"), ("Approx. guests", "number", "guests")],
    "fashion": [("Wear date", "date", "date"), ("What you're after", "text", "item")],
    "interior_arch": [("Location", "text", "venue"), ("Approx. area (sq ft)", "number", "area")],
}


def contact(b: Business, ctx: Ctx, band: str = "band-surface") -> str:
    _, cta_line = C.cta(b)
    extra = "".join(
        f'<div class="field"><label for="f-{key}">{e(label)}</label>'
        f'<input type="{ftype}" id="f-{key}" name="{key}"></div>'
        for label, ftype, key in FORM_FIELDS[b.vertical]
    )

    links = []
    if b.phone_display:
        links.append((f"tel:{b.phone_e164}", "phone", "Phone", b.phone_display, ""))
    if b.whatsapp:
        links.append((b.whatsapp, "whatsapp", "WhatsApp", "Message us directly", "_blank"))
    if b.email:
        links.append((f"mailto:{b.email}", "mail", "Email", b.email, ""))
    if b.instagram:
        handle = f"@{b.ig_handle}" if b.ig_handle else "Instagram"
        links.append((b.instagram, "instagram", "Instagram", handle, "_blank"))
    if b.facebook:
        links.append((b.facebook, "facebook", "Facebook", "Find us on Facebook", "_blank"))
    if b.address:
        maps = f"https://www.google.com/maps/search/?api=1&query={b.lat},{b.lng}" \
            if b.lat and b.lng else ""
        links.append((maps or "#contact", "pin", "Studio", b.address, "_blank" if maps else ""))

    link_html = "".join(
        f'<li><a href="{e(href)}"{f" target={t} rel=noopener" if t else ""}>'
        f'<span class="ic">{icon(ic)}</span><span><span class="k">{e(k)}</span>'
        f'<span class="v">{e(v)}</span></span></a></li>'
        for href, ic, k, v, t in links
    )

    # The form has no backend by design: on submit it builds a WhatsApp deep link.
    wa_number = b.phone_e164.lstrip("+") if b.phone_e164 else ""
    form_note = (
        "Sends straight to WhatsApp — no form service, no waiting on email."
        if wa_number else
        "This demo opens your email client. On a live site it would post to your inbox or CRM."
    )
    submit_label = "Send on WhatsApp" if wa_number else "Send enquiry"

    return (
        f'<section id="contact" class="{band} edge-top"><div class="wrap">'
        '<div class="sec-head rv"><span class="eyebrow">Contact</span>'
        f"<h2>{e(cta_line)}</h2></div>"
        '<div class="contact-grid">'
        '<form class="rv" id="enq" novalidate '
        f'data-wa="{e(wa_number)}" data-email="{e(b.email)}" data-name="{e(b.name)}">'
        '<div class="field"><label for="f-name">Your name</label>'
        '<input type="text" id="f-name" name="name" required autocomplete="name"></div>'
        '<div class="field"><label for="f-phone">Phone</label>'
        '<input type="tel" id="f-phone" name="phone" autocomplete="tel"></div>'
        f"{extra}"
        '<div class="field"><label for="f-msg">Tell us a little more</label>'
        '<textarea id="f-msg" name="message" rows="4"></textarea></div>'
        f'<button class="btn btn-primary btn-lg" type="submit">'
        f'{icon("whatsapp") if wa_number else ""}{e(submit_label)}</button>'
        f'<p class="form-note">{e(form_note)}</p>'
        '<p class="form-note" id="enqStatus" role="status" aria-live="polite"></p>'
        "</form>"
        f'<div class="rv"><ul class="contact-list">{link_html}</ul>'
        f"{_map_block(b, ctx)}</div>"
        "</div></div></section>"
    )


def _map_block(b: Business, ctx: Ctx) -> str:
    """A static locality card. A real map would be an external request."""
    if not (b.address or b.city):
        return ""
    where = b.locality or b.city
    return (
        f'<div class="map-frame" style="margin-top:2rem">'
        f'{street_plate(b.seed, ctx.palette)}'
        '<div class="map-pin"><div class="box">'
        f"<strong>{e(where)}</strong>"
        f"<span>{e(b.address or b.city)}</span>"
        "</div></div></div>"
    )


# ==========================================================================
# CTA band + footer + FAB
# ==========================================================================
def cta_band(b: Business, ctx: Ctx) -> str:
    label, line = C.cta(b)
    primary = (
        f'<a class="btn btn-primary btn-lg" href="{e(b.whatsapp)}" target="_blank" '
        f'rel="noopener">{icon("whatsapp")}{e(label)}</a>'
        if b.whatsapp else
        f'<a class="btn btn-primary btn-lg" href="#contact">{e(label)}{icon("arrow")}</a>'
    )
    secondary = (
        f'<a class="btn btn-ghost btn-lg" href="tel:{e(b.phone_e164)}">'
        f'{icon("phone")}{e(b.phone_display)}</a>'
        if b.phone_e164 else ""
    )
    return (
        '<section class="band-deep cta-band"><div class="wrap">'
        '<span class="eyebrow on-deep">Get in touch</span>'
        f"<h2>{e(line)}</h2>"
        f'<div class="hero-actions">{primary}{secondary}</div>'
        "</div></section>"
    )


def footer(b: Business, ctx: Ctx) -> str:
    nav = "".join(
        f'<li><a href="#{key}">{e(label)}</a></li>' for key, label in NAV_ITEMS[b.vertical]
    )
    reach = []
    if b.phone_display:
        reach.append(f'<li><a href="tel:{e(b.phone_e164)}">{e(b.phone_display)}</a></li>')
    if b.whatsapp:
        reach.append(f'<li><a href="{e(b.whatsapp)}" target="_blank" rel="noopener">'
                     "WhatsApp</a></li>")
    if b.email:
        reach.append(f'<li><a href="mailto:{e(b.email)}">{e(b.email)}</a></li>')
    if b.instagram:
        reach.append(f'<li><a href="{e(b.instagram)}" target="_blank" rel="noopener">'
                     f'{e("@" + b.ig_handle if b.ig_handle else "Instagram")}</a></li>')

    where = ""
    if b.address:
        where = f"<li>{e(b.address)}</li>"
    elif b.city:
        where = f"<li>{e(b.city)}</li>"

    return (
        '<footer class="site-foot"><div class="wrap">'
        '<div class="foot-grid">'
        '<div><div class="brand" style="margin-bottom:1rem">'
        f'<span class="brand-mark" aria-hidden="true"><span>{e(b.initials)}</span></span>'
        f'<span class="brand-text"><span class="brand-name">{e(b.name)}</span>'
        f'<span class="brand-sub">{e(C.tagline(b))}</span></span></div>'
        f'<p style="color:var(--ink-soft);font-size:.92rem;max-width:34ch">'
        f"{e(C.hero(b)[1])}</p></div>"
        f"<div><h4>Explore</h4><ul>{nav}</ul></div>"
        f'<div><h4>Reach us</h4><ul>{"".join(reach)}</ul></div>'
        f"<div><h4>Find us</h4><ul>{where}</ul></div>"
        "</div>"
        '<div class="foot-bottom">'
        f"<span>© <span id=\"yr\"></span> {e(b.name)}</span>"
        "<span>Concept demo · design template</span>"
        "</div></div></footer>"
    )


def fab(b: Business, ctx: Ctx) -> str:
    if not b.whatsapp:
        return ""
    return (
        f'<a class="fab" href="{e(b.whatsapp)}" target="_blank" rel="noopener" '
        f'aria-label="Message {e(b.name)} on WhatsApp">{icon("whatsapp")}'
        "<span>WhatsApp us</span></a>"
    )


# ==========================================================================
# lightbox markup + page script
# ==========================================================================
def lightbox() -> str:
    return (
        '<dialog class="lb" id="lb" aria-label="Gallery viewer">'
        '<button class="lb-close" id="lbClose" aria-label="Close">✕</button>'
        '<button class="lb-prev" id="lbPrev" aria-label="Previous">‹</button>'
        '<button class="lb-next" id="lbNext" aria-label="Next">›</button>'
        '<div><div class="lb-stage" id="lbStage"></div>'
        '<p class="lb-cap" id="lbCap"></p></div></dialog>'
    )


SCRIPT = """
(function(){
  var d=document;
  var yr=d.getElementById('yr'); if(yr) yr.textContent=new Date().getFullYear();

  // sticky header state
  var head=d.getElementById('siteHead');
  if(head){
    var onScroll=function(){head.dataset.stuck=window.scrollY>12?'1':'0';};
    onScroll(); addEventListener('scroll',onScroll,{passive:true});
  }

  // mobile nav
  var tog=d.getElementById('navToggle'), links=d.getElementById('navLinks');
  if(tog&&links){
    tog.addEventListener('click',function(){
      var open=tog.getAttribute('aria-expanded')==='true';
      tog.setAttribute('aria-expanded',String(!open));
      links.dataset.open=open?'0':'1';
    });
    links.addEventListener('click',function(ev){
      if(ev.target.tagName==='A'){tog.setAttribute('aria-expanded','false');links.dataset.open='0';}
    });
  }

  // scroll reveal
  var rv=d.querySelectorAll('.rv');
  if('IntersectionObserver' in window && !matchMedia('(prefers-reduced-motion:reduce)').matches){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(en){ if(en.isIntersecting){en.target.classList.add('in');io.unobserve(en.target);} });
    },{rootMargin:'0px 0px -8% 0px',threshold:.08});
    rv.forEach(function(el){io.observe(el);});
  } else { rv.forEach(function(el){el.classList.add('in');}); }

  // gallery lightbox
  var lb=d.getElementById('lb'), stage=d.getElementById('lbStage'), cap=d.getElementById('lbCap');
  var tiles=[].slice.call(d.querySelectorAll('.tile')), idx=0;
  function show(i){
    if(!tiles.length) return;
    idx=(i+tiles.length)%tiles.length;
    var t=tiles[idx], art=t.querySelector('img,svg');
    if(art && art.tagName==='IMG'){
      // Swap in the wide rendition rather than upscaling the 700px tile.
      var big=d.createElement('img');
      big.src=t.dataset.full||art.currentSrc||art.src;
      big.alt=art.alt||''; big.decoding='async';
      stage.innerHTML=''; stage.appendChild(big);
    } else {
      stage.innerHTML = art?art.outerHTML:'';
    }
    cap.textContent = t.dataset.cap||'';
  }
  function open(i){ if(!lb) return; show(i); if(lb.showModal){lb.showModal();} else {lb.setAttribute('open','');} }
  function close(){ if(!lb) return; if(lb.close){lb.close();} else {lb.removeAttribute('open');} }
  tiles.forEach(function(t,i){ t.addEventListener('click',function(){open(i);}); });
  var c=d.getElementById('lbClose'); if(c) c.addEventListener('click',close);
  var p=d.getElementById('lbPrev'); if(p) p.addEventListener('click',function(){show(idx-1);});
  var n=d.getElementById('lbNext'); if(n) n.addEventListener('click',function(){show(idx+1);});
  if(lb){
    lb.addEventListener('click',function(ev){ if(ev.target===lb) close(); });
    d.addEventListener('keydown',function(ev){
      if(!lb.open) return;
      if(ev.key==='ArrowRight') show(idx+1);
      if(ev.key==='ArrowLeft') show(idx-1);
    });
  }

  // enquiry form -> prefilled WhatsApp message (no backend needed)
  var f=d.getElementById('enq');
  if(f){
    f.addEventListener('submit',function(ev){
      ev.preventDefault();
      var status=d.getElementById('enqStatus');
      var name=(f.querySelector('#f-name')||{}).value||'';
      if(!name.trim()){
        if(status) status.textContent='Please add your name first.';
        (f.querySelector('#f-name')||{}).focus&&f.querySelector('#f-name').focus();
        return;
      }
      var lines=['Hi '+(f.dataset.name||'')+',',''];
      var labels=f.querySelectorAll('label');
      labels.forEach(function(l){
        var input=d.getElementById(l.getAttribute('for'));
        if(input&&input.value&&input.value.trim()){
          lines.push(l.textContent.trim()+': '+input.value.trim());
        }
      });
      lines.push('','(Sent from your website)');
      var body=lines.join('\\n');
      var wa=f.dataset.wa;
      if(wa){
        window.open('https://wa.me/'+wa+'?text='+encodeURIComponent(body),'_blank','noopener');
        if(status) status.textContent='Opening WhatsApp…';
      } else if(f.dataset.email){
        window.location.href='mailto:'+f.dataset.email+'?subject='+
          encodeURIComponent('Website enquiry')+'&body='+encodeURIComponent(body);
        if(status) status.textContent='Opening your email app…';
      }
    });
  }
})();
"""


def tail(js_src: str = "") -> str:
    """`defer` rather than inline-at-end: the parser never stalls on the script,
    and the shared file is fetched in parallel with the document."""
    script = (f'<script src="{e(js_src)}" defer></script>' if js_src
              else f"<script>{SCRIPT}</script>")
    return f"{lightbox()}{script}</body></html>"
