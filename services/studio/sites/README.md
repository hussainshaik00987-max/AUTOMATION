# studio/sites

Generates a bespoke concept website for every lead in the discovery export —
2,000 pages across **30 vertical-specialised templates**, built to be opened on a
call while the prospect is on the phone, and to survive being pasted into a chat
thread afterwards.

```bash
# Uses services/discovery/exports/top_leads.csv, or the synthetic sample if you
# have not run a scrape.
python build.py --base-url https://demo.example.com \
                --asset-base-url https://assets.example.com

open catalogue.html          # the calling list: open a demo, copy its link
```

> [!NOTE]
> The photo-relevance work below is deliberately market-specific — that is the
> whole point of it. The search queries, template set and copy live in
> `engine/` and `tools/harvest_photos.py`, and are configuration.

Output is a plain static tree with no build step on the host. `catalogue.html`
is the calling list and deliberately sits *outside* `out/`, because it is the
lead pipeline — see [Publishing](#publishing).

---

## Why it looks like this

### The dataset has no images, so it borrows some

The CSV contains no photography at all. Pages draw instead on a **582-photo
library harvested from Pexels** (`tools/harvest_photos.py`), whose licence
covers commercial and non-commercial use with no attribution required.

Stock photography is the thing that most obviously turns 2,000 demos into a
template farm, so two properties are engineered rather than hoped for:

**Relevance.** A Delhi wedding planner needs a mandap, not a barn wedding in
Vermont. Every photo is tagged by `vertical` *and* `topic` — 12 topics per
vertical, ~10 photos each — and each slot draws from the topics that suit it.
The search queries are India-specific on purpose: *indian wedding mandap
decoration*, *haldi ceremony*, *marigold flower decoration india*, *indian
menswear kurta sherwani*.

**Spread.** Two failure modes look equally cheap: six near-identical bride
portraits inside one gallery, and two studios *in the same city* opening on the
same photograph. So:

- galleries are filled **topic-first round-robin** — one photo per topic before
  any topic repeats;
- the hero walks a **per-city ordering by the lead's rank within its city**, so
  the first N studios in Mumbai are guaranteed different opening frames.

Measured over the full 2,000:

| | before cohort walk | after |
|---|---|---|
| distinct hero photographs | 368 | **486** |
| most-reused hero | 76 pages | **20 pages** |
| two leads in one city sharing a hero | 31.1% | **0.8%** |
| gallery tiles shared with the next lead in the same city | — | **1.2 of 12** |

The hero matters twice over: it is also the `og:image`, so a repeat is exactly
what two prospects comparing WhatsApp previews would notice.

### The procedural artwork is still there

The original SVG engine (`engine/art.py`) has not been deleted — it is the
fallback for any slot the library cannot fill, and `build.py --no-photos` still
produces a complete, photo-free build. Five motifs, one per vertical, each with
several compositional variants:

| Motif | Vertical | Draws |
|---|---|---|
| `film` | wedding photography | silhouette horizons, iris/aperture blades, bokeh at two depths, hard-edged light leaks, 35mm sprockets |
| `icing` | bakery | piped frosting swirls, scalloped frill layers, glaze drips, scattered sprinkles |
| `mandala` | wedding planning | rangoli rotational symmetry (8/12/16-fold), draped canopy swags, marigold sparkle |
| `drape` | fashion | fabric folds with directional shading, diagonal colour blocking, halftone, stitch seams |
| `plan` | interior & architecture | one-point perspective rooms, recursive floor plans with furniture and hatching, facade elevations with mullions and dimension strings |

Composition is deliberate rather than scattered — forms sit on thirds and
golden-ratio offsets, and each piece layers background wash → mid-ground form →
foreground texture so it reads as depth instead of noise.

Aspect ratios past 2:1 are treated as banners and switch to variants that carry
across the full width; a `slice` crop of a centred composition leaves flat
colour at both ends.

### Nothing is invented

These get sent to real business owners, so every claim on the page traces back
to a column in the CSV:

- rating, review count → Google Maps
- price, membership tier, recent bookings → WedMeGood
- follower count → Instagram
- city, area, category, address, geo → Google Maps

**Testimonials are the one place a demo normally lies.** Instead of fake names
against fake quotes, the review section renders the real Google aggregate as a
factual card, and the remaining cards are prompts explicitly badged **sample**
("Paste a message from a parent of the bride here"). Honest, and it doubles as a
prompt during the call.

Service lists, process steps and FAQ answers describe standard practice in each
trade, phrased so they never assert an unverified fact.

**The photography says so too.** The gallery carries a visible line — *"Stock
photography, shown to size the frames — your own work drops straight in"* —
because the one thing you cannot do is let a photographer believe you have
their portfolio. The `alt` text is the exception: it describes what the picture
shows and nothing else, since a disclaimer read aloud in place of an image is
worse accessibility, not better honesty.

### The enquiry form actually works

No backend, no form service. On submit it composes a prefilled WhatsApp message
and opens the deep link:

```
Hi Kalakshaya Photography,

Your name: Priya Sharma
Phone: +91 98765 43210
Wedding date: 2026-12-14
Venue or city: Taj West End, Bangalore

(Sent from your website)
```

That maps directly onto how these businesses already take orders, and it is the
single most demonstrable thing on the page during a call.

Pages also emit JSON-LD `LocalBusiness` structured data built from the real
Google fields — so the demo is genuinely better for search than the Instagram
page most of these leads currently use as a website.

---

## What was cleaned before rendering

The CSV is scraper output; three fields would have shown up broken on-page:

| Field | Problem | Fix |
|---|---|---|
| `Description` | hard-truncated at ~159 chars, often mid-word — *"…is called Arsim Worldwide Occas"* | recover to the last complete sentence, else drop the dangling word |
| `IG Bio` | usually the Instagram **page title**, not a bio, with numeric HTML entities | pattern-filtered; 764 handles → **102 real bios** |
| `Area` | raw Google locality chain up to four segments | keep the two most specific, drop the city if repeated |

Also: `Website` values pointing at instagram.com are moved to the Instagram field
(that *is* the scraper's `website_is_social` pain signal, not a real site), which
is why real-website coverage drops 46% → 15%. Phone numbers normalise to E.164,
and WhatsApp links are derived from the phone where missing — taking WhatsApp
coverage from 28% to **99%**.

---

## Templates

30 templates. A template is a *recipe*, not an HTML file: a `Skin` (corner
language, section density, rule weight, shadow style, nav case), a type system, a
set of candidate palettes, an ordered list of section blocks, and its own CSS
layer. Counts follow the lead distribution.

| Vertical | Leads | Templates |
|---|---|---|
| wedding photography | 1,059 | Noir · Atelier · Filmstrip · Journal · Vow · Archive · Canvas · Lumen |
| bakery | 371 | Counter · Parlour · Daily · Patisserie · Order · Crumb |
| wedding planning | 344 | Mandap · Soirée · Shubh · Saanjh · Utsav · Bandhan |
| fashion | 127 | Runway · Bolt · Atelier · Drape · Label |
| interior & architecture | 99 | Blueprint · Studio · Plot · Frame · Terra |

Assignment is deterministic and evenly spread, but steered by real signals:
leads at **≥₹1,50,000**, WedMeGood *Handpicked/Pro Plus*, or **≥40k Instagram
followers** get first refusal on the luxury layouts (268 premium leads, 72% land
on one).

With 25 palettes this produces **90 distinct template × palette combinations**,
plus per-business photography, procedural fallback artwork and rotated service
lists — so adjacent leads in the same city never open the same-looking page.

### Typography and colour

System font stacks only — a webfont costs a round trip and a flash of unstyled
text, and half these leads open the link on mobile data. Seven type systems
(editorial, couture, swiss, drafting, warm, poster, quiet) pair a display face, a
body face and a label face with their own tracking and case rules.

Each type system also carries a separate **wordmark face**. A display face is
chosen to work at 80px, and several of them stop working at 17px: the `poster`
system's Haettenschweiler collapses into an unreadable picket fence, and
`couture`'s Didone loses its hairlines outright. The header brand used to
inherit `--font-display` and was illegible on every page of six templates.
`--font-brand` is a deliberately legible face per system, at a heavier weight
than the headline, with the headline's negative tracking opened back up.

All **25 palettes are verified against WCAG AA at build time** — `build.py`
refuses to run if any fails. The audit caught four real failures during
development (two orange accents that looked fine and measured 4.07:1).

---

## Layout

```
demosites/
├── build.py                  CLI entry point
├── engine/
│   ├── data.py               CSV → cleaned Business records (+ per-city rank)
│   ├── theme.py              25 palettes, 7 type systems, WCAG auditor
│   ├── art.py                procedural SVG engine + street plate
│   ├── photos.py             photo library, slot selection, <img> markup
│   ├── content.py            copy engine (data-backed only)
│   ├── css.py                design system, generated per template
│   ├── blocks.py             ~20 section components + icon sprite
│   ├── templates.py          the 30 recipes + assignment
│   ├── render.py             page assembly, shared-asset writer
│   └── index_page.py         the catalogue
├── assets/photos/            the harvested library (not generated by build.py)
│   ├── photos.json           manifest: id, vertical, topic, alt, ratio, LQIP
│   └── <vertical>/<id>-{lg,sm,og}.{webp,jpg}
├── catalogue.html            the lead list — gitignored, never deploy it
├── tools/
│   ├── harvest_photos.py     Pexels harvester (Playwright + plain HTTP)
│   ├── preview_card.py       renders the WhatsApp link preview from real tags
│   ├── interact.py           behaviour test: nav, lightbox, enquiry form
│   ├── verify_live.py        checks the deployment over the real network
│   ├── link_leads.py         writes demo URLs back into the lead sheets
│   ├── contact_sheet.py      artwork proof sheet
│   ├── shots.py              screenshot pages (desktop/mobile/full)
│   ├── sheet.py              tile screenshots for review
│   └── verify.py             the test suite
└── out/                      <- this whole directory is the deployable site
    ├── index.html            searchable catalogue
    ├── vercel.json           clean URLs + cache headers
    ├── robots.txt            noindex, but social crawlers allowed
    ├── 404.html
    ├── assets/{css,js,photos}/
    └── sites/<slug>.html     2,000 demos
```

## Usage

```bash
python build.py --base-url https://demos.example.com   # the real build
python build.py --limit 50                    # quick sample
python build.py --vertical bakery             # one vertical
python build.py --template wp-noir            # force one template (design work)
python build.py --no-photos                   # procedural artwork only
python build.py --inline-assets               # self-contained pages, ~3x bytes

python tools/harvest_photos.py                # refresh the photo library
python tools/harvest_photos.py --download-only --force   # re-encode, no scrape

python tools/verify.py out/sites --sample 120  # the document audit
python tools/verify_live.py https://demo.example.com  # the deployment
python tools/link_leads.py                    # demo URLs -> the lead sheets
python tools/interact.py out/sites --sample 12 # the behaviour test
python tools/preview_card.py out/sites --sample 6 cards.png   # WhatsApp previews
python tools/shots.py out/sites /tmp/shots --mobile
python engine/theme.py                        # contrast audit alone
```

`--base-url` is not optional for anything you intend to send to a prospect:
`og:image` must be an absolute URL, and without one WhatsApp renders a
text-only preview. The build prints a warning when it is missing.

## Publishing

**Live at https://demo.example.com** — photos at
`https://assets.example.com`.

```bash
python build.py --base-url https://demo.example.com \
                --asset-base-url https://assets.example.com

cd assets/photos && npx vercel deploy --prod --yes   # 93 MB, only when photos change
cd out           && npx vercel deploy --prod --yes   # 62 MB
python tools/verify_live.py https://demo.example.com
```

### Why it is two projects

The build is 153 MB and the account is on Hobby, which caps **CLI uploads at
100 MB** (1 GB on Pro; a Git-connected deploy has no cap at all). Rather than
trim the photo library to fit, the library is deployed as its own project and
the pages reference it absolutely:

| project | contents | size |
|---|---|---|
| `studio-preview` | 2,000 pages · 90 stylesheets · site.js | 62 MB |
| `studio-preview-assets` | 1,746 photo files | 93 MB |

`--asset-base-url` is what switches this on: photos are then *not* copied into
`out/`, so the pages upload carries none of their weight. Omit the flag and
everything reverts to one relative, self-contained tree.

The cost is that the two must be redeployed together when photos change, and
that pages now make cross-origin image requests — so `verify.py`'s
"zero external requests" result only holds for a local build.

### Three things bit during the first deploy

Each was invisible locally and would have been invisible in a screenshot too:

- **Deployment Protection was on by default** (`ssoProtection:
  all_except_custom_domains`). Every page was a Vercel login wall, and
  `og:image` returned an HTML login page instead of a JPEG — so WhatsApp would
  have rendered no preview at all, with nothing about the page looking wrong.
- **`studio-preview.vercel.app` was already taken globally**, so Vercel quietly
  assigned `studio-preview-six.vercel.app`. The clean name is attached as a
  second domain on the same project.
- **The banner said "not published anywhere"**, which stopped being true at the
  moment of deploy. On a page whose whole premise is that nothing on it is
  invented, that sentence could not stay. It now reads *"private link, not
  listed or indexed"*.

`tools/verify_live.py` exists because of the first one: it fetches `og:image`
**as the WhatsApp crawler** and fails if the response is not an image.

### Two things must not be published### Two things must not be published

- **`catalogue.html` stays local.** It is the lead list — every business with
  its score, pitch angle, phone number and a WhatsApp deep link. `build.py`
  writes it to `demosites/catalogue.html`, deliberately *outside* `out/`, so
  that publishing the deployable tree cannot leak it. `out/index.html` is a
  neutral placeholder instead. `--publish-catalogue` overrides this; only use
  it behind access control.
- **`../../discovery/exports/top_leads.csv` stays local.** If you push this project rather
  than just `out/`, use a private repo.

`demosites/.gitignore` covers the catalogue as a second line of defence, and
excludes the 93MB source photo library — that one is fully reproducible from
`assets/photos/photos.json` with `harvest_photos.py --download-only`, while
`out/assets/photos/` (the copy that actually ships) stays tracked.

`tools/preview_card.py` composes the WhatsApp bubble from a page's real Open
Graph tags and its real `og:image` file, so a clipped title or a badly centred
1200x630 crop shows up before the link does.

The demo pages themselves only restate what is already on a business's public
Google listing, which is the whole premise of showing one to its owner.

### Link previews

`--base-url` is what makes a pasted link render as a card in WhatsApp instead
of bare text. Absolute URLs are mandatory there, so the tags are omitted
entirely without it rather than emitted broken, and the build warns.

Each page carries `og:title`, `og:description`, `og:url`, `og:site_name`,
`og:locale`, a 1200x630 JPEG `og:image` with declared dimensions, and
`twitter:card=summary_large_image`. Three details are specific to how WhatsApp
renders:

- **the image is a real JPEG**, not the SVG data-URI favicon the page uses
  everywhere else — WhatsApp's crawler ignores SVG and would show no image;
- **the title is capped at ~65 characters**, and when a scraped name is longer
  than that on its own the descriptor is dropped rather than letting the clamp
  eat the business name;
- **the description does not repeat the name.** The title already carries it,
  and the preview only shows about two lines, so the description opens with
  the Google rating instead.

`robots.txt` sends `Disallow: /` to indexers — these carry someone else's
business name and should not compete with them in search — while explicitly
allowing `WhatsApp`, `facebookexternalhit`, `Twitterbot`, `LinkedInBot`,
`TelegramBot` and `Slackbot-LinkExpanding`. A blocked crawler is exactly what
turns a shared link into a grey rectangle.

## The lead sheets

`tools/link_leads.py` writes each lead's live demo URL back into
`exports/leads.xlsx` (all ten sheets, as a clickable hyperlink),
`exports/top_leads.csv` and `exports/all_leads.csv`, as a **Demo Site** column
placed immediately after `Business Name` — the column you want on screen while
the phone is ringing, not forty columns to the right.

Only the 2,000 leads in `top_leads.csv` have a demo, so the cell is blank for
the other 13,603 rather than carrying a placeholder, which keeps the column
filterable.

**The join is the interesting part.** A slug is derived from the business name
and de-duplicated in row order, so it only exists as a by-product of reading
`top_leads.csv` start to finish — fine there, useless for the workbook's other
nine sheets, which are slices of the full 15,603-row export. Measured over the
real data:

| candidate key | collisions in the full export | top-2,000 rows matching exactly one row |
|---|---|---|
| `Google Place ID` | — | 1,674 / 2,000 (only 84% populated) |
| name + city | 366 | — |
| name + city + phone | 48 | 2,000 / 2,000 |
| name + city + phone + address + score | **3** | **2,000 / 2,000** |

The last one is used. The check that it worked is not the row count but the
*shape*: the per-vertical sheets come out at 1,059 / 344 / 371 / 127 / 99,
which sums to exactly 2,000 and matches the lead distribution the templates
were sized against.

Re-run it after any `export.py`, which rebuilds the sheets from the database
and drops the column. It is idempotent — a second run replaces the column
rather than adding another — and it writes `.bak` copies first unless given
`--no-backup`.

## The catalogue

`catalogue.html` (in `demosites/`, not in `out/`) is the lead list with the
demo attached, not a thumbnail gallery: search by name/city/area/category, filter by vertical and segment, sort
by score or review count, and one tap to **Open demo**, **WhatsApp** or **Call**.
The scraper's own `Pitch Angle` is printed on each card so the opening line is on
screen while the phone rings.

Once `--base-url` is set, **Open demo** goes to the live URL rather than a
`file://` path — the prospect's exact page, not a local approximation of it —
and each card grows a **Copy link** button that puts that URL on the clipboard
for the WhatsApp thread already open on the other screen.

## Verification

`tools/verify.py` drives a real browser and fails the build on any of:

- **network** — any request leaving the document
- **js** — uncaught page errors or console errors
- **a11y** — h1 count, landmarks, duplicate ids, unlabelled form controls, controls with no accessible name, `alt`/`role` on artwork
- **images** — every photo actually decodes, has alt text, and declares intrinsic width/height
- **social** — absolute `https` `og:image` with declared dimensions, plus title and description
- **typography** — no headline *or stat value* rendered wider than its own column
- **structure** — JSON-LD parses, meta description, title, favicon, unrendered placeholders
- **layout** — whether the page can actually be **scrolled sideways** at 390px and 1440px

The layout check is deliberately about scrollability rather than `scrollWidth`:
`scrollWidth` counts clipped SVG ink and reports overflow the user can never
reach.

Two of these are only meaningful because of *how* they measure. Images are
force-loaded before being asserted — everything below the fold is
`loading="lazy"`, so at a fixed viewport most tiles have simply never been
requested and a naive `img.complete` check reports a broken path for every one
of them. And the headline probe carries the computed `text-transform`, because
a display face set in uppercase renders ~20% wider than the string
`textContent` hands back; measuring the source text finds nothing.

Current state on the full 2,000-page build:

```
250 pages checked · 0 external requests · all checks passed
10.7 photographs per page · 200 distinct og:image over 250 pages
overflow at 390/768/1024/1440px: 0/250 pages
```

### What the document audit cannot see

`verify.py` reads the page. It cannot tell you that the WhatsApp button
composes the right message, because nothing about that exists in the DOM until
someone clicks — and the enquiry form is pure client-side JS with no backend to
fail loudly, which makes it exactly the kind of thing that breaks silently and
stays broken.

`tools/interact.py` drives five interactions per page and asserts the outcome:
the sticky header engages (proving the shared stylesheet and deferred script
both loaded), the mobile menu reaches `visibility:visible` rather than merely
being translated somewhere, the lightbox opens *and* swaps in the wide
rendition instead of upscaling the 700px tile, the enquiry form composes a
`wa.me` deep link with the typed fields inside it, and an empty form opens
nothing.

```
14 pages driven · nav, lightbox and enquiry form all behave
```

### Bugs this caught

Worth recording, because each was invisible in a screenshot and real on every
affected page:

- **Masonry row gaps** — `grid-row:span 26` crosses 25 *row gaps* as well as 26
  tracks, so with `grid-auto-rows:11px` and a 25px gap each tile resolved to
  911px instead of ~290px. The procedural artwork sliced to fit and hid it
  completely; the moment real photographs went in, every masonry wall was a
  column of vertical ribbons. Spans now follow each photo's own aspect ratio
  with the row gap at zero, so nothing in that layout is cropped at all.
- **Hero padding** — `.hero-ed/.hero-fr/.hero-st` lay out in normal flow and so
  inherited `section`'s padding *on top of* `.hero-inner`'s. 190px of dead
  space above the eyebrow, on half the templates.
- **Wordmark** — the header brand inherited `--font-display`. At 17px the
  `poster` system's Haettenschweiler is an unreadable picket fence. Six
  templates, every page, and it is the first thing on the page.
- **Masonry gallery** — `aspect-ratio` on tiles fought the `grid-row` span, and
  `height:100%` on the SVG resolved against an auto-height `<figure>`. Every
  masonry wall had holes in it.
- **Mobile nav** — closed menu used `translateY(-120%)`, a percentage of its own
  height, which never cleared the header. **121px of links sat over the hero on
  every phone-width page.**
- **Stat values, twice** — `₹3,50,000` at 3rem is one unbreakable token wider
  than its 132px grid track, dragging the whole two-column about section past
  the page. The fix for that (`overflow-wrap:anywhere`) bought the layout at the
  cost of the content: it stopped the overflow by splitting the token, so
  *"Visakhapatnam"* rendered as **"Visakhapatna m"** and read as broken rather
  than as data. Values are now sized by their longest token at build time — a
  number stays big, a place name does not.
- **Header** — long scraped names (*"… | Rental Photo Studio in Nashik | Studio
  for Photoshoot"*) pushed the nav CTA off-screen at 1024px.
- **Wide art bands** — 21:8 banners sliced centred compositions down to flat
  colour slabs.
- **Palette contrast** — four AA failures that looked fine by eye.
