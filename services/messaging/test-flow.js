// Offline rehearsal of the whole follow-up flow — no WhatsApp account, no
// messages actually sent. It swaps in a fake socket and compressed timings and
// drives all three outcomes a lead can have, printing exactly what each one
// would receive, in how many bubbles, and what the account did around it.
//
//   node test-flow.js
//
// Run this after changing messages.js, human.js, intent.js or queue.js.

import { config } from './config.js'
import { banks } from './messages.js'
import * as queue from './queue.js'
import * as store from './store.js'

// Compress the 30-40 minute wait to a few seconds so the timeout path is
// observable, and drop the anti-ban pacing and the human dawdling that would
// otherwise make this rehearsal take a minute per message.
config.waitMinMs = 4000
config.waitMaxMs = 6000
config.typingMinMs = 200
config.typingMaxMs = 400
config.typingCps = 400
config.typingCapMs = 800
config.typingPauseMinMs = 20
config.typingPauseMaxMs = 60
config.readDelayMinMs = 50
config.readDelayMaxMs = 150
config.bubbleGapMinMs = 30
config.bubbleGapMaxMs = 90
config.replyDelayMinMs = 300
config.replyDelayMaxMs = 600
config.sendGapMinMs = 100
config.sendGapMaxMs = 200
// The rehearsal has to work at any hour, including the ones a campaign would
// correctly refuse to send in.
config.sendWindow = false
// Force both shapes to appear in one run rather than leaving it to chance.
config.splitRatePing = 0.5
config.splitRateLink = 0.5
config.storeFile = new URL('./jobs.test.json', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')

const transcript = []
const presence = []

const fakeSock = {
  async onWhatsApp(jid) {
    const digits = jid.split('@')[0]
    // 919000000003 stands in for a landline that isn't on WhatsApp.
    if (digits.endsWith('0003')) return [{ exists: false }]
    return [{ exists: true, jid: `${digits}@s.whatsapp.net` }]
  },
  async presenceSubscribe(jid) { presence.push({ jid, state: 'subscribe' }) },
  async sendPresenceUpdate(state, jid) {
    presence.push({ jid, state })
    if (state === 'composing') transcript.push({ jid, kind: 'typing…' })
  },
  async sendMessage(jid, content) {
    transcript.push({
      jid,
      kind: 'message',
      text: content.caption ?? content.text,
      image: !!content.image,
    })
    return { key: { id: `fake-${Date.now()}` } }
  },
  // The profile lookups a real client makes on opening a chat.
  async profilePictureUrl(jid) { presence.push({ jid, state: 'dp' }); return 'https://example.invalid/dp.jpg' },
  async fetchStatus(jid) { presence.push({ jid, state: 'status' }); return [{ status: { status: 'Available' } }] },
  async getBusinessProfile(jid) { presence.push({ jid, state: 'bizcard' }); return undefined },
  async readMessages(keys) { transcript.push({ jid: keys[0]?.remoteJid, kind: 'read' }) },
}

const log = {
  info: (o, m) => console.log(`  · ${m}`, o?.business ? `(${o.business})` : ''),
  warn: (o, m) => console.log(`  ! ${m}`, o?.business ? `(${o.business})` : ''),
  error: (o, m) => console.log(`  ✗ ${m}`, o?.err || ''),
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const incoming = (jid, text) => ({ key: { remoteJid: jid, id: `in-${Date.now()}`, fromMe: false } })

function show(label, phone) {
  console.log(`\n${label}`)
  const key = store.phoneKey(phone)
  const mine = transcript.filter((t) => t.jid && store.phoneKey(t.jid.split('@')[0]) === key)
  if (!mine.some((t) => t.kind === 'message')) console.log('  (nothing sent)')

  for (const t of mine) {
    if (t.kind === 'typing…') console.log('  ⌨  typing…')
    else if (t.kind === 'read') console.log('  ✓✓ read their message')
    else console.log(`  ▶ ${t.image ? '[screenshot] ' : ''}${t.text.replace(/\n/g, '\n    ')}`)
  }

  const bubbles = mine.filter((t) => t.kind === 'message').length
  const looked = presence.filter((p) => store.phoneKey(p.jid?.split('@')[0] || '') === key && p.state === 'dp').length
  const job = store.getJob(key)
  console.log(`  → status: ${job.status}${job.note ? ` (${job.note})` : ''} · ${bubbles} bubble(s)${looked ? ' · profile looked up' : ''}`)
}

// ---------------------------------------------------------------------------
// Template audit — the thing that silently rots.
//
// A duplicated line pasted into a bank, or a bank that shrank in an edit, is
// invisible until a hundred leads have read the same sentence. Cheap to check.
// ---------------------------------------------------------------------------
function auditBanks() {
  const ctx = {
    business: 'Fairlight Studio',
    url: 'https://demo.example.com/sites/fairlight-studio',
    sir: ' Rakesh ji',
    you: 'Rakesh ji',
    hi: 'Rakesh ji, ',
  }

  let bad = 0
  console.log('\n══════════ TEMPLATE BANKS ══════════')
  for (const [name, bank] of Object.entries(banks())) {
    const rendered = bank.map((t) => t(ctx).replace(/§/g, '\n\n'))
    const unique = new Set(rendered).size
    const lengths = rendered.map((r) => r.length).sort((a, b) => a - b)
    const spread = `${lengths[0]}–${lengths[lengths.length - 1]} chars`

    const dupes = unique !== bank.length
    if (dupes) bad++
    console.log(
      `  ${dupes ? '✗' : '✓'} ${name.padEnd(10)} ${String(bank.length).padStart(2)} variants · ${unique} unique · ${spread}`,
    )
    if (dupes) {
      const seen = new Set()
      for (const r of rendered) {
        if (seen.has(r)) console.log(`      duplicate: ${r.slice(0, 70)}…`)
        seen.add(r)
      }
    }
  }

  // Every link template must carry the URL, and the URL must be in the LAST
  // segment — that is the one a screenshot is attached to as its caption.
  for (const name of ['linkHi', 'linkEn']) {
    for (const [i, t] of banks()[name].entries()) {
      const parts = t(ctx).split('§')
      if (!parts[parts.length - 1].includes(ctx.url)) {
        console.log(`  ✗ ${name}[${i}] — the URL is not in the final segment`)
        bad++
      }
    }
  }
  return bad
}

// Deal a bank dry and confirm all of it comes out before anything repeats.
function auditDeck() {
  const size = banks().pingHi.length
  const dealt = Array.from({ length: size }, () => store.dealFrom('audit', size))
  const ok = new Set(dealt).size === size
  console.log(`  ${ok ? '✓' : '✗'} deck       ${size} deals covered ${new Set(dealt).size}/${size} variants before reshuffling`)
  return ok ? 0 : 1
}

async function main() {
  // Start from a clean store so repeated runs are comparable.
  try { (await import('node:fs')).rmSync(config.storeFile, { force: true }) } catch {}
  store.load()
  queue.attach({ getSock: () => fakeSock, logger: log })

  const lead = (phone, business, ownerName, language = 'hi') => ({
    phone,
    business,
    ownerName,
    language,
    demoUrl: `https://demo.example.com/sites/${business.toLowerCase().replace(/\s+/g, '-')}`,
  })

  console.log('=== queueing 5 leads ===')
  store.upsertJob(lead('+919000000001', 'Fairlight Studio', 'Rakesh'))
  store.upsertJob(lead('+919000000002', 'Cake Corner', ''))
  store.upsertJob(lead('+919000000003', 'Landline Photos', 'Suresh'))
  store.upsertJob(lead('+919000000004', 'Nova Interiors', 'Priya', 'en'))
  store.upsertJob(lead('+919000000005', 'Silent Bakers', 'Imran'))

  console.log('\n=== tick 1 — pings go out ===')
  await queue.tick()

  console.log('\n=== replies arrive ===')
  await queue.onIncoming('919000000001@s.whatsapp.net', 'haan bhej do', incoming('919000000001@s.whatsapp.net'))
  await queue.onIncoming('919000000002@s.whatsapp.net', 'nahi chahiye bhai', incoming('919000000002@s.whatsapp.net'))
  await queue.onIncoming('919000000004@s.whatsapp.net', 'who is this?', incoming('919000000004@s.whatsapp.net'))

  console.log('\n=== answers go out after a human pause ===')
  await sleep(1200)
  await queue.tick()

  console.log('\n=== waiting out the no-reply window for anyone left ===')
  await sleep(7000)
  await queue.tick()
  await sleep(1500)

  console.log('\n\n══════════ WHAT EACH LEAD RECEIVED ══════════')
  show('▸ Fairlight Studio — replied "haan bhej do"', '+919000000001')
  show('▸ Cake Corner — replied "nahi chahiye bhai"', '+919000000002')
  show('▸ Landline Photos — number not on WhatsApp', '+919000000003')
  show('▸ Nova Interiors — replied "who is this?" (English call)', '+919000000004')
  show('▸ Silent Bakers — never replied, link sent after the wait', '+919000000005')

  const problems = auditBanks() + auditDeck()

  console.log('\n══════════ FINAL TALLY ══════════')
  console.log(store.stats())

  try { (await import('node:fs')).rmSync(config.storeFile, { force: true }) } catch {}
  if (problems) console.log(`\n✗ ${problems} problem(s) in the message banks`)
  process.exit(problems ? 1 : 0)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
