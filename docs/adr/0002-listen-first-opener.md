# ADR 0002 — Release the opener on positive evidence of a person only

**Status:** Accepted

## Context

The first line is pre-recorded, plays to completion, and is the single highest-stakes
moment of the call. It must not be spoken to an answering machine, and it must not be
delayed on a person.

Those two requirements pull in opposite directions, and the arbiter is slow. Premium
carrier machine-detection was the only reliable discriminator available — it got all
six answered calls in the first campaign right — but its verdicts landed at
**3.0, 3.1, 4.3, 4.9, 5.1 and 6.5 seconds** after pickup. Waiting for it means dead
air; ignoring it means pitching recordings, which is exactly what the first version
did, three times in one run.

The obvious heuristic does not work. Measured frame-by-frame off one run's own
prospect channels, a human answering and a machine answering produce the same signal
for the first two seconds:

| | Shape |
|---|---|
| human | spoke 1,320ms straight, then quiet for 6.6s |
| machine | spoke 280ms, **paused 940ms**, carried on |
| machine | spoke 60ms, **paused 1,020ms**, carried on |
| human | silent for 3.1s, then spoke |

And the hardest case, from a validation call: a person answered with a **280ms sound
and waited 8.4 seconds** — the same 280ms, in the same position, as a machine. Nothing
about the *sound* separates them. What separates them is the silence **after**:
8,440ms against 940ms.

## Decision

Release the opener on **positive evidence of a person and nothing else**:

- the detector says human; **or**
- one whole utterance landed and the line has gone quiet; **or**
- one short fragment followed by enough silence that no recorded greeting would still
  be pausing; **or**
- nothing said at all and the line quiet at a hard timeout.

Everything else is machine-shaped, and the opener is **held for the detector** —
bounded at 3.5s when the line is quiet and 7s when it is not. A `not_sure` verdict is
not a machine (it is the detector running out of time) and **releases** the hold
rather than extending it.

Implemented as a **pure function**, so real calls can be replayed against it in a test
rather than argued about.

## Consequences

The errors are deliberately asymmetric, and the thresholds are set from that:
speaking too early to a machine costs a couple of seconds of carrier time and the
detector hangs up anyway; **waiting too long on a person loses the lead.** Silence is
expensive and speech is free, because speaking over someone who is talking is not an
improvement on waiting.

The tell that does most of the work is behavioural, not acoustic: **a person who
answers says one thing and waits for the caller; a recorded greeting pauses for
breath and starts again.** A second utterance before the agent has spoken at all is
the only cheap machine signal that survived measurement.

Against the six recorded calls, all three machines are held past their verdict and no
human is delayed by a single tick.

**Known cost, measured in the next run:** the hard timeout still fires while some
people are mid-"hello" — the opener overlapped speech on 21 of 38 connected calls,
median 320ms. The fix is to split the two jobs the opener is doing at once, with a
cheap presence token at the timeout and the substantive line held until the far end
hands the turn back. See [the teardown](../campaign-teardown.md#part-d--conversation-quality).
