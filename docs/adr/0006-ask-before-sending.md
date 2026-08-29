# ADR 0006 — Ask before sending, even though it costs conversions

**Status:** Accepted

## Context

When a prospect agrees on the call, the fastest thing to do is send the link the
instant the line drops. Every extra step between "yes" and the link is a step where
the lead can be lost — and one *was* lost exactly there, in a 50-dial run.

The sidecar instead sends a short "shall I send it here?" first, waits for a reply or
30–40 minutes, and only then sends the link.

## Decision

Never send the link as the first message to a number that has never messaged you.

## Consequences

**Why, in order of weight:**

1. **It is the pattern spam detection is built for.** An unsolicited link as an
   opening message to a stranger is precisely the shape that gets an account banned —
   and precisely what a person reports.
2. **Any reply is the strongest signal the platform has** that this is a real
   conversation rather than a broadcast. Asking is the cheapest way to obtain one.
3. **It is better selling.** "Shall I send it?" invites a yes; a wall of text with a
   link invites a block.
4. **It is a second consent checkpoint**, on a different channel, after the one given
   on the call. A person who said yes to a caller and means no now has somewhere to
   say so — and "no" here means the link is **never** sent, permanently.

**Costs, accepted:**

- A lead who said yes and then never opens the app gets the link 30–40 minutes later
  instead of immediately.
- Two messages instead of one, which is more platform surface, not less — mitigated by
  never splitting the first cold message into two bubbles.
- Roughly 35–45 seconds of outbox time per message once human-paced delivery is
  included, so a full day's cap is 2½–3 hours of wall clock. **The pacing is the
  mitigation**, so this cost is the feature.

**The related decision that is not negotiable:** the agent refuses to dial at all if
the sidecar is not linked. This was a warning first, and a warning is something you
walk past — seven live calls went out against a dead sidecar, each one telling a real
person their link was on the way. An override exists for pipeline testing; it has to
be typed, and it says exactly what it costs.
