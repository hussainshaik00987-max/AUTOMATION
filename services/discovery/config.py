"""Central configuration: geography, verticals, search grid, scoring weights."""
import os
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# The SQLite file must NOT live on /mnt/* here. That is a 9p/v9fs mount (the WSL2 view of
# the Windows drive), where SQLite's WAL locking is unreliable - it uses shared-memory
# -shm files that 9p does not support properly. Symptom: intermittent "database is locked"
# crashes and collapsing throughput as soon as two stages write concurrently.
# Keep the DB on the Linux filesystem; exports still land in the project directory.
_default_db = Path.home() / ".local" / "share" / "coldcall" / "leads.db"
DB_PATH = Path(os.environ.get("COLDCALL_DB", _default_db))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
PB_TEMPLATE = ROOT / "pb_template.txt"
EXPORT_DIR = ROOT / "exports"
LOG_DIR = ROOT / "logs"
EXPORT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
IMPERSONATE = "chrome131"

# ---------------------------------------------------------------- geography
# (name, lat, lng, tier)  tier 1 = metro, 2 = tier-2
CITIES = [
    ("Mumbai",         19.0760, 72.8777, 1),
    ("Delhi",          28.6139, 77.2090, 1),
    ("Gurgaon",        28.4595, 77.0266, 1),
    ("Noida",          28.5355, 77.3910, 1),
    ("Bangalore",      12.9716, 77.5946, 1),
    ("Hyderabad",      17.3850, 78.4867, 1),
    ("Chennai",        13.0827, 80.2707, 1),
    ("Pune",           18.5204, 73.8567, 1),
    ("Kolkata",        22.5726, 88.3639, 1),
    ("Ahmedabad",      23.0225, 72.5714, 1),
    ("Jaipur",         26.9124, 75.7873, 2),
    ("Lucknow",        26.8467, 80.9462, 2),
    ("Indore",         22.7196, 75.8577, 2),
    ("Surat",          21.1702, 72.8311, 2),
    ("Coimbatore",     11.0168, 76.9558, 2),
    ("Kochi",           9.9312, 76.2673, 2),
    ("Chandigarh",     30.7333, 76.7794, 2),
    ("Bhopal",         23.2599, 77.4126, 2),
    ("Nagpur",         21.1458, 79.0882, 2),
    ("Vadodara",       22.3072, 73.1812, 2),
    ("Visakhapatnam",  17.6868, 83.2185, 2),
    ("Ludhiana",       30.9010, 75.8573, 2),
    ("Kanpur",         26.4499, 80.3319, 2),
    ("Nashik",         19.9975, 73.7898, 2),
    ("Rajkot",         22.3039, 70.8022, 2),
    ("Raipur",         21.2514, 81.6296, 2),
    ("Dehradun",       30.3165, 78.0322, 2),
    ("Guwahati",       26.1445, 91.7362, 2),
    ("Bhubaneswar",    20.2961, 85.8245, 2),
    ("Mysore",         12.2958, 76.6394, 2),
    ("Madurai",         9.9252, 78.1198, 2),
    ("Varanasi",       25.3176, 82.9739, 2),
    ("Amritsar",       31.6340, 74.8723, 2),
    ("Jodhpur",        26.2389, 73.0243, 2),
    ("Udaipur",        24.5854, 73.7125, 2),
]

# ---------------------------------------------------------------- verticals
# key: (label, target_share, [search queries], [category keywords that confirm fit])
VERTICALS = {
    "wedding_photo": (
        "Wedding Photographers & Videographers", 0.40,
        ["wedding photographer", "candid wedding photography", "wedding videographer",
         "pre wedding shoot photographer", "wedding photography studio"],
        ["photograph", "videograph", "wedding", "cinemat", "film"],
    ),
    "interior_arch": (
        "Interior Designers & Architects", 0.20,
        ["interior designer", "architect", "interior design studio",
         "turnkey interior contractor", "home interior designer"],
        ["interior", "architect", "design"],
    ),
    "wedding_planner": (
        "Wedding Planners & Event Decorators", 0.15,
        ["wedding planner", "wedding decorator", "event decorator",
         "event management company", "wedding event planner"],
        ["wedding", "event", "planner", "decorat", "caterer"],
    ),
    "bakery": (
        "Cake Studios, Bakeries & Home Food Brands", 0.15,
        ["cake shop", "custom cake bakery", "home baker",
         "dessert shop", "bakery"],
        ["bakery", "cake", "dessert", "patisserie", "confection", "chocolat", "food"],
    ),
    "fashion": (
        "Fashion Boutiques & Clothing Labels", 0.10,
        ["designer boutique", "womens clothing boutique", "ethnic wear store",
         "bridal lehenga store", "fashion designer"],
        ["boutique", "cloth", "fashion", "wear", "apparel", "tailor", "dress", "saree"],
    ),
}

TOTAL_TARGET = 10_000
MAX_PAGES_PER_QUERY = 6          # Google caps around 120 results per query+geo
PAGE_SIZE = 20

