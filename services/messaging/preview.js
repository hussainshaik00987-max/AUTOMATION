// Print every message template, rendered with real lead data, exactly as a lead
// would read it.
//
//   node preview.js              # everything
//   node preview.js pingHi       # one bank
//   node preview.js --no-name    # as it reads when the call never got a name
//
// The banks are the product here — this is the file to run before a campaign,
// and the one to keep open while editing messages.js. A template that reads
// wrong on the page reads wrong on three hundred phones.

import { banks } from './messages.js'

const args = process.argv.slice(2)
const noName = args.includes('--no-name')
const only = args.filter((a) => !a.startsWith('--'))

const ctx = noName
  ? {
      business: 'Fairlight Studio',
      url: 'https://demo.example.com/sites/fairlight-studio',
      sir: ' sir',
      you: 'sir',
      hi: 'Sir, ',
    }
  : {
      business: 'Fairlight Studio',
      url: 'https://demo.example.com/sites/fairlight-studio',
      sir: ' Rakesh ji',
      you: 'Rakesh ji',
      hi: 'Rakesh ji, ',
    }

const TITLES = {
  pingHi: 'PING — Hinglish        "link bhej doon?"',
  pingEn: 'PING — English         "shall I send the link?"',
  linkHi: 'LINK — Hinglish        the demo, with the screenshot as its caption',
  linkEn: 'LINK — English         the demo, with the screenshot as its caption',
  clarifyHi: 'CLARIFY — Hinglish     sent before the link when the reply was neither yes nor no',
  clarifyEn: 'CLARIFY — English      sent before the link when the reply was neither yes nor no',
}

const rule = (ch = '─') => console.log(ch.repeat(78))

console.log(`\nRendered for: ${ctx.business} · ${noName ? 'no owner name captured' : 'owner name "Rakesh"'}\n`)

for (const [name, bank] of Object.entries(banks())) {
  if (only.length && !only.includes(name)) continue

  rule('━')
  console.log(`  ${TITLES[name] || name}   ·   ${bank.length} variants`)
  rule('━')

  bank.forEach((template, i) => {
    const raw = template(ctx)
    // '§' is the seam the sender may or may not break at; showing it as the two
    // bubbles it can become is the whole point of reading these.
    const parts = raw.split('§')
    const chars = raw.replace(/§/g, '').length

    console.log(`\n  ${String(i + 1).padStart(2)}. ${chars} chars${parts.length > 1 ? ' · can split into 2' : ''}`)
    parts.forEach((part, p) => {
      const body = part.trim().split('\n').map((l) => `      ${l}`).join('\n')
      if (p > 0) console.log('      ┈┈┈ (second bubble, or joined into the first) ┈┈┈')
      console.log(body)
    })
  })
  console.log()
}
