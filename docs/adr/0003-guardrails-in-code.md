# ADR 0003 — Safety guarantees live in code, never in the prompt

**Status:** Accepted

## Context

Every guarantee this system makes to the people it calls started life as a sentence
in the playbook. Each of them was then violated on a live call, by a model that was
not malfunctioning — it was doing something reasonable with the instructions it had.

**A refusal.** The prompt said, on "I don't need anything": *send it anyway, it's
free*. Told exactly that by a real prospect, the agent said "oh sure, no problem",
delivered the whole pitch, called the send tool without ever getting a yes, and
logged a delivery. **It was obeying its instructions.**

**A repeated fact.** The pre-recorded opener spends one number out loud before the
system instruction is ever read. The prompt told the model not to repeat it. Nine
seconds into a validation call, it repeated it — not disobeying, just using what it
had been handed.

**An invented conversation.** Four tool calls in 4.1 seconds with no word from the far
end between them: it asked for the owner's name, answered itself `"human"`, asked when
to call back, answered itself, and hung up on a man still listening. The playbook said
STOP eleven times.

## Decision

A behaviour that must not happen is enforced by a mechanism that the model does not
mediate.

| Guarantee | Mechanism |
|---|---|
| A refusal ends the call | A transcript scanner latches refusal **independently of the model**, the send tool refuses once latched, and dispatch refuses **again** at the last gate before a message reaches a real person — because the refusal can arrive *after* the tool call, and on a live call it did. |
| No fabricated prospect input | A tool call is refused when there has been no prospect audio **and** no transcript since the model's previous tool call. Tools in the *same* batch are fine — "yes send it, my name's Alex" legitimately produces two. It is the next batch, arriving out of silence, that is fiction. |
| No repeating a fact already spent | The per-lead research builder **omits the figure entirely**. What the model cannot see, it cannot repeat. |
| No claim the data can't support | The pitch line is derived per-lead from that lead's own probe result, with an explicit **ask** branch when the classification is unknown. |

Two supporting rules fell out of the third failure:

- **Tool responses are prompt.** Each response names exactly **one** thing to say and
  requires silence after it. A list of steps is a list the model will complete.
- **A note that ends a turn must name the *next* step, not the last one.** A stage was
  added to the playbook while the tool response still ended with "then hang up", and
  the model obeyed the tool over the playbook.

## Consequences

The prompt is now a **guide to doing the job well**, not the mechanism that stops it
doing harm. Everything in it can be edited freely; nothing in it can remove a
guarantee.

Every guard is a pure function of recorded state, so every one of them is unit-tested
against the real call that motivated it, and the tests read as a list of things that
have actually gone wrong.

The hangup tool is deliberately **exempt** from the fabrication guard: ending a call
is the agent's own decision, and a call that has gone wrong is better ended than
looped.

**The cost is real.** A correct guard with no recovery path is still a lost outcome:
a send refused 1.3 seconds before the prospect said yes was never retried, and it cost
the best conversation of a 50-dial run. Guards need re-arm paths, not just gates. See
[the teardown](../campaign-teardown.md#c5--a-correctly-refused-send-had-no-retry-path-and-it-cost-the-runs-best-call).
