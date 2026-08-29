import fs from 'node:fs'
import path from 'node:path'
import { config } from './config.js'

// ---------------------------------------------------------------------------
// Job store.
//
// A job outlives the process by design: the whole point of this service is a
// 30–40 minute wait between two messages, and a campaign that runs for hours
// will hit a restart, a crash, or a WhatsApp reconnect somewhere in the middle.
// Everything therefore lives in one JSON file that is rewritten atomically, and
// the scheduler recomputes what to do from timestamps on boot rather than
// holding pending work only in memory.
//
// Job lifecycle:
//   queued        → accepted from the Go agent, nothing sent yet
//   pinged        → "link bhej doon?" delivered; waiting for a reply or deadline
//   sent          → the demo link went out (terminal, success)
//   declined      → they said no; the link is deliberately NOT sent (terminal)
//   unreachable   → the number is not on WhatsApp (terminal)
//   failed        → sending errored past retries (terminal)
// ---------------------------------------------------------------------------

export const QUEUED = 'queued'
export const PINGED = 'pinged'
export const SENT = 'sent'
export const DECLINED = 'declined'
export const UNREACHABLE = 'unreachable'
export const FAILED = 'failed'

const TERMINAL = new Set([SENT, DECLINED, UNREACHABLE, FAILED])
export const isTerminal = (job) => TERMINAL.has(job.status)

// A factory, not a constant: spreading a shared object would hand every fresh
// state the SAME nested jobs/decks objects, so a load() after a campaign had
// been running would inherit whatever the last one had written into them.
const empty = () => ({
  jobs: {},
  jidIndex: {},
  sentToday: 0,
  sentDay: '',
  decks: {},
  lastDealt: {},
})

let state = empty()
let writeTimer = null

export function load() {
  try {
    if (fs.existsSync(config.storeFile)) {
      const parsed = JSON.parse(fs.readFileSync(config.storeFile, 'utf8'))
      state = { ...empty(), ...parsed }
    }
  } catch (err) {
    // A corrupt store must not take the service down mid-campaign — keep the bad
    // file for inspection and carry on with an empty one.
    const backup = `${config.storeFile}.corrupt-${Date.now()}`
    try { fs.renameSync(config.storeFile, backup) } catch {}
    console.error(`[store] unreadable, moved to ${backup}:`, err.message)
  }
  rolloverDay()
  return state
}

// save is debounced: the scheduler touches jobs frequently, and one fsync per
// touch would be pointless churn. Anything important calls saveNow().
export function save() {
  if (writeTimer) return
  writeTimer = setTimeout(() => {
    writeTimer = null
    saveNow()
  }, 500)
}

export function saveNow() {
  if (writeTimer) {
    clearTimeout(writeTimer)
    writeTimer = null
  }
  const tmp = `${config.storeFile}.tmp`
  fs.mkdirSync(path.dirname(config.storeFile), { recursive: true })
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2))
  fs.renameSync(tmp, config.storeFile) // atomic — never leaves a half-written store
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

function rolloverDay() {
  const d = today()
  if (state.sentDay !== d) {
    state.sentDay = d
    state.sentToday = 0
  }
}

export function countSentToday() {
  rolloverDay()
  return state.sentToday
}

export function recordSend() {
  rolloverDay()
  state.sentToday += 1
  save()
}

// ---------------------------------------------------------------------------
// Template decks
//
// Picking a message variant with Math.random() repeats far sooner than anyone
// expects: over thirty variants the odds of a duplicate inside the first seven
// sends are better than even. That is the birthday problem, and it is precisely
// the coincidence that gets spotted when two shop owners on the same street put
// their phones side by side.
//
// So variants are DEALT, not picked: each bank is shuffled into a deck and every
// card goes out before the deck is reshuffled. The decks live in the store with
// everything else, so a restart in the middle of a campaign does not go back to
// dealing the same opening hand.
// ---------------------------------------------------------------------------

function shuffled(n) {
  const deck = Array.from({ length: n }, (_, i) => i)
  for (let i = deck.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[deck[i], deck[j]] = [deck[j], deck[i]]
  }
  return deck
}

export function dealFrom(name, size) {
  if (!size) return 0
  let deck = state.decks[name]

  // Reshuffle when the deck runs out — and also when it holds an index the bank
  // no longer has, which is what a deploy that shortened a bank looks like.
  if (!Array.isArray(deck) || deck.length === 0 || deck.some((i) => i >= size)) {
    deck = shuffled(size)
    // Do not let a reshuffle immediately repeat the last card dealt: two leads in
    // a row reading the same sentence is the one case a deck exists to prevent.
    const previous = state.lastDealt[name]
    if (size > 1 && deck[deck.length - 1] === previous) {
      ;[deck[0], deck[deck.length - 1]] = [deck[deck.length - 1], deck[0]]
    }
  }

  const index = deck.pop()
  state.decks[name] = deck
  state.lastDealt[name] = index
  save()
  return index
}

