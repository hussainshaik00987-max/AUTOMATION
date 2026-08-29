import { config, jitter, sleep } from './config.js'
import * as human from './human.js'
import { classify, NO, YES } from './intent.js'
import * as shot from './shot.js'
import { clarifyMessage, linkMessage, pingMessage } from './messages.js'
import * as store from './store.js'

// ---------------------------------------------------------------------------
// The follow-up scheduler.
//
// One lead, one job, two messages:
//
//   call ends
//      │
//      ├─ online ▸ dp loads ▸ typing… ──▶ "Namaste sir 🙏 … bhej doon?"  (ping)
//      │
//      ├─ they reply "haan"  ──▶ read ▸ 20s–2½min ▸ typing… ──▶ the link
//      ├─ they reply "nahi"  ──▶ nothing is ever sent            (declined)
//      └─ 30–40 min silence  ──▶ typing… ──▶ the link            (send anyway)
//
// Why the link is not simply sent straight away: an unsolicited link as the very
// first message to a number that has never messaged you is what WhatsApp's spam
// heuristics are built to catch, and it is what a person reports. Asking first
// turns the thread into a conversation the recipient opted into — and a reply,
// any reply, is the strongest possible signal to WhatsApp that this is a real
// exchange rather than a broadcast.
//
// Everything about HOW a message goes out — coming online, loading the display
// picture, blue ticks, typing at a speed with pauses in it, one bubble or two,
// going offline afterwards, and not sending at four in the morning — lives in
// human.js. This file decides what to send and when.
// ---------------------------------------------------------------------------

let getSock = () => null
let log = console

export function attach(opts) {
  getSock = opts.getSock
  log = opts.logger || console
  shot.attach({ logger: log })
}

// ---------------------------------------------------------------------------
// Outbound pacing
//
// Every message in the whole process goes through this one queue. WhatsApp bans
// unofficial clients on burst behaviour far more readily than on volume, so
// messages are strictly serialised with a randomised gap, and a daily ceiling
// stops a runaway campaign from torching the number in an afternoon.
// ---------------------------------------------------------------------------

const outbox = []
let draining = false
let lastSendAt = 0

function enqueueSend(task) {
  return new Promise((resolve, reject) => {
    outbox.push({ task, resolve, reject })
    drain()
  })
}

