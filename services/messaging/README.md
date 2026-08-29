# messaging

Delivers the demo link after the call. The [voice agent](../voice) does the
talking; this owns everything that happens on the messaging channel afterwards —
the ask, the wait, the reply handling, and the job state that survives a restart
mid-campaign.

```bash
npm install
cp .env.example .env      # add OPENAI_API_KEY
npm start                 # scan the QR with WhatsApp ▸ Linked devices
```

Leave it running for the whole campaign. The agent posts each lead to it the
moment a call hangs up.

---

## What a lead actually receives

```
call ends
   │
   ├─ online ▸ their dp loads ▸ ⌨ typing…
   │      ──▶  "Namaste sir 🙏 Meera bol rahi hoon Sharma IT IT
   │            Solution se — abhi call pe baat hui thi."
   │      ──▶  "Yahan link bhej doon?"      ← sometimes a second bubble
   │   …then offline again
   │
   ├─ replies "haan"        ──▶ ✓✓ read ▸ 20s–2½ min ▸ ⌨ typing… ▸ the link
   ├─ replies "nahi"        ──▶ nothing, ever               (declined)
   ├─ replies "kaun?"       ──▶ ⌨ typing… ──▶ context, then the link
   └─ 30–40 min of silence  ──▶ ⌨ typing… ──▶ the link      (sent anyway)
```

Every one of those messages is dealt from a bank of **thirty** variants, and the
whole sequence is held inside a **09:30–21:30 sending window** — see below.

The link message is the one that sells:

> Ye lijiye Rakesh ji 👇
>
> https://demo.example.com/sites/meridian-studios
>
> Maine ye demo **specially Meridian Studios ke liye banayi hai** — aapke kaam
> aur reviews ke hisaab se. Mobile pe khol ke ek baar dekh lijiye.
>
> Pasand aaye toh batayiega, isme aapke apne photos aur details daal denge. 🙂

…and the demo screenshot rides along as that message's **caption**, so the
picture and the link arrive in one notification rather than two.

`npm run preview` prints all 180 variants rendered with real lead data — that is
the file to read before a campaign and to keep open while editing `messages.js`.
`npm run preview -- --no-name` shows how they read when the call never got the
owner's name.

### Why the link isn't just sent straight away

An unsolicited link as the very first message to a number that has never
messaged you is precisely the pattern WhatsApp's spam detection is built to
catch, and precisely what a person reports. Asking first turns the thread into
something the recipient opted into — and *any* reply is the strongest signal
WhatsApp has that this is a real conversation rather than a broadcast.

It is also just better sales. "Bhej doon?" invites a yes; a wall of text with a
link invites a block.

---

## ⚠️ Read this before running it on your own number

This uses Baileys, an unofficial WhatsApp Web client. **WhatsApp bans numbers for
this.** Not "might" — cold outreach at volume from a personal number is the exact
behaviour their anti-spam systems target, and a ban takes the number and every
chat on it.

Mitigations built in:

| Setting | Default | What it does |
|---|---|---|
| `SEND_GAP_MIN_MS` / `SEND_GAP_MAX_MS` | 8–20s | Every message in the process is serialised through one queue with a randomised gap. Bursts are what get flagged, more than volume. |
| `DAILY_SEND_CAP` | 250 | Hard ceiling per day. The campaign parks the rest rather than pushing through. |
| `TYPING_CPS` | 13 chars/s | Typing indicator held for as long as the text would actually take to type, re-asserted every few seconds the way a real client does, with the pauses a person makes mid-message. URLs aren't counted — nobody types a URL. |
| Message variants | **30 each** | Dealt from a shuffled deck, so all thirty go out before any repeats and the deck survives a restart. Identical text at scale is a strong spam signal, and the first thing two shop owners notice when they compare phones. |
| `SEND_WINDOW` | 09:30–21:30 IST | Nothing proactive goes out overnight. A 03:40 message is the loudest bot tell there is and the fastest route to being reported. Replies are still answered ±90 min outside it. |
| `PROFILE_PEEK_RATE` | 0.7 | Opening a chat loads the contact's display picture, and sometimes their status and business card — the ordinary reads a real client makes. An account that only ever sends is a shape worth not having. |
| `REPLY_DELAY_MIN/MAX_MS` | 20s–2½ min | Replies aren't answered instantly. Nobody answers WhatsApp in under a second. |
| Read receipts | always | Their message is marked read before it's answered — replying to a message that was never opened isn't something a phone can do. |
| Presence | per message | Online → look → type → send → offline. Left permanently online, the account is available 24 hours a day, which no human number is. |
| `SPLIT_RATE_PING` / `_LINK` | 0.25 / 0.4 | Some messages arrive as two bubbles — greeting sent, rest typed after. Never on the cold no-reply path, where a burst is the exact risk this design exists to avoid. |
| `onWhatsApp()` check | always | Numbers not on WhatsApp are marked `unreachable` instead of being messaged into the void. |
| Ping-then-link | always | See above. |

**What this costs in pace.** A message now occupies the outbox for as long as it
would really take to write it — open the chat, look, dwell, type 10–22 seconds,
send — on top of the 8–20 s gap between leads. Reckon on **~35–45 s per message**,
so a full 250-message day is roughly 2½–3 hours of wall clock inside a 12-hour
window. That is the trade: the pacing *is* the mitigation.

