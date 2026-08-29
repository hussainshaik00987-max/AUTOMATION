# ADR 0001 — Text-out brain + dedicated TTS, not a native-audio model

**Status:** Accepted · supersedes the original design
**Date:** the second build of the voice service

## Context

The first working version of the agent used a **native-audio realtime model**: audio
in, audio out, one WebSocket, one vendor. It is the obvious architecture and it has a
real advantage — the model controls prosody directly, so an interruption or a change
of tone happens in the audio rather than being reconstructed from text.

Two things made it untenable for this campaign.

**The voice was wrong in a way that could not be fixed.** The campaign runs in a
language whose native speakers immediately hear a non-native accent, and the
available voices were not it. A voice that mispronounces the language it is speaking
is not a quality problem on a cold call — it is the first two seconds, which is the
whole call.

**Nothing could be inspected between the decision and the sound.** With audio out,
there is no text to log, to assert on, to scan for a refusal, or to refuse to say.
Several of the guarantees this system depends on ([ADR 0003](0003-guardrails-in-code.md))
operate on what the agent is *about to say*, and with native audio there is no such
artifact.

A third, smaller factor: a native-audio session could not be exercised in a text-only
simulation mode, so every change had to be tested against a live audio session.

## Decision

Split the loop:

```
prospect audio ──▶ realtime model (audio in, TEXT out) ──▶ text
                                                            │
                                            guards, logging, refusal scan
                                                            ▼
                                          streaming TTS ──▶ μ-law ──▶ carrier
```

The model reasons and calls tools; a dedicated low-latency TTS speaks. Audio never
leaves the TTS vendor's format — μ-law at 8kHz, matching the telephony codec, so
there is no transcode in the hot path.

## Consequences

**Gained**

- A voice actually native to the campaign's language, chosen independently of the
  model vendor and swappable without touching the conversation code.
- **Text exists at the moment it matters.** Every guard in ADR 0003 operates on it.
- The whole conversation layer is testable without audio.
- The two vendors fail independently, and the failure modes are different, which
  made the keepalive bug in [the teardown](../campaign-teardown.md#c1--the-brain-socket-died-during-a-long-ring-and-took-an-answered-call-with-it)
  diagnosable: the voice socket was fine, the brain socket was dead, and the
  difference between them *was* the bug.

**Paid**

- One more network hop in the reply path, and one more thing that can be down.
- Prosody is reconstructed from text rather than authored. Emphasis and hesitation
  have to be requested rather than felt.
- Two sockets to keep alive rather than one — and the failure to do so cost an
  answered call before it was found.
