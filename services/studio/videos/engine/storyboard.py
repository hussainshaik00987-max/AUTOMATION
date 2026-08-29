"""Turn a lead into a fully-timed scene.

Timing is the whole product here. Two rules drive every number below:

1. **The bot must feel fast, the human must feel human.** The gap before the
   typing indicator is ~0.4s and the indicator itself is capped at 2s, so the
   reply always lands inside three seconds — that *is* the pitch. The customer's
   gaps, by contrast, scale with how much they just had to read.
2. **Label the beat before it happens.** Captions land just before or with the
   message they describe, never after; a caption that explains a bubble the
   viewer has already read is dead weight.

Everything is emitted in milliseconds into a plain dict that runtime.js
interpolates. No timing lives in CSS.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import script as scripts

# ---------------------------------------------------------------------------
# Pacing constants (ms)
# ---------------------------------------------------------------------------

INTRO_HOLD = 2500        # phone settles + headline reads before the first message

AI_GAP = 420             # customer's message lands -> indicator appears
AI_GAP_FOLLOWUP = 300    # between two consecutive bot messages
TYPE_PER_CHAR = 24
TYPE_MIN, TYPE_MAX = 760, 2000
TYPE_MIN_2, TYPE_MAX_2 = 620, 1500      # a follow-up is always quicker

READ_PER_CHAR = 15       # customer reading what the bot just sent
READ_MIN, READ_MAX = 640, 1900
SETTLE = 250             # after a bubble lands, before the next thing starts

CHAT_TAIL_HOLD = 1300    # let the last message breathe before the reveal

REVEAL_HOLD = 3400
CARDS_STAGGER = 560
CARDS_HOLD_AFTER = 1500
CTA_HOLD = 3500

FPS = 30

# Frame geometry
STAGE_W, STAGE_H = 1080, 1920


@dataclass
class ChatItem:
    kind: str                 # "msg" | "typing" | "pill"
    side: str                 # "in" | "out" | "mid"
    at: int
    until: int | None = None
    text: str = ""
    stamp: str = ""
    tick: str = ""            # "" | "sent" | "read"
    tail: bool = False
    cls: str = ""


@dataclass
class Layer:
    id: str
    html: str
    top: int
    t_in: int
    t_out: int
    rise: int = 560
    fall: int = 340
    stagger: int = 90
    travel: int = 26
    blur: float = 8.0
    bar_delay: int = 240
    extra: str = ""


@dataclass
class Scene:
    lead: object
    trade: scripts.Trade
    trade_key: str
    duration: int
    items: list[ChatItem] = field(default_factory=list)
    layers: list[Layer] = field(default_factory=list)
    camera: list[dict] = field(default_factory=list)
    typing_windows: list[tuple[int, int]] = field(default_factory=list)
    clock: str = "11:47"
    stamp: str = "11:47 pm"
    gap: float = 3.0


# ---------------------------------------------------------------------------

def _type_ms(text: str, followup: bool) -> int:
    lo, hi = (TYPE_MIN_2, TYPE_MAX_2) if followup else (TYPE_MIN, TYPE_MAX)
    return max(lo, min(hi, int(len(text) * TYPE_PER_CHAR)))


def _read_ms(text: str) -> int:
    return max(READ_MIN, min(READ_MAX, int(len(text) * READ_PER_CHAR)))


def _stamps(rng: random.Random) -> tuple[str, list[str]]:
    """A late-night start time, and per-message stamps that creep forward.

    11-something PM is doing real work in this video: it is the moment every
    owner recognises, and it makes 'nobody was awake' land without saying it.
    """
    hour = 11
    minute = rng.choice([41, 43, 47, 48, 52, 56])
    clock = f"{hour}:{minute:02d}"
    return clock, [f"{hour}:{minute:02d} pm", f"{hour}:{(minute + 1) % 60:02d} pm"]


def build_chat(lead, rng: random.Random) -> tuple[list[ChatItem], list[tuple[int, int]],
                                                  int, str, list[tuple[int, str]], str]:
    """-> (items, typing_windows, chat_end, clock, beats, trade_key)

    `beats` are (time, kind) markers the caption track hangs off.
    """
    turns, trade_key = scripts.render_turns(lead, rng)
    clock, stamps = _stamps(rng)

    items: list[ChatItem] = []
    typing: list[tuple[int, int]] = []
    beats: list[tuple[int, str]] = []

    # Thread header: the encryption notice and date divider are always there in a
    # real chat, and their absence is the first thing that reads as fake.
    items.append(ChatItem("pill", "mid", at=0, text="Today", cls="pill"))
    items.append(ChatItem(
        "pill", "mid", at=0, cls="pill enc",
        text="Messages are end-to-end encrypted. Only people in this chat can read them."))

    t = INTRO_HOLD
    prev_side = None
    first_reply_at = None
    first_reply_delay = None
    price_at = None
    last_in_at = None

    for idx, (side, text) in enumerate(turns):
        tail = side != prev_side
        stamp = stamps[0] if t < 14000 else stamps[1]

        if side == "out":
            if prev_side is not None:
                t += _read_ms(turns[idx - 1][1])
            items.append(ChatItem("msg", "out", at=t, text=text, stamp=stamp,
                                  tick="read", tail=tail))
            beats.append((t, "customer"))
            t += SETTLE
        else:
            followup = prev_side == "in"
            t += AI_GAP_FOLLOWUP if followup else AI_GAP
            dur = _type_ms(text, followup)
            typing_start, typing_end = t, t + dur
            items.append(ChatItem("typing", "in", at=typing_start, until=typing_end,
                                  tail=True))
            typing.append((typing_start, typing_end))

            msg_at = typing_end
            items.append(ChatItem("msg", "in", at=msg_at, text=text, stamp=stamp,
                                  tail=tail))
            if first_reply_at is None and beats:
                # How long the *viewer* waits between the customer's first
                # message and the first reply — the number the caption quotes.
                first_reply_at = msg_at
                first_reply_delay = msg_at - beats[0][0]
            if price_at is None and "₹" in text:
                price_at = msg_at
            last_in_at = msg_at
            t = msg_at + SETTLE

        prev_side = side

    chat_end = t + CHAT_TAIL_HOLD
    marks = {
        "clock": clock,
        "first_reply_at": first_reply_at or (INTRO_HOLD + 2500),
        "first_reply_delay": first_reply_delay or 2500,
        "price_at": price_at,
        "last_in_at": last_in_at,
    }
    return items, typing, chat_end, clock, beats, (trade_key, marks)


# ---------------------------------------------------------------------------
# Copy layers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _possessive(name: str) -> str:
    """"Kettle Petal's", but "Align Interiors'" — a great many of these names end
    in s, and "Interiors's" on a slide reads as a typo the owner will notice."""
    return name + ("'" if name.rstrip().endswith(("s", "S")) else "'s")


def _head_size(name: str) -> int:
    """Headline size that keeps a business name to two display lines.

    The copy column is 912px wide; Inter at 680 weight averages ~0.55em per
    character, so a 30-character name needs roughly 60px to sit on one line.
    Longest word matters more than total length — a single long word cannot be
    broken and is what actually overflows.
    """
    if not name:
        return 82
    longest = max((len(w) for w in name.split()), default=0)
    budget = max(len(name), longest * 1.9)
    if budget <= 17:
        return 82
    if budget <= 23:
        return 70
    if budget <= 29:
        return 61
    return 54


def _lines(*html: str) -> str:
    return "".join(f'<div data-line>{h}</div>' for h in html)


def _pill(text: str) -> str:
    return f'<div data-line><span class="proof">{text}</span></div>'


def _card(ico_svg: str, title: str, sub: str) -> str:
    return (f'<div class="card" data-line><div class="ico">{ico_svg}</div>'
            f'<div class="ct"><div class="t1">{title}</div>'
            f'<div class="t2">{sub}</div></div></div>')


def build_layers(lead, trade: scripts.Trade, chat_end: int, marks: dict,
                 brand: dict, rng: random.Random) -> tuple[list[Layer], list[dict], int]:
    from . import assets

    L: list[Layer] = []
    name = _esc(lead.display_name)
    full = _esc(lead.name)
    poss = _possessive(name)
    clock = marks["clock"]

    # Openers and reveals are pooled separately from the scripts. Two leads on
    # the same script and the same street should still not open on the same
    # sentence — 859 of these are wedding photographers.
    intro = rng.choice([
        (f"{clock} PM",
         f'A customer messages<br><span class="hi">{name}</span>',
         "Everyone is asleep. The reply still goes out."),
        (f"{clock} PM",
         'This message would<br>normally <span class="hi">wait</span>',
         "Until morning. Or until they message someone else."),
        (f"{clock} PM · New enquiry",
         'A new customer.<br><span class="hi">Right now.</span>',
         f"{name} answers before anyone has even seen it."),
        (f"{clock} PM",
         f'Nobody at <span class="hi">{name}</span><br>is awake',
         "The enquiry gets answered anyway."),
    ])
    # Only the variants that set the name in display type need to shrink for it.
    L.append(Layer(
        id="intro", top=112, t_in=260, t_out=INTRO_HOLD - 100, rise=620, stagger=110,
        extra=f"--head:{_head_size(name if name in intro[1] else '')}px;",
        html=_lines(
            f'<div class="kicker">{intro[0]}</div>',
            f'<div class="head">{intro[1]}</div>',
            f'<div class="sub" style="margin-top:20px">{intro[2]}</div>',
        )))

    # ---- in-chat annotations --------------------------------------------
    # These sit in the band under the phone so they never cover a bubble.
    secs = max(1, round(marks["first_reply_delay"] / 1000))
    L.append(Layer(
        id="ann1", top=1748, t_in=marks["first_reply_at"] + 300,
        t_out=marks["first_reply_at"] + 3100, rise=460, travel=18, stagger=0,
        html=_pill(f"Answered in {secs} seconds")))

    if marks.get("price_at"):
        L.append(Layer(
            id="ann2", top=1748, t_in=marks["price_at"] + 200, t_out=marks["price_at"] + 3000,
            rise=460, travel=18, stagger=0,
            html=_pill("It knows your prices")))

    if marks.get("last_in_at"):
        L.append(Layer(
            id="ann3", top=1748, t_in=marks["last_in_at"] - 500,
            # Clamped to the chat: this pill must be gone before the reveal
            # headline arrives, or two captions share the frame.
            t_out=min(marks["last_in_at"] + 2600, chat_end - 200),
            rise=460, travel=18, stagger=0,
            html=_pill(f"{_esc(trade.booking).capitalize()} — without you")))

    # ---- reveal ----------------------------------------------------------
    t_reveal = chat_end + 500
    reveal = rng.choice([
        ("Nobody typed<br>a single word.", f"{poss} WhatsApp handled all of it."),
        ("That whole chat<br>was automatic.", f"{poss} WhatsApp, answering on its own."),
        ("You were<br>asleep.", f"{poss} WhatsApp still closed it."),
        ("No one saw<br>that enquiry.", f"{poss} WhatsApp did — and booked it."),
    ])
    L.append(Layer(
        id="reveal", top=1206, t_in=t_reveal, t_out=t_reveal + REVEAL_HOLD,
        rise=680, stagger=140,
        html=_lines(
            f'<div class="head">{reveal[0]}</div>',
            '<div class="rule" data-bar style="margin:34px auto"></div>',
            f'<div class="sub">{reveal[1]}</div>',
        )))

    # ---- value cards -----------------------------------------------------
    t_cards = t_reveal + REVEAL_HOLD + 400
    cards = [
        ("bolt", "Replies in seconds", "Every message, 24×7 — including 2 AM"),
        ("chat", "Answers the same 10 questions", _esc(trade.asks) + " — instantly"),
        ("calendar", _esc(trade.books_line), _esc(trade.books_sub)),
        ("globe", "English, हिन्दी, your language", "It replies the way your customer writes"),
        ("inbox", "Nothing gets lost", "Every name and number saved, not buried in DMs"),
    ]
    cards_html = "".join(_card(assets.icon(i), t1, t2) for i, t1, t2 in cards)
    cards_dur = len(cards) * CARDS_STAGGER + CARDS_HOLD_AFTER + 900
    L.append(Layer(
        id="cards", top=622, t_in=t_cards, t_out=t_cards + cards_dur,
        rise=620, stagger=CARDS_STAGGER, travel=44, blur=6,
        html=f'<div class="cards">{cards_html}</div>'))
    L.append(Layer(
        id="cardtitle", top=470, t_in=t_cards - 240, t_out=t_cards + cards_dur,
        rise=560, stagger=0, travel=22,
        html=_lines('<div class="kicker">On your own number</div>')))

    # ---- CTA -------------------------------------------------------------
    t_cta = t_cards + cards_dur + 320
    proof = ""
    if lead.has_rating:
        proof = _pill(f"★ {lead.rating} · {lead.reviews:,} Google reviews")
    elif lead.ig_followers and lead.ig_followers >= 4000:
        proof = _pill(f"{lead.ig_followers:,} followers already asking")
    elif lead.city:
        proof = _pill(f"Built for {_esc(lead.city)}")

    # "Put this on <business name>" scans wrong; the name belongs on its own
    # personalisation line, where it also reads as "this was made for you".
    where = f"{full} · {_esc(lead.city)}" if lead.city else full
    L.append(Layer(
        id="cta", top=596, t_in=t_cta, t_out=t_cta + CTA_HOLD, rise=680, stagger=150,
        html=_lines(
            f'<div class="kicker">{_esc(brand["kicker"])}</div>',
            f'<div class="head" style="margin-top:26px">Put this on<br>'
            f'<span class="hi">your WhatsApp</span></div>',
            f'<div class="sub" style="margin-top:30px">{brand["sub"]}</div>',
        ) + '<div style="height:52px"></div>' + proof
        + f'<div data-line style="margin-top:40px"><span class="cta-btn">{_esc(brand["cta"])}</span></div>'
        + f'<div data-line class="tiny" style="margin-top:40px">Prepared for {where}</div>'))

    duration = t_cta + CTA_HOLD + 700

    # ---- camera ----------------------------------------------------------
    # Poses, in the order the eye is meant to travel: the phone sits low and small
    # under the headline, rises into a full-frame chat, drifts imperceptibly
    # closer while the thread fills, then retreats to make room for the copy.
    camera = [
        # Intro sits the phone low enough that the frame crops it. A brand-new
        # thread is mostly empty wallpaper, and cropping it away is better than
        # holding on 900px of beige while the headline reads.
        {"t": 0,                "s": 0.88, "x": 0, "y": 470,  "o": 0.0, "ease": "outQuint"},
        {"t": 900,              "s": 0.92, "x": 0, "y": 410,  "o": 1.0, "ease": "outQuint"},
        {"t": INTRO_HOLD - 250, "s": 0.92, "x": 0, "y": 410,  "o": 1.0, "ease": "inOutCubic"},
        # Held dead still through the chat. A slow push-in was tempting, but at
        # ~2px/sec it is imperceptible next to bubbles arriving, and it would
        # change the phone's transform on every frame — which alone defeats the
        # duplicate-frame detection that removes ~40% of all captures.
        {"t": INTRO_HOLD + 800, "s": 1.0,  "x": 0, "y": 0,    "o": 1.0, "ease": "linear"},
        {"t": chat_end,         "s": 1.0,  "x": 0, "y": 0,    "o": 1.0, "ease": "inOutCubic"},
        {"t": t_reveal + 500,   "s": 0.58, "x": 0, "y": -330, "o": 1.0, "ease": "inOutCubic"},
        {"t": t_cards - 500,    "s": 0.58, "x": 0, "y": -330, "o": 1.0, "ease": "outQuad"},
        {"t": t_cards + 150,    "s": 0.50, "x": 0, "y": -440, "o": 0.0, "ease": "linear"},
        {"t": duration,         "s": 0.50, "x": 0, "y": -440, "o": 0.0, "ease": "linear"},
    ]
    return L, camera, duration


# ---------------------------------------------------------------------------

def build(lead, brand: dict) -> Scene:
    rng = random.Random(lead.seed)
    items, typing, chat_end, clock, beats, (trade_key, marks) = build_chat(lead, rng)
    trade = scripts.TRADES[trade_key]
    layers, camera, duration = build_layers(lead, trade, chat_end, marks, brand, rng)

    return Scene(
        lead=lead, trade=trade, trade_key=trade_key,
        duration=duration, items=items, layers=layers, camera=camera,
        typing_windows=typing, clock=clock,
    )
