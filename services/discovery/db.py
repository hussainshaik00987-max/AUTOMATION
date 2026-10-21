"""SQLite store: resumable task queue + deduped lead table."""
import sqlite3, json, time, re, random
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id          TEXT UNIQUE,
    feature_id        TEXT,
    name              TEXT NOT NULL,
    vertical          TEXT,
    source            TEXT,
    search_query      TEXT,
    search_city       TEXT,
    city_tier         INTEGER,
    -- contact
    phone             TEXT,
    phone_digits      TEXT,
    email             TEXT,
    whatsapp          TEXT,
    website           TEXT,
    website_domain    TEXT,
    instagram         TEXT,
    facebook          TEXT,
    other_links       TEXT,
    -- location
    address           TEXT,
    area              TEXT,
    city_state        TEXT,
    lat               REAL,
    lng               REAL,
    -- business signals
    rating            REAL,
    reviews           INTEGER,
    categories        TEXT,
    primary_cat       TEXT,
    description       TEXT,
    photo_count       INTEGER,
    has_hours         INTEGER,
    owner             TEXT,
    price_hint        TEXT,
    -- wedmegood signals
    wmg_profile_url   TEXT,
    wmg_membership    TEXT,
    wmg_bookings      TEXT,
    wmg_price_value   INTEGER,
    -- instagram enrichment
    ig_followers      INTEGER,
    ig_posts          INTEGER,
    ig_bio            TEXT,
    ig_external_url   TEXT,
    ig_bio_links      TEXT,
    ig_category       TEXT,
    ig_is_business    INTEGER,
    ig_is_verified    INTEGER,
    ig_email          TEXT,
    ig_phone          TEXT,
    ig_status         TEXT,
    -- website health
    site_status       INTEGER,
    site_https        INTEGER,
    site_responsive   INTEGER,
    site_title        TEXT,
    site_checked      INTEGER DEFAULT 0,
    -- scoring
    score             INTEGER,
    score_breakdown   TEXT,
    segment           TEXT,
    pitch_angle       TEXT,
    -- meta
    enriched_ig       INTEGER DEFAULT 0,
    created_at        INTEGER,
    updated_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_leads_phone   ON leads(phone_digits);
CREATE INDEX IF NOT EXISTS idx_leads_score   ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_vert    ON leads(vertical);
CREATE INDEX IF NOT EXISTS idx_leads_ig      ON leads(instagram);
CREATE INDEX IF NOT EXISTS idx_leads_enrich  ON leads(enriched_ig, score DESC);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vertical    TEXT,
    query       TEXT,
    city        TEXT,
    lat         REAL,
    lng         REAL,
    city_tier   INTEGER,
    offset      INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'pending',   -- pending|running|done|exhausted|error
    found       INTEGER DEFAULT 0,
    attempts    INTEGER DEFAULT 0,
    last_error  TEXT,
    updated_at  INTEGER,
    UNIQUE(vertical, query, city)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS runlog (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER,
    kind    TEXT,
    msg     TEXT,
    detail  TEXT
);

