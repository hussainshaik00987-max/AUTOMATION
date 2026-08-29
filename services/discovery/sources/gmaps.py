"""Google Maps collector.

Results are not server-rendered; they arrive via an XHR whose `pb` protobuf param we
captured once with a real browser (see refresh_pb.py). We replay that template over plain
HTTP, substituting query / coords / offset. No browser needed at scrape time.
"""
import re, json, time, random, sys
from curl_cffi import requests
from config import UA, IMPERSONATE, PB_TEMPLATE, GMAPS_DELAY, PAGE_SIZE, SOCIAL_HOSTS, LINKHUB_HOSTS

_PB = None


def pb_template():
    global _PB
    if _PB is None:
        _PB = PB_TEMPLATE.read_text().strip()
    return _PB


def build_pb(lat, lng, offset, zoom_m=120663.07):
    pb = pb_template()
    pb = re.sub(r'!1d[\d.]+!2d[\d.-]+!3d[\d.-]+', f'!1d{zoom_m}!2d{lng}!3d{lat}', pb, count=1)
    pb = re.sub(r'!7i\d+!8i\d+', f'!7i{PAGE_SIZE}!8i{offset}', pb, count=1)
    return pb


def g(o, *ix):
    for i in ix:
        try:
            o = o[i]
        except Exception:
            return None
    return o


def _harvest_links(o, out, depth=0):
    if depth > 9:
        return
    if isinstance(o, str) and o.startswith("http"):
        out.append(o)
    elif isinstance(o, list):
        for x in o:
            _harvest_links(x, out, depth + 1)


def parse_place(p):
    r = {}
    r["name"] = g(p, 11)
    r["place_id"] = g(p, 78)
    r["feature_id"] = g(p, 10)
    r["address"] = g(p, 39)
    r["city_state"] = g(p, 166)
    r["area"] = g(p, 14)
    r["lat"], r["lng"] = g(p, 9, 2), g(p, 9, 3)
    r["rating"] = g(p, 4, 7)
    r["reviews"] = g(p, 4, 8) if g(p, 4, 8) is not None else g(p, 37, 1)
    r["categories"] = g(p, 13) or []
    r["primary_cat"] = g(p, 13, 0)
    r["description"] = g(p, 32, 1, 1)
    r["owner"] = g(p, 57, 1)
    r["photo_count"] = len(g(p, 72, 0) or [])
    r["has_hours"] = 1 if g(p, 203) else 0

    site = g(p, 7, 0)
    r["website"] = site
    r["website_domain"] = g(p, 7, 1)

    ph = g(p, 178, 0)
    r["phone"] = None
    for pair in (g(ph, 1) or []):
        if isinstance(pair, list) and len(pair) > 1 and pair[1] == 2:
            r["phone"] = pair[0]           # +91 ... form
    if not r["phone"]:
        r["phone"] = g(ph, 0)

    links = []
    _harvest_links(g(p, 75), links)
    if site:
        links.append(site)
    links = list(dict.fromkeys(links))

    r["whatsapp"] = next((l for l in links if any(w in l.lower() for w in
                          ("wa.me", "wa.link", "whatsapp"))), None)
    # only real profile URLs - help./business./l. subdomains are not profiles
    r["instagram"] = next((l for l in links if "instagram.com" in l.lower()
                           and not re.search(r"//(?:help|about|business|developers?|l|blog)\.instagram\.com",
                                             l, re.I)), None)
    r["facebook"] = next((l for l in links if re.search(r"facebook\.com|fb\.me", l.lower())), None)
    r["other_links"] = [l for l in links
                        if l not in (r["whatsapp"], r["instagram"], r["facebook"], site)][:6]
    return r


def parse_response(txt):
    body = txt.split("\n", 1)[1] if txt.startswith(")]}'") else txt
    data = json.loads(body)
    out = []
    for entry in (g(data, 64) or []):
        p = g(entry, 1)
        if p and g(p, 11):
            out.append(parse_place(p))
    return out


class MapsClient:
    def __init__(self, proxy=None):
        self.s = requests.Session(impersonate=IMPERSONATE)
        self.proxy = proxy
        self.calls = 0
        self.blocks = 0

    def fetch_page(self, query, lat, lng, offset, retries=3):
        params = {"tbm": "map", "authuser": "0", "hl": "en", "gl": "in",
                  "q": query, "pb": build_pb(lat, lng, offset)}
        headers = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9",
                   "Referer": "https://www.google.com/maps/"}
        last = None
        for a in range(retries):
            try:
                kw = {"params": params, "headers": headers, "timeout": 45}
                if self.proxy:
                    kw["proxies"] = {"http": self.proxy, "https": self.proxy}
                r = self.s.get("https://www.google.com/search", **kw)
                self.calls += 1
                if r.status_code == 200 and len(r.text) > 20000:
                    return parse_place_list(r.text)
                if r.status_code in (429, 503) or "unusual traffic" in r.text[:2000].lower():
                    self.blocks += 1
                    time.sleep(30 * (a + 1))
                    last = f"blocked {r.status_code}"
                    continue
                # short body = no results for this offset
                if r.status_code == 200:
                    return []
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(4 * (a + 1))
        raise RuntimeError(last or "unknown")


def parse_place_list(txt):
    try:
        return parse_response(txt)
    except Exception:
        return []


def polite_sleep():
    time.sleep(random.uniform(*GMAPS_DELAY))
