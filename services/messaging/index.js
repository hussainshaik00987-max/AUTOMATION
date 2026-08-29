import http from 'node:http'
import baileys, { DisconnectReason, useMultiFileAuthState } from '@whiskeysockets/baileys'
import pino from 'pino'
import qrcode from 'qrcode-terminal'

import { config } from './config.js'
import * as human from './human.js'
import * as queue from './queue.js'
import * as store from './store.js'

// Baileys ships both CJS and ESM builds depending on version; normalise the
// default export so the socket factory is reachable either way.
const makeWASocket = baileys.default ?? baileys

const log = pino({
  level: config.logLevel,
  timestamp: pino.stdTimeFunctions.isoTime,
})

// ---------------------------------------------------------------------------
// WhatsApp connection
// ---------------------------------------------------------------------------

let sock = null
let connState = 'starting'
let lastQR = ''

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(config.authDir)

  sock = makeWASocket({
    auth: state,
    // Baileys' own logs are extremely chatty at anything below warn, and they
    // drown out the campaign logs that actually matter here.
    logger: pino({ level: 'warn' }),
    // Presenting as a normal desktop client; also what makes the typing
    // indicator behave the way a real chat does.
    browser: ['ProLine Outreach', 'Chrome', '121.0.0'],
    // Never auto-mark the whole history as read on connect — that would nuke the
    // unread state of a real person's WhatsApp account.
    markOnlineOnConnect: false,
    syncFullHistory: false,
  })

  sock.ev.on('creds.update', saveCreds)

  // Pairing-code login. Only meaningful before the device is registered; once
  // auth/ holds a session this is skipped and the socket just reconnects.
  if (config.pairingNumber && !sock.authState.creds.registered) {
    // The socket has to finish its opening handshake before WhatsApp will issue
    // a code; asking immediately returns a 428.
    setTimeout(async () => {
      try {
        const code = await sock.requestPairingCode(config.pairingNumber)
        const pretty = code.match(/.{1,4}/g).join('-')
        connState = 'awaiting-pairing-code'
        console.log(`
╔════════════════════════════════════════════════════════╗
║  WhatsApp ▸ Settings ▸ Linked devices                  ║
║  ▸ Link a device ▸ Link with phone number instead      ║
║                                                        ║
║           PAIRING CODE:   ${pretty.padEnd(29)}║
║                                                        ║
║  Number: +${config.pairingNumber.padEnd(45)}║
║  Expires in about a minute — restart to get a new one. ║
╚════════════════════════════════════════════════════════╝
`)
        log.info({ code: pretty }, 'pairing code issued — type it into WhatsApp')
      } catch (err) {
        log.error({ err: err.message }, 'could not request a pairing code — falling back to the QR above')
      }
    }, 3000)
  }

  sock.ev.on('connection.update', (update) => {
    const { connection, qr, lastDisconnect } = update

    if (qr) {
      lastQR = qr
      connState = 'awaiting-qr'
      console.log('\n─── Scan this with WhatsApp ▸ Linked devices ▸ Link a device ───\n')
      qrcode.generate(qr, { small: true })
      console.log('\nWaiting for you to scan…\n')
    }

    if (connection === 'open') {
      connState = 'open'
      lastQR = ''
      const me = sock?.user?.id || ''
      log.info({ me }, 'WhatsApp connected')
      queue.tick().catch(() => {})

      // Interactive linking run (see link.sh): the operator is watching a live QR
      // or typing a pairing code, and once that has worked this process has done
      // its job and systemd should own the long-lived one. Exit AFTER a beat so
      // the final creds.update is flushed to auth/ — quitting sooner leaves a
      // half-written session that cannot reconnect.
      if (process.env.WA_LINK_ONCE) {
        console.log(`\n✅  Linked as ${me}\n    auth/ now holds the session; handing back to systemd.\n`)
        setTimeout(() => {
          try { store.saveNow() } catch {}
          process.exit(0)
        }, 3000)
      }
    }

    if (connection === 'close') {
      // Baileys reports the reason as a Boom error; the status code is all we need.
      const status = lastDisconnect?.error?.output?.statusCode
      const loggedOut = status === DisconnectReason.loggedOut
      connState = loggedOut ? 'logged-out' : 'reconnecting'
      log.warn({ status, loggedOut }, 'WhatsApp connection closed')

      if (loggedOut) {
        log.error('This device was unlinked. Run `npm run logout`, restart, and scan the QR again.')
        return
      }
      // Anything else is transient (network, server restart, stream error) —
      // reconnect, because the campaign has jobs mid-wait that must survive it.
      setTimeout(() => {
        connect().catch((err) => log.error({ err: err.message }, 'reconnect failed'))
      }, 3000)
    }
  })

  sock.ev.on('messages.upsert', async (event) => {
    // 'notify' is a live incoming message; 'append' is history sync backfill,
    // which must be ignored or a reconnect would replay old replies as new ones.
    if (event.type !== 'notify') return

    for (const m of event.messages) {
      if (m.key.fromMe) continue
      const jid = m.key.remoteJid
      if (!jid || jid.endsWith('@g.us') || jid === 'status@broadcast') continue // groups/status

      const text =
        m.message?.conversation ||
        m.message?.extendedTextMessage?.text ||
        m.message?.imageMessage?.caption ||
        m.message?.videoMessage?.caption ||
        (m.message?.audioMessage ? '[voice note]' : '') ||
        ''

      if (!text) continue
      try {
        // The whole message goes through, not just its text: answering it starts
        // by putting blue ticks on it, and that needs the message key.
        await queue.onIncoming(jid, text, m)
      } catch (err) {
        log.error({ err: err.message, jid }, 'failed handling incoming message')
      }
    }
  })

  return sock
}

