/* Deterministic motion runtime.
 *
 * The page renders exactly one thing: `seek(t)`. Every visual property is a pure
 * function of the timeline position in milliseconds, so frame N is identical no
 * matter when — or how many times — it is drawn. Nothing here uses CSS
 * animations, transitions, requestAnimationFrame or Date: the capture loop calls
 * seek() and screenshots, and a re-run produces byte-comparable frames.
 *
 * The timeline itself is data (window.SCENE, emitted by storyboard.py). This file
 * only knows how to interpolate it.
 */
'use strict';

/* ---------- easing ------------------------------------------------------- */

const E = {
  linear: t => t,
  inQuad: t => t * t,
  outQuad: t => 1 - (1 - t) * (1 - t),
  outCubic: t => 1 - Math.pow(1 - t, 3),
  inOutCubic: t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  outQuart: t => 1 - Math.pow(1 - t, 4),
  outQuint: t => 1 - Math.pow(1 - t, 5),
  outExpo: t => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)),
  inOutSine: t => -(Math.cos(Math.PI * t) - 1) / 2,
  // Gentle overshoot. Tuned well below the classic 1.70158 back-ease: on a
  // message bubble anything springier reads as a toy, not as iOS.
  outBack: t => {
    const c = 0.9;
    return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
  },
};

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const lerp = (a, b, t) => a + (b - a) * t;

/** Normalised, eased progress of a [start, start+dur] window at time t. */
function prog(t, start, dur, ease) {
  if (dur <= 0) return t >= start ? 1 : 0;
  return (ease || E.outCubic)(clamp((t - start) / dur, 0, 1));
}

/** 0 -> 1 -> 0 envelope: fades in over `rise`, holds, fades out before `end`. */
function pulse(t, start, end, rise, fall, ease) {
  const up = prog(t, start, rise, ease || E.outCubic);
  const down = 1 - prog(t, end - fall, fall, E.inOutCubic);
  return Math.min(up, down);
}

const px = v => (Math.round(v * 100) / 100) + 'px';

/* ---------- element cache ------------------------------------------------ */

const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

const D = {};   // dom cache, filled by init()
const M = {};   // measurements, filled by measure()

/* ---------- chat stack --------------------------------------------------- */

/* Bubbles are absolutely positioned from the bottom of the thread and never
 * scrolled. Each item carries a continuous "presence" p in [0,1]; an item's
 * distance from the bottom is the sum of (height + gap) * p over everything
 * below it. Because p is continuous, the whole column slides smoothly as a
 * bubble grows in — and the typing indicator shrinking away while its reply
 * grows in nets out to one fluid motion instead of two competing ones.
 */

const ENTER = 340;   // ms for a bubble to take up its space
const EXIT = 220;    // ms for the typing indicator to give its space back

function itemPresence(item, t) {
  const inP = prog(t, item.at, ENTER, E.outCubic);
  if (item.until == null) return inP;
  const outP = 1 - prog(t, item.until, EXIT, E.inOutCubic);
  return Math.min(inP, outP);
}

function layoutChat(t) {
  const items = SCENE.chat.items;

  // presence + occupied height, bottom-up
  const p = new Array(items.length);
  for (let i = 0; i < items.length; i++) p[i] = itemPresence(items[i], t);

  let acc = 0;
  for (let i = items.length - 1; i >= 0; i--) {
    const el = D.items[i];
    const item = items[i];
    const pi = p[i];

    if (pi <= 0.0001) {
      el.style.opacity = '0';
      el.style.visibility = 'hidden';
      // Items already gone still hold no space; items not yet in hold none either.
      acc += 0;
      continue;
    }
    el.style.visibility = 'visible';

    // Visual envelope is deliberately snappier than the layout envelope, so the
    // bubble has finished appearing while the column is still settling.
    const vIn = prog(t, item.at, ENTER * 0.8, E.outBack);
    const vOut = item.until == null ? 1 : 1 - prog(t, item.until, EXIT * 0.8, E.outQuad);
    const v = Math.min(vIn, vOut);

    el.style.bottom = px(acc);
    el.style.opacity = String(clamp(v, 0, 1));
    const scale = lerp(0.82, 1, clamp(v, 0, 1));
    const rise = (1 - clamp(v, 0, 1)) * 14;
    el.style.transform = `translateY(${px(rise)}) scale(${scale.toFixed(4)})`;

    acc += (M.itemH[i] + item.gap) * pi;
  }
}

/* Typing indicator: three dots on a staggered sine. Period and travel are the
 * iOS values — 600ms cycle, ~4px lift — so it reads as WhatsApp rather than as
 * a generic loader.
 *
 * Only driven while an indicator is actually on screen. Left running the dots
 * would rewrite three styles on every single frame, and the capture loop's
 * duplicate-frame detection would never fire once in the whole video. */
