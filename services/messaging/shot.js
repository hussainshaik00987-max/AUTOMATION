// ---------------------------------------------------------------------------
// Demo-site screenshots, server side.
//
// The call now offers a picture as well as a link — "screenshot या demo link
// भेज दूँ?" — and a promise made on a call has to be one the sidecar can keep.
//
// Almost every screenshot is expected to be found, not made: shotgen.js renders
// the whole lead list on the laptop, off the local demo build, and uploads the
// results here. Both sides name files by the same hash of the demo URL, so this
// is usually a stat() and nothing more.
//
// The on-demand render below is the safety net for a lead who was never in that
// batch. It is deliberately allowed to fail: a screenshot is worth having, and
// never worth losing a lead over. When it returns null the caller sends the
// link on its own, exactly as it did before any of this existed.
// ---------------------------------------------------------------------------

import fs from 'node:fs'
import path from 'node:path'

import puppeteer from 'puppeteer-core'

import { launchArgs, renderHero, shotKey } from './shotlib.js'

const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome'
const SHOT_DIR = process.env.SHOT_DIR || 'shots'
// A render holds a Chrome tab open on a four-core box that is also carrying live
// calls. One at a time, and never for long.
const RENDER_TIMEOUT_MS = 60_000

let browserPromise = null
let log = console

export function attach(opts = {}) {
  log = opts.logger || console
}

// browser keeps ONE Chrome for the life of the process. Launching per screenshot
// costs about a second and a lot of memory, and the sidecar can be asked for
// several in a row when a campaign finishes.
async function browser() {
  if (!browserPromise) {
    browserPromise = puppeteer.launch({
      executablePath: CHROME,
      headless: true,
      args: [...launchArgs(), '--no-sandbox'],
    })
    browserPromise.catch(() => { browserPromise = null })
  }
  return browserPromise
}

export async function close() {
  if (!browserPromise) return
  try { (await browserPromise).close() } catch {}
  browserPromise = null
}

// forDemo returns a path to the screenshot for this demo URL, or null.
export async function forDemo(demoUrl, { render = true } = {}) {
  if (!demoUrl) return null

  const file = path.join(SHOT_DIR, await shotKey(demoUrl))
  try {
    if (fs.statSync(file).size > 1000) return file
  } catch {}

  if (!render) return null

  try {
    fs.mkdirSync(SHOT_DIR, { recursive: true })
    const buf = await Promise.race([
      renderHero(await browser(), demoUrl),
      new Promise((_, rej) => setTimeout(() => rej(new Error('render timed out')), RENDER_TIMEOUT_MS)),
    ])
    fs.writeFileSync(file, buf)
    log.info?.({ demoUrl, file, bytes: buf.length }, 'rendered demo screenshot on demand')
    return file
  } catch (err) {
    // Not an error path worth failing a lead over — see the header.
    log.warn?.({ demoUrl, err: err.message }, 'could not render a screenshot — sending the link on its own')
    return null
  }
}
