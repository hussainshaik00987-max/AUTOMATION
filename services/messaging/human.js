import { config, jitter, sleep } from './config.js'

// ---------------------------------------------------------------------------
// The layer that makes this account behave like a person holding a phone.
//
// Content variety (messages.js) only solves half the problem. The other half is
// the SHAPE of the account's activity, and an unofficial client that has never
// once looked anybody up, never gone online, never read an incoming message, and
// sends every message at 3am with a two-second typing indicator is describable in
// a single heuristic — regardless of how the text reads.
//
// So everything here is the ordinary noise a real client makes:
//
//   opening a chat   →  go online, load the display picture, sometimes tap
//                       through to the contact card
//   reading          →  blue ticks on what they sent, before replying
//   typing           →  proportional to the message, with the pauses a person
//                       makes mid-sentence, and re-asserted every few seconds
//                       the way a real client does
//   several bubbles  →  a greeting sent, then the rest typed after it
//   leaving          →  going offline again a beat later
//   the clock        →  nothing sent at four in the morning
//
// Nothing here is load-bearing for delivery. Every call is wrapped so a failure
// degrades to "we did not do the human thing this time" and the message still
// goes out — a lead is worth more than the performance.
// ---------------------------------------------------------------------------

const quietly = (p) => Promise.resolve(p).catch(() => null)

const chance = (p) => Math.random() < p

// ---------------------------------------------------------------------------
// Sending window
//
// A message at 03:40 is the single loudest bot tell there is, and it is also the
// fastest way to be reported by someone whose phone woke them up. Times are
// wall-clock in the campaign's timezone (IST by default), computed from an
// offset rather than from the server's TZ — this box runs on UTC.
// ---------------------------------------------------------------------------

function localMinutes(now = Date.now()) {
  const shifted = new Date(now + config.tzOffsetMinutes * 60_000)
  return shifted.getUTCHours() * 60 + shifted.getUTCMinutes()
}

// windowState answers "may I send right now, and if not, how long until I may?".
// `grace` widens the window at both ends and is used when we are REPLYING: if
// somebody messages us at 22:30 they are plainly awake, and a person would
// answer. Only proactive sends are held to the tighter window.
export function windowState(now = Date.now(), { grace = 0 } = {}) {
  if (!config.sendWindow) return { open: true, waitMs: 0 }

  const open = config.windowStartMin - grace
  const close = config.windowEndMin + grace
  const mins = localMinutes(now)

  if (mins >= open && mins < close) return { open: true, waitMs: 0 }

  // Minutes until the window next opens, wrapping over midnight.
  const untilOpen = mins < open ? open - mins : 24 * 60 - mins + open
  return { open: false, waitMs: untilOpen * 60_000, opensAt: hhmm(open) }
}

