# Lead sourcing research

> [!NOTE]
> This is the **reference deployment's** market research, kept as an artifact of how
> the sources were chosen and what was measured rather than assumed. The market
> sizing in §1 is specific to that market by definition; §2 onward — the source
> evaluation, the field maps, the response-code semantics and the rate-limit
> findings — generalise, and several of them are the reason the architecture looks
> the way it does.

Date: 2026-08-16. Every technical claim below was probed live from this machine, not assumed.

## 1. Market sizing / why these verticals

| Fact | Value | Implication |
|---|---|---|
| Indian SMBs with **no website** | 55–65% | Huge addressable base; the pitch is not "replace your site", it's "you have none" |
| US comparison | ~27% | India is ~2x more underserved |
| Indian SMBs using WhatsApp for business | 78% | WhatsApp automation is a *familiar* upsell, not an alien concept |
| Indian businesses on WhatsApp Business | 15M+ | |
| WhatsApp msg open rate vs email | ~98% vs 22% | Justifies the automation pitch commercially |
| MSME owners citing low digital understanding | >70% | Sell outcomes (bookings), never tech |
| Wedding photographers in India | ~24,700 | Vertical #1 alone can fill the 10k target with room to spare |
| WedMeGood photographer listings | 31,389 | Single directory exceeds our entire quota |
| IG business accounts exposing a public email | only 10–15% | **Do not** build the funnel on email. Phone + IG DM are the channels |

Top states by photographer density: Tamil Nadu (3,127), Maharashtra (2,989), Uttar Pradesh (2,415).

## 2. Source evaluation (all probed live)

| Source | Status | Yield per request | Notes |
|---|---|---|---|
| **Google Maps `pb` endpoint** | ✅ VALIDATED | 20 businesses, ~210KB | Plain HTTP, no browser. The backbone. |
| **Instagram `web_profile_info`** | ✅ VALIDATED | 1 profile, ~426KB | Rich enrichment. Rate-limited (~200/hr/IP). |
| **WedMeGood** | ✅ Accessible | ~30/page | `window.__INITIAL_STATE__` + JSON-LD `ItemList`. Carries **pricing**. |
| JustDial / Sulekha / IndiaMART | Secondary | — | Heavier anti-bot; use as gap-fill |
| COA architect register | ✗ Rejected | 3 searches/day/IP | Rate limit makes it worthless at scale |

### 2a. Google Maps — the critical unlock
Results are **not** server-rendered; they arrive via XHR. Guessing the `pb` protobuf param
returns only a 5KB query echo. Solution: drive Maps once with Playwright, capture the live
`pb` string, then **replay it over plain HTTP** with `q`, lat/lng and offset substituted.

- Pagination: `!7i20!8i{offset}` — offset in steps of 20.
- Coords: `!1d{zoom_m}!2d{lng}!3d{lat}`
- Template captured to `pb_template.txt` (2008 chars). Re-capture if yield drops (Google rotates it).

**Field index map** for `data[64][i][1]`:

| Idx | Field | Idx | Field |
|---|---|---|---|
| 11 | name | 13 | categories (list) |
| 78 | place_id (`ChIJ…`) | 14 | area / neighbourhood |
| 10 | feature_id (`0x…:0x…`) | 39 | full address |
| 7[0] | **website URL** | 166 | city, state |
| 7[1] | website domain | 9[2],9[3] | lat, lng |
| 178 | phone (local + `+91`) | 4[7] | rating |
| **75** | **links incl. `wa.link`/`wa.me`** | 37[1] | review count |
| 32[1][1] | description | 72 | photos |
| 203 | opening hours | 57[1] | owner name |

**Measured coverage on a live Mumbai query (n=20):**
`name 20/20 · phone 20/20 · website 20/20 · rating 20/20 · reviews 20/20 · address 20/20 · WhatsApp link 8/20`

### 2b. Instagram — enrichment, with caveats
Endpoint: `GET /api/v1/users/web_profile_info/?username=…` + header `X-IG-App-ID: 936619743392459`.
Requires TLS impersonation (`curl_cffi impersonate="chrome131"`) — plain `requests`/`httpx` are
fingerprinted and blocked.

Response-code semantics (**determined empirically, this matters**):
- `200` + `data.user` → OK
- `200` + `data.user = null` → profile hidden/removed
- `404` → **username does not exist** → therefore *never guess handles*; only query handles
  discovered from a Maps website field, a bio link, or a directory listing
- `400` (`ig_business_category_subvertical … deleted`) → a **persistent, profile-specific**
  server-side schema bug, NOT rate limiting. Confirmed non-transient over 6 consecutive retries.
  → needs an og:meta HTML fallback; retrying is pointless.

Useful fields: `edge_followed_by.count`, `edge_owner_to_timeline_media.count`, `biography`,
`bio_links[]`, `external_url`, `business_email`, `business_phone_number`,
`business_category_name`, `is_business_account`, `is_verified`.

## 3. Scoring signals discovered in live data

The thesis: **score the GAP** — strong business demand signals paired with weak web presence.
A booked-out studio with 900 reviews whose only link is an Instagram page is the perfect lead.
A studio with a good modern site is not a lead at all.

**Pain signals (they need the product):**
- No website field at all
- Website *is* an instagram.com / facebook.com URL — observed 2/20 in one live page
- Link is `linktr.ee`, `wa.me`, or a **Google Form** — observed live: `mandar_tawade_photography`
  (8.3k followers) takes enquiries through a Google Form and lists a YouTube video as its website
- Dead / non-HTTPS / non-responsive site

**Capacity signals (they can pay):**
- Review count (proxy for booking volume), rating ≥ 4.0, photo count, IG followers 5k–100k

