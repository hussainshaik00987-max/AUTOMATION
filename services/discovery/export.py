"""Export scored leads into call-ready and DM-ready sheets."""
import json, sqlite3
import pandas as pd
import db
from config import EXPORT_DIR, VERTICALS

COLS = [
    "score", "segment", "vertical", "name", "phone", "whatsapp", "instagram",
    "ig_followers", "ig_posts", "facebook", "other_links",
    "email", "ig_email", "website", "site_status",
    "site_responsive", "site_platform", "price_hint", "wmg_membership", "wmg_bookings",
    "rating", "reviews", "primary_cat", "area", "search_city",
    "city_tier", "city_state", "address", "ig_bio", "ig_external_url", "ig_bio_links",
    "description", "pitch_angle", "automation_score", "automation_reason",
    "source", "wmg_profile_url", "place_id", "lat", "lng", "score_breakdown",
]

PRETTY = {
    "score": "Score", "segment": "Segment", "vertical": "Vertical", "name": "Business Name",
    "phone": "Phone", "whatsapp": "WhatsApp Link", "instagram": "Instagram",
    "ig_followers": "IG Followers", "ig_posts": "IG Posts",
    "facebook": "Facebook", "other_links": "Other Links", "email": "Email",
    "ig_email": "IG Email", "website": "Website", "site_status": "Site HTTP",
    "site_responsive": "Mobile Friendly", "rating": "Rating", "reviews": "Reviews",
    "primary_cat": "Category", "area": "Area", "search_city": "City",
    "city_tier": "City Tier", "city_state": "City/State", "address": "Address",
    "ig_bio": "IG Bio", "ig_external_url": "IG Link", "ig_bio_links": "IG Bio Links",
    "description": "Description", "pitch_angle": "Pitch Angle",
    "place_id": "Google Place ID", "lat": "Lat", "lng": "Lng",
    "score_breakdown": "Score Breakdown", "site_platform": "Site Platform",
    "price_hint": "Price", "wmg_membership": "WMG Listing", "wmg_bookings": "Recent Bookings",
    "source": "Source", "wmg_profile_url": "WedMeGood Profile",
    "automation_score": "Automation Score", "automation_reason": "Automation Angle",
}


def _frame(conn, where="", params=()):
    q = f"SELECT {','.join(COLS)} FROM leads {where} ORDER BY score DESC"
    df = pd.read_sql_query(q, conn, params=params)
    return df


def run(top_n=2000):
    conn = db.connect()
    # Unscored rows have segment NULL, and `NULL != 'excluded'` is NULL in SQL - they
    # would vanish from the export without a word. Score them first instead.
    unscored = conn.execute("SELECT COUNT(*) FROM leads WHERE score IS NULL").fetchone()[0]
    if unscored:
        print(f"WARNING: {unscored} leads were unscored - scoring them now "
              f"(run `python run.py sites` first for accurate website signals)")
        from score import score_all
        score_all(conn, only_unscored=True)

    all_df = _frame(conn, "WHERE segment IS NOT NULL AND segment != 'excluded'")
    if all_df.empty:
        print("no leads to export")
        return

    # The working list must be actionable: a lead with neither a phone nor an Instagram
    # handle cannot be called or DM'd, so it should not occupy a slot in the top N
    # however well it scores.
    reachable = all_df[all_df["phone"].notna() | all_df["instagram"].notna()]
    unreachable = len(all_df) - len(reachable)

    top = reachable.head(top_n)
    call_ready = all_df[all_df["phone"].notna()].head(top_n)
    dm_ready = all_df[all_df["instagram"].notna()].head(top_n)
    no_site = reachable[(reachable["website"].isna()) | (reachable["website"] == "")]

    xlsx = EXPORT_DIR / "leads.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        # Leads with a good site score badly for a rebuild but are prime automation
        # prospects. Without this sheet the website score silently buries them.
        automation = (reachable.sort_values("automation_score", ascending=False)
                      .head(top_n)) if "automation_score" in reachable else reachable.head(0)
        sheets = {
            f"TOP {top_n}": top,
            "Call Ready": call_ready,
            "Instagram DM Ready": dm_ready,
            "No Website": no_site,
            "WhatsApp Automation Fit": automation,
        }
        for vkey, (label, *_rest) in VERTICALS.items():
            sub = all_df[all_df["vertical"] == vkey].head(top_n)
            if not sub.empty:
                sheets[label[:28]] = sub
        for name, d in sheets.items():
            d.rename(columns=PRETTY).to_excel(xw, sheet_name=name[:31], index=False)

    top.rename(columns=PRETTY).to_csv(EXPORT_DIR / "top_leads.csv", index=False)
    all_df.rename(columns=PRETTY).to_csv(EXPORT_DIR / "all_leads.csv", index=False)

    print(f"exported {len(all_df)} leads -> {xlsx}")
    print(f"  TOP {top_n}: {len(top)}   call-ready: {len(call_ready)}   "
          f"DM-ready: {len(dm_ready)}   no-website: {len(no_site)}")
    if unreachable:
        print(f"  ({unreachable} leads held out of the top list: no phone and no Instagram)")
    if len(top):
        print(f"  score range in top list: {top['score'].max()} .. {top['score'].min()}")
    print("  segments:", all_df["segment"].value_counts().to_dict())
    print("  verticals:", all_df["vertical"].value_counts().to_dict())


if __name__ == "__main__":
    run()
