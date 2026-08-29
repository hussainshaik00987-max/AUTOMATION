// ---------------------------------------------------------------------------
// Bulk demo-site screenshots, rendered on the laptop.
//
//   node shotgen.js --leads ../agent-openai/top_leads.csv \
//                   --sites ../Scrap/demosites/out/sites \
//                   --out shots [--limit 50] [--force] [--concurrency 4]
//
//   node shotgen.js --upload           # ...then push them to the server
//
// Why here rather than on the server: the demo pages are 2,000 static files that
// already exist locally, so rendering them needs no network and no Vercel round
// trip, and this machine is several times the VPS. The sidecar can still render
// one on demand — see shot.js — but that path exists as a safety net, not as the
// way the work normally gets done.
//
// Screenshots are named by the same hash of the demo URL that the sidecar uses,
// which is the whole trick: this writes a file, the server simply finds it.
// ---------------------------------------------------------------------------

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { execFileSync } from 'node:child_process'

import puppeteer from 'puppeteer-core'

import { launchArgs, renderHero, shotKey, VIEWPORT } from './shotlib.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`)
  if (i === -1) return fallback
  const next = process.argv[i + 1]
  return !next || next.startsWith('--') ? true : next
}

const CHROME =
  process.env.CHROME_PATH ||
  ['C:/Program Files/Google/Chrome/Application/chrome.exe',
   'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
   '/usr/bin/google-chrome'].find((p) => fs.existsSync(p))

// A very small CSV reader. It handles quoted fields with embedded commas, which
// the lead file has plenty of, and nothing else — this reads two columns out of
// a file the Go side already owns and validates.
function readCsv(file) {
  const text = fs.readFileSync(file, 'utf8')
  const rows = []
  let row = [], field = '', quoted = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++ } else quoted = false
      } else field += c
    } else if (c === '"') quoted = true
    else if (c === ',') { row.push(field); field = '' }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = '' }
    else if (c !== '\r') field += c
  }
  if (field || row.length) { row.push(field); rows.push(row) }
  if (!rows.length) return []

  const head = rows[0].map((h) => h.trim().toLowerCase())
  const iUrl = head.findIndex((h) => h.includes('demo'))
  const iBiz = head.findIndex((h) => h.includes('business') || h === 'name')
  return rows.slice(1)
    .filter((r) => r[iUrl]?.trim())
    .map((r) => ({ url: r[iUrl].trim(), business: (r[iBiz] || '').trim() }))
}

// localFor maps a published demo URL back to the file that produced it, so the
// render needs neither Vercel nor a network. Falls back to the URL itself when
// there is no local build for that lead.
function localFor(url, sitesDir) {
  const slug = url.split('/').filter(Boolean).pop()
  if (!slug) return url
  const file = path.join(sitesDir, slug.endsWith('.html') ? slug : `${slug}.html`)
  return fs.existsSync(file) ? pathToFileURL(file).href : url
}

