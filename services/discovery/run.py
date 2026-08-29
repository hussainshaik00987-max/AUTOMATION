"""Orchestrator.

  python run.py collect    [--limit N] [--target N]  # Google Maps -> leads
  python run.py wmg        [--limit PAGES]           # WedMeGood listings (price/budget)
  python run.py wmgprofile [--workers N]             # WedMeGood profiles (phone/email)
  python run.py dedupe                               # merge cross-source duplicates
  python run.py sites      [--workers N]             # website health check
  python run.py score                                # (re)score everything
  python run.py enrich     [--limit N]               # Instagram on top-scored leads
  python run.py stats                                # health snapshot (used by monitor)
  python run.py export                               # Excel + CSV segments

Recommended order: collect -> wmg -> wmgprofile -> dedupe -> sites -> score
                   -> enrich -> score -> export
"""
import sys, time, json, random, argparse, traceback
from datetime import datetime

import db
from config import (VERTICALS, CITIES, TOTAL_TARGET, MAX_PAGES_PER_QUERY, PAGE_SIZE,
                    LOG_DIR, TRANSIENT_STATUS)
from sources import gmaps, instagram, website
from score import score_all, score_lead

LOGF = LOG_DIR / "run.log"


def emit(kind, msg):
    """One line per event -> stdout for the monitor, and appended to the log file."""
    line = f"{datetime.now():%H:%M:%S} [{kind}] {msg}"
    print(line, flush=True)
    with open(LOGF, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# --------------------------------------------------------------------- collect
def cmd_collect(args):
    conn = db.init()
    seeded = db.seed_tasks(conn, VERTICALS, CITIES)
    if seeded:
        emit("SEED", f"seeded {seeded} new search tasks")

    client = gmaps.MapsClient()
    target = args.target or TOTAL_TARGET
    processed = 0
    t0 = time.time()

    while True:
        # Count only Maps-sourced leads toward the target. WedMeGood may be running
        # concurrently, and letting its rows satisfy this target would cut the Maps run
        # short - losing exactly the leads that carry phone + website data.
        own = conn.execute("SELECT COUNT(*) FROM leads WHERE source='gmaps'").fetchone()[0]
        if own >= target:
            emit("DONE", f"target reached: {own} google-maps leads")
            break
        if args.limit and processed >= args.limit:
            emit("STOP", f"task limit {args.limit} reached")
            break

        # Pull next task, preferring verticals that are under their target share
        task = _next_task(conn)
        if task is None:
            emit("DONE", "no pending tasks left")
            break

        def _mark_running():
            conn.execute("UPDATE tasks SET status='running', updated_at=? WHERE id=?",
                         (int(time.time()), task["id"]))
            conn.commit()
        db.write_retry(_mark_running)

        found_new = found_total = 0
        offset = task["offset"]
        err = None
        try:
            for page in range(MAX_PAGES_PER_QUERY):
                places = client.fetch_page(task["query"], task["lat"], task["lng"], offset)
                if not places:
                    break
                def _store(places=places):
                    n_new = 0
                    for p in places:
                        p.update(vertical=task["vertical"], source="gmaps",
                                 search_query=task["query"], search_city=task["city"],
                                 city_tier=task["city_tier"])
                        if db.upsert_lead(conn, p) == "new":
                            n_new += 1
                    conn.commit()
                    return n_new
                found_new += db.write_retry(_store)
                found_total += len(places)
                offset += PAGE_SIZE
                if len(places) < PAGE_SIZE:
                    break
                gmaps.polite_sleep()
            status = "done"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            status = "error"

        def _finish():
            conn.execute(
                "UPDATE tasks SET status=?, offset=?, found=found+?, attempts=attempts+1, "
                "last_error=?, updated_at=? WHERE id=?",
                (status, offset, found_total, err, int(time.time()), task["id"]))
            conn.commit()
        db.write_retry(_finish)
        processed += 1

        tot = db.stats(conn)["leads"]
        rate = tot / max((time.time() - t0) / 60, 0.01)
        if err:
            emit("ERROR", f"{task['vertical']}/{task['city']} '{task['query']}' -> {err}")
        else:
            emit("OK", f"{task['city']:<14} {task['query'][:32]:<34} "
                       f"+{found_new:>3} new ({found_total} seen) | total={tot} "
                       f"| {rate:.0f}/min | blocks={client.blocks}")
        gmaps.polite_sleep()

    _summary(conn, "collect")


def _next_task(conn):
    """Prefer the vertical furthest below its target share, to keep the mix balanced.

    Counts only Maps-sourced leads: this balancer steers Maps queries, and WedMeGood
    covers just two verticals. Counting its rows here made wedding_photo look already
    satisfied and starved the top-priority vertical of the richer Maps leads.
    """
    counts = {v: 0 for v in VERTICALS}
    for r in conn.execute("SELECT vertical, COUNT(*) c FROM leads "
                          "WHERE source='gmaps' GROUP BY vertical"):
        if r["vertical"] in counts:
            counts[r["vertical"]] = r["c"]
    total = max(sum(counts.values()), 1)
    deficits = []
    for v, (_l, share, _q, _k) in VERTICALS.items():
        want = share * max(total, TOTAL_TARGET * 0.05)
        deficits.append((counts[v] - want, v))
    deficits.sort()
    for _d, v in deficits:
        t = conn.execute(
            "SELECT * FROM tasks WHERE vertical=? AND status IN ('pending','error') "
            "AND attempts < 3 ORDER BY city_tier, RANDOM() LIMIT 1", (v,)).fetchone()
        if t:
            return t
    return conn.execute(
        "SELECT * FROM tasks WHERE status IN ('pending','error') AND attempts < 3 "
        "ORDER BY RANDOM() LIMIT 1").fetchone()


# ----------------------------------------------------------------- site check
def cmd_sites(args):
    """Threaded: network fetches fan out, but only this thread touches SQLite."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    conn = db.init()
    if args.recheck:
        # Re-fetch leads whose first check was throttled/blocked rather than answered.
        # Those statuses carry no information about the site, so leaving them is a
        # permanent blind spot in the strongest scoring group.
        ph = ",".join("?" * len(TRANSIENT_STATUS))
        rows = conn.execute(
            f"SELECT id, website FROM leads WHERE site_status IN ({ph}) "
            f"ORDER BY reviews DESC LIMIT ?",
            (*TRANSIENT_STATUS, args.limit or 20000)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, website FROM leads WHERE site_checked=0 ORDER BY reviews DESC "
            "LIMIT ?", (args.limit or 20000,)).fetchall()
    workers = args.workers or 12
    emit("SITES", f"checking {len(rows)} websites with {workers} workers"
                  f"{' (recheck of throttled fetches)' if args.recheck else ''}")

    # curl_cffi sessions are not thread-safe -> one checker per worker thread
    local = threading.local()

    def check(row):
        if not hasattr(local, "c"):
            local.c = website.SiteChecker()
        try:
            return row["id"], local.c.check(row["website"])
        except Exception:
            return row["id"], {"site_checked": 1, "site_status": 0}

    # site_* fields are authoritative and overwrite; harvested contacts only fill blanks
    OVERWRITE = {"site_checked", "site_status", "site_https", "site_responsive",
                 "site_title", "site_platform"}
    done = dead = found_ig = found_mail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for lead_id, res in ex.map(check, rows):
            sets = ",".join((f"{k}=?" if k in OVERWRITE else f"{k}=COALESCE({k},?)")
                            for k in res)
            # commit per row - see the note in cmd_wmgprofile on holding write locks
            def _w(res=res, lead_id=lead_id, sets=sets):
                conn.execute(f"UPDATE leads SET {sets} WHERE id=?",
                             list(res.values()) + [lead_id])
                conn.commit()
            db.write_retry(_w)
            if res.get("site_status") == 0:
                dead += 1
            if res.get("instagram"):
                found_ig += 1
            if res.get("email"):
                found_mail += 1
            done += 1
            if done % 200 == 0:
                rate = done / max(time.time() - t0, 0.01)
                emit("SITES", f"{done}/{len(rows)} | dead={dead} +ig={found_ig} "
                              f"+email={found_mail} | {rate:.1f}/s")
    conn.commit()
    emit("SITES", f"done: {done} checked, {dead} dead, "
                  f"{found_ig} instagram handles, {found_mail} emails harvested")
    _summary(conn, "sites")


# -------------------------------------------------------------------- enrich
def cmd_enrich(args):
    """Spend scarce Instagram calls only on already-promising leads."""
    conn = db.init()
    score_all(conn, only_unscored=True)
    ig = instagram.IGClient()
    # Only handles discovered from a real URL. Scanning names/descriptions for "@foo" was
    # tried and dropped: across 8k leads it yielded 6 candidates, half of them locations
    # ("Cheesecakes @ Indiranagar"), and every false handle burns a rate-limited call.
    rows = conn.execute(
        "SELECT id, name, instagram, website, score FROM leads "
        "WHERE enriched_ig=0 AND segment IS NOT NULL AND segment != 'excluded' "
        "AND (instagram IS NOT NULL OR website LIKE '%instagram%') "
        "ORDER BY score DESC LIMIT ?", (args.limit or 500,)).fetchall()
    emit("ENRICH", f"{len(rows)} leads queued for Instagram enrichment")

    ok = miss = rl = 0
    for i, r in enumerate(rows, 1):
        h = instagram.handle_from_url(r["instagram"] or r["website"])
        if not h:
            conn.execute("UPDATE leads SET enriched_ig=1, ig_status='no_handle' WHERE id=?", (r["id"],))
            conn.commit()
            miss += 1
            continue
        status, data = ig.fetch(h)
        if status == "ratelimit":
            rl += 1
            wait = (data or {}).get("wait", 900)
            streak = (data or {}).get("streak", 1)
            emit("RATELIMIT", f"rate limited after {ig.calls} calls (streak {streak}) "
                              f"- backing off {wait // 60}m")
            # Six straight limits means the IP is in a long penalty box; continuing just
            # keeps it there. Stop cleanly - nothing is lost, unenriched leads keep
            # enriched_ig=0 and the next run resumes exactly here.
            if streak >= 6:
                emit("ENRICH", f"aborting: {streak} consecutive rate limits. "
                               f"Resume later with the same command.")
                break
            continue
        if status == "ok" and data:
            data = dict(data)
            data.pop("ig_handle", None)
            for k in ("ig_bio_links",):
                if isinstance(data.get(k), list):
                    data[k] = json.dumps(data[k], ensure_ascii=False)
            data["enriched_ig"] = 1
            sets = ",".join(f"{k}=?" for k in data)
            conn.execute(f"UPDATE leads SET {sets} WHERE id=?", list(data.values()) + [r["id"]])
            ok += 1
        else:
            conn.execute("UPDATE leads SET enriched_ig=1, ig_status=? WHERE id=?", (status, r["id"]))
            miss += 1
        conn.commit()
        if i % 10 == 0:
            emit("ENRICH", f"{i}/{len(rows)} | ok={ok} miss={miss} ratelimits={rl}")
        instagram.polite_sleep()
    emit("ENRICH", f"done: ok={ok} miss={miss} ratelimits={rl}")
    score_all(conn)
    _summary(conn, "enrich")


# --------------------------------------------------------------------- misc
def cmd_wmg(args):
    """WedMeGood: listings give price + membership; profiles give phone/email/WhatsApp."""
    from sources import wedmegood as wmg
    conn = db.init()
    client = wmg.WMGClient()
    cities = [c for c in CITIES if c[0] in wmg.CITY_SLUGS]
    max_pages = args.limit or 8
    total_new = 0

    for cat, vertical in wmg.CATEGORIES.items():
        for (city, _la, _ln, tier) in cities:
            slug = wmg.CITY_SLUGS[city]
            for page in range(1, max_pages + 1):
                try:
                    vendors, numpages = client.fetch_page(slug, cat, page)
                except Exception as e:
                    emit("ERROR", f"wmg {city}/{cat} p{page}: {type(e).__name__}: {e}")
                    break
                if not vendors:
                    break
                new = 0
                for v in vendors:
                    row = wmg.shape(v, city, vertical)
                    row["city_tier"] = tier
                    row["wmg_profile_url"] = row.pop("_profile_url", None)
                    row["wmg_membership"] = row.pop("_membership", None)
                    row["wmg_price_value"] = row.pop("_price_value", None)
                    row["search_query"] = cat
                    if db.upsert_lead(conn, row) == "new":
                        new += 1
                conn.commit()
                total_new += new
                emit("WMG", f"{city:<12} {cat:<24} p{page}/{numpages or '?'} "
                            f"+{new:>2} new | total_new={total_new}")
                if numpages and page >= numpages:
                    break
                wmg.polite_sleep()
    emit("WMG", f"done: {total_new} new leads")
    _summary(conn, "wmg")


def cmd_wmgprofile(args):
    """Fetch WedMeGood profiles for the best listing-only leads (adds phone/email)."""
    from sources import wedmegood as wmg
    conn = db.init()
    client = wmg.WMGClient()
    rows = conn.execute(
        "SELECT id, name, wmg_profile_url FROM leads "
        "WHERE wmg_profile_url IS NOT NULL AND phone IS NULL "
        "ORDER BY COALESCE(reviews,0) DESC LIMIT ?", (args.limit or 2000,)).fetchall()
    # profile pages are ~1.4MB each, so keep concurrency lower than the site checker
    workers = args.workers or 6
    emit("WMGPROF", f"{len(rows)} profiles queued ({workers} workers)")

    import threading
    from concurrent.futures import ThreadPoolExecutor
    local = threading.local()

    def grab(row):
        if not hasattr(local, "c"):
            local.c = wmg.WMGClient()
        try:
            return row["id"], local.c.fetch_profile(row["wmg_profile_url"])
        except Exception:
            return row["id"], None

    ok = got_phone = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (lead_id, d) in enumerate(ex.map(grab, rows), 1):
            if d:
                d["wmg_membership"] = d.pop("_membership", None)
                d["wmg_bookings"] = d.pop("_bookings", None)
                d["wmg_price_value"] = d.pop("_price_value", None)
                d = {k: v for k, v in d.items() if v is not None}
                if d.get("phone"):
                    d["phone_digits"] = db.norm_phone(d["phone"])
                    got_phone += 1
                sets = ",".join(f"{k}=COALESCE({k},?)" for k in d)
                # Commit per row. Batching writes here holds the SQLite write
                # transaction open for the whole batch - at network speed that is
                # minutes, which starves any concurrent stage into "database is locked".
                def _w(d=d, lead_id=lead_id, sets=sets):
                    conn.execute(f"UPDATE leads SET {sets} WHERE id=?",
                                 list(d.values()) + [lead_id])
                    conn.commit()
                db.write_retry(_w)
                ok += 1
            if i % 50 == 0:
                emit("WMGPROF", f"{i}/{len(rows)} | enriched={ok} phones={got_phone} "
                                f"| {i/max(time.time()-t0,0.01):.1f}/s")
    conn.commit()
    emit("WMGPROF", f"done: {ok}/{len(rows)} enriched, {got_phone} phone numbers added")
    _summary(conn, "wmgprofile")


def cmd_dedupe(args):
    """Merge cross-source duplicates.

    A business can enter twice: once from Google Maps and once from WedMeGood. At insert
    time the WedMeGood row has no phone yet, so it cannot match. Once `wmgprofile` fills
    phones in, the duplicate becomes detectable - hence this as a separate later pass.
    Keeps the richest row (most non-null fields) and folds the other's values into it.
    """
    conn = db.init()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)")
            if r[1] not in ("id", "created_at", "updated_at")]

    # Two dedupe keys, in order:
    #  1. wmg_profile_url - several config cities share one WedMeGood slug (Delhi,
    #     Gurgaon and Noida all map to delhi-ncr), so the same vendor is ingested more
    #     than once. Doing this FIRST matters: it stops `wmgprofile` paying for the same
    #     1.5MB page two or three times against a rate-limited host.
    #  2. phone_digits - catches cross-source duplicates and multi-branch businesses,
    #     but only works after wmgprofile has filled phones in.
    keys = [("wmg_profile_url", "wmg_profile_url IS NOT NULL"),
            ("phone_digits", "phone_digits IS NOT NULL AND LENGTH(phone_digits)=10")]
    merged = 0
    for col, cond in keys:
        groups = conn.execute(
            f"SELECT {col} AS k FROM leads WHERE {cond} "
            f"GROUP BY {col} HAVING COUNT(*) > 1").fetchall()
        emit("DEDUPE", f"{len(groups)} duplicate groups on {col}")
        merged += _merge_groups(conn, col, [g["k"] for g in groups])
    conn.commit()
    emit("DEDUPE", f"merged and removed {merged} duplicate rows")
    _summary(conn, "dedupe")


def _merge_groups(conn, col, keys):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)")
            if r[1] not in ("id", "created_at", "updated_at")]
    merged = 0
    for key in keys:
        rows = conn.execute(f"SELECT * FROM leads WHERE {col}=?", (key,)).fetchall()
        if len(rows) < 2:
            continue
        # richest row wins
        rows = sorted(rows, key=lambda r: sum(1 for c in cols if r[c] is not None),
                      reverse=True)
        keep, drop = rows[0], rows[1:]
        fills, vals = [], []
        # Volume signals are counted independently by each source (Google reviews are not
        # WedMeGood reviews), so filling only NULLs would keep whichever row won and
        # silently under-report demand - e.g. keeping reviews=13 when the other row says
        # 42. Take the strongest evidence for these; fill-if-null for everything else.
        MAXED = ("reviews", "photo_count", "ig_followers", "ig_posts", "wmg_price_value")
        for c in cols:
            if c in MAXED:
                best = max([r[c] for r in rows if r[c] is not None], default=None)
                if best is not None and best != keep[c]:
                    fills.append(f"{c}=?")
                    vals.append(best)
            elif keep[c] is None:
                v = next((d[c] for d in drop if d[c] is not None), None)
                if v is not None:
                    fills.append(f"{c}=?")
                    vals.append(v)
        # Delete the duplicates BEFORE folding their values in. place_id is UNIQUE, so
        # copying it onto the kept row while the donor row still exists trips the
        # constraint and aborts the whole pass.
        conn.executemany("DELETE FROM leads WHERE id=?", [(d["id"],) for d in drop])
        if fills:
            conn.execute(f"UPDATE leads SET {','.join(fills)} WHERE id=?",
                         vals + [keep["id"]])
        merged += len(drop)
    conn.commit()
    return merged


def cmd_score(args):
    conn = db.init()
    n = score_all(conn)
    emit("SCORE", f"scored {n} leads")
    _summary(conn, "score")


def cmd_stats(args):
    conn = db.init()
    _summary(conn, "stats")


def _summary(conn, tag):
    s = db.stats(conn)
    segs = {r["segment"] or "unscored": r["c"] for r in conn.execute(
        "SELECT segment, COUNT(*) c FROM leads GROUP BY segment")}
    verts = {r["vertical"]: r["c"] for r in conn.execute(
        "SELECT vertical, COUNT(*) c FROM leads GROUP BY vertical")}
    emit("STATS", f"leads={s['leads']} phone={s['with_phone']} ig={s['with_ig']} "
                  f"nosite={s['no_website']} scored={s['scored']} "
                  f"tasks={s['tasks_done']}/{s['tasks_total']} err={s['tasks_error']}")
    emit("STATS", f"segments={segs}")
    emit("STATS", f"verticals={verts}")
    db.log(conn, tag, json.dumps(s), json.dumps({"segments": segs, "verticals": verts}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "sites", "enrich", "score", "stats", "export",
                 "wmg", "wmgprofile", "dedupe"):
        p = sub.add_parser(name)
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--target", type=int, default=None)
        p.add_argument("--workers", type=int, default=None)
        p.add_argument("--recheck", action="store_true",
                       help="sites: re-fetch leads whose check was throttled/blocked")
    a = ap.parse_args()
    if a.cmd == "export":
        import export
        export.run()
        return
    {"collect": cmd_collect, "sites": cmd_sites, "enrich": cmd_enrich,
     "score": cmd_score, "stats": cmd_stats, "wmg": cmd_wmg,
     "wmgprofile": cmd_wmgprofile, "dedupe": cmd_dedupe}[a.cmd](a)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        emit("ABORT", "interrupted by user")
    except Exception as e:
        # Lead with the exception itself - a truncated traceback that cuts off before
        # the actual error line is useless for diagnosis.
        emit("FATAL", f"{type(e).__name__}: {e}")
        with open(LOGF, "a", encoding="utf-8") as fh:
            fh.write(traceback.format_exc() + "\n")
        traceback.print_exc()
        sys.exit(1)
