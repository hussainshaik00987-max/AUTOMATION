"""Load `exports/top_leads.csv` and turn each row into a clean `Business` profile.

The CSV is scraper output, so nothing is trusted as-is. Three fields in particular
are damaged in ways that would show up on a rendered page:

* ``Description`` is hard-truncated at ~159 chars, often mid-word
  ("...organization with its central command in Lucknow is called Arsim Worldwide Occas").
* ``IG Bio`` is usually the Instagram *page title*, not a bio
  ("Fusion Frames Production (&#064;fusionframes_production) - Instagram photos and videos"),
  and carries numeric HTML entities.
* ``Area`` is a raw Google Maps locality chain up to four segments long
  ("Gyan Vihar Colony, Shivaji Nagar, Shankar Puri, Chinhat").

Everything downstream reads the cleaned dataclass, never the raw row.
"""

from __future__ import annotations

import csv
import html
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

VERTICALS = ("wedding_photo", "bakery", "wedding_planner", "fashion", "interior_arch")

VERTICAL_LABEL = {
    "wedding_photo": "Wedding Photography",
    "bakery": "Bakery & Cake Studio",
    "wedding_planner": "Wedding Planning & Decor",
    "fashion": "Fashion & Boutique",
    "interior_arch": "Interior Design & Architecture",
}

# Words that must keep their casing when a name arrives SHOUTING.
_KEEP_UPPER = {
    "3D", "AC", "AV", "BFF", "DJ", "HD", "II", "III", "IV", "JW", "Meridian", "LED",
    "LLP", "MG", "NRI", "PVT", "RK", "SK", "TV", "UK", "USA", "VFX", "VIP",
}
_LOWER_PARTICLES = {"and", "by", "de", "for", "in", "of", "on", "the", "to", "with", "at"}

_IG_TITLE_JUNK = re.compile(
    r"(instagram photos and videos|"
    r"•\s*instagram|"
    r"^\s*login\s*•|"
    r"page not found|"
    r"see instagram photos)",
    re.I,
)

_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_WS = re.compile(r"\s+")
_PRICE_RE = re.compile(r"₹\s*([\d,]+)\s*(.*)")
_DIGITS = re.compile(r"\D+")


def _clean_ws(value: str) -> str:
    """Collapse whitespace and decode HTML entities (the scraper leaves `&#064;` etc.)."""
    if not value:
        return ""
    text = html.unescape(str(value))
    text = text.replace(" ", " ").replace("​", "")
    return _WS.sub(" ", text).strip()