**Reachability signals (we can contact them):**
- Phone, `wa.link`/`wa.me` presence, discovered IG handle, public email

**Disqualifiers:** chains/franchises, an already-good modern site, <5 reviews (not yet a real
business), rating <3.5 (failing — no budget).

## 4. Rate-limit reality (drives the architecture)

Three successive numbers, and the first two were both wrong — worth recording because the
mistake is easy to repeat:

1. **Published guidance: ~200 requests/hour/IP.**
2. **A 22-request burst at 6s intervals ran clean** (21 ok / 1 notfound / 0 rate limits),
   implying ~477/hr. **This was over-optimistic** — 22 requests is far too short a sample to
   reveal a *rolling-window* limit. Do not size a long run from a burst test.
3. **The real run hit the limit after 228 calls** at ~8s spacing (≈500 req/hr).

The trap that made #2 misleading in practice: **~38% of profiles hit Instagram's 400 schema
bug and fall back to a second HTML request.** So one *lead* costs ~1.4 *requests*, and pacing
computed per-lead silently runs ~40% hotter than intended. **Pace on requests, not leads.**

`IG_DELAY` is now 11–16s (~13.5s avg) → ~265 leads/hr → ~370 req/hr, which holds. The client
backs off 15 minutes on a 429 and resumes, so a limit costs time but never data.

Enrichment is the scarce resource either way, so the architecture stands: **collect wide from
Maps → score on Maps data alone → enrich only the top slice with Instagram.** The top 2,500
takes ~9 hours at the safe rate.

### Throughput of the other stages (measured)
- Google Maps collect: ~175–280 leads/min serial, **0 blocks** across a full run at 2–4.5s spacing
- Website health check: **~5/sec at 12 workers** (7x serial)
- WedMeGood profiles: **~8/sec at 6 workers**

The website stage doubles as a contact harvester — on a 200-site sample it recovered
**78 Instagram handles and 74 emails** from pages it was already fetching for health checks.

## 5. What the collected data actually shows

Measured across ~13,900 real Indian businesses in the five target verticals — this is the
market evidence behind the pitch, not a secondary source:

**Web presence (n = 13,890):**

| Class | Count | Share |
|---|---|---|
| **No website at all** | 8,386 | **60.4%** |
| Real website | 4,554 | 32.8% |
| A social profile used as the website | 944 | 6.8% |
| Link-hub (linktr.ee / Google Form / wa.me) | 49 | 0.4% |
| A directory profile used as the website | 12 | 0.1% |

60.4% with no website independently reproduces the published "55–65% of Indian SMBs have
no website" figure — on our own sample, in our own verticals.

**Of the 4,554 that do have a site:** 2,818 answer 200 · **464 are outright dead**
(DNS failure/timeout) · 74 return 404 · 461 return 403 (block scrapers — scored
*unverified*, never *dead*) · 105 sit on abandoned builders (GoDaddy Sites 90, Blogspot 15).

Platforms seen: WordPress 921 · Wix 227 · Shopify 136 · GoDaddy 90 · Squarespace 44 ·
Blogspot 15 · Webflow 1.

**So roughly 2 in 3 businesses in these verticals have no working website**, and only about
20% of the total have a live site that isn't dead, blocked, or on an abandoned builder.

**Contactability after enrichment:** phone ~7,600 · Instagram handle ~2,560 · email ~1,870.
Instagram handles more than quadrupled (600 → 2,560) purely from parsing pages the website
health check was already fetching.

## 6. Things tried and rejected (so they don't get re-tried)

- **Harvesting Instagram handles from `@mentions` in business names/descriptions.**
  Sounded free — some listings really are named `Baker's Nest Homemade Delights
  (@bakersnest2022)`. Measured across 8,356 leads: **6 candidates, ~half false positives**
  (`Cheesecakes @ Indiranagar`, `@ Sadashivanagar` — in Indian listings `@` usually means
  *at a location*). Every false handle costs a rate-limited Instagram call and returns 404.
  Removed.
- **COA architect register** (`coa.gov.in/search_arch.php`): 3 searches per day per IP.
  Useless at any scale.
- **Guessing Instagram handles from business names**: `404` responses confirm the endpoint
  will not fuzzy-match, so handles must come from a discovered URL.
- **Hand-writing the Google Maps `pb` parameter**: returns a 5KB query echo with zero
  results. It must be captured from a live browser session.

## Sources
- [Small businesses without a website — 2026 stats](https://b2bleadfinder.io/blog/small-business-without-website-statistics)
- [WhatsApp marketing statistics India 2026](https://whatsender.dev/blog/whatsapp-marketing-statistics-india-2026)
- [MSMEs in India — Statista](https://www.statista.com/topics/10949/msmes-in-india/)
- [Wedding photographers in India — SmartScrapers](https://rentechdigital.com/smartscraper/business-report-details/list-of-wedding-photographers-in-india)
- [WedMeGood photographer directory](https://www.wedmegood.com/vendors/all/wedding-photographers/)
- [How to Scrape Instagram in 2026 — Scrapfly](https://scrapfly.io/blog/posts/how-to-scrape-instagram)
- [Which Instagram scrapers still work in 2026 — SocialCrawl](https://www.socialcrawl.dev/blog/instagram-scraping-2026)
- [curl_cffi TLS impersonation](https://brightdata.com/blog/web-data/web-scraping-with-curl-cffi)
- [serpapi/google-maps-pb-decoder](https://github.com/serpapi/google-maps-pb-decoder)
- [Council of Architecture directory](https://coa.gov.in/search_arch.php)
