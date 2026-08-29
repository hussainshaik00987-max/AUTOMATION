# discovery

Builds a **scored lead list**: businesses that measurably need what you sell, ranked by
how badly they need it and how well they can pay for it.

The thesis is **score the gap** — reward strong demand and capacity, reward the *absence*
of a real web presence, and penalise businesses that are already well served.

> [!NOTE]
> Verticals, cities, search queries, scoring weights and the blocklist all live in
> `config.py`. The set below is the reference deployment's — a services market where the
> portfolio *is* the product — and the pipeline has nothing vertical-specific in it.
> Currency figures are that market's.

| Vertical | Share | Why |
|---|---|---|
| Wedding photographers & videographers | 40% | Portfolio *is* the product; large ticket sizes make a site trivially affordable |
| Interior designers & architects | 20% | Highest deal sizes; acute credibility gap when clients search for them |
| Wedding planners & decorators | 15% | Constantly asked for portfolios and packages |
| Cake studios, bakeries, home food | 15% | Order chaos in DMs; a menu + payment link solves a daily problem |
| Fashion boutiques & clothing labels | 10% | Already selling through social; real revenue, real motivation |

## Setup

> [!WARNING]
> **The database must not live on a network-backed filesystem.** WAL mode needs `-shm`
> shared-memory files that a 9p/v9fs mount (for example, WSL2's view of a Windows drive)
> does not properly support, and the symptom is intermittent `database is locked` the
> moment two stages run at once. Measured after moving it to a local filesystem:
> **8,798 concurrent writes/sec, 0 lock errors**, versus a hard crash before.
>
> Set `COLDCALL_DB` to a path on a local filesystem. Code, logs and exports can stay
> anywhere. Virtualenv symlinks break on the same mounts, for the same reason.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install curl_cffi lxml pandas phonenumbers tldextract rapidfuzz \
            beautifulsoup4 playwright openpyxl
playwright install chromium          # only needed for refresh_pb.py

export COLDCALL_DB=~/.local/share/coldwire/leads.db
```

A synthetic sample of what this stage produces is in
[`data/sample_top_leads.csv`](data/sample_top_leads.csv) — the downstream services run
on it unmodified.

## Pipeline

Run in this order. Every stage is resumable — kill it any time and re-run.

```bash
python run.py collect --target 10000          # Google Maps -> raw leads
python run.py wmg --limit 4                   # WedMeGood listings (price + paid-listing signal)
python run.py wmgprofile --workers 6          # WedMeGood profiles (phone/email/WhatsApp)
python run.py dedupe                          # merge cross-source + multi-branch duplicates
python run.py sites --workers 12              # website health -> the core pain signals
python run.py sites --recheck                 # re-fetch any throttled/blocked checks
python run.py score                           # score everything
python run.py enrich --limit 500              # Instagram, top-scored leads only
python run.py score                           # re-score with Instagram data
python run.py export                          # -> exports/leads.xlsx + CSVs
python run.py stats                           # health snapshot any time
```

**Stage order matters.** `sites` must run before the final `score`: an unchecked website
cannot be judged, so those leads score as if they had no pain signal.

`collect`, `wmg` and `wmgprofile` hit different hosts and can run concurrently. Three
things had to be true before that was actually safe:

1. **The DB must be off `/mnt/*`** (see the Setup note) — 9p breaks WAL locking outright.
2. **Writes commit per row, not per batch.** Batching 50 updates behind one commit holds
   the SQLite write transaction open for the *whole batch*; at network speed that is
   minutes, and every other stage dies with `database is locked`. Commits are cheap
   (~8,800/sec measured), so batching bought nothing and cost the run.
3. **Writes go through `db.write_retry`.** `busy_timeout` is not sufficient on its own:
   in WAL mode a connection that reads and then writes after another has committed gets
   an *immediate* "database is locked" snapshot conflict that the timeout does not wait
   out. `write_retry` backs off and retries, and re-raises non-lock errors untouched.

Rate limits differ by host: Google Maps tolerated a full run at 2–4.5s spacing with
**0 blocks**, but WedMeGood returns **HTTP 429** on profile fetches at 6 workers — use
`--workers 3` and note that `fetch_profile` backs off 20s+ on a 429 rather than burning
its retry budget on 4s sleeps.

### Throughput
`sites` and `wmgprofile` are threaded (`--workers`, network fans out but only the main
thread touches SQLite; `curl_cffi` sessions are per-thread because they are not
thread-safe). Measured: **~5 sites/sec at 12 workers** (7x the serial rate) and
**~8 profiles/sec at 6 workers**. `collect` stays serial and paced — Google is the one
source that will actually block you.

The `sites` stage pays for itself twice: while checking health it also harvests
Instagram handles, emails, WhatsApp links and the site platform from the same fetch.
Measured on a 200-site sample: **78 Instagram handles and 74 emails recovered**.

## Why the sources were chosen

| Source | Role | Gives |
|---|---|---|
| **Google Maps** | volume backbone | name, phone (~100%), rating, reviews, website, address, categories, WhatsApp link |
| **Vertical marketplace** <br/><sub>(the reference build uses a wedding-industry directory)</sub> | budget qualifier | **price per day**, paid-listing membership, recent bookings, email, phone |
| **Instagram** | intent + reach | followers, posts, bio, bio links, business email/phone |
| Website check | the actual pain | dead / http-only / not mobile-friendly / is-a-social-link |

### Google Maps: how it works
Maps results are not in the HTML — they arrive via an XHR carrying a `pb` protobuf
parameter. `refresh_pb.py` drives a real browser once to capture that string; the scraper
then **replays it over plain HTTP**, substituting query, coordinates, and offset. No
browser at scrape time, so it runs fast and cheap.

If yield suddenly drops to ~0, Google has rotated the format:

```bash
python refresh_pb.py     # re-captures pb_template.txt
```

### Instagram: the sharp edges
- Requires TLS impersonation (`curl_cffi impersonate="chrome131"`). Plain
  `requests`/`httpx` are fingerprinted and blocked outright.
- **Rate limiting is a rolling window — do not size it from a burst test.** A clean
  22-request burst at 6s suggested ~477/hr; the real run was limited after 228 calls.
  `IG_DELAY` is 11–16s (~370 req/hr), which holds.
- **One lead ≠ one request.** ~38% of profiles hit the 400 bug and cost a second HTML
  request, so pacing per-lead runs ~40% hotter than intended. Pace on requests.
- **The penalty outlasts the pause.** Once limited, the IP stayed blocked through a
  10-minute drain and 429'd on the very first call after it. Backoff therefore escalates
  (15m → 30m → 60m → 2h) and aborts after 6 consecutive limits — probing every 15 minutes
  just re-arms the block. Aborting costs nothing: unenriched leads keep `enriched_ig=0`,
  so re-running `enrich` resumes exactly where it stopped.
- Enrichment therefore runs *only* on the top-scored slice, never all 15k.
- `404` means the handle does not exist, so handles are only ever taken from a discovered
  URL, never guessed from a business name.
- `400` is a persistent Instagram-side schema bug on certain profiles, **not** a rate
  limit — confirmed unchanged across 6 consecutive retries. Those fall back to og:meta
  HTML parsing. Retrying a 400 is wasted budget.

## Scoring model

The thesis is **score the gap**: reward strong demand and capacity, reward the *absence*
of a real web presence, and penalise businesses that are already well served.

A studio with 900 reviews whose only link is an Instagram page is the perfect lead.
The same studio with a modern responsive site is not a lead at all — it scores negative.

| Group | Signals |
|---|---|
| **Pain** (they need it) | no website · website is a social profile · link-hub/Google Form as the site · dead site · http-only · not mobile-friendly |
| **Capacity** (they can pay) | review count tiers · rating ≥4.0 · photo count · IG followers (5k–100k sweet spot) · **WedMeGood price/day** · **paid listing** · recent bookings |
| **Reach** (we can contact) | phone · WhatsApp link · Instagram · email |
| **Negative** | rating <3.5 (failing) · <5 reviews (not a real business) · already has a good site · chain/franchise · **1800/1860 toll-free number** |

The website group has five classes, not two: `none`, `social`, `linkhub`, **`directory`**
and `real`. A vendor whose "website" is their WedMeGood or JustDial profile is renting a
page on someone else's marketplace — same pitch as "your website is your Instagram".

Four false-positive traps the model deliberately avoids:
- **A throttled fetch is not a dead site.** 429/403/503 say we were blocked, not that the
  site is broken. Scoring them as dead invented a +24 pain signal and pushed leads to the
  top for a reason that wasn't true. They score as *unverified*; `sites --recheck` retries them.
- **A directory profile is not a website** — and it is also not "dead" just because the
  marketplace rate-limited our check.
- **A missing website field is not a missing website.** WedMeGood listings never carry
  one, so a blank there scores as *unknown*, not as absence — otherwise every WedMeGood
  lead would rocket to the top of the list on a signal we never actually measured.
- **A toll-free 1800/1860 line means a chain**, not a studio. Real small businesses use
  mobile numbers; toll-free numbers caught HomeLane, Nirula's and INIFD franchises that
  the name blocklist missed.

Segments: `A_hot` ≥85 · `B_strong` ≥65 · `C_worth_a_try` ≥45 · `D_low` below · `excluded` (chains).

### The second score: `automation_score`

There are **two scores per lead**, because there are two products.

The website score deliberately destroys leads that already have a good site — correctly,
they are not rebuild prospects. But those are frequently the *best* WhatsApp-automation
prospects: digitally mature, high enquiry volume, fielding DMs by hand.

This turned out to matter. Measured across the collected data, the share of businesses
that already have a real website varies enormously by vertical:

| Vertical | Already has a real website | No website at all |
|---|---|---|
| Interior designers & architects | **81.4%** | 15.0% |
| Wedding planners & decorators | 63.2% | 26.6% |
| Wedding photographers | 50.9% | 31.9% |
| Fashion boutiques | 40.5% | 48.3% |
| Cake studios & bakeries | 33.0% | 50.0% |

So the "you have no website" pitch barely applies to interior designers — scoring them on
that alone buried the #2 vertical to 111 of the top 2,000. `automation_score` recovers
them: 159 interior/architect leads score ≥60 for automation.

Example the website score would have thrown away: *Northgate Creations* — 3,470 reviews,
5.0 rating, working site. Website score 33 (rightly: they don't need a site).
Automation score 75 (rightly: that is a lot of enquiries being handled by hand).

**Bakeries and boutiques are far more underserved than their rank suggests** — roughly
half have no website at all, versus 15% of interior designers.

Each lead also gets a **`pitch_angle`** — the specific opening line justified by its own
data ("your site is dead", "your website is your Instagram", "enquiries run through a
Google Form"). Weights live in `config.py → WEIGHTS`; tune them and re-run `score`.

## Output

`exports/leads.xlsx` with sheets:
- **TOP 2000** — the working list, ranked by website score. Every row has a phone or an
  Instagram handle; leads with neither are held out no matter how well they score.
- **Call Ready** — has a phone number
- **Instagram DM Ready** — has an IG handle
- **No Website** — the strongest pain segment
- **WhatsApp Automation Fit** — ranked by `automation_score` instead; the leads the
  website pitch would miss
- one sheet per vertical

Plus `top_leads.csv` and `all_leads.csv`.

Two columns do the selling for you: **`Pitch Angle`** (why *this* business needs a site,
derived from its own data) and **`Automation Angle`** (why it needs automation instead).

## Files

```
config.py             cities, verticals, search queries, scoring weights, blocklist
db.py                 SQLite schema, dedupe/merge, resumable task queue, migrations
run.py                orchestrator CLI (collect/wmg/sites/enrich/score/export/stats)
score.py              scoring + segmentation + pitch angle
export.py             Excel/CSV output
refresh_pb.py         re-capture the Google Maps pb template when it rotates
sources/gmaps.py      pb replay + the array field-index map
sources/wedmegood.py  listing + profile parsing (INITIAL_STATE, undefined -> null)
sources/instagram.py  web_profile_info + og:meta fallback
sources/website.py    site health -> pain signals
logs/run.log          append-only event log
exports/              leads.xlsx, top_leads.csv, all_leads.csv
RESEARCH.md           source evaluation, field maps, measured coverage
```

## Dedupe
At insert time: on `place_id` first, then `(phone, name-prefix)`. Merges never clobber
existing values — a later source can only fill blanks, so Google Maps phone data survives
a WedMeGood merge.

`run.py dedupe` is a second, later pass, and it has to be separate: a WedMeGood row has no
phone until `wmgprofile` runs, so its duplicate of a Google Maps row is simply *invisible*
at insert time. Once phones are filled in, this pass groups by phone number, keeps the row
with the most populated fields, and folds the others' values into it.

It also catches multi-branch businesses (two "Cake O Clocks" outlets on one owner's phone).
Merging those is intentional — they are one sales conversation, not two.

## Operating notes
- Delays are set in `config.py` (`GMAPS_DELAY`, `IG_DELAY`, `SITE_DELAY`). Measured
  0 blocks across a full Maps run at 2–4.5s.
- `--limit` bounds any stage; `stats` is safe to run mid-scrape.
- All stages are restartable; `tasks` tracks per-query offsets so nothing is re-fetched.
- Scraped data is public business contact information, which is **not** the same thing
  as data you are free to use for any purpose. Platform terms and data-protection law
  both apply, and automated calling and messaging are separately regulated.
  **Read [`docs/compliance.md`](../../docs/compliance.md) before pointing this at anyone.**
