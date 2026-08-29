import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))

// Minimal .env loader — one small file, no dependency worth taking for it.
// Values already present in the real environment always win.
function loadEnv() {
  const file = path.join(here, '.env')
  if (!fs.existsSync(file)) return
  for (const raw of fs.readFileSync(file, 'utf8').split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const eq = line.indexOf('=')
    if (eq < 0) continue
    const key = line.slice(0, eq).trim()
    let val = line.slice(eq + 1).trim()
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1)
    }
    if (process.env[key] === undefined) process.env[key] = val
  }
}
loadEnv()

const num = (key, fallback) => {
  const v = Number(process.env[key])
  return Number.isFinite(v) && v > 0 ? v : fallback
}

// Like num(), but 0 is a legitimate value — a rate of 0 means "never do this",
// which num() would silently turn back into the default.
const rate = (key, fallback) => {
  const v = Number(process.env[key])
  return Number.isFinite(v) && v >= 0 && v <= 1 ? v : fallback
}

const bool = (key, fallback) => {
  const v = (process.env[key] || '').trim().toLowerCase()
  if (!v) return fallback
  return v === '1' || v === 'true' || v === 'yes' || v === 'on'
}

// "21:30" → 1290. Anything unparseable falls back rather than silently pinning
// the send window to midnight.
const clock = (key, fallback) => {
  const m = /^(\d{1,2}):(\d{2})$/.exec((process.env[key] || '').trim())
  if (!m) return fallback
  const mins = Number(m[1]) * 60 + Number(m[2])
  return mins >= 0 && mins <= 24 * 60 ? mins : fallback
}

export const config = {
  port: num('PORT', 8082),
  authDir: path.join(here, process.env.AUTH_DIR || 'auth'),
  storeFile: path.join(here, process.env.STORE_FILE || 'jobs.json'),
  logLevel: process.env.LOG_LEVEL || 'info',

  // How long to wait after the "bhej doon?" ping before sending the link anyway.
  // Randomised per lead inside this window so a whole campaign's follow-ups don't
  // arrive in a detectable, machine-perfect cadence.
  waitMinMs: num('WAIT_MIN_MINUTES', 30) * 60_000,
  waitMaxMs: num('WAIT_MAX_MINUTES', 40) * 60_000,

  // ── Behaving like a person (see human.js) ────────────────────────────────
  //
  // Typing indicator shown before each message. A real person takes a few seconds
  // to get going; sending instantly on a chat that was never open is the single
  // most obvious bot tell in WhatsApp.
  typingMinMs: num('TYPING_MIN_MS', 2500),
  typingMaxMs: num('TYPING_MAX_MS', 5000),
  // …and then types at a speed, rather than at a fixed cost per message. 13
  // characters/second is a fast thumb-typist on a phone. The cap keeps a long
  // link message from holding "typing…" for a minute and a half; URLs in the
  // text are not counted, because nobody types a URL.
  typingCps: num('TYPING_CPS', 13),
  typingCapMs: num('TYPING_CAP_MS', 26_000),
  // The stop-and-think mid-message. One or two per longer message.
  typingPauseMinMs: num('TYPING_PAUSE_MIN_MS', 900),
  typingPauseMaxMs: num('TYPING_PAUSE_MAX_MS', 2800),

  // Dwell between opening the chat and starting to type — reading the thread,
  // deciding how to address them.
  readDelayMinMs: num('READ_DELAY_MIN_MS', 1500),
  readDelayMaxMs: num('READ_DELAY_MAX_MS', 5000),

  // Gap between the two bubbles of a split message.
  bubbleGapMinMs: num('BUBBLE_GAP_MIN_MS', 900),
  bubbleGapMaxMs: num('BUBBLE_GAP_MAX_MS', 2600),

  // How often a message is sent as two bubbles instead of one — a greeting sent,
  // then the rest typed after it. The ping is the first message to a cold number
  // and two cold messages in a row is a worse shape than one, so it splits less.
  splitRatePing: rate('SPLIT_RATE_PING', 0.25),
  splitRateLink: rate('SPLIT_RATE_LINK', 0.4),

  // How often opening a chat also loads the contact's display picture, and how
  // often that goes further into the status text and business card. A client
  // that has never once looked anybody up is a client that only ever sends.
  profilePeekRate: rate('PROFILE_PEEK_RATE', 0.7),
  profileDeepRate: rate('PROFILE_DEEP_RATE', 0.35),

  // How long a reply sits before it is answered. Nobody replies to WhatsApp in
  // under a second, and a campaign where every reply is answered instantly is a
  // campaign that looks exactly like what it is.
  replyDelayMinMs: num('REPLY_DELAY_MIN_MS', 20_000),
  replyDelayMaxMs: num('REPLY_DELAY_MAX_MS', 150_000),

  // Sending window, wall-clock in the campaign's timezone. A message at 03:40 is
  // the loudest bot tell there is and the fastest way to be reported by somebody
  // whose phone woke them up. Computed from the offset below, not the server's
  // TZ, because the box runs on UTC. A lead who messages US outside the window
  // still gets answered — see QUIET_REPLY_GRACE_MINUTES.
  sendWindow: bool('SEND_WINDOW', true),
  windowStartMin: clock('SEND_WINDOW_START', 9 * 60 + 30),
  windowEndMin: clock('SEND_WINDOW_END', 21 * 60 + 30),
  replyGraceMinutes: num('QUIET_REPLY_GRACE_MINUTES', 90),
  tzOffsetMinutes: Number.isFinite(Number(process.env.TZ_OFFSET_MINUTES))
    ? Number(process.env.TZ_OFFSET_MINUTES)
    : 330, // IST

  // Global send pacing. WhatsApp rate-limits and bans aggressively on unofficial
  // clients, so outbound messages are serialised with a randomised gap and a hard
  // daily ceiling — see README for why this matters more than throughput.
  sendGapMinMs: num('SEND_GAP_MIN_MS', 8000),
  sendGapMaxMs: num('SEND_GAP_MAX_MS', 20000),
  dailySendCap: num('DAILY_SEND_CAP', 250),

  // OpenAI — classifies replies so a "haan bhejo" gets the link instantly and a
  // "nahi chahiye" never does.
  openaiKey: process.env.OPENAI_API_KEY || '',
  openaiModel: process.env.OPENAI_INTENT_MODEL || 'gpt-5-nano',

  // Safety valve: when set, every job is routed to this number instead of the
  // real lead. Use it to rehearse the whole flow against your own phone.
  testNumber: process.env.TEST_NUMBER || '',

  // Link by PAIRING CODE instead of a QR. Set to the account's own number in
  // E.164 without the '+' (e.g. 919876543210) and WhatsApp shows a box to type
  // an eight-character code into, under Linked devices ▸ Link with phone number.
  //
  // This exists because the QR path is genuinely hard here: the code is ASCII art
  // in a terminal on a remote box, it is regenerated every ~20 seconds, and it
  // has to be scanned by the same phone whose screen you are reading. That loop
  // failed for fifteen hours straight — 2,103 regenerations, zero links — while
  // every "haan bhej do" on a live call went undelivered.
  pairingNumber: (process.env.WA_PAIRING_NUMBER || '').replace(/[^0-9]/g, ''),
}

export const jitter = (min, max) => Math.floor(min + Math.random() * Math.max(0, max - min))
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