**Upgrading an existing deploy:** `SEND_WINDOW` defaults to **on**, so a sidecar
that used to send around the clock will now hold new pings outside 09:30–21:30
IST. Nothing is dropped — queued jobs go out when the window opens, and replies
are still answered ±90 min either side. Set `SEND_WINDOW=false` to restore the
old behaviour.

**Strong recommendations:**

1. **Use a dedicated number**, not your personal one, and not one tied to
   anything you can't lose.
2. **Warm it up.** A brand-new SIM that starts messaging 250 strangers on day one
   is banned within hours. Use it normally for a week or two first.
3. **Start at 20–30 leads/day** and climb slowly. `DAILY_SEND_CAP=250` is a
   ceiling, not a target.
4. **Watch the reply rate.** Many sends and no replies is the profile of a
   spammer. This is the number that actually predicts a ban.
5. If you want to run this at real volume without the risk, the answer is the
   official **WhatsApp Business Cloud API** with approved message templates.
   That is a different (paid, slower to set up) integration — this sidecar is
   built for the scrappy version.

---

## HTTP API

Listens on `127.0.0.1:8082` (loopback only — nothing is exposed to the network).

| Endpoint | Purpose |
|---|---|
| `POST /queue` | Accept a lead. Body: `{phone, business, ownerName, demoUrl, vertical, city, language, callId}`. `phone` and `demoUrl` are required. Idempotent per phone number. |
| `GET /status` | `{connected, state, pending, sent, declined, unreachable, failed, sentToday}`. The agent checks this at campaign start. |
| `GET /jobs` | Every job with its full state — the quickest way to see what happened to a given lead. |
| `GET /health` | Liveness. |

---

## Job lifecycle

| Status | Meaning |
|---|---|
| `queued` | Accepted from the agent, nothing sent yet |
| `pinged` | "Link bhej doon?" delivered; waiting on a reply or the deadline |
| `sent` | The demo link went out ✅ |
| `declined` | They said no — the link is deliberately never sent |
| `unreachable` | The number is not on WhatsApp |
| `failed` | Sending errored past 4 attempts |

State lives in `jobs.json`, rewritten atomically. A restart mid-campaign resumes
every job exactly where it left off, **including 30–40 minute waits already
running** — the scheduler works off stored timestamps, not in-memory timers.

---

## Reply classification

Two layers, in `intent.js`:

1. **Keyword pass** — resolves the one-word Hinglish replies that make up most
   traffic (`haan`, `bhej do`, `theek hai`, `nahi chahiye`, `👍`) with zero
   latency and zero cost.
2. **OpenAI** (`gpt-5-nano`) — only for what the keywords can't read.

The bias is deliberately asymmetric: **only a clear refusal blocks the link.**
Anything ambiguous still gets it, because the link is free, was verbally agreed
to on the call, and a confused "kya?" is an engaged lead, not a no. A question
gets a one-line reminder of who you are, then the link.

Without `OPENAI_API_KEY` the keyword pass still runs; ambiguous replies fall
through to the timeout path and get the link anyway.

---

## Testing

```bash
npm run rehearse      # the whole flow, offline
npm run preview       # read all 180 message variants
```

`rehearse` runs the whole thing against a fake socket with compressed timings —
no WhatsApp account, no messages sent. It prints exactly what each lead would
receive across all five outcomes (yes / no / question / silence /
not-on-WhatsApp), how many bubbles each arrived in, and whether the account
looked the contact up first. It then audits the message banks: every variant
unique, every link template carrying the URL in its final segment, and a full
deck dealt without a repeat. It exits non-zero if any of that fails.

Run it after touching `messages.js`, `human.js`, `intent.js` or `queue.js`.

To rehearse against a **real** phone, set `TEST_NUMBER=+91…` in `.env` — every
message routes to that number instead of the lead.

---

## Files

| File | Responsibility |
|---|---|
| `index.js` | Baileys socket, reconnect, QR, incoming messages, HTTP API |
| `queue.js` | Job lifecycle, send pacing, the 30–40 min wait, the sending window |
| `messages.js` | Every message a lead reads — 30 variants each, Hinglish + English |
| `human.js` | Presence, typing, blue ticks, profile lookups, the wall clock |
| `intent.js` | Reply classification (keywords → OpenAI) |
| `shot.js` | The demo screenshot that rides along as the link message's caption |
| `store.js` | Atomic JSON persistence, jid↔phone index, daily counters, template decks |
| `config.js` | `.env` loading and defaults |
| `test-flow.js` | Offline rehearsal of every path + message-bank audit |
| `preview.js` | Prints every variant rendered with real lead data |

## Troubleshooting

**"WhatsApp not linked"** — `npm start` and scan the QR. The QR expires; if you
miss it, restart.

**Logged out / device unlinked** — `npm run logout`, restart, scan again.

**Nothing is sending** — check `GET /status`. `connected: false` means no
WhatsApp session; `sentToday` at the cap means it's parked until tomorrow.

**A specific lead didn't get it** — `GET /jobs` and find them by phone. The
`status` and `note` fields say exactly why.
