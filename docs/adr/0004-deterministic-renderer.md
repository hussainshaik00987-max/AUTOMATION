# ADR 0004 — Deterministic frame-by-frame renderer, not a real-time recorder

**Status:** Accepted

## Context

2,000 personalised videos have to be produced on one workstation, reviewed, and
re-produced when the design changes. The default approach is to animate the scene in
the browser and screen-record it.

A real-time recorder has one property that makes it unusable at this scale: **the
output depends on the machine.** A loaded box drops frames, so the file that ships is
a function of what else was running. That is unacceptable when the artifact goes to a
customer and when a QA failure has to be reproducible.

## Decision

The page **never animates itself.** A `seek(t)` function writes every animated
property as a pure function of timeline position. The capture loop calls it frame by
frame and screenshots. Nothing uses CSS animation, transition, `requestAnimationFrame`
or `Date`. Timing lives in a storyboard, in milliseconds, and is emitted as data; the
runtime only knows how to interpolate it.

## Consequences

**Correctness**

- The same input always produces the same file.
- A slow machine produces the *same file* as a fast one, rather than dropping frames.
- One clip can be sharded across workers by **timeline**, not by scene: a worker
  starting at t=45s draws exactly the frames it would have drawn playing from zero.
  Measured on a single-scene explainer: 100s wall clock for a 61.6s clip.

**Performance — the part that was not obvious**

Purity is what makes the largest optimisation legal. `seek()` returns a hash of every
style it wrote; an equal hash means identical pixels, so the previous encoded JPEG is
re-sent instead of screenshotting. **~36% of frames are never captured.**

That has a design consequence that looks like a creative choice and is not: **the
camera holds dead still through the conversation.** A slow push-in would be
imperceptible at ~2px/sec and would change a transform on *every* frame, defeating the
whole optimisation. The cheapest-looking decision in the piece is load-bearing.

**Cost**

- Every animation must be expressible as a closed-form function of `t`. No physics, no
  state carried between frames, no easing that depends on the previous value.
- Element heights are measured once at init **with transforms cleared**, because a
  scaled element reports its scaled box.
- Authoring is slower than dropping in a CSS transition.

**What it enabled downstream:** because the scene is a pure function of data, a
verifier can drive a real browser over a sample and assert on things a contact sheet
cannot show — copy that ran outside the frame (`overflow:hidden` crops it *silently*),
a title that wrapped, an unsubstituted slot, any network request, any console error.