// phoneKey normalises any phone representation to bare digits, which is how jobs
// are keyed. "+91 98765 43210", "919876543210" and "919876543210@s.whatsapp.net"
// all collapse to the same key.
export function phoneKey(phone) {
  return String(phone || '').replace(/[^0-9]/g, '')
}

// Fields a later call can legitimately improve on an earlier one. The name is
// the clearest case: call one may hang up before he gives it, call two gets
// "Krishna", and the greeting should use it. Blank incoming values never
// overwrite something we already know.
function mergeCallData(target, job) {
  if (job.business) target.business = job.business
  if (job.ownerName) target.ownerName = job.ownerName
  if (job.demoUrl) target.demoUrl = job.demoUrl
  if (job.vertical) target.vertical = job.vertical
  if (job.city) target.city = job.city
  if (job.language) target.language = job.language === 'en' ? 'en' : 'hi'
  if (job.callId) target.callId = job.callId
  return target
}

// upsertJob records what a finished call promised.
//
// A second call to the same number is NEW INFORMATION, not noise — it may carry
// the owner's name, a language switch, or a different demo site — so the stored
// job is always refreshed from it. What changes is whether that produces another
// message:
//
//   * no job yet          → create one                          (queued)
//   * job already done    → re-queue it; they agreed again      (queued)
//   * job still in flight → refresh it, but send nothing extra  (not queued)
//
// The last case is the one that used to silently drop everything: seven calls
// were folded into one job from the first of them, and each still reported
// success back to the dialer.
export function upsertJob(job) {
  const key = phoneKey(job.phone)
  const existing = state.jobs[key]

  if (existing && !isTerminal(existing)) {
    // Already queued or pinged. One conversation is in flight; a second message
    // would talk over it. Take the better data and leave the schedule alone.
    mergeCallData(existing, job)
    saveNow()
    return { job: existing, created: false, refreshed: true }
  }

  if (existing) {
    // Terminal — sent, declined, unreachable or failed. They have just agreed on
    // a fresh call, so this is a new attempt, not a duplicate of the old one.
    mergeCallData(existing, job)
    Object.assign(existing, {
      status: QUEUED,
      queuedAt: Date.now(),
      pingedAt: 0,
      deadlineAt: 0,
      repliedAt: 0,
      lastReply: '',
      sentAt: 0,
      attempts: 0,
      linkReason: '',
      note: `re-queued after ${existing.status}`,
    })
    saveNow()
    return { job: existing, created: true, requeued: true }
  }

  state.jobs[key] = mergeCallData(
    {
      key,
      phone: job.phone,
      business: '',
      ownerName: '',
      demoUrl: '',
      vertical: '',
      city: '',
      language: 'hi',
      callId: '',
      status: QUEUED,
      jid: '',
      queuedAt: Date.now(),
      pingedAt: 0,
      deadlineAt: 0,
      repliedAt: 0,
      lastReply: '',
      sentAt: 0,
      attempts: 0,
      note: '',
      // Whatever the profile lookup turned up when we opened their chat — a
      // display picture, a status line, and for a business account the website
      // field, which is worth reading before anyone follows this lead up.
      profile: null,
      // Why the link is due to go out, decided when they replied and carried
      // through to the send by the tick loop.
      linkReason: '',
    },
    job,
  )
  saveNow()
  return { job: state.jobs[key], created: true }
}

export function getJob(key) {
  return state.jobs[key]
}

export function allJobs() {
  return Object.values(state.jobs)
}

export function pendingJobs() {
  return allJobs().filter((j) => !isTerminal(j))
}

export function updateJob(key, patch) {
  const job = state.jobs[key]
  if (!job) return null
  Object.assign(job, patch)
  save()
  return job
}

// The jid a message arrives from is not always the phone-number jid we sent to —
// WhatsApp migrated to opaque @lid identifiers, so the sender of an incoming
// message may be unrecognisable from the number alone. Every jid we ever resolve
// or receive for a job is therefore remembered, so replies can be matched back.
export function linkJid(jid, key) {
  if (!jid || !key) return
  if (state.jidIndex[jid] === key) return
  state.jidIndex[jid] = key
  save()
}

export function jobForJid(jid) {
  if (!jid) return null
  const byIndex = state.jidIndex[jid]
  if (byIndex && state.jobs[byIndex]) return state.jobs[byIndex]
  // Fall back to the digits in the jid, which works for plain @s.whatsapp.net.
  const key = phoneKey(jid.split('@')[0])
  return key ? state.jobs[key] || null : null
}

export function stats() {
  const jobs = allJobs()
  const by = (s) => jobs.filter((j) => j.status === s).length
  return {
    total: jobs.length,
    queued: by(QUEUED),
    pinged: by(PINGED),
    sent: by(SENT),
    declined: by(DECLINED),
    unreachable: by(UNREACHABLE),
    failed: by(FAILED),
    sentToday: countSentToday(),
  }
}
