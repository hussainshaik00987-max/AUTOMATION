"""Lead records for the video build.

Reuses `demosites.engine.data` for CSV cleaning — that module already repairs the
truncated descriptions, the Instagram-page-title bios and the four-deep locality
chains — and adds the two things a video needs and a web page does not:

* a **display name** short enough to sit in a 402pt WhatsApp header without
  ellipsing. Scraped names carry SEO tails ("… Best Photographers In Nashik Top
  Photographers In") that are invisible on a wide page and fatal in a nav bar.
* a **spoken name** for prose, which keeps a legal suffix the header drops.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DEMOSITES = _HERE.parent.parent / "demosites"


def _load_data_module():
    """Import demosites' cleaner without requiring demosites to be a package."""
    if str(_DEMOSITES) not in sys.path:
        sys.path.insert(0, str(_DEMOSITES))
    spec = importlib.util.spec_from_file_location(
        "_demosites_data", _DEMOSITES / "engine" / "data.py")
    mod = importlib.util.module_from_spec(spec)
    # @dataclass(slots=True) rebuilds the class and looks itself up in
    # sys.modules to resolve annotations — registering before exec, not after.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


data = _load_data_module()
VERTICAL_LABEL = data.VERTICAL_LABEL

# ---------------------------------------------------------------------------
# Name shortening
# ---------------------------------------------------------------------------

_TRADEMARK = re.compile(r"[®™©]")
_SPLIT_HARD = re.compile(r"\s*[|•·:/]\s*|\s+[–—]\s+|\s*\(")
# " - " is only a separator when what follows looks like a descriptor, not when
# it is part of the name ("Studio 9 - 5" stays whole).
_SPLIT_DASH = re.compile(r"\s+-\s+(?=[A-Za-z])")
_SEO_TAIL = re.compile(
    r"\b(best|top|no\.?\s*1|number\s*1|leading|premier|professional|award[- ]winning|"
    r"cheap|affordable|famous|popular)\b.*$", re.I)
_IN_CITY = re.compile(r"\s+(?:in|at|near)\s+[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)?\s*$")
_TRADE_TAIL = re.compile(
    r"\s*[-,&]?\s*\b(pvt\.?\s*ltd\.?|private\s+limited|ltd\.?|llp|inc\.?|co\.?)\b\s*$", re.I)
_STRAY_L = re.compile(r"\s+[Ll]\s+")          # "Pr L Events L Weddings"
_MULTISPACE = re.compile(r"\s{2,}")

_KEEP_WORDS = 5      # a header name past this reads as a sentence, not a name
_MAX_CHARS = 30
# Words that must never be the last thing in a shortened name: "The Glare
# Designer Boutique by" reads as a truncation bug, which is exactly what it is.
_DANGLING = {"by", "and", "&", "the", "in", "at", "of", "with", "for", "from",
             "a", "an", "-", "|", "or", "to", "on"}


def _trim_dangling(name: str) -> str:
    words = name.split()
    while words and words[-1].strip(".,-&").lower() in _DANGLING:
        words.pop()
    return " ".join(words)


def display_name(raw: str) -> str:
    """Shorten a scraped business name to something that fits a chat header."""
    name = _TRADEMARK.sub("", raw or "").strip()
    name = _STRAY_L.sub(" & ", name)
    name = _SPLIT_HARD.split(name)[0].strip()
    name = _SPLIT_DASH.split(name)[0].strip()

    # Only strip an SEO tail if real name survives in front of it.
    trimmed = _SEO_TAIL.sub("", name).strip(" -,&")
    if len(trimmed.split()) >= 2:
        name = trimmed

    name = _TRADE_TAIL.sub("", name).strip(" -,&")
    stripped_city = _IN_CITY.sub("", name).strip(" -,&")
    if len(stripped_city.split()) >= 2:
        name = stripped_city

    words = name.split()
    if len(words) > _KEEP_WORDS:
        words = _trim_dangling(" ".join(words[:_KEEP_WORDS])).split()
    name = _MULTISPACE.sub(" ", " ".join(words)).strip(" -,&.")

    while len(name) > _MAX_CHARS and len(name.split()) > 2:
        name = _trim_dangling(name.rsplit(" ", 1)[0]).strip(" -,&.")
    if len(name) > _MAX_CHARS:
        name = name[:_MAX_CHARS - 1].rstrip(" -,&.") + "…"
    return _trim_dangling(name) or (raw or "").strip()[:_MAX_CHARS]


# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Lead:
    """Everything a scene needs, already cleaned and display-safe."""

    slug: str
    name: str              # full cleaned name, for prose and the end card
    display_name: str      # short name, for the WhatsApp header
    initials: str
    vertical: str
    category: str
    segment: str
    score: int
    city: str
    area: str
    city_tier: int
    price: dict | None
    rating: float | None
    reviews: int
    ig_followers: int | None
    phone_display: str
    whatsapp: str
    seed: int

    @property
    def has_rating(self) -> bool:
        return bool(self.rating and self.reviews >= 20)

    @property
    def vertical_label(self) -> str:
        return VERTICAL_LABEL.get(self.vertical, "Local Business")


def load(csv_path: str | Path, limit: int | None = None) -> list[Lead]:
    out = []
    for b in data.load(csv_path, limit=limit):
        out.append(Lead(
            slug=b.slug,
            name=b.name,
            display_name=display_name(b.name),
            initials=b.initials,
            vertical=b.vertical,
            category=b.category,
            segment=b.segment,
            score=b.score,
            city=b.city,
            area=b.area,
            city_tier=b.city_tier,
            price=b.price,
            rating=b.rating,
            reviews=b.reviews,
            ig_followers=b.ig_followers,
            phone_display=b.phone_display,
            whatsapp=b.whatsapp,
            seed=b.seed,
        ))
    return out
