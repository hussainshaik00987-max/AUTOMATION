"""Conversation templates, one pool per trade.

A "trade" is finer-grained than the scraper's vertical: a wedding photographer, a
rental photo studio and a video production house are all ``wedding_photo``, but
they get asked completely different questions, so they get different scripts.
Google's ``Category`` column is mapped onto a trade, and the trade selects both
the script pool and the vocabulary used in the outro.

Rules the copy follows, because these go to real business owners:

* Every claim about *this* business comes from the CSV (price, city, area) or is
  phrased as a range that is true of the trade generally. Nothing specific is
  invented — a prospect who reads a fabricated price in their own demo is lost.
* Prices quoted by the bot are the lead's own ``Price`` when the scraper found
  one, otherwise a market range for the trade and city tier.
* Every script ends on the thing the owner actually cares about: a held date, a
  confirmed order, a booked visit.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Category -> trade
# ---------------------------------------------------------------------------

TRADE_BY_CATEGORY = {
    # wedding_photo
    "wedding photographer": "wedding_photographer",
    "photographer": "wedding_photographer",
    "photography service": "wedding_photographer",
    "portrait studio": "photo_studio",
    "photography studio": "photo_studio",
    "video production service": "video_production",
    "videographer": "video_production",
    # bakery
    "bakery and cake shop": "cake_studio",
    "bakery": "bakery",
    "cake shop": "cake_studio",
    "dessert shop": "dessert",
    "ice cream shop": "dessert",
    "cafe": "bakery",
    "baking supply store": "bakery",
    "chocolate shop": "dessert",
    "patisserie": "cake_studio",
    # wedding_planner
    "wedding planner": "wedding_planner",
    "event planner": "event_planner",
    "event management company": "event_planner",
    "wedding service": "wedding_planner",
    "wedding venue": "event_planner",
    "party planner": "event_planner",
    # fashion
    "boutique": "boutique",
    "bridal shop": "bridal_wear",
    "fashion designer": "fashion_designer",
    "clothing store": "clothing_store",
    "designer clothing store": "boutique",
    "men's clothing store": "clothing_store",
    "women's clothing store": "boutique",
    "sari store": "boutique",
    "tailor": "boutique",
    # interior_arch
    "interior designer": "interior_designer",
    "architect": "architect",
    "architecture firm": "architect",
    "interior architect office": "interior_designer",
    "furniture store": "interior_designer",
}

DEFAULT_TRADE = {
    "wedding_photo": "wedding_photographer",
    "bakery": "cake_studio",
    "wedding_planner": "wedding_planner",
    "fashion": "boutique",
    "interior_arch": "interior_designer",
}


def trade_for(category: str, vertical: str) -> str:
    key = re.sub(r"\s+", " ", (category or "").strip().lower())
    if key in TRADE_BY_CATEGORY:
        return TRADE_BY_CATEGORY[key]
    for needle, trade in TRADE_BY_CATEGORY.items():
        if needle in key:
            return trade
    return DEFAULT_TRADE.get(vertical, "wedding_photographer")


# ---------------------------------------------------------------------------
# Trade vocabulary — drives the outro copy and the fallback price ranges
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Trade:
    label: str            # "wedding photography"
    enquiry: str          # what a new message usually is
    booking: str          # the conversion the bot drives to
    books_line: str       # outro card title — must fit one line at 34px
    books_sub: str        # outro card sub — the qualifying it did first
    asks: str             # outro card: what it answers
    price_low: str        # fallback range, tier-1 city
    price_low_t2: str
    doodle_ink: str


TRADES: dict[str, Trade] = {
    "wedding_photographer": Trade(
        "wedding photography", "a date enquiry", "a held date",
        "Holds the date, books the call", "Straight into your calendar",
        "Dates, packages, albums, travel",
        "₹65,000", "₹40,000", "#d3c8ba"),
    "photo_studio": Trade(
        "studio photography", "a shoot enquiry", "a booked slot",
        "Books the slot", "And takes the advance to hold it",
        "Slots, rates, backdrops, delivery",
        "₹8,000", "₹5,000", "#d3c8ba"),
    "video_production": Trade(
        "film production", "a project enquiry", "a booked call",
        "Books the call", "After it has taken the brief",
        "Formats, crew, edits, timelines",
        "₹85,000", "₹55,000", "#d0c7bd"),
    "cake_studio": Trade(
        "custom cakes", "an order", "a confirmed order",
        "Takes the order", "And the advance that confirms it",
        "Flavours, weight, eggless, delivery",
        "₹950", "₹750", "#d6c9b6"),
    "bakery": Trade(
        "the bakery", "an order", "a confirmed order",
        "Takes the order", "And the advance that confirms it",
        "Menu, timings, bulk orders, delivery",
        "₹850", "₹650", "#d6c9b6"),
    "dessert": Trade(
        "the dessert counter", "an order", "a confirmed order",
        "Takes the order", "And the advance that confirms it",
        "Menu, party packs, delivery, timings",
        "₹700", "₹550", "#d6c9b6"),
    "wedding_planner": Trade(
        "wedding planning", "a wedding enquiry", "a booked meeting",
        "Books the meeting", "After it has asked guest count and budget",
        "Guest count, budget, venues, decor",
        "₹2,50,000", "₹1,50,000", "#d5c6c0"),
    "event_planner": Trade(
        "event management", "an event enquiry", "a booked meeting",
        "Books the meeting", "After it has taken the brief",
        "Dates, headcount, venues, packages",
        "₹1,50,000", "₹90,000", "#d5c6c0"),
    "boutique": Trade(
        "the boutique", "a design enquiry", "a booked fitting",
        "Books the measurement", "Fitting slots, straight into your diary",
        "Fabrics, sizes, stitching time, price",
        "₹4,500", "₹3,200", "#d6c5c2"),
    "bridal_wear": Trade(
        "bridal wear", "a bridal enquiry", "a booked trial",
        "Books the trial", "Room blocked, measurement slot held",
        "Trials, alterations, timelines, price",
        "₹18,000", "₹12,000", "#d6c5c2"),
    "fashion_designer": Trade(
        "the design studio", "a custom enquiry", "a booked consult",
        "Books the consultation", "Straight into your calendar",
        "Custom work, timelines, fittings, price",
        "₹9,500", "₹6,500", "#d6c5c2"),
    "clothing_store": Trade(
        "the store", "a product enquiry", "a held piece",
        "Holds the piece", "In their name, until they can come in",
        "Sizes, stock, alterations, timings",
        "₹2,200", "₹1,600", "#d6c5c2"),
    "interior_designer": Trade(
        "interior design", "a project enquiry", "a booked site visit",
        "Books the free site visit", "Straight into your calendar",
        "Budgets, timelines, scope, materials",
        "₹9,00,000", "₹6,00,000", "#cdc9c0"),
    "architect": Trade(
        "the practice", "a project enquiry", "a booked consult",
        "Books the consultation", "Straight into your calendar",
        "Scope, drawings, approvals, fees",
        "₹150 / sq ft", "₹110 / sq ft", "#cdc9c0"),
}


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------

# A turn is (side, text). "out" = the customer (green, right), "in" = the AI
# answering as the business (white, left). Consecutive "in" turns are how a real
# assistant writes — one short answer, then the next step — and they let the
# video show a second typing indicator without the pacing dragging.

SCRIPTS: dict[str, list[list[tuple[str, str]]]] = {

    "wedding_photographer": [
        [   # date-first
            ("out", "Hi! Saw your work on Instagram 😍 are you free for a wedding in {month}?"),
            ("in", "Hi! Congratulations 🎉 Yes, {name} is taking {month} dates. Which date is the wedding?"),
            ("out", "{date} is the wedding, {date_prev} is the sangeet"),
            ("in", "{date} is open right now 🙌"),
            ("in", "For sangeet + wedding we'd put 2 photographers and 1 cinematographer on it. Want me to send what's included?"),
            ("out", "Yes please. And what does it cost?"),
            ("in", "Coverage starts at {price}. That's candid + traditional, a printed album, and a 3-4 minute teaser film."),
            ("in", "Shall I hold {date} for you and have the team call you tomorrow at 11 AM?"),
            ("out", "Yes please hold it 🙏"),
            ("in", "Done ✅ {date} is held for you and I've saved your number. You'll get a call at 11 AM. Talk soon!"),
        ],
        [   # price-first, cautious buyer
            ("out", "hi, what are your charges for wedding photography?"),
            ("in", "Hi! Depends on how many days and whether you want a film as well. Wedding coverage with {name} starts at {price}."),
            ("out", "It's one day only. {city} itself"),
            ("in", "Perfect — a single day in {city} has no travel or stay cost, so it stays at the base package."),
            ("in", "That's full-day candid + traditional, 350+ edited photos, and an album. Delivery in 4 weeks."),
            ("out", "And a highlight video?"),
            ("in", "A 3-4 min highlight film is an add-on — I'll send exact pricing with the package. What's the date?"),
            ("out", "{date}"),
            ("in", "{date} is free ✅ I'll hold it for 48 hours and send the packages here in 5 minutes. 📄"),
        ],
        [   # late-night, comparison shopper
            ("out", "Hi, are you available on {date}? Sorry for the late message"),
            ("in", "No problem at all — I'm up 🙂 Let me check {date} for you."),
            ("in", "{date} is available. Is it in {city} or outstation?"),
            ("out", "{city}. We're comparing 3-4 photographers, what makes you different?"),
            ("in", "Fair question. Two things: you get the same team you meet — no substitutions on the day — and every wedding gets a written shot list before the date."),
            ("in", "Coverage starts at {price}, and I can send you a full wedding from last season so you're judging real work, not a showreel."),
            ("out", "Yes send that"),
            ("in", "Sending now 📁 And I'll hold {date} for 48 hours so it doesn't go while you decide. That okay?"),
            ("out", "Perfect, thank you 🙏"),
            ("in", "Held ✅ You'll get a call tomorrow at 11 AM. Good luck with the planning!"),
        ],
        [   # pre-wedding shoot
            ("out", "Hi, do you do pre-wedding shoots? 💑"),
            ("in", "Hi! Yes — pre-wedding is a half or full day, in {city} or outstation."),
            ("out", "What locations do you suggest around {city}?"),
            ("in", "Depends on the look you want. We keep a shortlist of 8-10 spots around {city} — palace, water, old-city lanes, and open fields for golden hour."),
            ("in", "Do you want one location or two? Two means an outfit change and a longer day."),
            ("out", "Two. And what's the cost?"),
            ("in", "A two-location pre-wedding day starts at ₹35,000 including edits and a short reel for Instagram."),
            ("in", "Shall I check dates? Golden-hour slots go first, so earlier is better."),
            ("out", "Yes, looking at {date}"),
            ("in", "{date} is free ✅ Holding it. Sending you 3 recent pre-weddings and the location list now 📍"),
        ],
        [   # destination / outstation
            ("out", "Hi, wedding is in Udaipur but we're based in {city}. Do you travel?"),
            ("in", "Hi! Yes — we shoot destination weddings regularly. Congratulations 🎉"),
            ("out", "How does the cost work for outstation?"),
            ("in", "Coverage stays at {price}. On top of that it's actual travel and stay for the crew — we don't mark that up, we just share the bills."),
            ("out", "How many people would come?"),
            ("in", "For a destination wedding, usually 3-4: two photographers, one cinematographer, one assistant. We arrive a day early so nothing depends on a delayed flight."),
            ("in", "Which dates are the functions?"),
            ("out", "{date_prev} to {date}"),
            ("in", "Both are open ✅ Holding them for 48 hours. The team will call you tomorrow at 11 AM to go through the venue."),
        ],
        [   # already has a photographer, wants a film
            ("out", "Hi, photographer is booked already. Do you do only the wedding film?"),
            ("in", "Hi! Yes — we take film-only weddings and work alongside another photo team, that's very common."),
            ("out", "Ok good. What do you deliver?"),
            ("in", "A 3-4 minute highlight film, a 30-40 minute full-length edit, and vertical cuts for Instagram."),
            ("in", "Which date, and how many functions?"),
            ("out", "{date}, three functions"),
            ("in", "Three functions across one day is comfortable with two cinematographers."),
            ("in", "Film-only coverage starts at ₹55,000. Shall I hold {date} while you decide?"),
            ("out", "Yes please hold"),
            ("in", "Held ✅ Sending two recent films so you can hear the edit, not just see it 🎬"),
        ],
    ],

    "photo_studio": [
        [
            ("out", "Hi, do you have a slot this weekend for a family shoot? 📸"),
            ("in", "Hi! Yes — Saturday has 12 PM and 4 PM open, Sunday has 11 AM. How many people?"),
            ("out", "6 adults 2 kids"),
            ("in", "8 people fits comfortably. I'd suggest the 4 PM Saturday slot — softer light and the kids are usually fresher."),
            ("in", "It's a 90-minute session starting at {price}, includes 25 edited photos."),
            ("out", "Do you have backdrops?"),
            ("in", "Yes — 6 backdrops, plus a natural-light corner. We're at {area}, parking in the building."),
            ("in", "Shall I book Saturday 4 PM for you?"),
            ("out", "Yes book it 👍"),
            ("in", "Booked ✅ Saturday 4 PM, 8 people. I'll send a reminder on Friday with the location pin. See you!"),
        ],
        [   # product / e-commerce shoot
            ("out", "Hi, I need product photos for my online store. Do you shoot products?"),
            ("in", "Hi! Yes — white background, lifestyle and model shots. What's the product?"),
            ("out", "Handmade jewellery. About 40 pieces"),
            ("in", "Jewellery needs careful lighting — we shoot it on a macro setup so the stones don't blow out."),
            ("in", "For 40 pieces it's usually 2 angles each, done in one day."),
            ("out", "What would that cost?"),
            ("in", "Product shoots start at ₹250 per image with white background and basic retouch. 40 pieces × 2 angles works out well below the per-image rate."),
            ("in", "Shall I block a day this week? Send 2-3 sample pieces first so we can test the lighting."),
            ("out", "Yes, Thursday works"),
            ("in", "Blocked ✅ Thursday. Drop the samples any time before then — we'll have the setup ready. 📷"),
        ],
        [   # maternity / newborn / portfolio
            ("out", "Hi, do you do maternity shoots? 🤰"),
            ("in", "Hi! Congratulations 🎉 Yes — maternity in studio or outdoor, and newborn once the baby arrives."),
            ("out", "Studio. I'm in my 7th month"),
            ("in", "7-8 months is exactly the right window, so the timing is good."),
            ("in", "It's a 2-hour session, 2-3 outfit changes, starting at {price}. Outfits are available at the studio if you'd rather not carry any."),
            ("out", "Oh that helps. Which days are free?"),
            ("in", "This week we have Wednesday 11 AM and Saturday 10 AM. Mornings are better — the studio light is softer and it's less tiring."),
            ("out", "Saturday 10 AM"),
            ("in", "Booked ✅ Saturday 10 AM. I'll send the outfit options and a short what-to-bring list tomorrow. Take care! 💛"),
        ],
    ],

    "video_production": [
        [
            ("out", "Hi, we need a brand film for our company. Do you do corporate work?"),
            ("in", "Hi! Yes — brand films, product videos and founder interviews. What's the film for?"),
            ("out", "Website + investor pitch. About 2 minutes"),
            ("in", "Got it. A 2-minute brand film is typically a 1-day shoot with a 3-person crew, scripted before we roll."),
            ("in", "That's from {price} including script, shoot, edit, colour and licensed music."),
            ("out", "How long does it take?"),
            ("in", "3 weeks: script and approval in week 1, shoot in week 2, edit and 2 rounds of changes in week 3."),
            ("in", "Can I set up a 20-minute call tomorrow at 4 PM to take the brief properly?"),
            ("out", "4 PM works"),
            ("in", "Booked ✅ Tomorrow 4 PM. I'll send 3 recent brand films before the call so you can point at what you like."),
        ],
        [   # social / reels retainer
            ("out", "Hi, do you do monthly reels? We need content for Instagram"),
            ("in", "Hi! Yes — monthly retainers are how most of our brand clients work with us."),
            ("out", "How many reels per month and what's the cost?"),
            ("in", "The common one is 8 reels a month, shot in a single day and edited through the month so your feed stays consistent."),
            ("in", "That starts at {price} a month including shoot, edit, captions and thumbnails."),
            ("out", "One shoot day for 8 reels? Is that enough?"),
            ("in", "It is if we plan properly — we build the 8 concepts and a shot list before the day, so nothing is decided on set."),
            ("in", "Can I book a 20-minute call tomorrow at 4 PM to see your feed and suggest the first month's plan?"),
            ("out", "Sure, 4 PM"),
            ("in", "Booked ✅ Tomorrow 4 PM. Send your handle here and I'll come to the call with ideas, not questions. 🎬"),
        ],
    ],

    "cake_studio": [
        [
            ("out", "Hi! Do you make photo cakes? Need one for tomorrow evening 🎂"),
            ("in", "Hi! Yes, edible photo print. For tomorrow evening I'd need the order confirmed by 9 PM tonight."),
            ("out", "Ok. 1 kg chocolate truffle. How much?"),
            ("in", "1 kg chocolate truffle with photo print is {price}. Eggless is the same price."),
            ("out", "Eggless please 🙏 Do you deliver to {area}?"),
            ("in", "Yes — {area} delivery is ₹60 and it goes out 2 hours before your slot so it arrives cold. 🚚"),
            ("in", "Shall I confirm 1 kg eggless chocolate truffle with photo, delivered tomorrow 7 PM?"),
            ("out", "Yes confirm 👍"),
            ("in", "Confirmed ✅ Please send the photo here and the name for the cake. Advance link is on its way. See you tomorrow!"),
        ],
        [
            ("out", "hi price for 2kg cake for birthday"),
            ("in", "Hi! 2 kg starts at {price2} — depends on the flavour and design. Which flavour were you thinking?"),
            ("out", "Red velvet. Something for a 5 year old, unicorn theme 🦄"),
            ("in", "Lovely 🙂 Unicorn themes are fondant work — that's a custom design, so I'll get you an exact quote tonight."),
            ("in", "When is the party, and is eggless needed?"),
            ("out", "Saturday evening. Yes eggless"),
            ("in", "Saturday is doable — fondant needs 48 hours, so I'll block the slot now before it fills."),
            ("out", "Please block it"),
            ("in", "Blocked ✅ 2 kg eggless red velvet, unicorn theme, Saturday. Design options and price coming in a few minutes. 🎨"),
        ],
        [   # wedding / large tiered order
            ("out", "Hi! Do you make wedding cakes? Need a 3-tier for {month} 💒"),
            ("in", "Hi! Congratulations 🎉 Yes — 3 and 4 tier, fondant or cream. {month} is comfortable notice."),
            ("out", "How many people does 3-tier serve?"),
            ("in", "A 3-tier is usually 5-6 kg and serves about 60-70 slices. If it's a cutting cake for photos, we often do 2 real tiers plus a dummy so you're not paying for cake nobody eats."),
            ("out", "That's smart. Price?"),
            ("in", "Tiered cakes start at ₹2,400 per kg for fondant work. The dummy tier is a flat ₹800."),
            ("in", "Delivery to the venue is included within {city}, and we set it up on site."),
            ("out", "Perfect. How do I confirm?"),
            ("in", "50% advance blocks the date. Shall I send the link and three design options to pick from?"),
            ("out", "Yes please 🙏"),
            ("in", "Sent ✅ Once you pick a design I'll lock the {month} date. Congratulations again! 🥂"),
        ],
        [   # urgent, same-day
            ("out", "hi is it possible to get a cake in 3 hours?? forgot my mom's birthday 😅"),
            ("in", "Hi! Don't panic 🙂 Yes — from the ready counter we can do it in 3 hours."),
            ("in", "Chocolate truffle, pineapple, black forest and butterscotch are ready today. Custom designs need 24 hours, ready cakes don't."),
            ("out", "Chocolate truffle, 1kg. Can you write on it?"),
            ("in", "Yes, writing is free and takes 10 minutes. What should it say?"),
            ("out", "Happy Birthday Maa ❤️"),
            ("in", "Noted. 1 kg chocolate truffle, \"Happy Birthday Maa\" — {price}, plus candles and a knife in the box."),
            ("in", "Pickup or delivery to {area}?"),
            ("out", "Delivery please"),
            ("in", "Done ✅ It'll reach {area} within 3 hours. Sending the payment link now — you've saved the day 🎂"),
        ],
    ],

    "bakery": [
        [
            ("out", "Hi, are you open tomorrow? And do you take bulk orders?"),
            ("in", "Hi! Yes, open 9 AM to 9:30 PM daily. Bulk orders need 24 hours' notice. What do you need?"),
            ("out", "60 brownies for an office event"),
            ("in", "60 brownies is comfortable with a day's notice. Boxed in 6s, from {price} per box."),
            ("out", "Can you deliver to {area} by 10 AM?"),
            ("in", "Yes — for a 10 AM delivery we'd bake at 6 AM so they're fresh, and dispatch by 9. Delivery is ₹80."),
            ("in", "Shall I put 60 brownies down for tomorrow 10 AM at {area}?"),
            ("out", "Yes please, and 10 need to be eggless"),
            ("in", "Noted ✅ 50 regular + 10 eggless, labelled separately. Advance link coming now — thank you!"),
        ],
        [   # daily / subscription bread
            ("out", "Hi, is your sourdough available daily?"),
            ("in", "Hi! Sourdough is baked Tuesday, Thursday and Saturday — it's a 24-hour ferment, so we can't do it every day properly."),
            ("out", "Ah ok. Can I pre-order so it doesn't sell out?"),
            ("in", "Yes — standing orders are the best way. It's usually gone by 11 AM otherwise."),
            ("in", "Would you like one loaf on each baking day, or just weekends?"),
            ("out", "Tuesday and Saturday. Whole wheat if you have it"),
            ("in", "We do a whole wheat sourdough on both days. {price} a loaf, kept aside under your name."),
            ("in", "Shall I start the standing order from this Tuesday?"),
            ("out", "Yes please 🙏"),
            ("in", "Done ✅ Tuesday and Saturday, whole wheat, held till 6 PM each day. I'll message you if a day's bake changes. 🥖"),
        ],
    ],

    "dessert": [
        [
            ("out", "Hi! Do you do party orders? Need desserts for 40 people 🍮"),
            ("in", "Hi! Yes — party packs for 20, 40 and 60. For 40 we usually do a 4-flavour mix."),
            ("out", "What's in it and how much?"),
            ("in", "40-person pack starts at {price2} — 4 flavours, individually cupped, served chilled and ready to put out."),
            ("out", "When do you need the order by?"),
            ("in", "48 hours ahead for party packs. When's the event?"),
            ("out", "Sunday evening, at {area}"),
            ("in", "Sunday works and {area} is on our delivery route ✅ Shall I block it?"),
            ("out", "Yes please 🙏"),
            ("in", "Blocked ✅ 40-person pack, Sunday evening. Flavour list coming now so you can pick your 4."),
        ],
        [   # walk-in hours / gifting
            ("out", "Hi, till what time are you open today? And do you do gift boxes? 🎁"),
            ("in", "Hi! Open till 10 PM today. Yes — gift boxes in 6, 12 and 24 pieces."),
            ("out", "12 piece. It's for a client, so it should look premium"),
            ("in", "The 12-piece comes in a rigid box with a ribbon and a card — that's the one people send to clients."),
            ("in", "It's {price}. Would you like the card printed with a message?"),
            ("out", "Yes. \"Thank you for a great year\""),
            ("in", "Noted. Do you want it collected, or delivered to their office?"),
            ("out", "Delivered. It's in {area}"),
            ("in", "Confirmed ✅ 12-piece premium box, printed card, delivered to {area}. Sending the payment link and a photo of the box before it goes out. 🎀"),
        ],
    ],

    "wedding_planner": [
        [
            ("out", "Hi, we're planning a wedding in {month}. Do you handle decor and catering both?"),
            ("in", "Hi! Congratulations 🎊 Yes — {name} does full planning: venue, decor, catering, entertainment and on-day coordination."),
            ("in", "How many guests are you expecting?"),
            ("out", "Around 400. Venue not finalised yet"),
            ("in", "Good — deciding decor before the venue usually costs money later. For 400 in {city} we'd shortlist 3-4 venues that actually suit that headcount."),
            ("in", "What total budget are you working with? It changes the shortlist completely."),
            ("out", "Around 25-30 lakhs"),
            ("in", "That's a comfortable range for 400 guests here. Full planning with us starts at {price}."),
            ("in", "Can I put a 20-minute call with the planner tomorrow at 5 PM?"),
            ("out", "Yes that works"),
            ("in", "Booked ✅ Tomorrow 5 PM. You'll get a reminder here. Congratulations again! 🎉"),
        ],
        [
            ("out", "hello, do you do haldi and mehendi decor only? Not full wedding"),
            ("in", "Hi! Yes, we take individual functions too — haldi, mehendi and sangeet on their own."),
            ("out", "Ok. Haldi at home, around 80 people"),
            ("in", "Home haldi for 80 is our most-asked setup 🌼 Marigold work, seating, a backdrop for photos and the haldi ceremony setup."),
            ("in", "Which date, and is it a terrace, garden or hall?"),
            ("out", "{date}, terrace"),
            ("in", "Terrace is good — we'd do a lighter structure and add drapes for wind. {date} is free."),
            ("out", "What's the cost roughly?"),
            ("in", "Home function decor at that scale typically runs ₹45,000-₹85,000 depending on flowers. I'll send 3 setups at different budgets."),
            ("in", "Shall I hold {date}?"),
            ("out", "Yes hold it please"),
            ("in", "Held ✅ Setups coming in a few minutes. The planner will call you tomorrow at 11 AM."),
        ],
        [   # destination wedding
            ("out", "Hi, we're thinking of a destination wedding. Do you handle that?"),
            ("in", "Hi! Congratulations 🎊 Yes — we've done Udaipur, Jaipur, Goa and Rishikesh. Do you have a place in mind?"),
            ("out", "Not yet. Around 150 guests, {month}"),
            ("in", "150 in {month} is a good size — you get the palace and resort properties without the 400-guest logistics."),
            ("in", "The two things that decide everything are guest travel and how many nights. Are you thinking 2 nights or 3?"),
            ("out", "3 nights probably"),
            ("in", "3 nights lets you spread the functions properly instead of stacking two a day."),
            ("in", "Full destination planning with us starts at {price}, and we handle venue, decor, F&B, guest travel and rooms."),
            ("in", "Can I book a 30-minute call tomorrow at 5 PM to shortlist properties for {month}?"),
            ("out", "Yes, 5 PM works"),
            ("in", "Booked ✅ Tomorrow 5 PM. I'll send 4 properties for {month} before the call so it's a real conversation. 🏰"),
        ],
        [   # small, budget-first
            ("out", "Hi, ours is a small wedding, only 120 people. Is that too small for you?"),
            ("in", "Hi! Not at all — small weddings are often the nicest to plan, because the budget goes into things guests actually notice."),
            ("out", "Good to hear 🙂 We have about 8 lakhs total"),
            ("in", "That's a workable budget for 120 in {city}. Roughly: venue and F&B take 55-60%, decor 20%, and the rest is photography, entertainment and buffer."),
            ("in", "Is the venue booked yet?"),
            ("out", "No, not yet"),
            ("in", "Then that's the first call — venue choice moves the F&B number more than anything else you'll decide."),
            ("in", "Shall I set up a 20-minute call tomorrow at 5 PM? We'll come with 3 venues that fit 120 at your budget."),
            ("out", "Perfect, thank you 🙏"),
            ("in", "Booked ✅ Tomorrow 5 PM. Congratulations again — this is a very doable plan. 🎉"),
        ],
    ],

    "event_planner": [
        [
            ("out", "Hi, do you manage corporate events? We have an annual day coming up"),
            ("in", "Hi! Yes — annual days, offsites, product launches and conferences. How many attending?"),
            ("out", "About 250 employees, in {city}"),
            ("in", "Got it. For 250 in {city} we'd handle venue, stage and AV, F&B, entertainment and on-day crew."),
            ("in", "What's the date, and do you have a venue already?"),
            ("out", "{date}. No venue yet"),
            ("in", "Then venue first — good ones for 250 go 6-8 weeks ahead. Full management starts at {price}."),
            ("in", "Can I book a 20-minute call tomorrow at 4 PM to take the brief?"),
            ("out", "Yes, 4 PM is fine"),
            ("in", "Booked ✅ Tomorrow 4 PM. I'll send 3 venue options for {date} before the call."),
        ],
        [   # milestone birthday / anniversary
            ("out", "Hi, planning my parents' 25th anniversary 🎉 Do you do private parties?"),
            ("in", "Hi! Lovely occasion 💛 Yes — private parties, anniversaries and milestone birthdays."),
            ("out", "About 80 guests. Something elegant, not loud"),
            ("in", "Understood. For 80 we'd do a seated dinner layout, warm lighting, a photo wall of the 25 years, and a live singer rather than a DJ."),
            ("in", "Which date, and do you have a venue?"),
            ("out", "{date}. Thinking a banquet hall in {city}"),
            ("in", "{date} is open with us. Banquet works well — we can bring the whole look in without fighting the weather."),
            ("in", "Packages at this scale start at {price}. Shall I hold {date} and set a call for tomorrow 5 PM?"),
            ("out", "Yes please"),
            ("in", "Held ✅ Tomorrow 5 PM. I'll send two anniversary setups we did recently so you can see the tone. 🕯️"),
        ],
        [   # sangeet / haldi entertainment
            ("out", "Hi, we need someone to manage just the sangeet night. Is that possible?"),
            ("in", "Hi! Yes — single-function management is something we do a lot."),
            ("out", "Sangeet for around 200. Stage, sound, lights, and someone to run the show"),
            ("in", "Got it. That's stage and truss, line array sound, intelligent lighting, an anchor and a floor manager to keep the performances moving."),
            ("in", "Are there family performances? That changes the rehearsal time we plan for."),
            ("out", "Yes, about 8 performances"),
            ("in", "8 is a proper show — we'd do a 2-hour rehearsal in the afternoon so nobody is finding their music at 9 PM."),
            ("in", "Which date is the sangeet?"),
            ("out", "{date_prev}"),
            ("in", "Free ✅ Holding it. Sending the sangeet package and two stage designs now 🎤"),
        ],
    ],

    "boutique": [
        [
            ("out", "Hi, do you stitch custom blouses? 👗"),
            ("in", "Hi! Yes — fully custom, measurement to final fitting. Is it for a wedding or a general occasion?"),
            ("out", "My sister's wedding in {month}"),
            ("in", "Lovely 🙂 For {month} we're comfortable — plain blouses take 10-12 days, hand-work takes 3-4 weeks."),
            ("out", "Aari work. What's the price?"),
            ("in", "Aari work blouses start at {price} and go up with the density of the work."),
            ("in", "We're at {area}, open 11 AM to 8 PM. Shall I book a measurement slot for Saturday 4 PM?"),
            ("out", "Yes please 🙏"),
            ("in", "Booked ✅ Saturday 4 PM. Please bring the fabric and a blouse that fits you well. Sending 3 recent aari designs now 📸"),
        ],
        [   # saw a piece on Instagram
            ("out", "Hi! Saw the green anarkali on your page 😍 is it available?"),
            ("in", "Hi! Yes, that one's in stock. Which size are you looking for?"),
            ("out", "M usually, sometimes L depending on the fit"),
            ("in", "That piece runs slightly fitted at the bust, so M with a small alteration is what most people take. Alterations are free here."),
            ("in", "It's {price}. Would you like to try it, or shall I hold it and courier?"),
            ("out", "I'll come try. Where are you?"),
            ("in", "{area}, {city} — open 11 AM to 8 PM, closed Mondays."),
            ("in", "I'll hold it in your name till tomorrow evening. Any time that suits?"),
            ("out", "Tomorrow around 6"),
            ("in", "Held ✅ Ask for it by your number at the counter. See you at 6! 👗"),
        ],
        [   # alteration / restyle
            ("out", "Hi, do you do alterations? I have my mother's old saree"),
            ("in", "Hi! Yes — and restyling an heirloom saree is one of our favourite things to do 💛"),
            ("out", "I want to turn it into a lehenga. Is that possible?"),
            ("in", "Very possible. A 6-yard silk saree usually gives a full lehenga skirt, and there's often enough border left for the blouse."),
            ("in", "Is the fabric silk or georgette? Silk holds the pleats better."),
            ("out", "Kanjeevaram silk"),
            ("in", "Perfect fabric for it. Kanjeevaram-to-lehenga starts at {price} depending on the lining and how much hand-work you want added."),
            ("in", "It takes 3 weeks with one fitting. Shall I book a consultation on Saturday at 4 PM? Bring the saree so we can see the border."),
            ("out", "Yes, Saturday 4"),
            ("in", "Booked ✅ Saturday 4 PM at {area}. Bring the saree and a petticoat — we'll drape it and show you the plan before anything is cut. ✂️"),
        ],
    ],

    "bridal_wear": [
        [
            ("out", "Hi! Do you have lehengas for a {month} wedding? I'm the bride 👰"),
            ("in", "Hi! Congratulations 🎉 Yes — bridal lehengas, and there's enough time for alterations before {month}."),
            ("in", "Are you looking to buy or rent, and do you have a colour in mind?"),
            ("out", "Buy. Something in red or maroon"),
            ("in", "Red and maroon is our largest section 🌹 Bridal lehengas start at {price}, and heavier hand-work goes higher."),
            ("out", "How many trials do I get?"),
            ("in", "As many as you need — most brides do 2, then 2 alteration fittings. All included."),
            ("in", "Shall I book a trial for Saturday 4 PM? We block the room so it's just you."),
            ("out", "Yes, book it 🙏"),
            ("in", "Booked ✅ Saturday 4 PM at {area}. Bring the heels you'll wear — it changes the fall. See you!"),
        ],
        [   # groom / family shopping
            ("out", "Hi, do you have sherwanis? Wedding is in {month}"),
            ("in", "Hi! Congratulations 🎉 Yes — sherwanis, indo-westerns and bandhgalas, buy or rent."),
            ("out", "Buy. And my brothers need something coordinated too"),
            ("in", "Coordinating the family is the part most people leave too late, so this is good timing."),
            ("in", "We usually put the groom in the strongest piece and keep the brothers one tone quieter so photos don't compete."),
            ("out", "Exactly what I wanted. Price range?"),
            ("in", "Sherwanis start at {price}; the coordinated pieces for family are lower. Alterations included."),
            ("in", "Shall I book a trial for Saturday 4 PM? Bring one brother — we'll build the palette from what suits you both."),
            ("out", "Done, Saturday 4"),
            ("in", "Booked ✅ Saturday 4 PM at {area}. Allow an hour — sherwani trials always take longer than people expect. 🤵"),
        ],
    ],

    "fashion_designer": [
        [
            ("out", "Hi, do you take custom outfits? Need something for a reception"),
            ("in", "Hi! Yes — custom, from sketch to final fitting. When is the reception?"),
            ("out", "In {month}. I want something indo-western"),
            ("in", "Indo-western works beautifully for a reception ✨ We'd sketch 2-3 directions first, then you pick one."),
            ("in", "Custom pieces start at {price}, and it's 4-5 weeks including two fittings."),
            ("out", "That timeline works. Where are you located?"),
            ("in", "{area}, {city} — studio visits by appointment so you get the full time."),
            ("in", "Shall I book a design consultation for Saturday 5 PM?"),
            ("out", "Yes please"),
            ("in", "Booked ✅ Saturday 5 PM. Send any references you've saved — it makes the first sketch much closer. 🎨"),
        ],
        [   # bulk / label enquiry
            ("out", "Hi, we're starting a small label. Do you take design + production?"),
            ("in", "Hi! Yes — we do design, patterns and small-batch production. What are you launching with?"),
            ("out", "6 styles, around 30 pieces each to start"),
            ("in", "180 pieces is a sensible first run — big enough to sell properly, small enough that a mistake isn't expensive."),
            ("in", "Do you have sketches or references already, or are we designing from scratch?"),
            ("out", "References only. We know the vibe, not the details"),
            ("in", "That's the usual starting point. We'd do a sample of each style first, you approve fit and fabric, then we run the batch."),
            ("in", "Design plus sampling starts at {price} per style, and production is quoted per piece once the sample is locked."),
            ("in", "Can I book a studio consultation for Saturday 5 PM to go through the references?"),
            ("out", "Yes, that works"),
            ("in", "Booked ✅ Saturday 5 PM. Send the references here beforehand and I'll have fabric options ready. 🧵"),
        ],
    ],

    "clothing_store": [
        [
            ("out", "Hi, do you have this in L? Saw it on your Instagram"),
            ("in", "Hi! Send me the screenshot and I'll check stock right now 📸"),
            ("out", "Sent"),
            ("in", "Got it — L is in stock, and we have it in two more colours."),
            ("in", "It's {price}. Alterations are free in-store if the fit needs adjusting."),
            ("out", "Can you hold it? I'll come tomorrow evening"),
            ("in", "Yes — I'll hold it in your name until tomorrow 9 PM. We're at {area}, open till 9:30."),
            ("out", "Perfect 👍"),
            ("in", "Held ✅ Ask for it at the counter by your number. See you tomorrow!"),
        ],
        [   # exchange / sizing
            ("out", "Hi, I bought a shirt last week but the size is wrong. Can I exchange?"),
            ("in", "Hi! Yes — exchange within 15 days with the bill, and the tags still on."),
            ("out", "Bill is on WhatsApp, is that ok?"),
            ("in", "That's fine, a photo of the bill works."),
            ("in", "Which size do you need instead? I'll check stock before you travel."),
            ("out", "I need L instead of M"),
            ("in", "L is in stock in that style ✅ I'll keep it aside so it isn't sold in the meantime."),
            ("in", "We're at {area}, open till 9:30. Coming today or tomorrow?"),
            ("out", "Today evening"),
            ("in", "Kept aside ✅ Ask for the exchange counter and mention your number. See you this evening!"),
        ],
    ],

    "interior_designer": [
        [
            ("out", "Hi, we just got possession of a 3BHK in {area}. Do you do full interiors?"),
            ("in", "Hi! Congratulations on the new home 🏡 Yes — turnkey, design to handover."),
            ("out", "What would a 3BHK roughly cost?"),
            ("in", "In {city}, essentials — modular kitchen, wardrobes, TV unit — run ₹8-12L. Full turnkey with ceilings, lighting and furniture is ₹18-25L."),
            ("out", "And how long does it take?"),
            ("in", "45-60 days from design sign-off. You see a full 3D and approve it before anything is cut."),
            ("in", "Can our designer do a site visit this Saturday? It's free and takes about 40 minutes."),
            ("out", "Saturday 11 AM works"),
            ("in", "Booked ✅ Saturday 11 AM at {area}. You'll get the designer's number a day before. See you then!"),
        ],
        [
            ("out", "Hi, only kitchen and wardrobes. Not full house. Do you take small projects?"),
            ("in", "Hi! Yes — modular kitchen and wardrobes on their own is a very common scope for us."),
            ("out", "Ok, what does that cost?"),
            ("in", "A modular kitchen typically runs ₹2.5-4.5L depending on shutters and hardware; wardrobes are ₹1,600-2,400 per sq ft."),
            ("in", "Do you have the floor plan? Even a photo of it lets us give a real number instead of a range."),
            ("out", "Yes I'll send it"),
            ("in", "Perfect. Once I have it you'll get a quote in 24 hours, with the hardware brands written down."),
            ("in", "Shall I book a free site measurement for Saturday 11 AM?"),
            ("out", "Yes please 🙏"),
            ("in", "Booked ✅ Saturday 11 AM. Sending 3 recent kitchens in {city} so you can see the finish level. 📐"),
        ],
        [   # commercial / office fitout
            ("out", "Hi, we need an office interior done. 2,200 sq ft in {area}"),
            ("in", "Hi! Yes — we do commercial fitouts. Is it a bare shell or an existing office being redone?"),
            ("out", "Bare shell. Around 30 seats plus 2 cabins and a meeting room"),
            ("in", "Clear brief, thank you. At 2,200 sq ft, 30 seats plus 2 cabins and a meeting room is a comfortable fit without it feeling packed."),
            ("in", "Office fitouts in {city} run ₹1,600-2,600 per sq ft depending on ceiling, HVAC and furniture spec."),
            ("out", "What about timeline? We need to move in 2 months"),
            ("in", "2 months is achievable on a bare shell if drawings are signed off in week 1 and the long-lead items — HVAC and workstations — are ordered immediately."),
            ("in", "Can our team do a site visit this Saturday at 11 AM and give you a dated plan against that move-in?"),
            ("out", "Yes, Saturday 11"),
            ("in", "Booked ✅ Saturday 11 AM at {area}. Please keep the floor plate drawing handy if you have it. 🏢"),
        ],
    ],

    "architect": [
        [
            ("out", "Hi, we have a 30x40 plot in {city}. Looking for an architect for a residence"),
            ("in", "Hi! Yes — 30x40 residences are a scope we do often. Is it G+1 or G+2?"),
            ("out", "G+2. Ground floor for parking and one rental unit"),
            ("in", "Understood — that changes the staircase and setbacks, so it's worth resolving early."),
            ("in", "Our fee for a residence is {price}, covering concept, drawings, approval set and site coordination."),
            ("out", "Do you handle the approvals also?"),
            ("in", "Yes — we prepare the sanction set and coordinate the submission. Sanction timelines depend on the authority, and we track it for you."),
            ("in", "Can I book a 30-minute consultation on Saturday at 11 AM? Bring the plot documents if you have them."),
            ("out", "Yes, Saturday works"),
            ("in", "Booked ✅ Saturday 11 AM. Sending two recent G+2 residences so you can see how we handle a similar plot. 📐"),
        ],
        [   # renovation / vertical extension
            ("out", "Hi, we want to add a floor to our existing house. Do you take renovation work?"),
            ("in", "Hi! Yes. The first question with a vertical extension is always whether the existing foundation and columns can carry another floor."),
            ("out", "House is about 18 years old. Is that a problem?"),
            ("in", "Not necessarily — but it does need a structural check before any design work, otherwise you're drawing something that can't be built."),
            ("in", "Do you have the original structural drawings?"),
            ("out", "Somewhere, yes. I can look"),
            ("in", "That would save time and money. If they're missing we can still assess it on site, it just takes an extra visit."),
            ("in", "Our fee for a residential extension is {price}, covering assessment, drawings, the approval set and site coordination."),
            ("in", "Shall I book a consultation for Saturday 11 AM? Bring whatever documents you find."),
            ("out", "Yes, Saturday"),
            ("in", "Booked ✅ Saturday 11 AM. Even a photo of the old drawings before then helps us come prepared. 📐"),
        ],
    ],
}


# ---------------------------------------------------------------------------
# Slot filling
# ---------------------------------------------------------------------------

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]

# Indian wedding season skews Nov-Feb; a demo that says "a wedding in July" reads
# wrong to anyone in the trade.
_WEDDING_MONTHS = ["November", "December", "January", "February", "April"]


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def build_slots(biz, rng: random.Random) -> dict[str, str]:
    """Values substituted into a script. Real data wherever the CSV has it."""
    trade = TRADES[trade_for(biz.category, biz.vertical)]

    month = rng.choice(_WEDDING_MONTHS) if biz.vertical in ("wedding_photo", "wedding_planner") \
        else rng.choice(_MONTHS)
    day = rng.randrange(8, 26)

    # The lead's own scraped price beats any invented range.
    if biz.price and biz.price.get("display"):
        unit = biz.price.get("unit") or ""
        price = biz.price["display"] + (f" {unit}" if unit and unit != "onwards" else "")
    else:
        price = trade.price_low if biz.city_tier == 1 else trade.price_low_t2

    return {
        "name": biz.display_name,
        "city": biz.city or "the city",
        "area": biz.area.split(",")[0].strip() if biz.area else (biz.city or "your area"),
        "price": price,
        "price2": price,
        "month": month,
        "date": f"{_ordinal(day)} {month[:3]}",
        "date_prev": f"{_ordinal(day - 1)} {month[:3]}",
    }


_SLOT = re.compile(r"\{(\w+)\}")


def render_turns(biz, rng: random.Random) -> tuple[list[tuple[str, str]], str]:
    """Pick a script for this business and fill its slots. -> (turns, trade_key)."""
    trade_key = trade_for(biz.category, biz.vertical)
    pool = SCRIPTS.get(trade_key) or SCRIPTS[DEFAULT_TRADE[biz.vertical]]
    turns = pool[rng.randrange(len(pool))]
    slots = build_slots(biz, rng)

    def sub(text: str) -> str:
        return _SLOT.sub(lambda m: slots.get(m.group(1), m.group(0)), text)

    return [(side, sub(text)) for side, text in turns], trade_key