async function drain() {
  if (draining) return
  draining = true
  try {
    while (outbox.length) {
      const gap = jitter(config.sendGapMinMs, config.sendGapMaxMs)
      const since = Date.now() - lastSendAt
      if (lastSendAt && since < gap) await sleep(gap - since)

      const { task, resolve, reject } = outbox.shift()
      try {
        const result = await task()
        lastSendAt = Date.now()
        resolve(result)
      } catch (err) {
        lastSendAt = Date.now()
        reject(err)
      }
    }
  } finally {
    draining = false
  }
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

// resolveJid confirms the number is actually on WhatsApp and returns the jid the
// server wants us to address. Skipping this and guessing "<digits>@s.whatsapp.net"
// is how you end up sending into the void for every landline in the list.
async function resolveJid(job) {
  const sock = getSock()
  if (!sock) throw new Error('whatsapp not connected')

  const digits = config.testNumber
    ? store.phoneKey(config.testNumber)
    : store.phoneKey(job.phone)
  if (!digits) throw new Error('no phone digits')

  const [result] = await sock.onWhatsApp(`${digits}@s.whatsapp.net`)
  if (!result?.exists) return null
  store.linkJid(result.jid, job.key)
  return result.jid
}

// deliver runs the whole arrival-to-departure sequence for one message: open the
// chat like a person opening a chat, type it out, send it, and go back offline.
//
// `parts` is one logical message, already split into the one or two bubbles it
// should arrive as. An image is attached to the LAST bubble as its caption —
// never sent as a message of its own, because a picture followed by a link is
// two messages to a number that has never written to you, and that shape is
// exactly what gets unofficial clients banned.
async function deliver(job, jid, parts, { image = null, stayOnline = false } = {}) {
  const sock = getSock()
  if (!sock) throw new Error('whatsapp not connected')

  const profile = await human.openChat(sock, jid, { peek: !job.profile })
  if (profile) {
    store.updateJob(job.key, { profile })
    if (profile.website) {
      log.info({ business: job.business, website: profile.website }, 'lead already lists a website on their WhatsApp business profile')
    }
  }

  const sent = await human.sendBubbles(sock, jid, parts, {
    image,
    // Counted per bubble, because the daily cap is a cap on messages leaving the
    // account — which is what WhatsApp is counting too.
    onSent: () => store.recordSend(),
  })

  // Not awaited: going back offline is housekeeping, and the queue should be
  // free to move to the next lead rather than sitting through it. Skipped when
  // another message is following immediately — flicking offline in the middle of
  // typing the next one is worse than never going offline at all.
  if (!stayOnline) human.leaveChat(sock, jid).catch(() => {})
  return sent
}

function capReached() {
  return store.countSentToday() >= config.dailySendCap
}

// ---------------------------------------------------------------------------
// Job steps
// ---------------------------------------------------------------------------

async function sendPing(job) {
  const jid = await resolveJid(job)
  if (!jid) {
    log.warn({ business: job.business, phone: job.phone }, 'number is not on WhatsApp')
    store.updateJob(job.key, { status: store.UNREACHABLE, note: 'not on whatsapp' })
    return
  }

  const parts = pingMessage(job)
  await deliver(job, jid, parts)

  const wait = jitter(config.waitMinMs, config.waitMaxMs)
  store.updateJob(job.key, {
    status: store.PINGED,
    jid,
    pingedAt: Date.now(),
    deadlineAt: Date.now() + wait,
  })
  log.info(
    { business: job.business, bubbles: parts.length, waitMin: Math.round(wait / 60000) },
    'ping sent — waiting for reply',
  )
}

async function sendLink(job, reason) {
  const jid = job.jid || (await resolveJid(job))
  if (!jid) {
    store.updateJob(job.key, { status: store.UNREACHABLE, note: 'not on whatsapp' })
    return
  }

  // A reply we couldn't read as yes or no gets a one-line reminder of who we are
  // before the link — otherwise "ye lijiye 👇" lands with no context on someone
  // who just asked "kaun?".
  const introduced = reason === 'unclear-reply'
  if (introduced) {
    await deliver(job, jid, clarifyMessage(job), { stayOnline: true })
    await sleep(jitter(config.bubbleGapMinMs, config.bubbleGapMaxMs))
  }

  // The picture is the point of the message now: on the call they were asked
  // "screenshot या demo link भेज दूँ?", and a screenshot is what most of them
  // said yes to. It rides along as the caption of the bubble carrying the link,
  // so they can see the site without deciding to click anything first.
  //
  // A missing screenshot is not a failure. forDemo returns null whenever it
  // cannot produce one, and the link goes on its own exactly as it always did —
  // the lead is worth far more than the image.
  const image = await shot.forDemo(job.demoUrl)

  // Two bubbles is a natural shape once there is a conversation — but only then.
  // The no-reply path is still a cold thread and a burst there is the risk this
  // whole design exists to avoid; and the unclear-reply path has already spent a
  // message on the reminder above, so splitting would make three in a row.
  const parts = linkMessage(job, {
    allowSplit: reason === 'replied-yes',
    // The clarify message above has already said their name; saying it again in
    // the very next message is how two templates read as two templates.
    afterIntro: introduced,
  })

  await deliver(job, jid, parts, { image })
  store.updateJob(job.key, {
    status: store.SENT, jid, sentAt: Date.now(), note: reason, withImage: !!image, linkReason: '',
  })
  log.info(
    { business: job.business, reason, url: job.demoUrl, withImage: !!image, bubbles: parts.length },
    'demo link sent',
  )
}

// runJobStep is the single place a job is advanced, so the retry/backoff rules
// live in exactly one spot. Failures are retried a few times before the job is
// parked — a transient socket drop must not silently lose a lead.
async function runJobStep(job, step, reason) {
  try {
    await enqueueSend(() => (step === 'ping' ? sendPing(job) : sendLink(job, reason)))
  } catch (err) {
    const attempts = (job.attempts || 0) + 1
    log.error({ business: job.business, step, attempts, err: err.message }, 'send failed')
    if (attempts >= 4) {
      store.updateJob(job.key, { status: store.FAILED, attempts, note: err.message })
    } else {
      // Push the next attempt out a few minutes; the tick loop will pick it up.
      store.updateJob(job.key, {
        attempts,
        deadlineAt: Date.now() + attempts * 120_000,
      })
    }
  }
}

// ---------------------------------------------------------------------------
// Reply handling
// ---------------------------------------------------------------------------

// onIncoming is called for every inbound message. It only acts on jobs that are
// waiting on a reply — once a lead has their link, further messages from them are
// a real conversation for a human to pick up, not something to automate over.
export async function onIncoming(jid, text, msg) {
  const job = store.jobForJid(jid)
  if (!job) return

  store.linkJid(jid, job.key)

  if (job.status !== store.PINGED) {
    log.info({ business: job.business, status: job.status, text }, 'reply on a job that is no longer waiting — leaving it for a human')
    return
  }

  // Blue ticks before anything else. Answering a message that was never opened
  // is not something a phone can actually do, and read receipts are also the
  // clearest signal to WhatsApp that a real two-way conversation is happening.
  const sock = getSock()
  if (sock) human.markRead(sock, msg).catch(() => {})

  const { intent, via } = await classify(text)
  store.updateJob(job.key, { repliedAt: Date.now(), lastReply: text.slice(0, 300) })
  log.info({ business: job.business, text, intent, via }, 'reply classified')

  if (intent === NO) {
    store.updateJob(job.key, { status: store.DECLINED, note: `declined: ${text.slice(0, 80)}` })
    return
  }

  if (capReached()) {
    log.warn({ business: job.business }, 'daily send cap reached — link deferred to tomorrow')
    return
  }

  // Do not answer instantly. Nobody replies to WhatsApp in under a second, and a
  // campaign in which every reply is answered the moment it lands is a campaign
  // that reads as automated from the transcript alone.
  //
  // The delay is stored as a deadline rather than held in a timer, so it is one
  // more thing that survives a restart — the tick loop below picks it up, and
  // the reason travels with the job so the right message is still sent.
  const reason = intent === YES ? 'replied-yes' : 'unclear-reply'
  store.updateJob(job.key, {
    linkReason: reason,
    deadlineAt: Date.now() + human.replyLatency(),
  })
}

// ---------------------------------------------------------------------------
// Tick loop
// ---------------------------------------------------------------------------

let ticking = false
let quietSince = 0

// tick advances every job that is due. It is deliberately driven off stored
// timestamps rather than in-memory timers, so a restart mid-campaign resumes
// exactly where it left off — including the 30–40 minute waits already running,
// and the short pause before answering somebody who just wrote back.
export async function tick() {
  if (ticking) return
  const sock = getSock()
  if (!sock) return

  ticking = true
  try {
    const now = Date.now()

    for (const job of store.pendingJobs()) {
      if (capReached()) break

      if (job.status === store.QUEUED) {
        // A retry backoff parks the job by pushing deadlineAt into the future.
        if (job.deadlineAt && now < job.deadlineAt) continue
        if (!windowOpen(now, false)) continue
        await runJobStep(job, 'ping')
        continue
      }

      if (job.status === store.PINGED && job.deadlineAt && now >= job.deadlineAt) {
        // A lead who wrote back gets answered on a wider clock than a cold
        // follow-up does: if they message us at half past ten at night they are
        // plainly awake, and a person would reply. So a shut proactive window
        // never blocks an answer — hence `continue` rather than `break` above.
        const answering = !!job.linkReason
        if (!windowOpen(now, answering)) continue
        await runJobStep(job, 'link', job.linkReason || 'no-reply-timeout')
      }
    }
  } catch (err) {
    log.error({ err: err.message }, 'tick failed')
  } finally {
    ticking = false
  }
}

// windowOpen gates on the wall clock, and announces a closure once rather than
// once per job per tick — a campaign left running overnight would otherwise
// write a hundred thousand identical lines before breakfast. Only the proactive
// gate is tracked for that logging; answers ride a wider window and are not
// what anyone reading the log is waiting on.
function windowOpen(now, answering) {
  const win = human.windowState(now, {
    grace: answering ? config.replyGraceMinutes : 0,
  })

  if (answering) return win.open

  if (win.open) {
    if (quietSince) {
      log.info({ heldFor: `${Math.round((now - quietSince) / 60000)}m` }, 'sending window open again')
      quietSince = 0
    }
    return true
  }

  if (!quietSince) {
    quietSince = now
    log.info(
      { opensAt: win.opensAt, inMinutes: Math.round(win.waitMs / 60000) },
      'outside the sending window — holding new pings until it opens',
    )
  }
  return false
}

export function start() {
  log.info({ window: human.describeWindow() }, 'scheduler running')
  setInterval(() => {
    tick().catch((err) => log.error({ err: err.message }, 'tick crashed'))
  }, 15_000)
  tick().catch(() => {})
}