# ---------------------------------------------------------------- politeness
GMAPS_DELAY = (2.0, 4.5)         # seconds between Maps requests (min, max)
# Rate history, because the obvious number is wrong twice over:
#   - Published guidance says ~200/hr per IP.
#   - A 22-request burst at 6s intervals ran clean, implying ~477/hr. That sample was far
#     too short to reveal a rolling-window limit, so it over-promised.
#   - In the real run at ~8s avg we were limited after 228 calls (~500 req/hr).
# The trap: ~38% of profiles hit Instagram's 400 schema bug and fall back to a second,
# HTML request. So each *lead* costs ~1.4 *requests* - pace on requests, not leads.
# 13.5s avg => ~265 leads/hr => ~370 req/hr, which holds.
IG_DELAY = (11.0, 16.0)
SITE_DELAY = (0.3, 1.0)
MAX_RETRIES = 3

# ---------------------------------------------------------------- scoring
# "Score the gap": strong demand signals + weak web presence = best lead.
WEIGHTS = {
    # --- pain: they need what we sell (max 50)
    "no_website": 30,
    "website_is_social": 28,       # site field points at instagram/facebook
    "website_is_linkhub": 26,      # linktr.ee, google form, wa.me as the "website"
    "website_is_directory": 27,    # a JustDial/WedMeGood profile used as the website
    "website_dead": 24,            # DNS fail / 5xx / 404
    "website_insecure": 10,        # http:// only
    "website_not_responsive": 8,
    "website_dated_platform": 12,   # blogspot/godaddy-sites = effectively abandoned
    "no_hours": 3,

    # --- capacity: they can pay (max ~35)
    "reviews_tier": {0: 0, 5: 4, 20: 9, 50: 14, 150: 19, 400: 23, 1000: 26},
    "rating_good": 6,              # >= 4.3
    "rating_ok": 3,                # >= 4.0
    "photos_many": 4,
    "ig_followers_tier": {0: 0, 1000: 3, 5000: 8, 20000: 12, 60000: 14, 150000: 8},
    "ig_active_posts": 4,          # >= 150 posts

    # --- reachability (max ~20)
    "has_phone": 8,
    "has_whatsapp": 6,
    "has_instagram": 6,
    "has_email": 4,

    # --- proven budget (WedMeGood only). A vendor already paying for listings and
    #     charging real money is the one who actually signs.
    "paid_listing": 12,            # membership = Handpicked/paid placement
    "recent_bookings": 8,          # "N bookings recently" -> live demand
    "price_tier": {0: 0, 15000: 4, 30000: 8, 60000: 12, 120000: 15},

    # --- negatives
    "rating_poor": -25,            # < 3.5 -> failing business
    "too_few_reviews": -18,        # < 5 -> not a real business yet
    "has_good_website": -30,       # modern responsive site = not our lead
    "chain_or_franchise": -20,
}

# Businesses we never want to pitch
BLOCKLIST_NAME = [
    "taj ", "marriott", "hyatt", "radisson", "oberoi", "itc ", "lemon tree",
    "starbucks", "dominos", "domino's", "pizza hut", "mcdonald", "kfc", "subway",
    "monginis", "theobroma", "cafe coffee day", "ccd ", "haldiram", "bikanervala",
    "fabindia", "westside", "pantaloons", "reliance ", "shoppers stop", "lifestyle store",
    "decathlon", "ikea", "urban company", "asian paints", "berger paints",
    "livspace", "homelane", "pepperfry", "godrej interio", "sleepwell",
]

SOCIAL_HOSTS = ("instagram.com", "facebook.com", "fb.com", "fb.me", "m.facebook.com",
                "youtube.com", "youtu.be", "twitter.com", "x.com", "pinterest.")
LINKHUB_HOSTS = ("linktr.ee", "linktree", "wa.me", "wa.link", "api.whatsapp.com",
                 "docs.google.com", "forms.gle", "bit.ly", "beacons.ai", "taplink",
                 "linkin.bio", "carrd.co", "znap.link", "mssg.me")

# A directory profile page is not a website - the business rents a page on someone else's
# marketplace. Same pitch as "your website is your Instagram", and a real pattern: some
# vendors list their WedMeGood/JustDial profile as their website on Google Maps.
DIRECTORY_HOSTS = ("wedmegood.com", "weddingwire.in", "shaadisaga.com", "justdial.com",
                   "sulekha.com", "indiamart.com", "urbancompany.com", "weddingz.in",
                   "zomato.com", "swiggy.com", "practo.com", "tradeindia.com",
                   "weddingbazaar.com", "99acres.com", "magicbricks.com")

# HTTP statuses that mean "we were blocked or throttled", not "this site is broken".
# Scoring a rate-limited fetch as a dead website is a false positive that promotes the
# lead for the wrong reason.
TRANSIENT_STATUS = (401, 403, 405, 406, 408, 429, 500, 502, 503, 504)