function typingActive(t) {
  for (const w of SCENE.chat.typingWindows) {
    if (t >= w[0] - ENTER && t <= w[1] + EXIT + 40) return true;
  }
  return false;
}

/* The dots are the one thing on screen that moves continuously for seconds at a
 * time, so at 30fps they alone make ~8 seconds of every video unique and force a
 * fresh screenshot per frame. Sampling them on a 100ms grid drops that to 10
 * distinct states per second — invisible on a 7.6pt dot rendered inside a phone
 * inside a video — and hands roughly a fifth of all frames back to the
 * duplicate-frame detector. */
const TYPING_STEP = 100;

function layoutTyping(t) {
  if (!D.typingDots.length || !typingActive(t)) return;
  const tq = Math.round(t / TYPING_STEP) * TYPING_STEP;
  for (let i = 0; i < D.typingDots.length; i++) {
    const phase = (tq / 600) * Math.PI * 2 - i * 0.62;
    const s = Math.sin(phase);
    const lift = Math.max(0, s) * 4.2;
    const op = 0.42 + Math.max(0, s) * 0.5;
    D.typingDots[i].style.transform = `translateY(${px(-lift)})`;
    D.typingDots[i].style.opacity = op.toFixed(3);
  }
}

/* ---------- header presence ---------------------------------------------- */

/* WhatsApp swaps the contact's subtitle between "online" and "typing…" — the
 * cross-fade is what sells that a real person is on the other end. */
function layoutHeader(t) {
  let typing = 0;
  for (const w of SCENE.chat.typingWindows) {
    if (t >= w[0] - 120 && t < w[1] + 60) {
      typing = Math.min(prog(t, w[0] - 120, 200, E.outQuad),
                        1 - prog(t, w[1] - 60, 200, E.outQuad));
      break;
    }
  }
  typing = clamp(typing, 0, 1);
  D.statusOnline.style.opacity = (1 - typing).toFixed(3);
  D.statusOnline.style.transform = `translateY(${px(-typing * 6)})`;
  D.statusTyping.style.opacity = typing.toFixed(3);
  D.statusTyping.style.transform = `translateY(${px((1 - typing) * 6)})`;
}

/* ---------- camera ------------------------------------------------------- */

/* One slow continuous push-in across the chat, then the phone parks left/small
 * or exits for the outro. Keyframes are (t, scale, x, y, rot) from the scene. */
function layoutPhone(t) {
  const k = SCENE.camera;
  let a = k[0], b = k[0];
  for (let i = 0; i < k.length - 1; i++) {
    if (t >= k[i].t && t <= k[i + 1].t) { a = k[i]; b = k[i + 1]; break; }
    if (t > k[i + 1].t) { a = k[i + 1]; b = k[i + 1]; }
  }
  const span = b.t - a.t;
  const u = span <= 0 ? 1 : (E[b.ease] || E.inOutCubic)(clamp((t - a.t) / span, 0, 1));

  const scale = lerp(a.s, b.s, u);
  const x = lerp(a.x, b.x, u);
  const y = lerp(a.y, b.y, u);
  const rot = lerp(a.r || 0, b.r || 0, u);
  const op = lerp(a.o == null ? 1 : a.o, b.o == null ? 1 : b.o, u);

  D.phone.style.transform =
    `translate(-50%,-50%) translate(${px(x)}, ${px(y)}) scale(${scale.toFixed(4)}) rotate(${rot.toFixed(3)}deg)`;
  D.phone.style.opacity = op.toFixed(3);
}

/* ---------- text layers -------------------------------------------------- */

/* Every caption, card and CTA is a "layer": a element with an in/out window and
 * a named entrance. Lines inside a layer stagger, because a block of type that
 * arrives all at once has no reading order. */
