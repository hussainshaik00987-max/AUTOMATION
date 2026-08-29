"""WedMeGood collector.

Why this source matters: it carries two things Google Maps does not.
  - vendor_price / starting_price_new -> a direct BUDGET qualifier (a photographer
    charging Rs 49,000/day can obviously afford a Rs 40-60k site)
  - membership == 'Handpicked' -> a PAID listing, i.e. proven willingness to spend
    money on lead generation. That is the single best "will actually buy" signal.

Data lives in window.__INITIAL_STATE__ (a Redux dump). It is JS, not JSON - bare
`undefined` values break json.loads, so we rewrite them to null before parsing.
Pagination is ?page=N; vendorList.numPages tells us when to stop.
"""
import re, json, time, random
from curl_cffi import requests
from config import UA, IMPERSONATE

BASE = "https://www.wedmegood.com/vendors/{city}/{cat}/"
STATE_RE = re.compile(r'window\.__INITIAL_STATE__\s*=\s*(\{.*)', re.S)
UNDEF_RE = re.compile(r':\s*undefined\b')

# WedMeGood category slug -> our vertical key
CATEGORIES = {
    "wedding-photographers": "wedding_photo",
    "wedding-planners": "wedding_planner",
    "wedding-decorators": "wedding_planner",
    "bridal-makeup-artists": "fashion",
    "bridal-lehengas": "fashion",
    "wedding-cakes": "bakery",
}

# WedMeGood city slugs (lowercased city name works for most)
CITY_SLUGS = {
    "Delhi": "delhi-ncr", "Gurgaon": "delhi-ncr", "Noida": "delhi-ncr",
    "Bangalore": "bangalore", "Mumbai": "mumbai", "Hyderabad": "hyderabad",
    "Chennai": "chennai", "Pune": "pune", "Kolkata": "kolkata",
    "Ahmedabad": "ahmedabad", "Jaipur": "jaipur", "Lucknow": "lucknow",
    "Indore": "indore", "Surat": "surat", "Chandigarh": "chandigarh",
    "Kochi": "kochi", "Coimbatore": "coimbatore", "Bhopal": "bhopal",
    "Nagpur": "nagpur", "Vadodara": "vadodara", "Udaipur": "udaipur",
    "Jodhpur": "jodhpur", "Varanasi": "varanasi", "Amritsar": "amritsar",
    "Dehradun": "dehradun", "Guwahati": "guwahati", "Bhubaneswar": "bhubaneswar",
    "Ludhiana": "ludhiana", "Nashik": "nashik", "Raipur": "raipur",
    "Mysore": "mysore", "Madurai": "madurai", "Rajkot": "rajkot",
    "Visakhapatnam": "visakhapatnam", "Kanpur": "kanpur",
}


def parse_state(html):
    m = STATE_RE.search(html)
    if not m:
        return None
    raw = UNDEF_RE.sub(":null", m.group(1))
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw)
        return obj
    except Exception:
        return None