// ---------------------------------------------------------------------------
// HTTP API — the only thing the Go agent talks to
// ---------------------------------------------------------------------------

function json(res, code, body) {
  const payload = JSON.stringify(body)
  res.writeHead(code, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
  })
  res.end(payload)
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = ''
    req.on('data', (chunk) => {
      data += chunk
      if (data.length > 64 * 1024) reject(new Error('body too large'))
    })
    req.on('end', () => resolve(data))
    req.on('error', reject)
  })
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`)

  if (req.method === 'GET' && url.pathname === '/health') {
    return json(res, 200, { ok: true })
  }

  if (req.method === 'GET' && url.pathname === '/status') {
    const s = store.stats()
    const win = human.windowState()
    return json(res, 200, {
      connected: connState === 'open',
      state: connState,
      pending: s.queued + s.pinged,
      awaitingQR: connState === 'awaiting-qr',
      awaitingPairingCode: connState === 'awaiting-pairing-code',
      // The dialer watches sentToday against this. Reporting the cap here rather
      // than duplicating it in the Go config means one number, in one place —
      // and the campaign can warn before it walks into a ceiling it cannot see.
      dailySendCap: config.dailySendCap,
      // Same number under the name the Go agent actually unmarshals
      // (outreach.go: `json:"dailyCap"`). Without it s.DailyCap is always 0, so
      // the "approaching the cap" guard in outreach.go never fires and the
      // campaign walks into the ceiling it was supposed to be warned about.
      // Emitting both keeps old and new agent builds working.
      dailyCap: config.dailySendCap,
      // Nothing proactive goes out at four in the morning. The dialer needs to
      // know that, or a late campaign reports leads as messaged when in fact
      // they are parked until the window opens.
      sendWindow: human.describeWindow(),
      sendWindowOpen: win.open,
      sendWindowOpensAt: win.open ? '' : win.opensAt,
      me: sock?.user?.id || '',
      ...s,
    })
  }

  if (req.method === 'GET' && url.pathname === '/jobs') {
    return json(res, 200, { jobs: store.allJobs() })
  }

  if (req.method === 'POST' && url.pathname === '/queue') {
    let payload
    try {
      payload = JSON.parse(await readBody(req))
    } catch (err) {
      return json(res, 400, { error: `bad json: ${err.message}` })
    }

    if (!payload.phone) return json(res, 400, { error: 'phone is required' })
    if (!payload.demoUrl) return json(res, 400, { error: 'demoUrl is required' })

    const { job, created, refreshed, requeued } = store.upsertJob(payload)
    log.info(
      { business: job.business, phone: job.phone, created, status: job.status },
      requeued
        ? 'lead re-queued — they agreed again on a later call'
        : refreshed
          ? 'lead already in flight — details refreshed, no extra message'
          : 'lead queued',
    )

    // Kick the scheduler rather than waiting up to 15s for the next tick, so the
    // ping lands while the phone call is still fresh in their mind.
    queue.tick().catch(() => {})

    // `queued` answers the dialer's only real question: will THIS call produce a
    // message? A refreshed in-flight job will not — one is already on its way —
    // and reporting otherwise is what let results.csv claim seven deliveries
    // backed by a single job.
    return json(res, 202, { queued: created, refreshed: !!refreshed, status: job.status, key: job.key })
  }

  return json(res, 404, { error: 'not found' })
})

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

store.load()
queue.attach({ getSock: () => (connState === 'open' ? sock : null), logger: log })
queue.start()

server.listen(config.port, '127.0.0.1', () => {
  const s = store.stats()
  log.info({ port: config.port, ...s }, 'sidecar listening')
  if (s.queued + s.pinged > 0) {
    log.info(`resuming ${s.queued + s.pinged} job(s) left over from the last run`)
  }
})

connect().catch((err) => {
  log.error({ err: err.message }, 'could not start WhatsApp connection')
  process.exit(1)
})

// Flush the store on the way out so a Ctrl+C mid-campaign doesn't lose the last
// few seconds of state — every job is a lead that was promised a link.
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => {
    log.info('shutting down — saving jobs')
    try { store.saveNow() } catch {}
    process.exit(0)
  })
}
