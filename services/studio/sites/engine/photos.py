"""Stock photography — selection, spreading and markup.

The leads CSV contains no images, so every page borrows from a curated library
harvested by `tools/harvest_photos.py` (Pexels; free for commercial and
non-commercial use, no attribution required).

Two problems have to be solved for this not to read as a template farm:

**Relevance.** A Delhi wedding planner needs a mandap, not a barn wedding in
Vermont. Photos are tagged by `vertical` *and* `topic`, and each slot on the
page draws from the topics that suit it — establishing shots for heroes,
close/human shots for the about column.

**Spread.** Two failure modes look equally cheap: six near-identical bride
portraits inside one gallery, and two studios in the same city opening on the
same photograph. Selection is therefore topic-first round-robin — one photo per
topic before any topic repeats — with the entry point into every list driven by
the business seed, so galleries are varied *within* a page and different
*between* pages.

Everything is deterministic: the same slug always gets the same photographs, on
every machine and every rebuild.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "assets" / "photos" / "photos.json"

# Where the photo directory sits relative to a rendered page. Pages live in
# `out/sites/<slug>.html`, assets in `out/assets/photos/`.
ASSET_PREFIX = "../assets/photos/"


@dataclass(frozen=True, slots=True)
class Photo:
    id: str
    vertical: str
    topic: str
    alt: str
    ratio: float          # width / height of the source
    large: str            # <vertical>/<id>-w1400.webp
    tile: str             # <vertical>/<id>-w700.webp
    og: str               # <vertical>/<id>-og.jpg
    lqip: str             # 20px data URI, painted while the real file loads

    def src(self, rendition: str = "tile", prefix: str = ASSET_PREFIX) -> str:
        return prefix + (self.large if rendition == "large" else self.tile)

    def og_url(self, asset_origin: str) -> str:
        """Absolute URL for `og:image`. `asset_origin` must already be absolute —
        a relative path here silently produces a text-only WhatsApp preview."""
        return asset_origin.rstrip("/") + "/" + self.og


# --------------------------------------------------------------------------
# which topics suit which slot
# --------------------------------------------------------------------------
# Establishing shots. No longer used to pick the hero (see `select`), but kept
# because it documents which subjects read as an opening frame, and the about
# slot's list is defined against it.
HERO_TOPICS = {
    "wedding_photo": ("couple", "ceremony", "reception", "celebration", "preweddin",
                      "southindian", "baraat"),
    "bakery": ("shop", "cake", "wedcake", "pastry", "bread"),
    "wedding_planner": ("mandap", "stage", "venue", "table", "lights", "floral"),
    "fashion": ("boutique", "model", "rack", "lehenga", "saree"),
    "interior_arch": ("living", "kitchen", "facade", "minimal", "dining", "office"),
}

# Closer, human, behind-the-scenes — reads as "who we are" beside about copy.
ABOUT_TOPICS = {
    "wedding_photo": ("candid", "details", "bride", "groom", "mehendi"),
    "bakery": ("baker", "bread", "chocolate", "cookies"),
    "wedding_planner": ("floral", "candles", "entrance", "marigold"),
    "fashion": ("tailor", "embroidery", "fabric", "silk"),
    "interior_arch": ("materials", "plan", "stair", "retail"),
}

# Portrait-leaning slots (editorial hero, about column) look wrong with a wide
# crop; this is the ratio below which a photo counts as portrait/square.
PORTRAIT_MAX_RATIO = 1.05
LANDSCAPE_MIN_RATIO = 1.25

# Must match tools/harvest_photos.py. The tile is capped on width, the large
# rendition on its LONG edge — so a portrait's large width is less than
# LARGE_LONG_EDGE, and `srcset` has to say so. Declaring a width the file does
# not have makes the browser pick the wrong candidate and quietly show a soft
# image on exactly the screens the big rendition exists for.
TILE_WIDTH = 600
LARGE_LONG_EDGE = 1100


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------
def _load() -> dict[str, list[Photo]]:
    if not MANIFEST.exists():
        return {}
    raw = json.loads(MANIFEST.read_text("utf-8"))
    out: dict[str, list[Photo]] = {}
    for rec in raw.get("photos", []):
        files = rec.get("files") or {}
        if not files.get("lg") or not files.get("sm") or not rec.get("og"):
            continue
        photo = Photo(
            id=rec["id"], vertical=rec["vertical"], topic=rec["topic"],
            alt=rec.get("alt", "").strip(), ratio=float(rec.get("ratio") or 1.5),
            large=files["lg"], tile=files["sm"], og=rec["og"],
            lqip=rec.get("lqip", ""),
        )
        out.setdefault(photo.vertical, []).append(photo)
    # Stable order regardless of manifest ordering, so selection is reproducible.
    for photos in out.values():
        photos.sort(key=lambda p: (p.topic, p.id))
    return out


LIBRARY: dict[str, list[Photo]] = _load()


def available(vertical: str = "") -> bool:
    return bool(LIBRARY.get(vertical) if vertical else LIBRARY)


def stats() -> dict[str, int]:
    return {v: len(p) for v, p in sorted(LIBRARY.items())}


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def _by_topic(photos: list[Photo]) -> dict[str, list[Photo]]:
    groups: dict[str, list[Photo]] = {}
    for p in photos:
        groups.setdefault(p.topic, []).append(p)
    return groups


def _spread(photos: list[Photo], seed: int, count: int) -> list[Photo]:
    """Pick `count` photos, one topic at a time, entering each list by seed.

    Round-robin over topics guarantees subject variety inside one gallery; the
    per-topic offset guarantees two businesses that happen to draw the same
    topics still get different frames.
    """
    if not photos or count <= 0:
        return []
    groups = _by_topic(photos)
    topics = sorted(groups)
    start = seed % len(topics)
    topics = topics[start:] + topics[:start]

    picked: list[Photo] = []
    used: set[str] = set()
    for lap in range(16):
        for i, topic in enumerate(topics):
            bucket = groups[topic]
            idx = (seed // (7 * (i + 1) + 1) + lap) % len(bucket)
            for step in range(len(bucket)):
                cand = bucket[(idx + step) % len(bucket)]
                if cand.id not in used:
                    used.add(cand.id)
                    picked.append(cand)
                    break
            if len(picked) >= count:
                return picked
    # Library smaller than the request — repeat rather than leave holes.
    while len(picked) < count and photos:
        picked.append(photos[(seed + len(picked)) % len(photos)])
    return picked


def _round_robin(photos: list[Photo], seed: int) -> list[Photo]:
    """The whole pool, ordered one topic at a time.

    Walking this list in single steps gives subject variety for free: rank 0
    gets a mandap, rank 1 a stage, rank 2 a venue, rather than three near
    identical mandaps.
    """
    groups = _by_topic(photos)
    topics = sorted(groups)
    start = seed % len(topics)
    topics = topics[start:] + topics[:start]
    for name in topics:
        offset = seed % len(groups[name])
        groups[name] = groups[name][offset:] + groups[name][:offset]
    out: list[Photo] = []
    for lap in range(max(len(g) for g in groups.values())):
        for name in topics:
            if lap < len(groups[name]):
                out.append(groups[name][lap])
    return out


def _prefer(photos: list[Photo], topics: tuple[str, ...]) -> list[Photo]:
    subset = [p for p in photos if p.topic in topics]
    return subset if len(subset) >= 4 else photos


def _orient(photos: list[Photo], shape: str) -> list[Photo]:
    """Narrow to photos whose native crop suits the slot, if enough exist."""
    if shape == "landscape":
        subset = [p for p in photos if p.ratio >= LANDSCAPE_MIN_RATIO]
    elif shape == "portrait":
        subset = [p for p in photos if p.ratio <= PORTRAIT_MAX_RATIO]
    else:
        return photos
    return subset if len(subset) >= 4 else photos


@dataclass(frozen=True, slots=True)
class PhotoSet:
    """Every photograph one page will use, chosen once and reused by blocks."""

    hero: Photo | None
    about: Photo | None
    gallery: tuple[Photo, ...]

    def tile(self, index: int) -> Photo | None:
        return self.gallery[index % len(self.gallery)] if self.gallery else None


EMPTY = PhotoSet(hero=None, about=None, gallery=())


def select(vertical: str, seed: int, *, gallery: int = 8,
           hero_shape: str = "landscape", cohort: int = 0,
           city_seed: int = 0) -> PhotoSet:
    """Choose the full photo set for one business.

    `cohort` is the business's rank among leads in the same city and vertical,
    and `city_seed` a hash of the city. Together they walk the hero pool one
    step at a time from a per-city starting point, so the first N studios in a
    city are guaranteed different opening photographs, while cohort 0 in Pune
    and cohort 0 in Jaipur still differ.
    """

    photos = LIBRARY.get(vertical) or []
    if not photos:
        return EMPTY

    # The hero is the single most exposed photograph on the page — it is also
    # what WhatsApp shows as the link preview, so two prospects comparing links
    # is exactly where a repeat gets noticed. It therefore draws from the whole
    # vertical rather than the curated establishing-shot subset: inside one
    # vertical every photo is on topic anyway, and the wider pool roughly
    # doubles the number of distinct openings.
    hero_pool = _orient(photos, hero_shape)
    hero_photo = None
    if hero_pool:
        # Interleave by topic first so consecutive cohort ranks step across
        # subjects, not down one topic's list.
        #
        # The ordering is keyed on `city_seed`, NOT on the per-business seed:
        # every lead in a city has to be walking the *same* list for its rank
        # in that list to mean anything. Keying it per business reshuffles the
        # list under each lead and collisions come straight back.
        ordered = _round_robin(hero_pool, city_seed)
        hero_photo = ordered[cohort % len(ordered)]

    about_pool = _prefer(photos, ABOUT_TOPICS.get(vertical, ()))
    about_pool = [p for p in about_pool if not hero_photo or p.id != hero_photo.id]
    about = _spread(about_pool, seed ^ 0x5DEECE66D ^ (cohort * 0x2545F491), 1)
    about_photo = about[0] if about else None

    taken = {p.id for p in (hero_photo, about_photo) if p}
    tiles = _spread([p for p in photos if p.id not in taken],
                    seed ^ 0x9E3779B9 ^ (cohort * 0x85EBCA6B), gallery)
    return PhotoSet(hero=hero_photo, about=about_photo, gallery=tuple(tiles))


# --------------------------------------------------------------------------
# markup
# --------------------------------------------------------------------------
def _esc(value: str) -> str:
    import html

    return html.escape(str(value or ""), quote=True)


def _alt_for(photo: Photo, business: str, context: str) -> str:
    """Alt text describing what the picture shows.

    Deliberately *not* "stock photo standing in for <business>": alt text is
    read aloud in place of the image, and a disclaimer there is both worse
    a11y and 50 wasted bytes on every one of fourteen images per page. The
    disclosure belongs in visible copy, and the gallery caption carries it.
    """
    base = (photo.alt or context).strip()
    return (base[:1].upper() + base[1:]) if base else context


def img(photo: Photo | None, *, rendition: str = "tile", sizes: str = "100vw",
        css_class: str = "ph", business: str = "", context: str = "",
        eager: bool = False, prefix: str = ASSET_PREFIX,
        decorative: bool = False) -> str:
    """One responsive `<img>`, LQIP-backed, with intrinsic dimensions."""
    if photo is None:
        return ""
    ratio = max(photo.ratio, 0.05)
    large_w = LARGE_LONG_EDGE if ratio >= 1 else max(1, round(LARGE_LONG_EDGE * ratio))
    width = large_w if rendition == "large" else TILE_WIDTH
    height = max(1, round(width / ratio))
    srcset = (f"{prefix}{photo.tile} {TILE_WIDTH}w, "
              f"{prefix}{photo.large} {large_w}w")
    # Only the URL is inline; sizing and position live in the .ph rule.
    style = f'style="background-image:url({photo.lqip})"' if photo.lqip else ""
    if decorative:
        alt_attr = 'alt="" role="presentation"'
    else:
        alt_attr = f'alt="{_esc(_alt_for(photo, business, context))}"'
    loading = 'loading="eager" fetchpriority="high"' if eager else 'loading="lazy"'
    return (
        f'<img class="{css_class}" src="{prefix}{photo.src(rendition, "")}" '
        f'srcset="{srcset}" sizes="{_esc(sizes)}" width="{width}" height="{height}" '
        f'{alt_attr} {loading} decoding="async" {style}>'
    )