def _clean_multiline(value: str) -> str:
    """Like `_clean_ws` but keeps line breaks — Instagram bios are line-oriented."""
    if not value:
        return ""
    text = html.unescape(str(value)).replace(" ", " ").replace("​", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def smart_title(name: str) -> str:
    """Title-case only when the source is shouting; leave deliberate casing alone.

    "Meridian PRODUCTIONS" -> "Meridian Studios", but "iStudio by RK" is returned untouched.
    """
    name = _clean_ws(name)
    if not name:
        return ""
    letters = [c for c in name if c.isalpha()]
    if not letters:
        return name
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    if upper_ratio < 0.8:  # already mixed case - the owner meant it
        return name

    out: list[str] = []
    for idx, word in enumerate(name.split(" ")):
        bare = word.strip(".,-&()")
        if bare.upper() in _KEEP_UPPER:
            out.append(word.upper())
        elif idx and bare.lower() in _LOWER_PARTICLES:
            out.append(word.lower())
        elif "." in bare and len(bare) <= 4:  # initials like "S.K."
            out.append(word.upper())
        else:
            out.append(word.capitalize())
    return " ".join(out)


def slugify(value: str, fallback: str = "site") -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    return (value or fallback)[:70].strip("-") or fallback


def _to_float(value: str) -> float | None:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # drop NaN


def _to_int(value: str) -> int | None:
    out = _to_float(value)
    return None if out is None else int(out)


def _json_list(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw or raw == "[]":
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    seen, out = set(), []
    for item in parsed:
        url = _clean_ws(str(item))
        if url.startswith("http") and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def repair_truncated(text: str, min_keep: int = 45) -> str:
    """Recover a readable sentence from a mid-word truncation.

    Prefers the last complete sentence. Falls back to dropping the dangling
    final word and adding an ellipsis. Returns "" if too little survives to
    be worth printing.
    """
    text = _clean_ws(text)
    if not text:
        return ""

    ends = [m.end() for m in _SENTENCE_END.finditer(text)]
    if ends and ends[-1] >= min_keep:
        return text[: ends[-1]].strip()

    if text[-1] in ".!?":
        return text
    # Mid-word cut: drop the partial tail word.
    head = text.rsplit(" ", 1)[0].rstrip(" ,;:-&/")
    if len(head) < min_keep:
        return ""
    return head + "…"


def clean_ig_bio(raw: str) -> str:
    """Return a real Instagram bio, or "" when the scraper captured the page title."""
    bio = _clean_multiline(raw)
    if not bio or _IG_TITLE_JUNK.search(bio):
        return ""
    # A bare "Name (@handle)" line is title residue, not a bio.
    if re.fullmatch(r"[^\n]{0,80}\(@[\w.]+\)", bio):
        return ""
    return bio


def tidy_area(raw: str, city: str) -> str:
    """Google locality chains run 3-4 segments deep; keep the two most specific."""
    raw = _clean_ws(raw)
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    city_l = (city or "").lower()
    parts = [p for p in parts if p.lower() != city_l]
    if not parts:
        return ""
    return ", ".join(parts[:2])


def normalise_phone(raw: str) -> tuple[str, str]:
    """-> (E.164 for tel:/wa.me, pretty display). Assumes +91 for bare 10-digit."""
    raw = _clean_ws(raw)
    if not raw:
        return "", ""
    digits = _DIGITS.sub("", raw)
    if digits.startswith("0"):
        digits = digits.lstrip("0")
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        local = digits[2:]
        return "+" + digits, f"+91 {local[:5]} {local[5:]}"
    if 10 <= len(digits) <= 15:
        return "+" + digits, "+" + digits
    return "", raw


def parse_price(raw: str) -> dict | None:
    """`₹75,000 per day` / `₹2,00,000 Onwards` -> structured amount + qualifier."""
    raw = _clean_ws(raw)
    if not raw:
        return None
    match = _PRICE_RE.search(raw)
    if not match:
        return None
    amount = _to_int(match.group(1).replace(",", ""))
    if not amount:
        return None
    tail = match.group(2).strip().lower()
    if "day" in tail:
        unit, qualifier = "per day", "per day"
    elif "person" in tail or "plate" in tail:
        unit, qualifier = "per plate", "per plate"
    elif "onward" in tail:
        unit, qualifier = "onwards", "onwards"
    else:
        unit, qualifier = "", tail
    return {
        "amount": amount,
        "unit": unit,
        "qualifier": qualifier,
        "display": _inr(amount),
        "raw": raw,
    }


def _inr(amount: int) -> str:
    """Indian digit grouping: 200000 -> ₹2,00,000."""
    s = str(int(amount))
    if len(s) <= 3:
        return "₹" + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups + [tail])


def _norm_url(raw: str) -> str:
    raw = _clean_ws(raw)
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")
    return raw


def _handle_from_ig(url: str) -> str:
    match = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url or "")
    return match.group(1).strip(".") if match else ""


@dataclass(slots=True)
class Business:
    """One lead, cleaned and ready to render. All strings are display-safe."""

    row_id: int
    name: str
    slug: str
    vertical: str
    segment: str
    score: int

    # contact
    phone_e164: str = ""
    phone_display: str = ""
    whatsapp: str = ""
    email: str = ""
    instagram: str = ""
    ig_handle: str = ""
    ig_followers: int | None = None
    ig_posts: int | None = None
    facebook: str = ""
    website: str = ""
    other_links: list[str] = field(default_factory=list)

    # place
    city: str = ""
    city_tier: int = 1
    area: str = ""
    address: str = ""
    state: str = ""
    lat: float | None = None
    lng: float | None = None

    # proof
    rating: float | None = None
    reviews: int = 0
    category: str = ""
    price: dict | None = None
    wmg_listing: str = ""
    recent_bookings: str = ""
    wmg_profile: str = ""

    # narrative
    description: str = ""
    ig_bio: str = ""
    pitch_angle: str = ""
    automation_angle: str = ""
    automation_score: int = 0

    # Rank of this lead among everyone in the same city and vertical, assigned
    # in `load()`. Photo selection walks it so that the first N studios in
    # Mumbai all open on a different photograph instead of colliding by chance —
    # two competitors in one city comparing links is the only place a repeat
    # actually gets noticed.
    cohort: int = 0

    # ---- derived helpers -------------------------------------------------
    @property
    def initials(self) -> str:
        words = [w for w in re.split(r"[^A-Za-z0-9]+", self.name) if w]
        if not words:
            return "ST"
        if len(words) == 1:
            return words[0][:2].upper()
        return (words[0][0] + words[-1][0]).upper()

    @property
    def has_rating(self) -> bool:
        return bool(self.rating and self.reviews >= 5)

    @property
    def locality(self) -> str:
        """Most specific human place string available."""
        return f"{self.area}, {self.city}" if self.area else self.city

    @property
    def seed(self) -> int:
        """Stable per-business seed so art/copy never shift between builds."""
        import hashlib

        return int(hashlib.sha256(self.slug.encode()).hexdigest()[:12], 16)


