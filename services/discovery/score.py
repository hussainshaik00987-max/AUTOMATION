"""Lead scoring.

Thesis: score the GAP. A booked-out studio with 900 reviews whose only link is an
Instagram page is the perfect lead. The same studio with a good modern site is not a
lead at all. So we reward demand + capacity, and reward the ABSENCE of a real website.
"""
import json
from config import WEIGHTS, BLOCKLIST_NAME, VERTICALS, TRANSIENT_STATUS
from sources.website import classify_url
from db import is_tollfree

W = WEIGHTS


def _tier(value, table):
    if value is None:
        return 0
    best = 0
    for threshold in sorted(table):
        if value >= threshold:
            best = table[threshold]
    return best


def score_lead(r):
    """r: dict-like row. Returns (score, breakdown, segment, pitch_angle)."""
    b = {}
    name = (r.get("name") or "").lower()

    # ---------------------------------------------------------- disqualifiers
    tollfree = is_tollfree(r.get("phone") or r.get("phone_digits"))
    if any(x in name for x in BLOCKLIST_NAME) or tollfree:
        b["chain_or_franchise"] = W["chain_or_franchise"]

    # ---------------------------------------------------------- web presence (the pain)
    site = r.get("website")
    kind = classify_url(site)
    status = r.get("site_status")
    responsive = r.get("site_responsive")

    if kind == "none":
        # "No website" is only a real signal from a source that actually reports the
        # website field. Google Maps always does (null or not); WedMeGood listings never
        # do, so treating their blank as absence floods the top of the list.
        #
        # NB: site_checked is NOT evidence here. The site stage marks every lead checked,
        # including ones with no URL to fetch - checking a NULL URL learns nothing. Use
        # provenance instead: place_id is only ever set by Google Maps, so it survives a
        # merge even when `source` reads 'wedmegood'.
        if r.get("source") == "gmaps" or r.get("place_id"):
            b["no_website"] = W["no_website"]
        else:
            b["website_unknown"] = 0
    elif kind == "social":
        b["website_is_social"] = W["website_is_social"]
    elif kind == "linkhub":
        b["website_is_linkhub"] = W["website_is_linkhub"]
    elif kind == "directory":
        b["website_is_directory"] = W["website_is_directory"]
    else:
        # status 0 = DNS failure/timeout, 4xx = genuinely gone. But a throttled or
        # blocked fetch (429/403/503...) says nothing about the site - counting it as
        # dead invents a pain signal and promotes the lead for a reason that isn't true.
        if status == 0 or (status and status >= 400 and status not in TRANSIENT_STATUS):
            b["website_dead"] = W["website_dead"]
        elif status in TRANSIENT_STATUS:
            b["website_unverified"] = 0
        else:
            if r.get("site_https") == 0:
                b["website_insecure"] = W["website_insecure"]
            if responsive == 0:
                b["website_not_responsive"] = W["website_not_responsive"]
            if (r.get("site_platform") or "") in ("blogspot", "godaddy"):
                b["website_dated_platform"] = W["website_dated_platform"]
            # a live, https, responsive site on a modern platform means they are served
            if (status and status < 400 and responsive == 1 and r.get("site_https") == 1
                    and "website_dated_platform" not in b):
                b["has_good_website"] = W["has_good_website"]

    # bio links that reveal a manual funnel even when a site exists
    try:
        bl = r.get("ig_bio_links")
        bl = json.loads(bl) if isinstance(bl, str) else (bl or [])
    except Exception:
        bl = []
    ig_ext = r.get("ig_external_url")
    for u in list(bl) + ([ig_ext] if ig_ext else []):
        if classify_url(u) == "linkhub" and "website_is_linkhub" not in b and kind != "linkhub":
            b["website_is_linkhub"] = W["website_is_linkhub"] // 2
            break

    if not r.get("has_hours"):
        b["no_hours"] = W["no_hours"]

    # ---------------------------------------------------------- capacity to pay
    reviews = r.get("reviews") or 0
    b["reviews"] = _tier(reviews, W["reviews_tier"])
    rating = r.get("rating")
    if rating is not None:
        if rating >= 4.3:
            b["rating_good"] = W["rating_good"]
        elif rating >= 4.0:
            b["rating_ok"] = W["rating_ok"]
        elif rating < 3.5:
            b["rating_poor"] = W["rating_poor"]
    if reviews < 5:
        b["too_few_reviews"] = W["too_few_reviews"]
    if (r.get("photo_count") or 0) >= 2:
        b["photos_many"] = W["photos_many"]

    f = r.get("ig_followers")
    if f:
        b["ig_followers"] = _tier(f, W["ig_followers_tier"])
    if (r.get("ig_posts") or 0) >= 150:
        b["ig_active_posts"] = W["ig_active_posts"]

    # ---------------------------------------------------------- reachability
    if r.get("phone_digits") and not tollfree:
        b["has_phone"] = W["has_phone"]
    wa = r.get("whatsapp") or ""
    if wa.startswith("http"):          # a real wa.me/wa.link URL, not a availability flag
        b["has_whatsapp"] = W["has_whatsapp"]
    if r.get("instagram"):
        b["has_instagram"] = W["has_instagram"]
    if r.get("email") or r.get("ig_email"):
        b["has_email"] = W["has_email"]

    # ---------------------------------------------------------- proven budget
    mem = (r.get("wmg_membership") or "").lower()
    if mem and mem not in ("free", "basic", "none"):
        b["paid_listing"] = W["paid_listing"]
    bk = r.get("wmg_bookings")
    if bk and any(ch.isdigit() for ch in str(bk)):
        b["recent_bookings"] = W["recent_bookings"]
    if r.get("wmg_price_value"):
        b["price_tier"] = _tier(r["wmg_price_value"], W["price_tier"])

    total = sum(b.values())

    # ---------------------------------------------------------- segment + pitch
    if "chain_or_franchise" in b:
        seg = "excluded"
    elif total >= 85:
        seg = "A_hot"
    elif total >= 65:
        seg = "B_strong"
    elif total >= 45:
        seg = "C_worth_a_try"
    else:
        seg = "D_low"

    if "website_unknown" in b:
        pitch = ("Website status unknown (listing source doesn't report it) - check before "
                 "calling, then pitch on whichever gap you find.")
    elif "no_website" in b:
        pitch = "No website at all - they run entirely on DMs/calls. Lead with: clients Google you before booking."
    elif "website_is_social" in b:
        s = (site or "").lower()
        if "instagram" in s:
            net = "an Instagram page"
        elif "facebook" in s or "fb.me" in s or "fb.com" in s:
            net = "a Facebook page"
        elif "youtu" in s:
            net = "a YouTube channel"
        else:
            net = "a social profile"
        pitch = (f"Their 'website' is {net}. Lead with: you're renting your portfolio "
                 f"from a platform you don't control.")
    elif "website_is_directory" in b:
        pitch = ("Their 'website' is a directory profile (WedMeGood/JustDial). Lead with: "
                 "you're paying a marketplace for a page you don't own and can't rank.")
    elif "website_is_linkhub" in b:
        pitch = "Enquiries run through a link-hub/Google Form. Lead with: replace the form with a real booking page."
    elif "website_dead" in b:
        pitch = "Website is down/broken. Lead with: your site is dead - here's what clients see right now."
    elif "website_dated_platform" in b:
        pitch = ("Site is on an abandoned builder (Blogspot/GoDaddy Sites). Lead with: "
                 "your site looks a decade old next to your actual work.")
    elif "website_not_responsive" in b:
        pitch = "Site not mobile-friendly. Lead with: most of your enquiries are on mobile and your site breaks."
    elif "website_insecure" in b:
        pitch = "HTTP only - browsers flag it 'Not Secure'. Lead with: Chrome is warning your clients away."
    else:
        pitch = "Site is decent - pitch WhatsApp automation / booking flow instead of a rebuild."

    if r.get("whatsapp") and total >= 45:
        pitch += " | Already on WhatsApp -> strong fit for automated enquiry replies."

    return total, b, seg, pitch


