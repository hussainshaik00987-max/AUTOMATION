"""Website health check - turns a URL into pain signals.

We care about four questions:
  1. Does it resolve at all?          (dead site  -> strong lead)
  2. Is it HTTPS?                     (http-only  -> credibility gap)
  3. Is it mobile responsive?         (not        -> losing mobile bookings)
  4. Is it actually a site, or a social/link-hub redirect?
"""
import re, time, random
from curl_cffi import requests
from config import (UA, IMPERSONATE, SITE_DELAY, SOCIAL_HOSTS, LINKHUB_HOSTS,
                    DIRECTORY_HOSTS)

VIEWPORT_RE = re.compile(r'<meta[^>]+name=["\']viewport["\']', re.I)
TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)

# Harvest contact channels from the page we are already fetching - this is free
# coverage. Most businesses link Instagram in their header/footer, which is how we
# lift IG-handle coverage well past what Google Maps alone gives us.
IG_RE = re.compile(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})', re.I)
FB_RE = re.compile(r'https?://(?:www\.)?facebook\.com/([A-Za-z0-9_.\-]{2,50})', re.I)
WA_RE = re.compile(r'https?://(?:api\.whatsapp\.com/send\?phone=|wa\.me/|wa\.link/)([A-Za-z0-9]+)', re.I)
MAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')

IG_SKIP = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "share",
           "direct", "about", "legal", "developer", "web", "help", "privacy", "terms"}
MAIL_SKIP = ("sentry.io", "example.com", "wixpress", "godaddy", "@2x", ".png", ".jpg",
             ".gif", ".webp", "yourdomain", "domain.com", "email.com")

PLATFORM_HINTS = [
    ("wordpress", ("wp-content", "wp-includes", "wp-json")),
    ("wix", ("wix.com", "wixstatic", "_wixCssImports")),
    ("squarespace", ("squarespace", "sqsp.net")),
    ("shopify", ("cdn.shopify", "shopify.com")),
    ("webflow", ("webflow.io", "assets.website-files")),
    ("godaddy", ("godaddysites", "img1.wsimg.com")),
    ("blogspot", ("blogspot", "blogger.com")),
]


def extract_contacts(html, base_url=""):
    out = {}
    ig = None
    for m in IG_RE.finditer(html):
        h = m.group(1).strip(".").lower()
        if h in IG_SKIP or h.isdigit():
            continue
        ig = h
        break
    if ig:
        out["instagram"] = f"https://www.instagram.com/{ig}/"
    FB_SKIP = {"sharer", "share", "plugins", "tr", "dialog", "login", "profile.php"}
    for m in FB_RE.finditer(html):          # iterate: a share button often precedes the real page
        if m.group(1).lower() in FB_SKIP:
            continue
        out["facebook"] = m.group(0)
        break
    wa = WA_RE.search(html)
    if wa:
        out["whatsapp"] = wa.group(0)
    for e in MAIL_RE.findall(html):
        el = e.lower()
        if any(s in el for s in MAIL_SKIP):
            continue
        out["email"] = e
        break
    for name, hints in PLATFORM_HINTS:
        if any(h in html for h in hints):
            out["site_platform"] = name
            break
    return out


def classify_url(url):
    """social | linkhub | directory | real | none - decided before we even fetch."""
    if not url or not url.strip():
        return "none"
    u = url.lower()
    if any(h in u for h in SOCIAL_HOSTS):
        return "social"
    if any(h in u for h in LINKHUB_HOSTS):
        return "linkhub"
    if any(h in u for h in DIRECTORY_HOSTS):
        return "directory"
    return "real"


class SiteChecker:
    def __init__(self, proxy=None):
        self.s = requests.Session(impersonate=IMPERSONATE)
        self.proxy = proxy
        self.calls = 0

    def check(self, url):
        """Returns dict of site_* fields. Never raises."""
        out = {"site_checked": 1, "site_status": None, "site_https": None,
               "site_responsive": None, "site_title": None}
        kind = classify_url(url)
        if kind in ("none", "social", "linkhub", "directory"):
            return out                      # nothing to fetch; classify_url drives scoring
        out["site_https"] = 1 if url.lower().startswith("https://") else 0
        try:
            kw = {"timeout": 20, "headers": {"User-Agent": UA}, "allow_redirects": True}
            if self.proxy:
                kw["proxies"] = {"http": self.proxy, "https": self.proxy}
            r = self.s.get(url, **kw)
            self.calls += 1
            out["site_status"] = r.status_code
            if r.status_code < 400:
                html = r.text[:400_000]
                out["site_responsive"] = 1 if VIEWPORT_RE.search(html) else 0
                m = TITLE_RE.search(html)
                if m:
                    out["site_title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
                if r.url and r.url.lower().startswith("https://"):
                    out["site_https"] = 1
                out.update(extract_contacts(html, url))
        except Exception:
            out["site_status"] = 0          # dead / DNS fail / timeout
        return out


def polite_sleep():
    time.sleep(random.uniform(*SITE_DELAY))
