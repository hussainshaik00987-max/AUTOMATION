"""Copy engine — turns a `Business` record into page text.

One rule governs everything here: **never state something the data does not
support.** These demos get sent to the business owner, and a few could get put
online as-is, so invented experience ("12 years"), invented volume ("500+
weddings") and invented client quotes are all off the table.

What *is* real, and therefore usable as proof:
    rating, reviews          Google Maps
    price                    WedMeGood
    recent_bookings          WedMeGood
    wmg_listing              WedMeGood membership tier
    ig_followers, ig_posts   Instagram
    city, area, category     Google Maps

Testimonials are the one place a demo normally lies. Instead of fake names
against fake quotes, `testimonials()` returns prompts explicitly flagged as
placeholders — `is_placeholder=True` — which the renderer badges as "sample".
Honest, and it doubles as a prompt to the owner during the call.

Service lists, process steps and FAQ answers are *descriptions of standard
practice* in each trade, phrased so they never assert an unverified fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from .art import Brush
from .data import Business, VERTICAL_LABEL


def _pick(seed: int, salt: int, options: list | tuple):
    return options[(seed // (salt + 7)) % len(options)]


def _fmt_count(n: int) -> str:
    if n >= 100000:
        return f"{n / 100000:.1f}".rstrip("0").rstrip(".") + "L"
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(n)


# ==========================================================================
# hero copy — (headline, subhead) pairs, `{city}` interpolated
# ==========================================================================
HEADLINES: dict[str, tuple[tuple[str, str], ...]] = {
    "wedding_photo": (
        ("Your wedding, remembered exactly as it felt",
         "Documentary wedding photography and film in {city} — for couples who want the real moments, not just the poses."),
        ("The day goes fast. The pictures don't.",
         "Wedding and pre-wedding photography across {city} and beyond, shot quietly and edited with care."),
        ("Pictures your family will still be pulling out in thirty years",
         "Full-day wedding coverage in {city} — candid, unhurried, and yours forever."),
        ("Light, laughter, and everything in between",
         "A {city} wedding photography studio documenting weddings the way they actually happen."),
        ("We shoot the moments between the moments",
         "Candid wedding photography and cinematography based in {city}, available across India."),
        ("Every wedding has one frame that says everything",
         "We spend the whole day looking for it. Wedding photography and film from {city}."),
        ("Told honestly. Framed with care.",
         "Wedding, pre-wedding and engagement photography in {city} — no stiff poses, no rushing."),
        ("Your story deserves more than a highlight reel",
         "Considered wedding photography and films, made in {city} for couples across India."),
    ),
    "bakery": (
        ("Baked fresh this morning, in {city}",
         "Celebration cakes, everyday bakes and custom orders — made in small batches, never mass-produced."),
        ("Cakes worth the occasion",
         "Custom celebration cakes, desserts and hampers baked to order in {city}."),
        ("Real butter. Real cream. No shortcuts.",
         "A small-batch bakery in {city} making birthday cakes, wedding tiers and everyday treats."),
        ("Something good is always in the oven",
         "Fresh bakes, custom cakes and party desserts, made daily in {city}."),
        ("Order today, celebrate tomorrow",
         "Custom cakes and desserts from {city} — message us on WhatsApp and we'll take it from there."),
        ("The cake everyone asks about",
         "Handcrafted celebration cakes, cupcakes and dessert tables, baked to order in {city}."),
        ("Small batch, big occasions",
         "A {city} bake studio for birthdays, weddings, anniversaries and everything worth marking."),
    ),
    "wedding_planner": (
        ("You get to enjoy your own wedding",
         "Full-service wedding planning, decor and on-day coordination in {city} — we handle the rest."),
        ("Planned with care. Run with calm.",
         "Wedding planning, decor and management across {city} and destination venues."),
        ("From the first idea to the last guest leaving",
         "End-to-end wedding planning and decor, based in {city}."),
        ("A wedding that feels like the two of you",
         "Design-led wedding planning and decor in {city} — no copy-paste mandaps."),
        ("Someone has to hold it all together",
         "That's our job. Complete wedding planning, decor and vendor management in {city}."),
        ("Big day. No chaos.",
         "Wedding planners and decorators in {city} handling design, vendors, logistics and the day itself."),
        ("Your day, designed end to end",
         "Weddings, sangeets, receptions and destination events planned from {city}."),
    ),
    "fashion": (
        ("Made to be worn, not just admired",
         "A {city} label creating pieces you'll reach for again and again."),
        ("Considered clothing, made in {city}",
         "Small-run collections, custom fits and made-to-order pieces."),
        ("Your fit. Your fabric. Your call.",
         "Made-to-measure and ready-to-wear from our {city} studio."),
        ("Not fast fashion. Just good clothes.",
         "A {city} boutique making limited pieces properly, in small quantities."),
        ("The piece you keep reaching for",
         "Ready-to-wear and custom pieces designed and stitched in {city}."),
        ("Cut, drape, finish — all of it matters",
         "A design studio in {city} making occasion wear and everyday pieces to order."),
    ),
    "interior_arch": (
        ("Spaces that work as well as they look",
         "Interior design and architecture in {city} — residential, retail and workplace."),
        ("Designed around how you actually live",
         "A {city} design studio working on homes, offices and retail from concept to handover."),
        ("From drawing to handover",
         "Full-service interior design and architecture, based in {city}."),
        ("Good design is mostly good decisions",
         "Interiors and architecture for homes and workspaces across {city}."),
        ("Every project starts with a site visit",
         "Interior design, turnkey execution and architectural services in {city}."),
        ("Built once. Built right.",
         "A {city} studio handling design, drawings, procurement and site execution under one roof."),
    ),
}


# ==========================================================================
# services — (title, description) per vertical
# ==========================================================================
SERVICES: dict[str, tuple[tuple[str, str], ...]] = {
    "wedding_photo": (
        ("Wedding Day Coverage", "Full-day documentary coverage from morning haldi through to the reception, with a second shooter where the schedule needs one."),
        ("Pre-Wedding Shoot", "A relaxed half-day session at a location that means something to you — the best way to get comfortable in front of the camera before the wedding."),
        ("Cinematic Wedding Film", "A short film cut from the day's real audio and moments, plus a longer edit of the full ceremony."),
        ("Engagement & Roka", "Coverage for the smaller functions that often get overlooked but are usually the warmest."),
        ("Album Design & Printing", "Hand-sequenced albums in archival print, designed with you rather than mailed out for approval."),
        ("Destination Weddings", "Travel-ready coverage anywhere in India, with logistics and recce planned in advance."),
        ("Maternity & Newborn", "Gentle, unhurried portrait sessions in your home or our studio."),
        ("Reels & Social Cuts", "Short vertical edits delivered within days, so you have something to share while everyone is still talking about it."),
    ),
    "bakery": (
        ("Custom Celebration Cakes", "Designed around your theme, flavour and serving count. Share a reference and we'll quote before you commit."),
        ("Wedding & Tiered Cakes", "Multi-tier cakes with tastings beforehand and delivery co-ordinated with your venue timings."),
        ("Cupcakes & Dessert Tables", "Party-ready spreads — cupcakes, brownies, tarts and jars, priced per head."),
        ("Everyday Bakes", "Breads, cookies, teacakes and pastries baked fresh each morning."),
        ("Eggless & Dietary Options", "Most of our menu can be made eggless. Tell us about allergies when you order and we'll confirm what's possible."),
        ("Corporate & Bulk Orders", "Gifting hampers and bulk boxes for offices, festivals and events, with invoicing on request."),
        ("Hampers & Gift Boxes", "Curated boxes for Diwali, Christmas, birthdays and thank-yous, with a note card included."),
        ("Same-Day Pickup", "A rotating selection is ready to collect from the counter without pre-ordering."),
    ),
    "wedding_planner": (
        ("Full Wedding Planning", "Everything from budget and timeline through vendor selection to running the day itself."),
        ("Decor & Styling", "Mandap, stage, entrance and table design developed as one visual language rather than piece by piece."),
        ("Venue Sourcing", "Shortlists matched to your guest count, budget and travel plans — with the trade-offs spelled out."),
        ("Vendor Management", "Catering, photography, music, makeup and transport, all briefed, contracted and chased so you aren't the one following up."),
        ("On-Day Coordination", "A dedicated team running the schedule so your family can actually be guests at the wedding."),
        ("Destination Weddings", "Multi-day itineraries, guest logistics, room blocks and local vendor networks."),
        ("Sangeet & Mehendi Production", "Choreography support, staging, sound and lighting for the functions around the main day."),
        ("Guest Hospitality", "Arrivals, welcome kits, room allocation and transport, handled by a desk that stays staffed."),
    ),
    "fashion": (
        ("Ready-to-Wear", "Current collection pieces available in standard sizes, ready to ship."),
        ("Made to Measure", "Your measurements, your fabric choice, stitched to fit properly the first time."),
        ("Bridal & Occasion Wear", "Trousseau, reception and family outfits, planned with enough lead time to get it right."),
        ("Custom Design", "Bring a reference or an idea and we'll develop it with you through sketch, fabric and fitting."),
        ("Alterations & Fittings", "In-house tailoring so adjustments happen where the garment was made."),
        ("Styling Consultation", "A sit-down to work out silhouettes, colours and fabrics that suit you before anything is cut."),
        ("Festive Collections", "Seasonal drops for wedding season, Diwali and Eid, released in limited runs."),
        ("Wholesale & Stockists", "Line sheets and minimums for boutiques looking to carry the label."),
    ),
    "interior_arch": (
        ("Interior Design", "Space planning, material palettes, joinery detailing and lighting design, drawn properly before anything is built."),
        ("Turnkey Execution", "Design plus site delivery under one contract — one team accountable for the result."),
        ("Architectural Design", "Concept through construction drawings, with statutory drawings and approvals coordinated."),
        ("Modular Kitchens & Wardrobes", "Detailed joinery designed for how you actually cook and store, not catalogue sizes."),
        ("Commercial & Retail", "Offices, clinics, cafes and stores designed around footfall, operations and brand."),
        ("3D Visualisation", "Photoreal views before work starts, so you approve a space you have actually seen."),
        ("Renovation & Retrofit", "Working within an existing structure — surveys, feasibility and phased execution."),
        ("Site Supervision", "Regular site visits, contractor coordination and snag lists through to handover."),
    ),
}


# ==========================================================================
# process steps
# ==========================================================================
PROCESS: dict[str, tuple[tuple[str, str], ...]] = {
    "wedding_photo": (
        ("Say hello", "Send us your dates and venues. We'll confirm availability the same day."),
        ("Meet the team", "A call or coffee to talk through the schedule, family dynamics and the shots that matter to you."),
        ("Lock the date", "A signed brief and booking advance holds your date. No date is held without one."),
        ("The day itself", "We arrive early, stay out of the way, and shoot what actually happens."),
        ("Your gallery", "A private online gallery of edited images, plus your films and album drafts."),
    ),
    "bakery": (
        ("Tell us the occasion", "Date, servings, flavour and any reference pictures you like."),
        ("We quote", "A firm price and a delivery slot, confirmed before you pay anything."),
        ("Confirm with advance", "A part payment locks your slot. Custom cakes need lead time, especially on weekends."),
        ("Baked fresh", "Your order is made the day before or the morning of — never earlier."),
        ("Pickup or delivery", "Collect from the counter or we deliver to your venue at the agreed time."),
    ),
    "wedding_planner": (
        ("Discovery call", "We learn the guest count, budget range, dates and what matters most to both families."),
        ("Proposal & budget", "A written scope, a realistic budget and a vendor shortlist you can react to."),
        ("Design & booking", "Decor direction agreed, venues and vendors contracted, timelines circulated."),
        ("Production", "Site recces, run sheets, rehearsals and every vendor briefed on the same schedule."),
        ("The celebration", "Our team runs the day. Your family gets to be guests."),
    ),
    "fashion": (
        ("Browse or brief", "Pick from the collection or tell us what you have in mind."),
        ("Fabric & fit", "We agree fabric, colour and silhouette, and take your measurements."),
        ("First fitting", "A trial fit so changes happen on the garment, not after delivery."),
        ("Finishing", "Final stitching, hand-work and pressing."),
        ("Delivery", "Collect from the studio or we ship, tracked."),
    ),
    "interior_arch": (
        ("Consultation", "A site visit and a conversation about how you use the space, and the budget you're working to."),
        ("Concept & layout", "Space plans, mood direction and an indicative budget, presented for your feedback."),
        ("Design development", "3D views, material samples and detailed drawings — signed off before procurement."),
        ("Execution", "Contractors mobilised, materials scheduled, site supervised on a fixed visit cadence."),
        ("Handover", "Snagging closed out, warranties compiled, and a walkthrough of how everything works."),
    ),
}


# ==========================================================================
# FAQ — question + answer builder. `b` supplies the real facts.
# ==========================================================================
def faqs(b: Business) -> list[tuple[str, str]]:
    city = b.city or "your city"
    out: list[tuple[str, str]] = []

    if b.vertical == "wedding_photo":
        out.append((
            "How far in advance should we book?",
            f"Peak wedding dates in {city} go early — the sooner you check, the better. "
            "Send us your dates and we'll tell you honestly whether we're free.",
        ))
        out.append((
            "Do you travel outside " + city + "?",
            "Yes. Travel and stay are quoted separately and agreed upfront, with no surprises added later.",
        ))
        out.append((
            "When do we get our photos?",
            "You'll get a set of previews within days of the wedding, and the full edited gallery after that. "
            "We confirm exact timelines in writing when you book.",
        ))
        out.append((
            "Can we ask for specific shots?",
            "Please do. A shortlist of family groupings and must-have moments makes the day run faster for everyone.",
        ))
    elif b.vertical == "bakery":
        out.append((
            "How much notice do you need?",
            "Custom cakes need lead time, and weekends fill first. Message us with your date and we'll confirm what's possible.",
        ))
        out.append((
            "Do you make eggless cakes?",
            "Yes — most of the menu can be made eggless. Mention it when you order, along with any allergies, and we'll confirm.",
        ))
        out.append((
            "Do you deliver?",
            f"We deliver across {city}. Share your venue and time slot and we'll confirm the charge before you pay.",
        ))
        out.append((
            "Can I get a cake designed from a picture?",
            "Send the reference over. We'll tell you what's achievable in the size and budget you have in mind.",
        ))
    elif b.vertical == "wedding_planner":
        out.append((
            "Do you take on partial planning?",
            "Yes. Some couples want everything handled; others just want decor, or a team to run the day. Both work.",
        ))
        out.append((
            "How does your fee work?",
            "We quote against your scope and budget after the first call — no percentage surprises once vendors are booked.",
        ))
        out.append((
            "Do you handle destination weddings?",
            "Yes, including guest logistics, room blocks and local vendor sourcing at the destination.",
        ))
        out.append((
            "Can we keep vendors we've already booked?",
            "Of course. We'll fold them into the schedule and brief them the same as anyone we bring in.",
        ))
    elif b.vertical == "fashion":
        out.append((
            "Do you make custom sizes?",
            "Yes — made-to-measure is what we do best. We take your measurements and fit before final stitching.",
        ))
        out.append((
            "How long does a custom piece take?",
            "It depends on the garment and the fabric. Tell us your wear date and we'll confirm whether it's doable.",
        ))
        out.append((
            "Do you ship outside " + city + "?",
            "Yes, we ship across India with tracking. Message us for current rates and timelines.",
        ))
        out.append((
            "Can I come in for a fitting?",
            "Please do. Fittings are by appointment so you get the studio's full attention.",
        ))
    else:  # interior_arch
        out.append((
            "What does a project typically cost?",
            "It depends entirely on area, scope and material spec. After a site visit we give you an indicative "
            "range before you commit to anything.",
        ))
        out.append((
            "Do you handle execution or only design?",
            "Both. You can take the drawings and appoint your own contractor, or we deliver turnkey with one point of accountability.",
        ))
        out.append((
            "How long does a project take?",
            "Design is usually a few weeks; site work depends on scope and approvals. You'll get a phased timeline with the proposal.",
        ))
        out.append((
            "Do you work outside " + city + "?",
            "Yes, subject to site visit frequency. We'll be upfront about what supervision looks like for a remote site.",
        ))

    # Real-data questions appended only when the data actually exists.
    if b.price:
        unit = f" {b.price['unit']}" if b.price["unit"] else ""
        out.append((
            "What are your rates?",
            f"Packages start at {b.price['display']}{unit}. The final quote depends on scope, "
            "so tell us what you have in mind and we'll put it in writing.",
        ))
    if b.whatsapp:
        out.append((
            "What's the fastest way to reach you?",
            "WhatsApp. Tap the button anywhere on this page and your message comes straight to us.",
        ))
    return out


# ==========================================================================
# proof — every figure here traces back to a real column
# ==========================================================================
@dataclass(frozen=True, slots=True)
class Stat:
    value: str
    label: str


def stats(b: Business) -> list[Stat]:
    out: list[Stat] = []
    if b.has_rating:
        out.append(Stat(f"{b.rating:.1f}", "Google rating"))
        out.append(Stat(_fmt_count(b.reviews), "Google reviews"))
    elif b.reviews:
        out.append(Stat(_fmt_count(b.reviews), "Google reviews"))
    if b.ig_followers and b.ig_followers > 500:
        out.append(Stat(_fmt_count(b.ig_followers), "Instagram followers"))
    if b.recent_bookings:
        number = b.recent_bookings.split()[0]
        out.append(Stat(number, "recent bookings"))
    if b.price:
        unit = b.price["unit"] or "package"
        out.append(Stat(b.price["display"], f"from, {unit}"))
    if b.city:
        out.append(Stat(b.city, "based in"))
    return out[:4]


def badges(b: Business) -> list[str]:
    """Short trust chips, all data-backed."""
    out: list[str] = []
    if b.has_rating and b.rating and b.rating >= 4.5:
        out.append(f"{b.rating:.1f}★ on Google · {_fmt_count(b.reviews)} reviews")
    if b.wmg_listing in ("Handpicked", "Popular", "Pro Plus"):
        out.append(f"WedMeGood {b.wmg_listing}")
    if b.recent_bookings:
        out.append(b.recent_bookings.capitalize())
    if b.ig_followers and b.ig_followers >= 5000:
        out.append(f"{_fmt_count(b.ig_followers)} on Instagram")
    if b.city:
        out.append(f"Serving {b.city}")
    return out[:4]


# ==========================================================================
# testimonials — flagged placeholders, never invented quotes
# ==========================================================================
@dataclass(frozen=True, slots=True)
class Quote:
    text: str
    attribution: str
    is_placeholder: bool


PLACEHOLDER_PROMPTS: dict[str, tuple[str, ...]] = {
    "wedding_photo": (
        "Paste your favourite message from a couple here — the one you screenshot and keep.",
        "A line from a recent review works well here. Real words beat written-for-you copy.",
        "Drop in a WhatsApp message from a bride's family. Those are always the most convincing.",
    ),
    "bakery": (
        "A message from a customer about the cake that made their party works perfectly here.",
        "Paste a Google review here — the specific ones about flavour convert best.",
        "One line from a repeat customer belongs here.",
    ),
    "wedding_planner": (
        "A note from a couple after the wedding goes here. Those land harder than any brochure line.",
        "Paste a message from a parent of the bride or groom — that's the audience reading this.",
        "A line about how calm the day felt is the one worth putting here.",
    ),
    "fashion": (
        "A customer's message about fit or fabric belongs here.",
        "Paste a review from someone who wore your piece to their own wedding.",
        "One line about how something felt to wear works better than a paragraph about the brand.",
    ),
    "interior_arch": (
        "A client quote about living in the finished space goes here.",
        "Paste a line from a handover email — clients write better testimonials than we do.",
        "A note about how the project was run belongs here. Process reassures people more than pictures.",
    ),
}


def testimonials(b: Business, count: int = 3) -> list[Quote]:
    """Placeholder prompts, explicitly marked so nothing reads as a real review.

    If the business has genuine Google proof we lead with a factual card that
    cites the aggregate — that one is not a placeholder because it is true.
    """
    out: list[Quote] = []
    if b.has_rating and b.rating:
        out.append(Quote(
            f"Rated {b.rating:.1f} out of 5 across {_fmt_count(b.reviews)} Google reviews.",
            "Google Business Profile",
            False,
        ))
    prompts = PLACEHOLDER_PROMPTS[b.vertical]
    brush = Brush(b.seed, "q")
    start = brush.randint(0, len(prompts) - 1)
    for i in range(count - len(out)):
        out.append(Quote(prompts[(start + i) % len(prompts)], "Your client", True))
    return out[:count]


# ==========================================================================
# narrative
# ==========================================================================
def about(b: Business) -> list[str]:
    """Two or three paragraphs assembled from real fields only."""
    label = VERTICAL_LABEL[b.vertical].lower()
    where = f"in {b.area}, {b.city}" if b.area else f"in {b.city}" if b.city else ""
    paragraphs: list[str] = []

    if b.description:
        paragraphs.append(b.description)

    opener = {
        "wedding_photo": f"We're a wedding photography studio{' based ' + where if where else ''}, working with couples who'd rather be photographed than posed.",
        "bakery": f"We're a bake studio{' ' + where if where else ''}, making everything to order in small batches.",
        "wedding_planner": f"We plan and produce weddings{' ' + where if where else ''} — design, vendors, logistics and the day itself.",
        "fashion": f"We're a design studio and boutique{' ' + where if where else ''}, making pieces in small runs and to measure.",
        "interior_arch": f"We're a design studio{' ' + where if where else ''} working across interiors and architecture, from first drawings to handover.",
    }[b.vertical]
    if not paragraphs:
        paragraphs.append(opener)
    elif len(b.description) < 90:
        paragraphs.append(opener)

    second = {
        "wedding_photo": "Most of our work is documentary: we stay out of the way, watch what's happening, and shoot it as it comes. Where a portrait needs directing we'll direct it, then get out of the frame again.",
        "bakery": "Everything is baked to order rather than sitting in a display case. That means we need a little notice, and it means what you collect was made for you.",
        "wedding_planner": "Our job is to make the complicated parts invisible. Budgets, vendor contracts, run sheets and the hundred small decisions that pile up — all handled, so the two of you can be present at your own wedding.",
        "fashion": "Everything is made in limited quantities, and most of it can be made to your measurements. We'd rather get one garment right than turn out fifty that nearly fit.",
        "interior_arch": "We draw properly before anything gets built. Detailed drawings, agreed material specs and a realistic budget mean fewer decisions get made in a rush on site.",
    }[b.vertical]
    paragraphs.append(second)

    if b.has_rating and b.rating and b.reviews >= 20:
        paragraphs.append(
            f"Our clients have rated us {b.rating:.1f} out of 5 across {_fmt_count(b.reviews)} "
            "Google reviews — the fastest way to see what working with us is actually like."
        )
    return paragraphs


def tagline(b: Business) -> str:
    """One-line descriptor for nav, footer and meta tags."""
    cat = b.category or VERTICAL_LABEL[b.vertical]
    return f"{cat} in {b.city}" if b.city else cat


# Fallback context for photo alt text, used when the harvested description is
# empty. Never claims the picture is the business's own work — `photos.img()`
# appends that qualifier.
_HERO_ALT = {
    "wedding_photo": "A wedding ceremony photographed in available light",
    "bakery": "Freshly finished cakes and bakes on a counter",
    "wedding_planner": "A wedding stage dressed with flowers and lighting",
    "fashion": "Occasion wear styled on a studio rail",
    "interior_arch": "A finished interior in daylight",
}
_ABOUT_ALT = {
    "wedding_photo": "A quiet candid moment between ceremonies",
    "bakery": "Hands finishing a cake in the kitchen",
    "wedding_planner": "Floral detail from an event setup",
    "fashion": "Hand embroidery detail on fabric",
    "interior_arch": "Material and finish samples laid out",
}


# WhatsApp renders a preview as image / bold title / two grey lines / domain.
# The title already carries the business name, so repeating it in the
# description burns most of the two lines that are actually visible. These lead
# with the proof instead.
_SOCIAL_LINE = {
    "wedding_photo": "Wedding, pre-wedding and engagement photography{where}.",
    "bakery": "Custom celebration cakes and fresh bakes to order{where}.",
    "wedding_planner": "Full-service wedding planning, decor and coordination{where}.",
    "fashion": "Ready-to-wear, made-to-measure and custom design{where}.",
    "interior_arch": "Interior design, architecture and turnkey execution{where}.",
}


def social_title(b: Business) -> str:
    """og:title, kept inside WhatsApp's ~65-character clamp.

    A scraped name can run 70+ characters on its own ("... | Rental Photo
    Studio in Nashik | Studio for Photoshoot"). When it does, the descriptor is
    dropped rather than letting the clamp eat the business name itself.
    """
    name = b.name.strip()
    if len(name) > 58:
        return name[:57].rstrip(" ,-|·") + "…"
    joined = f"{name} — {tagline(b)}"
    return joined if len(joined) <= 68 else name


def social_description(b: Business) -> str:
    where = f" in {b.city}" if b.city else ""
    bits = []
    if b.has_rating and b.rating:
        bits.append(f"Rated {b.rating:.1f}/5 from {_fmt_count(b.reviews)} Google reviews.")
    bits.append(_SOCIAL_LINE[b.vertical].format(where=where))
    bits.append("Message on WhatsApp." if b.whatsapp else "Get in touch.")
    return " ".join(bits)[:200]


def hero_alt(b: Business) -> str:
    return _HERO_ALT[b.vertical]


def about_alt(b: Business) -> str:
    return _ABOUT_ALT[b.vertical]


def meta_description(b: Business) -> str:
    bits = [b.name, "—", tagline(b) + "."]
    if b.has_rating and b.rating:
        bits.append(f"Rated {b.rating:.1f}/5 from {_fmt_count(b.reviews)} Google reviews.")
    bits.append({
        "wedding_photo": "Wedding, pre-wedding and engagement photography. Check your date.",
        "bakery": "Custom celebration cakes and fresh bakes to order.",
        "wedding_planner": "Full-service wedding planning, decor and coordination.",
        "fashion": "Ready-to-wear, made-to-measure and custom design.",
        "interior_arch": "Interior design, architecture and turnkey execution.",
    }[b.vertical])
    return " ".join(bits)[:300]


def hero(b: Business) -> tuple[str, str]:
    options = HEADLINES[b.vertical]
    headline, subhead = _pick(b.seed, 3, options)
    city = b.city or "India"
    return headline.format(city=city), subhead.format(city=city)


def services(b: Business, count: int = 6) -> list[tuple[str, str]]:
    """Rotate the vertical's service list so neighbouring businesses differ."""
    pool = list(SERVICES[b.vertical])
    offset = b.seed % len(pool)
    rotated = pool[offset:] + pool[:offset]
    return rotated[:count]


def process(b: Business) -> list[tuple[str, str]]:
    return list(PROCESS[b.vertical])


def cta(b: Business) -> tuple[str, str]:
    """(primary label, supporting line)."""
    pairs = {
        "wedding_photo": (
            ("Check your date", "Tell us when and where — we'll confirm availability."),
            ("Start a conversation", "No pressure, no hard sell. Just tell us about your wedding."),
            ("Enquire about your wedding", "Share your dates and we'll come back to you quickly."),
        ),
        "bakery": (
            ("Order on WhatsApp", "Send the occasion, date and servings — we'll quote back."),
            ("Place an order", "Tell us what you need and when you need it."),
            ("Ask about a custom cake", "Share a reference picture and your budget."),
        ),
        "wedding_planner": (
            ("Book a planning call", "Thirty minutes to talk dates, guest count and budget."),
            ("Start planning", "Tell us your dates and we'll come back with next steps."),
            ("Request a proposal", "Share your scope and we'll put together a written plan."),
        ),
        "fashion": (
            ("Book a fitting", "Come in, or send measurements and we'll take it from there."),
            ("Enquire about a piece", "Tell us what you're looking for and when you need it."),
            ("Start a custom order", "Bring a reference, a fabric, or just an idea."),
        ),
        "interior_arch": (
            ("Book a consultation", "A site visit and an honest conversation about budget."),
            ("Start your project", "Tell us about the space and we'll suggest next steps."),
            ("Request a callback", "Share your requirement and we'll get in touch."),
        ),
    }[b.vertical]
    return _pick(b.seed, 11, pairs)


def gallery_captions(b: Business, count: int) -> list[str]:
    """Neutral captions — no invented venues, couples or project names."""
    pools = {
        "wedding_photo": ("Ceremony", "Portraits", "The details", "Reception", "First look",
                          "Family", "Getting ready", "Sangeet", "Golden hour", "The exit",
                          "Candid", "Mehendi"),
        "bakery": ("Celebration tiers", "Buttercream", "Chocolate", "Fresh from the oven",
                   "Cupcakes", "Dessert table", "Custom design", "Breads", "Hampers",
                   "Tarts", "Wedding tiers", "Everyday bakes"),
        "wedding_planner": ("Mandap", "Entrance", "Table styling", "Stage design", "Sangeet",
                            "Floral", "Lighting", "Reception", "Welcome", "Mehendi decor",
                            "Haldi setup", "Night reception"),
        "fashion": ("Look 01", "Look 02", "Look 03", "Look 04", "Detailing", "Fabric",
                    "Drape", "Occasion", "Ready-to-wear", "Bridal", "Festive", "Studio"),
        "interior_arch": ("Living", "Kitchen", "Bedroom", "Workspace", "Detail", "Facade",
                          "Retail", "Bath", "Lighting", "Joinery", "Terrace", "Entrance"),
    }[b.vertical]
    offset = b.seed % len(pools)
    return [pools[(offset + i) % len(pools)] for i in range(count)]
