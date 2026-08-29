"""One-line pipeline status. Used by monitors and safe to run mid-scrape."""
import db
from config import TRANSIENT_STATUS


def line():
    c = db.connect()
    q = lambda s, *a: c.execute(s, a).fetchone()[0]
    ph = ",".join("?" * len(TRANSIENT_STATUS))
    gmaps = q("SELECT COUNT(*) FROM leads WHERE source='gmaps'")
    return (
        f"gmaps={gmaps} "
        f"total={q('SELECT COUNT(*) FROM leads')} "
        f"phone={q('SELECT COUNT(*) FROM leads WHERE phone_digits IS NOT NULL')} "
        f"ig={q('SELECT COUNT(*) FROM leads WHERE instagram IS NOT NULL')} "
        f"email={q('SELECT COUNT(*) FROM leads WHERE email IS NOT NULL')} "
        f"sited={q('SELECT COUNT(*) FROM leads WHERE site_checked=1')} "
        f"throttled={q(f'SELECT COUNT(*) FROM leads WHERE site_status IN ({ph})', *TRANSIENT_STATUS)} "
        f"igdone={q('SELECT COUNT(*) FROM leads WHERE enriched_ig=1')}"
    )


if __name__ == "__main__":
    print(line())