function layoutLayers(t) {
  for (const L of SCENE.layers) {
    const el = D.layer[L.id];
    if (!el) continue;
    const live = t >= L.in - 400 && t <= L.out + 400;
    if (!live) { el.style.visibility = 'hidden'; continue; }
    el.style.visibility = 'visible';

    const a = prog(t, L.in, L.rise == null ? 520 : L.rise, E.outQuint);
    const b = 1 - prog(t, L.out, L.fall == null ? 320 : L.fall, E.inOutCubic);
    const o = Math.min(a, b);
    el.style.opacity = o.toFixed(3);

    const kids = D.layerLines[L.id] || [];
    const stagger = L.stagger == null ? 90 : L.stagger;
    for (let i = 0; i < kids.length; i++) {
      const s = prog(t, L.in + i * stagger, L.rise == null ? 620 : L.rise, E.outQuint);
      const e = 1 - prog(t, L.out + i * (stagger * 0.4), L.fall == null ? 320 : L.fall, E.inOutCubic);
      const q = Math.min(s, e);
      const dy = (1 - s) * (L.travel == null ? 26 : L.travel);
      const blur = (1 - s) * (L.blur == null ? 8 : L.blur);
      kids[i].style.opacity = q.toFixed(3);
      kids[i].style.transform = `translateY(${px(dy)})`;
      kids[i].style.filter = blur > 0.15 ? `blur(${blur.toFixed(2)}px)` : 'none';
    }

    // Optional wipe on a highlight rule/underline.
    const bar = el.querySelector('[data-bar]');
    if (bar) {
      const w = prog(t, L.in + (L.barDelay == null ? 240 : L.barDelay), 620, E.outQuint);
      bar.style.transform = `scaleX(${w.toFixed(4)})`;
    }
  }
}

/* ---------- init / measure ----------------------------------------------- */

function init() {
  D.phone = $('#phone');
  D.items = $$('#thread .item');
  D.typingDots = $$('#thread .tdot');
  D.statusOnline = $('#st-online');
  D.statusTyping = $('#st-typing');

  D.layer = {};
  D.layerLines = {};
  for (const L of SCENE.layers) {
    const el = document.getElementById('L-' + L.id);
    D.layer[L.id] = el;
    D.layerLines[L.id] = el ? Array.from(el.querySelectorAll('[data-line]')) : [];
  }
  // Everything seek() can write to, in a stable order.
  D.digestEls = [D.phone, D.statusOnline, D.statusTyping]
    .concat(D.items, D.typingDots);
  for (const L of SCENE.layers) {
    if (D.layer[L.id]) D.digestEls.push(D.layer[L.id]);
    D.digestEls = D.digestEls.concat(D.layerLines[L.id] || []);
    const bar = D.layer[L.id] && D.layer[L.id].querySelector('[data-bar]');
    if (bar) D.digestEls.push(bar);
  }
  D.digestEls = D.digestEls.filter(Boolean);

  fitStamps();
  measure();
}

/* WhatsApp keeps the timestamp inside the bubble by reserving room for it at the
 * end of the last line. The reserved width has to match the rendered stamp
 * exactly — guess low and the time sits on top of the final word, guess high and
 * short bubbles gain a visible gutter. So measure the stamp and size the spacer
 * from it, which also survives a different font or a two-digit hour. */
function fitStamps() {
  for (const el of D.items) {
    const meta = el.querySelector('.meta');
    const spacer = el.querySelector('.spacer');
    if (!meta || !spacer) continue;
    // offsetWidth is in the zoomed subtree's own units, the same units the
    // spacer's width is written in, so no scale correction is needed.
    spacer.style.width = px(meta.offsetWidth + 5);
  }
}

/* Heights must be read with every bubble laid out at its natural size and with
 * no transform applied — a scaled element reports its scaled box. Run this after
 * fitStamps(), because a wider spacer can push a bubble onto another line. */
function measure() {
  M.itemH = D.items.map(el => {
    const prev = el.style.cssText;
    el.style.cssText = prev + ';opacity:0;visibility:visible;transform:none;bottom:0;';
    const h = el.offsetHeight;
    el.style.cssText = prev;
    return h;
  });
}

/* ---------- entry point -------------------------------------------------- */

/* A 32-bit FNV-1a over every inline style this frame wrote.
 *
 * Roughly 40% of a finished video is frames where nothing moves at all — the
 * gap while the customer "reads" a reply, the beat before the indicator
 * appears, the hold on a card. Screenshotting those again costs ~107ms each and
 * produces a byte-identical JPEG. The capture loop compares this digest against
 * the previous frame and re-sends the buffer it already has. It is exact rather
 * than approximate: identical digest means identical written styles, which
 * means identical pixels. */
function digest() {
  let s = '';
  for (let i = 0; i < D.digestEls.length; i++) {
    s += D.digestEls[i].getAttribute('style') || '';
    s += '|';
  }
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function seek(t) {
  layoutPhone(t);
  layoutChat(t);
  layoutTyping(t);
  layoutHeader(t);
  layoutLayers(t);
  document.documentElement.dataset.t = String(Math.round(t));
  return digest();
}

window.__seek = seek;
window.__init = init;
window.__duration = () => SCENE.duration;

document.addEventListener('DOMContentLoaded', () => {
  init();
  seek(0);
  document.documentElement.dataset.ready = '1';
});