def _money(v):
    if not v:
        return None
    try:
        return int(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _wa_number(v):
    """Normalise a WhatsApp number to a 10-digit Indian mobile.
    NB: never use lstrip('+91') here - it strips *characters*, not a prefix,
    and silently mangles numbers that begin with 9, 1 or 0."""
    if not v:
        return None
    d = re.sub(r"\D", "", str(v))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 else None


def shape(v, city, vertical):
    slug = v.get("new_slug") or ""
    price = _money(v.get("vendor_price") or v.get("starting_price_new"))
    return {
        "name": v.get("name"),
        "source": "wedmegood",
        "vertical": vertical,
        # Several config cities share one WMG slug (Delhi/Gurgaon/Noida -> delhi-ncr),
        # so trust the vendor's own city over the slug we searched under.
        "search_city": v.get("base_city") or v.get("city") or city,
        "address": v.get("address"),
        "area": v.get("locality"),
        "city_state": v.get("base_city") or v.get("city"),
        "rating": _f(v.get("vendor_rating")),
        "reviews": _i(v.get("reviews_count")),
        "photo_count": _i(v.get("image_count")),
        "description": (v.get("information") or "")[:1500] or None,
        "primary_cat": v.get("category_alias"),
        "price_hint": (f"{v.get('vendor_currency','')}{v.get('vendor_price')} "
                       f"{v.get('vendor_price_subtext') or ''}".strip()
                       if v.get("vendor_price") else None),
        "other_links": [f"https://www.wedmegood.com{slug}"] if slug else [],
        # Deliberately NOT setting `whatsapp` here. The listing only says a number
        # exists, not what it is; storing a placeholder would win the COALESCE merge
        # and permanently block the real wa.me link the profile fetch provides.
        "_price_value": price,
        "_membership": v.get("membership"),
        "_profile_url": f"https://www.wedmegood.com{slug}" if slug else None,
    }


class WMGClient:
    def __init__(self, proxy=None):
        self.s = requests.Session(impersonate=IMPERSONATE)
        self.proxy = proxy
        self.calls = 0
        self.throttled = 0

    def fetch_profile(self, url, retries=2):
        """Profile pages are ~1.4MB but carry phone + whatsapp + email + booking_count.
        Only worth spending on vendors that already look good from the listing."""
        for a in range(retries):
            try:
                kw = {"timeout": 60, "headers": {"User-Agent": UA,
                                                 "Accept-Language": "en-IN,en;q=0.9"}}
                if self.proxy:
                    kw["proxies"] = {"http": self.proxy, "https": self.proxy}
                r = self.s.get(url, **kw)
                self.calls += 1
                if r.status_code == 429:
                    # WedMeGood really does rate-limit profile fetches. A 4s retry is far
                    # too short and just burns the attempt budget - back off properly.
                    self.throttled += 1
                    time.sleep(20 * (a + 1))
                    continue
                if r.status_code != 200:
                    time.sleep(4 * (a + 1))
                    continue
                st = parse_state(r.text)
                if not st:
                    return None
                p = (st.get("vendorProfile") or {}).get("profile")
                if not p:
                    return None
                phones = p.get("phone") or []
                addr = (p.get("address") or [{}])
                a0 = addr[0] if addr else {}
                wa = _wa_number(p.get("vendor_whatsapp_number"))
                out = {
                    "phone": phones[0] if phones else None,
                    "email": p.get("login_email"),
                    "whatsapp": f"https://wa.me/91{wa}" if wa else None,
                    "price_hint": (f"{p.get('vendor_currency','')}{p.get('vendor_price')}"
                                   if p.get("vendor_price") else None),
                    "photo_count": p.get("total_images"),
                    "rating": p.get("vendor_rating"),
                    "description": (p.get("information") or "")[:1500] or None,
                    "address": a0.get("display_address"),
                    "lat": _f(a0.get("latitude")),
                    "lng": _f(a0.get("longitude")),
                    "_membership": p.get("membership"),
                    "_bookings": p.get("booking_count"),
                    "_price_value": _money(p.get("vendor_price")),
                }
                return {k: v for k, v in out.items() if v not in (None, "")}
            except Exception:
                time.sleep(4 * (a + 1))
        return None

    def fetch_page(self, city_slug, cat, page=1, retries=3):
        url = BASE.format(city=city_slug, cat=cat)
        params = {"page": page} if page > 1 else {}
        for a in range(retries):
            try:
                kw = {"params": params, "timeout": 45,
                      "headers": {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}}
                if self.proxy:
                    kw["proxies"] = {"http": self.proxy, "https": self.proxy}
                r = self.s.get(url, **kw)
                self.calls += 1
                if r.status_code == 404:
                    return [], 0
                if r.status_code != 200:
                    time.sleep(5 * (a + 1))
                    continue
                st = parse_state(r.text)
                if not st:
                    return [], 0
                vl = st.get("vendorList", {})
                return vl.get("vendorsArray") or [], vl.get("numPages") or 0
            except Exception:
                time.sleep(5 * (a + 1))
        return [], 0


def polite_sleep():
    time.sleep(random.uniform(1.5, 3.5))
