"""Instagram enrichment.

Endpoint: /api/v1/users/web_profile_info/ with the web App-ID header.
Requires TLS impersonation - plain requests/httpx get fingerprinted and blocked.

Response-code semantics (determined empirically, see RESEARCH.md):
  200 + data.user      -> ok
  200 + data.user null -> hidden/removed
  404                  -> username does not exist (so never guess handles)
  400                  -> persistent profile-specific server bug
                          ("ig_business_category_subvertical deleted").
                          NOT rate limiting - retrying is useless. Fall back to og:meta HTML.
  429 / challenge      -> real rate limit; back off hard.
"""
import re, json, time, random
from curl_cffi import requests
from config import UA, IMPERSONATE, IG_DELAY

APP_ID = "936619743392459"
HEADERS = {
    "User-Agent": UA,
    "X-IG-App-ID": APP_ID,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

HANDLE_RE = re.compile(r"(?:www\.)?instagram\.com/(?:p/|reel/)?@?([A-Za-z0-9_.]{2,30})", re.I)
RESERVED = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "direct",
            "about", "legal", "developer", "web", "s", "channel", "help", "privacy",
            "terms", "login", "signup", "emails", "challenge", "oauth"}
# help.instagram.com / business.instagram.com etc. are not profiles
BAD_SUBDOMAIN = re.compile(r"//(?:help|about|business|developers?|l|blog)\.instagram\.com", re.I)


def handle_from_url(url):
    if not url:
        return None
    if BAD_SUBDOMAIN.search(url):
        return None
    m = HANDLE_RE.search(url)
    if not m:
        return None
    h = m.group(1).strip(".").lower()
    if h in RESERVED or len(h) < 2:
        return None
    if h.isdigit():                 # numeric ids are article/help refs, not handles
        return None
    return h


class IGClient:
    def __init__(self, proxy=None):
        self.s = requests.Session(impersonate=IMPERSONATE)
        self.proxy = proxy
        self.calls = 0
        self.rate_limited = 0
        self.cooldown_until = 0
        self.consecutive_limits = 0

    def _get(self, url, **kw):
        if self.proxy:
            kw["proxies"] = {"http": self.proxy, "https": self.proxy}
        return self.s.get(url, headers=HEADERS, timeout=35, **kw)

    def fetch(self, handle):
        """Returns (status, dict|None). status in ok|notfound|hidden|schemabug|ratelimit|error"""
        now = time.time()
        if now < self.cooldown_until:
            time.sleep(self.cooldown_until - now)
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={handle}"
        try:
            r = self._get(url)
            self.calls += 1
        except Exception as e:
            return "error", {"err": f"{type(e).__name__}: {e}"}

        if r.status_code == 404:
            return "notfound", None
        if r.status_code == 400:
            return self._html_fallback(handle)
        if r.status_code in (429, 401, 403):
            # Escalating backoff. Once Instagram penalises an IP the block outlasts a
            # flat 15 minutes - observed rate-limiting on the very first call after a
            # 10-minute pause. Probing every 15 min just re-arms the penalty, so each
            # consecutive limit doubles the wait: 15m, 30m, 60m, capped at 2h.
            self.rate_limited += 1
            self.consecutive_limits += 1
            wait = min(900 * (2 ** (self.consecutive_limits - 1)), 7200)
            self.cooldown_until = time.time() + wait
            return "ratelimit", {"wait": wait, "streak": self.consecutive_limits}
        if r.status_code != 200:
            return "error", {"err": f"HTTP {r.status_code}"}
        try:
            user = r.json().get("data", {}).get("user")
        except Exception:
            return "error", {"err": "bad json"}
        self.consecutive_limits = 0        # a clean response means we're out of the box
        if not user:
            return "hidden", None
        return "ok", self._shape(user, handle)

    def _shape(self, u, handle):
        links = [b.get("url") for b in (u.get("bio_links") or []) if b.get("url")]
        return {
            "instagram": f"https://www.instagram.com/{handle}/",
            "ig_handle": handle,
            "ig_followers": (u.get("edge_followed_by") or {}).get("count"),
            "ig_posts": (u.get("edge_owner_to_timeline_media") or {}).get("count"),
            "ig_bio": u.get("biography"),
            "ig_external_url": u.get("external_url"),
            "ig_bio_links": links,
            "ig_category": u.get("business_category_name") or u.get("category_name"),
            "ig_is_business": 1 if u.get("is_business_account") else 0,
            "ig_is_verified": 1 if u.get("is_verified") else 0,
            "ig_email": u.get("business_email"),
            "ig_phone": u.get("business_phone_number"),
            "ig_status": "ok",
        }

    def _html_fallback(self, handle):
        """400-bug profiles: scrape og:description for follower/post counts."""
        try:
            r = self._get(f"https://www.instagram.com/{handle}/")
            self.calls += 1
            if r.status_code != 200:
                return "schemabug", {"ig_status": f"htmlfallback_{r.status_code}"}
            m = re.search(r'<meta property="og:description" content="([^"]+)"', r.text)
            desc = m.group(1) if m else ""
            def num(pat):
                mm = re.search(pat, desc)
                if not mm:
                    return None
                s = mm.group(1).replace(",", "").upper()
                mul = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
                if s and s[-1] in mul:
                    try:
                        return int(float(s[:-1]) * mul[s[-1]])
                    except ValueError:
                        return None
                try:
                    return int(float(s))
                except ValueError:
                    return None
            t = re.search(r'<title>([^<]+)</title>', r.text)
            return "ok", {
                "instagram": f"https://www.instagram.com/{handle}/",
                "ig_handle": handle,
                "ig_followers": num(r'([\d.,]+[KMB]?)\s+Followers'),
                "ig_posts": num(r'([\d.,]+[KMB]?)\s+Posts'),
                "ig_bio": (t.group(1)[:300] if t else None),
                "ig_status": "html_fallback",
            }
        except Exception as e:
            return "schemabug", {"ig_status": f"htmlfallback_err:{type(e).__name__}"}


def polite_sleep():
    time.sleep(random.uniform(*IG_DELAY))