def _dedupe_slug(base: str, taken: set[str], city: str) -> str:
    slug = base
    if slug in taken and city:
        slug = f"{base}-{slugify(city)}"
    counter = 2
    while slug in taken:
        slug = f"{base}-{counter}"
        counter += 1
    taken.add(slug)
    return slug


def load(csv_path: str | Path, limit: int | None = None) -> list[Business]:
    """Read the leads CSV into cleaned `Business` records."""
    csv_path = Path(csv_path)
    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if limit:
        rows = rows[:limit]

    taken: set[str] = set()
    out: list[Business] = []

    for idx, row in enumerate(rows):
        name = smart_title(row.get("Business Name", ""))
        if not name:
            continue

        vertical = _clean_ws(row.get("Vertical", "")) or "wedding_photo"
        if vertical not in VERTICALS:
            vertical = "wedding_photo"

        city = _clean_ws(row.get("City", ""))
        slug = _dedupe_slug(slugify(name, f"lead-{idx}"), taken, city)

        phone_e164, phone_display = normalise_phone(row.get("Phone", ""))
        whatsapp = _clean_ws(row.get("WhatsApp Link", ""))
        if not whatsapp and phone_e164:
            whatsapp = "https://wa.me/" + phone_e164.lstrip("+")

        instagram = _norm_url(row.get("Instagram", ""))
        # Some rows put the Instagram URL in the Website column - that is the
        # scraper's "website_is_social" pain signal, not a real site.
        website = _norm_url(row.get("Website", ""))
        if "instagram.com" in website.lower():
            instagram = instagram or website
            website = ""

        city_state = _clean_ws(row.get("City/State", ""))
        state = city_state.split(",")[-1].strip() if "," in city_state else ""

        out.append(
            Business(
                row_id=idx,
                name=name,
                slug=slug,
                vertical=vertical,
                segment=_clean_ws(row.get("Segment", "")),
                score=_to_int(row.get("Score", 0)) or 0,
                phone_e164=phone_e164,
                phone_display=phone_display,
                whatsapp=whatsapp,
                email=_clean_ws(row.get("Email", "")) or _clean_ws(row.get("IG Email", "")),
                instagram=instagram,
                ig_handle=_handle_from_ig(instagram),
                ig_followers=_to_int(row.get("IG Followers", "")),
                ig_posts=_to_int(row.get("IG Posts", "")),
                facebook=_norm_url(row.get("Facebook", "")),
                website=website,
                other_links=_json_list(row.get("Other Links", "")),
                city=city,
                city_tier=_to_int(row.get("City Tier", 1)) or 1,
                area=tidy_area(row.get("Area", ""), city),
                address=_clean_ws(row.get("Address", "")),
                state=state,
                lat=_to_float(row.get("Lat", "")),
                lng=_to_float(row.get("Lng", "")),
                rating=_to_float(row.get("Rating", "")),
                reviews=_to_int(row.get("Reviews", 0)) or 0,
                category=_clean_ws(row.get("Category", "")),
                price=parse_price(row.get("Price", "")),
                wmg_listing=_clean_ws(row.get("WMG Listing", "")),
                recent_bookings=_clean_ws(row.get("Recent Bookings", "")),
                wmg_profile=_clean_ws(row.get("WedMeGood Profile", "")),
                description=repair_truncated(row.get("Description", "")),
                ig_bio=clean_ig_bio(row.get("IG Bio", "")),
                pitch_angle=_clean_ws(row.get("Pitch Angle", "")),
                automation_angle=_clean_ws(row.get("Automation Angle", "")),
                automation_score=_to_int(row.get("Automation Score", 0)) or 0,
            )
        )

    # Deterministic and independent of CSV row order, so a lead keeps its rank
    # whether or not the file is re-sorted or --limit is used.
    by_group: dict[tuple[str, str], list[Business]] = {}
    for b in out:
        by_group.setdefault((b.city, b.vertical), []).append(b)
    for group in by_group.values():
        for rank, b in enumerate(sorted(group, key=lambda x: x.slug)):
            b.cohort = rank
    return out