CREATE TABLE IF NOT EXISTS lead_reviews (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id            INTEGER NOT NULL UNIQUE,
    status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'review_later')),
    notification_state TEXT NOT NULL DEFAULT 'unnotified' CHECK (notification_state IN ('unnotified', 'sent', 'failed')),
    notified_at        INTEGER,
    decision_ts        INTEGER,
    decision_by        TEXT,
    history            TEXT DEFAULT '[]',
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL,
    FOREIGN KEY(lead_id) REFERENCES leads(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reviews_lead_id ON lead_reviews(lead_id);
CREATE INDEX IF NOT EXISTS idx_reviews_status  ON lead_reviews(status);
CREATE INDEX IF NOT EXISTS idx_reviews_notif   ON lead_reviews(notification_state);
"""


def connect():
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


MIGRATIONS = [
    ("wmg_profile_url", "TEXT"), ("wmg_membership", "TEXT"),
    ("wmg_bookings", "TEXT"), ("wmg_price_value", "INTEGER"),
    ("site_platform", "TEXT"),
    ("automation_score", "INTEGER"), ("automation_reason", "TEXT"),
]


def init():
    c = connect()
    c.executescript(SCHEMA)
    # additive migrations for DBs created before a column existed
    have = {r[1] for r in c.execute("PRAGMA table_info(leads)")}
    for col, typ in MIGRATIONS:
        if col not in have:
            c.execute(f"ALTER TABLE leads ADD COLUMN {col} {typ}")
    c.commit()
    global COLS
    COLS = None                      # force re-read after migration
    return c


def write_retry(fn, attempts=6, base=0.4):
    """Run a write closure, retrying on transient SQLite lock errors.

    busy_timeout alone is not enough: in WAL mode a connection that reads and then tries
    to write after another connection has committed gets an immediate "database is
    locked" (a snapshot conflict) which busy_timeout does NOT wait out. Retrying with
    backoff is the documented remedy, and it lets concurrent stages coexist instead of
    losing a whole unit of work.
    """
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            if i == attempts - 1:
                raise
            time.sleep(base * (2 ** i) + random.uniform(0, 0.3))


def log(conn, kind, msg, detail=""):
    conn.execute("INSERT INTO runlog(ts,kind,msg,detail) VALUES(?,?,?,?)",
                 (int(time.time()), kind, msg, str(detail)[:2000]))
    conn.commit()


def norm_phone(p):
    if not p:
        return None
    d = re.sub(r"\D", "", str(p))
    if d.startswith("91") and len(d) == 12:
        d = d[2:]
    if d.startswith("0") and len(d) == 11:
        d = d[1:]
    return d if len(d) == 10 else (d or None)


# 1800/1860/1861 are Indian toll-free / corporate lines. A small studio does not have
# one - it means we've picked up a chain, a franchise, or a lead-gen aggregator.
TOLLFREE_RE = re.compile(r"^(?:91)?(?:1800|1860|1861)")


def is_tollfree(p):
    if not p:
        return False
    return bool(TOLLFREE_RE.match(re.sub(r"\D", "", str(p))))


COLS = None


def upsert_lead(conn, row):
    """Insert or merge a lead. Dedupes on place_id, then on (phone, name-prefix)."""
    global COLS
    if COLS is None:
        COLS = {r[1] for r in conn.execute("PRAGMA table_info(leads)")}
    row = {k: v for k, v in row.items() if k in COLS}
    for k in ("categories", "other_links", "ig_bio_links", "score_breakdown"):
        if isinstance(row.get(k), (list, dict)):
            row[k] = json.dumps(row[k], ensure_ascii=False)
    row["phone_digits"] = norm_phone(row.get("phone"))
    now = int(time.time())
    row.setdefault("created_at", now)
    row["updated_at"] = now

    pid = row.get("place_id")
    existing = None
    if pid:
        existing = conn.execute("SELECT id FROM leads WHERE place_id=?", (pid,)).fetchone()
    if existing is None and row.get("phone_digits"):
        existing = conn.execute(
            "SELECT id FROM leads WHERE phone_digits=? AND substr(lower(name),1,12)=substr(lower(?),1,12)",
            (row["phone_digits"], row.get("name", ""))).fetchone()
    if existing:
        # merge: only fill NULLs, never clobber good data
        sets, vals = [], []
        for k, v in row.items():
            if k in ("id", "created_at") or v is None or v == "":
                continue
            sets.append(f"{k}=COALESCE({k},?)")
            vals.append(v)
        if sets:
            vals.append(existing["id"])
            conn.execute(f"UPDATE leads SET {','.join(sets)} WHERE id=?", vals)
        return "merged"
    cols = ",".join(row)
    ph = ",".join("?" * len(row))
    try:
        conn.execute(f"INSERT INTO leads({cols}) VALUES({ph})", list(row.values()))
        return "new"
    except sqlite3.IntegrityError:
        return "dupe"


def seed_tasks(conn, verticals, cities):
    n = 0
    for vkey, (_label, _share, queries, _kw) in verticals.items():
        for q in queries:
            for (city, lat, lng, tier) in cities:
                try:
                    conn.execute(
                        "INSERT INTO tasks(vertical,query,city,lat,lng,city_tier,updated_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (vkey, q, city, lat, lng, tier, int(time.time())))
                    n += 1
                except sqlite3.IntegrityError:
                    pass
    conn.commit()
    return n


def stats(conn):
    q = lambda s, *a: conn.execute(s, a).fetchone()[0]
    return {
        "leads":        q("SELECT COUNT(*) FROM leads"),
        "with_phone":   q("SELECT COUNT(*) FROM leads WHERE phone_digits IS NOT NULL"),
        "with_ig":      q("SELECT COUNT(*) FROM leads WHERE instagram IS NOT NULL"),
        "no_website":   q("SELECT COUNT(*) FROM leads WHERE website IS NULL OR website=''"),
        "ig_enriched":  q("SELECT COUNT(*) FROM leads WHERE enriched_ig=1"),
        "site_checked": q("SELECT COUNT(*) FROM leads WHERE site_checked=1"),
        "scored":       q("SELECT COUNT(*) FROM leads WHERE score IS NOT NULL"),
        "tasks_done":   q("SELECT COUNT(*) FROM tasks WHERE status IN ('done','exhausted')"),
        "tasks_total":  q("SELECT COUNT(*) FROM tasks"),
        "tasks_error":  q("SELECT COUNT(*) FROM tasks WHERE status='error'"),
    }


REVIEW_STATUSES = ("pending", "approved", "rejected", "review_later")
NOTIFICATION_STATES = ("unnotified", "sent", "failed")


def init_review(conn, lead_id):
    """Ensure a review record exists for the given lead_id.

    Returns the review Row/dict, or None if the lead does not exist.
    Safe against duplicates: if a review record exists, returns it unmodified.
    """
    lead = conn.execute("SELECT id FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        return None

    existing = conn.execute("SELECT * FROM lead_reviews WHERE lead_id=?", (lead_id,)).fetchone()
    if existing:
        return existing

    now = int(time.time())
    history = json.dumps([{"status": "pending", "ts": now, "actor": "system"}])
    try:
        conn.execute(
            "INSERT INTO lead_reviews(lead_id, status, notification_state, history, created_at, updated_at) "
            "VALUES(?, 'pending', 'unnotified', ?, ?, ?)",
            (lead_id, history, now, now)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    return conn.execute("SELECT * FROM lead_reviews WHERE lead_id=?", (lead_id,)).fetchone()


def get_review(conn, lead_id):
    """Get the review record for a lead_id, or None if not found."""
    return conn.execute("SELECT * FROM lead_reviews WHERE lead_id=?", (lead_id,)).fetchone()


def set_review_status(conn, lead_id, status, actor="owner", note=None):
    """Update review status for a lead.

    Validates status against REVIEW_STATUSES.
    Appends the transition to the history log with timestamp and actor.
    Returns the updated review Row/dict, or None if the lead does not exist.
    Raises ValueError if status is invalid.
    """
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Invalid review status: '{status}'. Allowed: {REVIEW_STATUSES}")

    lead = conn.execute("SELECT id FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        return None

    review = get_review(conn, lead_id)
    if not review:
        init_review(conn, lead_id)
        review = get_review(conn, lead_id)

    now = int(time.time())
    try:
        hist = json.loads(review["history"]) if review["history"] else []
    except Exception:
        hist = []

    entry = {"status": status, "ts": now, "actor": actor}
    if note:
        entry["note"] = note
    hist.append(entry)

    conn.execute(
        "UPDATE lead_reviews SET status=?, decision_ts=?, updated_at=?, history=? WHERE lead_id=?",
        (status, now, now, json.dumps(hist), lead_id)
    )
    conn.commit()
    return get_review(conn, lead_id)


def set_notification_state(conn, lead_id, state):
    """Update notification state for a lead review.

    Validates state against NOTIFICATION_STATES ('unnotified', 'sent', 'failed').
    Sets notified_at timestamp if state is 'sent'.
    Returns updated review Row/dict, or None if lead does not exist.
    Raises ValueError if state is invalid.
    """
    if state not in NOTIFICATION_STATES:
        raise ValueError(f"Invalid notification state: '{state}'. Allowed: {NOTIFICATION_STATES}")

    lead = conn.execute("SELECT id FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        return None

    review = get_review(conn, lead_id)
    if not review:
        init_review(conn, lead_id)

    now = int(time.time())
    notified_at = now if state == "sent" else None
    conn.execute(
        "UPDATE lead_reviews SET notification_state=?, notified_at=COALESCE(?, notified_at), updated_at=? WHERE lead_id=?",
        (state, notified_at, now, lead_id)
    )
    conn.commit()
    return get_review(conn, lead_id)


def get_pending_reviews(conn, unnotified_only=False):
    """Retrieve pending lead reviews with associated lead details."""
    sql = """
        SELECT r.*,
               l.name, l.search_city, l.area, l.address, l.phone, l.whatsapp, l.email,
               l.website, l.instagram, l.facebook, l.score, l.score_breakdown,
               l.pitch_angle, l.rating, l.reviews, l.primary_cat, l.vertical,
               l.site_status, l.site_responsive, l.automation_score, l.automation_reason
        FROM lead_reviews r
        JOIN leads l ON r.lead_id = l.id
        WHERE r.status = 'pending'
    """
    if unnotified_only:
        sql += " AND r.notification_state = 'unnotified'"
    sql += " ORDER BY r.created_at ASC"
    return conn.execute(sql).fetchall()


def get_failed_reviews(conn):
    """Retrieve pending reviews whose notification send failed."""
    sql = """
        SELECT r.*,
               l.name, l.search_city, l.area, l.address, l.phone, l.whatsapp, l.email,
               l.website, l.instagram, l.facebook, l.score, l.score_breakdown,
               l.pitch_angle, l.rating, l.reviews, l.primary_cat, l.vertical,
               l.site_status, l.site_responsive, l.automation_score, l.automation_reason
        FROM lead_reviews r
        JOIN leads l ON r.lead_id = l.id
        WHERE r.status = 'pending' AND r.notification_state = 'failed'
        ORDER BY r.created_at ASC
    """
    return conn.execute(sql).fetchall()


def reset_failed_notifications(conn):
    """Reset failed notifications back to 'unnotified' so they can be retried.

    Returns the number of rows updated.
    """
    cur = conn.execute(
        "UPDATE lead_reviews SET notification_state='unnotified', updated_at=? "
        "WHERE status='pending' AND notification_state='failed'",
        (int(time.time()),)
    )
    conn.commit()
    return cur.rowcount


def init_unreviewed_leads(conn):
    """Initialize review records for any existing leads in leads table that lack one.

    Returns the count of newly initialized review records.
    """
    rows = conn.execute("SELECT id FROM leads WHERE id NOT IN (SELECT lead_id FROM lead_reviews)").fetchall()
    count = 0
    for r in rows:
        init_review(conn, r["id"])
        count += 1
    return count


CSV_HEADER_MAP = {
    "Business Name": "name",
    "Score": "score",
    "Segment": "segment",
    "Vertical": "vertical",
    "Phone": "phone",
    "WhatsApp Link": "whatsapp",
    "Instagram": "instagram",
    "IG Followers": "ig_followers",
    "IG Posts": "ig_posts",
    "Facebook": "facebook",
    "Other Links": "other_links",
    "Email": "email",
    "IG Email": "ig_email",
    "Website": "website",
    "Site HTTP": "site_status",
    "Mobile Friendly": "site_responsive",
    "Rating": "rating",
    "Reviews": "reviews",
    "Category": "primary_cat",
    "Area": "area",
    "City": "search_city",
    "City Tier": "city_tier",
    "City/State": "city_state",
    "Address": "address",
    "IG Bio": "ig_bio",
    "IG Link": "ig_external_url",
    "IG Bio Links": "ig_bio_links",
    "Description": "description",
    "Pitch Angle": "pitch_angle",
    "Google Place ID": "place_id",
    "Lat": "lat",
    "Lng": "lng",
    "Score Breakdown": "score_breakdown",
    "Site Platform": "site_platform",
    "Price": "price_hint",
    "WMG Listing": "wmg_membership",
    "Recent Bookings": "wmg_bookings",
    "Source": "source",
    "WedMeGood Profile": "wmg_profile_url",
    "Automation Score": "automation_score",
    "Automation Angle": "automation_reason",
}


def import_leads_from_csv(conn, csv_path):
    """Import leads from a CSV file into the database and initialize review records.

    Handles both exported CSVs (with pretty column headers) and internal-keyed CSVs.
    Returns (imported_count, new_reviews_count).
    """
    import csv
    from pathlib import Path

    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    imported = 0
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            row = {}
            for k, v in raw_row.items():
                if k is None:
                    continue
                db_key = CSV_HEADER_MAP.get(k.strip(), k.strip())
                row[db_key] = v.strip() if isinstance(v, str) else v

            # Clean empty strings
            for col in list(row.keys()):
                if row[col] == "":
                    row[col] = None

            # Convert numeric fields if possible
            for num_col in ("score", "reviews", "site_status", "automation_score", "city_tier"):
                if row.get(num_col) is not None:
                    try:
                        row[num_col] = int(row[num_col])
                    except (ValueError, TypeError):
                        pass

            for float_col in ("rating", "lat", "lng"):
                if row.get(float_col) is not None:
                    try:
                        row[float_col] = float(row[float_col])
                    except (ValueError, TypeError):
                        pass

            if not row.get("name"):
                continue

            upsert_lead(conn, row)
            imported += 1

    conn.commit()
    new_reviews = init_unreviewed_leads(conn)
    return imported, new_reviews