function hhmm(mins) {
  const m = ((mins % 1440) + 1440) % 1440
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

// replyLatency is how long we sit on a reply before answering it. Nobody answers
// a WhatsApp message in 900ms, and a campaign where every single reply is
// answered in under a second is a campaign that looks exactly like what it is.
export const replyLatency = () => jitter(config.replyDelayMinMs, config.replyDelayMaxMs)

// ---------------------------------------------------------------------------
// Opening a chat
// ---------------------------------------------------------------------------

// peekProfile does what the real client does the moment a chat is opened: it
// subscribes to the contact's presence and fetches their display picture, and a
// person who is deciding how to address a stranger often taps the header to see
// the contact card too.
//
// It is also worth something to us. A business account's profile carries the
// website field, and a lead who already has a site is a different conversation —
// so whatever comes back is recorded on the job for whoever reads the log.
export async function peekProfile(sock, jid, { deep = false } = {}) {
  const out = { checkedAt: Date.now() }

  await quietly(sock.presenceSubscribe(jid))
  await sleep(jitter(300, 1100))

  const dp = await quietly(sock.profilePictureUrl(jid, 'preview'))
  out.hasPhoto = !!dp

  if (!deep) return out

  await sleep(jitter(400, 1400))
  const status = await quietly(sock.fetchStatus(jid))
  const entry = Array.isArray(status) ? status[0] : null
  const text = entry?.status?.status ?? entry?.status
  if (typeof text === 'string' && text.trim()) out.status = text.trim().slice(0, 140)

  await sleep(jitter(400, 1200))
  const biz = await quietly(sock.getBusinessProfile(jid))
  if (biz && typeof biz === 'object') {
    out.business = true
    if (biz.description) out.description = String(biz.description).slice(0, 140)
    if (biz.category) out.category = String(biz.category).slice(0, 60)
    const site = Array.isArray(biz.website) ? biz.website[0] : biz.website
    if (site) out.website = String(site).slice(0, 200)
  }

  return out
}

// openChat is the whole arrival sequence: come online, look at who this is, and
// dwell for a moment before starting to type. The dwell is the give-away that is
// cheapest to fix — a client that goes from "chat opened" to "message sent" with
// no gap has not read anything.
export async function openChat(sock, jid, { peek = true } = {}) {
  await quietly(sock.sendPresenceUpdate('available'))

  let profile = null
  if (peek && chance(config.profilePeekRate)) {
    // The deeper lookups — status text, business card — are what a person does
    // when they are curious, not on every chat they open.
    profile = await peekProfile(sock, jid, { deep: chance(config.profileDeepRate) })
  } else {
    await quietly(sock.presenceSubscribe(jid))
  }

  await sleep(jitter(config.readDelayMinMs, config.readDelayMaxMs))
  return profile
}

// leaveChat puts the account back offline a beat after the last message, the way
// a phone screen locks. Left permanently "available", the account is online
// twenty-four hours a day, which no human number is.
export async function leaveChat(sock, jid) {
  await sleep(jitter(1200, 4000))
  await quietly(sock.sendPresenceUpdate('paused', jid))
  await quietly(sock.sendPresenceUpdate('unavailable'))
}

// markRead puts the blue ticks on what they sent. Replying to a message that was
// never opened is not something a phone can actually do.
export async function markRead(sock, msg) {
  if (!msg?.key) return
  await quietly(sock.readMessages([msg.key]))
}

// ---------------------------------------------------------------------------
// Typing
// ---------------------------------------------------------------------------

// typingDuration: how long this text would have taken to type. URLs are excluded
// because nobody types a URL — it is pasted, and charging sixty characters of
// typing time for it produces a suspiciously slow typist.
function typingDuration(text) {
  const typed = text.replace(/https?:\/\/\S+/g, '').length
  const base = jitter(config.typingMinMs, config.typingMaxMs)
  // ±20% on the rate, so the same message is not always typed at the same speed.
  const speed = config.typingCps * (0.8 + Math.random() * 0.4)
  return Math.min(base + (typed / speed) * 1000, config.typingCapMs)
}

// hold keeps "typing…" up for a stretch. WhatsApp expires the composing state
// after a few seconds of silence, so a real client re-asserts it — without this
// the indicator flickers off mid-message and the recipient sees a long silence
// followed by a wall of text appearing at once.
async function hold(sock, jid, ms) {
  let left = ms
  while (left > 0) {
    const slice = Math.min(left, 6000)
    await sleep(slice)
    left -= slice
    if (left > 0) await quietly(sock.sendPresenceUpdate('composing', jid))
  }
}

// typeOut shows the typing indicator for as long as the message warrants, broken
// by the one or two pauses a person makes — to think, to check the spelling of a
// name, because a notification landed on top of what they were writing.
export async function typeOut(sock, jid, text) {
  const total = typingDuration(text)
  const pauses = text.length > 150 ? (chance(0.6) ? 2 : 1) : chance(0.3) ? 1 : 0
  const chunks = pauses + 1

  await quietly(sock.sendPresenceUpdate('composing', jid))
  for (let i = 0; i < chunks; i++) {
    await hold(sock, jid, Math.round(total / chunks))
    if (i === chunks - 1) break
    await quietly(sock.sendPresenceUpdate('paused', jid))
    await sleep(jitter(config.typingPauseMinMs, config.typingPauseMaxMs))
    await quietly(sock.sendPresenceUpdate('composing', jid))
  }
  await quietly(sock.sendPresenceUpdate('paused', jid))
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

// sendBubbles delivers one logical message as the one or two bubbles a person
// would have sent, each typed out separately with a gap between them.
//
// An image, when there is one, is attached to the LAST bubble as its caption —
// never as a message of its own. messages.js guarantees the link is in that
// bubble, so the screenshot and the link arrive together and a lead who was
// promised "screenshot ya link" gets both in a single notification.
export async function sendBubbles(sock, jid, parts, { image = null, onSent } = {}) {
  const bubbles = parts.filter((p) => p && p.trim())
  let last = null

  for (let i = 0; i < bubbles.length; i++) {
    const text = bubbles[i]
    const isLast = i === bubbles.length - 1

    if (i > 0) await sleep(jitter(config.bubbleGapMinMs, config.bubbleGapMaxMs))
    await typeOut(sock, jid, text)

    last = image && isLast
      ? await sock.sendMessage(jid, { image: { url: image }, caption: text })
      : await sock.sendMessage(jid, { text })

    onSent?.(last)
  }

  return last
}

// Handy for the log line that says what the account actually did.
export const describeWindow = () => {
  if (!config.sendWindow) return 'always on'
  return `${hhmm(config.windowStartMin)}–${hhmm(config.windowEndMin)} (UTC${config.tzOffsetMinutes >= 0 ? '+' : ''}${config.tzOffsetMinutes / 60})`
}