def automation_score(r):
    """Second product line: WhatsApp automation / booking flow.

    The website score deliberately kills leads that already have a good site - they are
    not website prospects. But they are often the BEST automation prospects: digitally
    mature, high enquiry volume, already fielding DMs by hand. This surfaced as a real
    finding in the data - 81% of interior designers/architects already have a real
    website, so scoring them only on "you have no site" buried the user's #2 vertical.

    Returns (score, reason). High = worth pitching automation rather than a rebuild.
    """
    s, why = 0, []
    reviews = r.get("reviews") or 0
    s += _tier(reviews, W["reviews_tier"])
    if reviews >= 150:
        why.append(f"{reviews} reviews = real enquiry volume")

    rating = r.get("rating")
    if rating and rating >= 4.0:
        s += 6

    # digital maturity helps here rather than hurting
    if r.get("site_status") and r["site_status"] < 400:
        s += 12
        why.append("has a working site (so the gap is the booking flow, not the site)")
    if (r.get("site_platform") or "") in ("shopify", "wordpress", "wix", "webflow"):
        s += 5

    wa = r.get("whatsapp") or ""
    if wa.startswith("http"):
        s += 18
        why.append("already publishes a WhatsApp link - doing it manually today")
    if r.get("phone_digits") and not is_tollfree(r.get("phone")):
        s += 8
    f = r.get("ig_followers")
    if f and f >= 5000:
        s += 10
        why.append(f"{f:,} IG followers driving DM enquiries")
    if r.get("wmg_price_value"):
        s += _tier(r["wmg_price_value"], W["price_tier"])
    if r.get("wmg_bookings"):
        s += 8

    name = (r.get("name") or "").lower()
    if any(x in name for x in BLOCKLIST_NAME) or is_tollfree(r.get("phone")):
        s -= 40
    if reviews < 20:
        s -= 15                      # not enough enquiry volume to need automating

    return s, ("; ".join(why[:3]) or "Low enquiry volume - automation is a weak pitch here")


def score_all(conn, only_unscored=False):
    where = "WHERE score IS NULL" if only_unscored else ""
    rows = conn.execute(f"SELECT * FROM leads {where}").fetchall()
    n = 0
    for r in rows:
        d = dict(r)
        s, b, seg, pitch = score_lead(d)
        a_score, a_why = automation_score(d)
        conn.execute(
            "UPDATE leads SET score=?, score_breakdown=?, segment=?, pitch_angle=?, "
            "automation_score=?, automation_reason=? WHERE id=?",
            (s, json.dumps(b), seg, pitch, a_score, a_why, d["id"]))
        n += 1
    conn.commit()
    return n