async function main() {
  if (arg('upload')) return upload()

  const leadsFile = arg('leads', '../agent-openai/top_leads.csv')
  const sitesDir = path.resolve(HERE, arg('sites', '../Scrap/demosites/out/sites'))
  const outDir = path.resolve(HERE, arg('out', 'shots'))
  const limit = Number(arg('limit', 0)) || 0
  const force = !!arg('force')
  const concurrency = Math.max(1, Number(arg('concurrency', 4)) || 4)
  // Deliberately NOT inside outDir. The profile is tens of megabytes of image
  // cache, and outDir is the directory that gets copied to the server — the
  // first upload spent four minutes shipping a Chrome cache nobody wanted.
  const profileDir = path.resolve(HERE, arg('profile', '.shotcache'))

  if (!CHROME) {
    console.error('No Chrome found. Set CHROME_PATH.')
    process.exit(1)
  }

  let leads = readCsv(path.resolve(HERE, leadsFile))
  if (limit) leads = leads.slice(0, limit)
  fs.mkdirSync(outDir, { recursive: true })

  console.log(`${leads.length} leads · ${VIEWPORT.width}x${VIEWPORT.height}@${VIEWPORT.deviceScaleFactor}x · ${concurrency} at a time`)
  console.log(`sites  ${sitesDir}`)
  console.log(`out    ${outDir}\n`)

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    // The GPU flags are honest about what they buy: for a static page this is a
    // few percent, not a transformation. What actually makes this fast is doing
    // it here, off local files, several pages at once.
    // A PERSISTENT profile, which is the single biggest win here. The pages are
    // local but their photographs are not: 2,000 demos draw on a shared library
    // of 582 images from a remote asset host, so a cold run spends nearly all
    // its time re-downloading pictures it has already seen. With the disk cache
    // kept between runs the hit rate approaches 100% after the first few dozen
    // pages.
    //
    // The GPU flags are honest about what they buy: on a static page this is a
    // few percent, not a transformation. What makes this fast is local files, a
    // warm image cache, and several pages at once.
    userDataDir: profileDir,
    args: [...launchArgs(), '--enable-gpu-rasterization', '--ignore-gpu-blocklist'],
  })

  let done = 0, made = 0, cached = 0, failed = 0
  const started = Date.now()

  const queue = leads.slice()
  async function worker() {
    for (;;) {
      const lead = queue.shift()
      if (!lead) return
      const out = path.join(outDir, await shotKey(lead.url))
      done++
      if (!force && fs.existsSync(out) && fs.statSync(out).size > 1000) {
        cached++
        continue
      }
      try {
        const buf = await renderHero(browser, localFor(lead.url, sitesDir))
        fs.writeFileSync(out, buf)
        made++
        if (made % 25 === 0 || leads.length < 25) {
          const rate = made / ((Date.now() - started) / 1000)
          console.log(`  ${done}/${leads.length}  ${lead.business || lead.url}  (${rate.toFixed(1)}/s)`)
        }
      } catch (err) {
        failed++
        console.error(`  FAILED ${lead.business || lead.url}: ${err.message}`)
      }
    }
  }

  await Promise.all(Array.from({ length: concurrency }, worker))
  await browser.close()

  const secs = ((Date.now() - started) / 1000).toFixed(1)
  console.log(`\nrendered ${made} · cached ${cached} · failed ${failed} · ${secs}s`)
  console.log(`\nNow push them to the server:\n  node shotgen.js --upload`)
}

// upload puts the rendered shots where the sidecar looks for them. Rsync-ish via
// scp -r, which is enough: the files are small, named by content-independent
// hash, and overwriting one with an identical one costs nothing.
function upload() {
  const outDir = path.resolve(HERE, arg('out', 'shots'))
  const host = arg('host', 'root@YOUR_VPS_IP')
  const key = arg('key', `${process.env.USERPROFILE || process.env.HOME}/.ssh/id_ed25519`)
  const dest = arg('dest', '/opt/whatsapp-sidecar/shots')
  const n = fs.existsSync(outDir) ? fs.readdirSync(outDir).filter((f) => f.endsWith('.jpg')).length : 0
  if (!n) {
    console.error(`nothing to upload — ${outDir} has no .jpg files`)
    process.exit(1)
  }
  console.log(`uploading ${n} screenshots to ${host}:${dest}`)
  execFileSync('ssh', ['-i', key, host, `mkdir -p ${dest}`], { stdio: 'inherit' })
  // Named files only. A glob would also sweep up anything else that has landed
  // in the directory, and this copy goes to a production box.
  const jpgs = fs.readdirSync(outDir).filter((f) => f.endsWith('.jpg')).map((f) => path.join(outDir, f))
  for (let i = 0; i < jpgs.length; i += 200) {
    execFileSync('scp', ['-i', key, ...jpgs.slice(i, i + 200), `${host}:${dest}/`], { stdio: 'inherit' })
  }
  console.log('done')
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
