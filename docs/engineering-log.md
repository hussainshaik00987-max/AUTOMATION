# Engineering log

Findings from building and running this system, kept because each one cost
something to learn and every one of them contradicted a reasonable prior.

**How to read this.** Every number here was measured on this system. Where a number
is an estimate it says so. Where a first measurement turned out to be wrong, the
wrong one is left in — the mistake is usually the useful part.

<!-- TOC -->
- [Latency](#latency)
- [Turn-taking](#turn-taking)
- [Answer supervision](#answer-supervision)
- [Person or machine](#person-or-machine)
- [Guardrails that had to be code](#guardrails-that-had-to-be-code)
- [Scraping at rate limits](#scraping-at-rate-limits)
- [Concurrency and SQLite](#concurrency-and-sqlite)
- [Rendering](#rendering)
- [Instrumentation](#instrumentation)
<!-- /TOC -->

---

## Latency

### Cold calls are won or lost in the first two seconds of silence

Five levers, in the order they were worth:

| Lever | Effect |
|---|---|
| **Pre-synthesised personal opener** | The full opening line, *including the business name*, is generated with TTS while earlier calls are still running and streamed the instant a human is detected. **Time-to-first-audio ≈ 0** instead of 1.5–3.5s. |
| **Opener prefetch pool** | Three leads ahead, entirely inside the previous call's runtime — so the cost is hidden, not paid. |
| **Model pre-connect at dial** | The WebSocket handshake and session setup happen *while the phone is still ringing*. ~630ms, hidden. |
| **Media stream opened at dial, not on answer** | The old sequence cost a full second at the worst possible moment: they say hello, the carrier posts `call.answered`, we post `streaming_start` back, and only *then* can we physically send audio. |
| No deliberation pass before speaking | Removes a variable-length think from every turn. |

Opening the media socket during ringing has three consequences, and all three are
load-bearing:

- **Inbound frames now arrive during RINGING** — ringback tone, not a person.
  Everything must gate on `call.answered`, or the listen-first opener greets a
  ringing phone at second 3.
- **Every opener timer anchors to the answer**, not to the socket opening.
- **The media stream can attach before the session object exists**, since the
  carrier may connect before the dial POST returns its id. A session miss is a race
  to wait out, not an error.

If the carrier ever rejects the dial parameters, the dial retries once without them
and logs loudly. One unsupported parameter would otherwise fail every lead
identically and look like an outage.

### The anchor site was worth 1.6 seconds per turn

A bench harness replays a **real prospect turn** — lifted from the prospect channel
of an actual recording — through a live session at realtime pace, and reports the
silence the caller actually sits through. No phone call, no stranger woken up, and
enough samples to tune from.

On the reference box:

| | |
|---|---|
| Model reply, median / p90 | **~1.0s / ~1.3s** |
| First turn vs later turns | ~200–400ms slower (prompt processing) |
| Session setup | ~630ms — already hidden inside ringing |
| End-of-turn silence window, 100ms vs 200ms | **no improvement** (1036ms vs 966ms median) |

The same measurement on a live call read **median 2.6s, p90 3.0s, worst 6.8s**.

The difference was not the model. The carrier had anchored the call's media on the
wrong continent, so every turn ran handset → US → server → US → handset. **~1.6s
per turn was pure detour.** The auto-selecting `Latency` anchor had chosen it
because it picks by RTT to the *connection*, which is not the same thing as RTT
along the *path* — and for a topology where the anchor sits between a handset in
one region and a server in another, the shortest detour is frequently neither
endpoint's nearest site.

Worth recording as a method note: the 100ms-vs-200ms result is the one that
justified the harness. The intuition points the other way, tightening the window
*feels* obviously better, and it bought nothing measurable while starting to answer
people who had merely paused.

### Fillers convert dead air into a held floor — and have been set too eager twice

A short acknowledgement ("mm-hm") plays when the prospect stops and nothing comes
back within a threshold. It does not reduce latency; it converts dead air into a
held floor, which is the part the prospect actually judges. The real answer queues
behind it and follows seamlessly.

The threshold is a real trade: a filler only helps if it starts before the answer
would have, and **delays that answer by its own length (~0.7s) when the answer was
already coming.**

It has been set too eager twice, both times by reading it off the model's *median*
instead of off the gap a person actually notices:

- At 800ms with two fills per turn, one 87-second call carried **nine**
  acknowledgements — one in front of very nearly every sentence.
- Cutting to one fill and 1,000ms was still wrong. A validation call measured six
  turns at 1072, 1130, 1017, 1205, 1489 and 215ms and fired a filler on **five of
  them**, each landing 100–500ms before an answer that was already on its way. That
  is not covering dead air, it is manufacturing it.

A 1.0–1.5s gap is ordinary turn-taking and nobody wonders whether the line dropped.
What produced the "hello? hello?" calls was 2.5s+. So: **1600ms, one fill per
turn** — above every turn measured on a healthy call.

**And then it printed six fillers on a 97-second call at that threshold.** Nothing
had regressed. That call's turns ran 2854, 6756, 1074, 1404, 3003, 2607 and 2115ms
— five of seven above 1600ms, so five fillers were *correct* and only the sixth was
early.

The lesson is that this threshold is **not independently tunable**. It is a
threshold on a distribution, and it was set from a distribution measured when reply
latency ran ~1.0s. Raising it to silence the tic would paper over a 2.6s median the
prospect is sitting through either way. Fix the latency and the number becomes
right again on its own.

---

## Turn-taking

### Barge-in is gated on duration, not loudness

While the agent is speaking, inbound audio is one of three things: echo, a
back-channel, or a real interruption. The model's own VAD abandons the turn for
anything it receives, and **loudness cannot separate the last two** — on the
measured route the prospect's acknowledging "haan" arrived at amplitude 1000–3999,
exactly where a real interruption does. Lowering the model's start-sensitivity was
already in place and did not help.

A live call lost **11 seconds** to this. An acknowledging "haan" killed the turn
after two words, the playout buffer was cleared, the prospect heard nothing and
said "hello?" — which killed the recovery turn too.

So a loud frame no longer passes on its own. It opens a **run**, and only a run
that survives ~360ms is admitted. Four rules ride with it:

- **The pre-recorded opener is exempt.** It plays to completion whatever the model
  does, so there is no turn to protect — and a "yes" over it is their *answer*, not
  an interruption. Gating it would leave the model nothing to reply to at exactly
  the moment the listen-first design exists to cover.
- **An interrupt inside the first 500ms does not clear the playout buffer.** The
  agent got two words out; let the sentence finish.
- **A gap of up to 100ms does not end a run.** Requiring strictly consecutive loud
  frames was too literal about how people talk: on a live call a prospect broke in
  eleven times in short bursts and not one reached 360ms unbroken, so the agent
  talked over them for six straight seconds and they hung up. Quiet frames inside a
  run are parked too, so a released utterance is continuous audio rather than
  speech with the gaps cut out.
- **Withheld frames are released, never dropped.** People habitually start
  answering over the tail of a sentence. Discarding what the gate was holding cost
  the model the first ~360ms of their reply — 23 speech frames on one measured call
  — and left it waiting for someone who had already spoken.

### The model's own VAD must stay eager

It was set to low start-sensitivity to fight the same thrash, and it worked — by
making the model **deaf**. On a live call the prospect said "hello?" five times
across 33 seconds and got no reply: each was ~0.5s, too brief to open a user turn
at that sensitivity, so from the model's side nobody had spoken.

Barge-in protection belongs in the gate and nowhere else. The gate filters short
sounds *before* they reach the VAD, which is precisely what lets the VAD afford to
be eager.

### Inbound audio floors must be measured per route, never inherited

The floors that decide "the prospect is talking" and "the prospect is interrupting"
were inherited from a different campaign on a different route and were badly wrong.
The per-call **loudness histogram** is what to tune them from. On one route:

```
0-99      = 3832 frames   line noise (95%)
300-1999  =  181          the prospect actually speaking
2000+     =    1
```

At the inherited barge-in floor of 2500, the prospect **could never interrupt** —
every word spoken over the agent was discarded as echo, which is exactly what "the
agent can't hear me" feels like.

On another route the histogram read:

```
0-99 = 4211   100-299 = 483   300-599 = 155
600-999 = 78  1000-1999 = 95  2000+ = 5
```

Here the prospect's speech lived in the **100–299 band, below the 250 speech
floor** — so his "hello" registered as zero milliseconds of speech and zero
utterances, and the opener waited out its timer instead of answering him.

A companion accounting line separates the three ways a call can look silent:
frames never arrived, the gate ate them, or the person genuinely said nothing.
Those are three different bugs and they are indistinguishable without it.

### A cut-short turn needs a watchdog

The model will not restart on its own — from its side the prospect took the floor
and never gave it back. So an interrupted turn arms a resume timer, and after ~1.5s
of silence the model is prompted to carry on. The ordinary 10s silence supervisor is
far too slow to catch this.

---

## Answer supervision

### The ring timer was cancelling calls people had already answered

Two controlled dials to a phone picked up on the first ring, by someone sitting
waiting for it. Both died at ~28s:

```
14:17:56 dialing → 14:18:01 call.initiated → 14:18:29 hangup
         cause=timeout · source=caller · sip_cause=487 · 0 media frames
```

The proxy logged **exactly three POSTs per call** — initiated, hangup, streaming
stopped. The carrier never sent `call.answered` at all, so this is not a webhook we
dropped: from the carrier's side the phone rang out and its own 30-second dial
timer cancelled the call. The person on the other end held a live handset to their
ear through the whole thing and heard nothing. The results file recorded
`no_answer` — the same row a phone nobody was near produces.

**The scale of it was already in the previous campaign's own log, unread.** Every
one of that run's 21 answered dials, measured from the dial:

```
 5.5  7.8  8.8  9.0  9.5  9.8 10.8 10.9 11.5 11.8 13.2
13.6 14.1 16.0 16.0 16.3 19.7 20.0 20.1 20.6 29.2   seconds
```

Five of those are answering machines, and **a machine does not take eleven seconds
to pick up** — so most of that number is transit, not somebody reaching for their
phone. The largest sample landed at 29.2s against a 30-second timer: **0.8 seconds
of headroom.** A distribution whose top sample sits that close to the cutoff is a
distribution being clipped, and the 19 dials that same run filed as "rang out" are
where everything past the cut went.

Read as a no-answer rate, 19 of 40 is an unremarkable cold-calling number and there
is nothing to investigate. **That reading was wrong, and wrong in the most
expensive possible direction**: an unknown fraction of those were real people who
picked up, heard silence, and hung up. That loses the lead *and* teaches their
carrier that this number is worth blocking — which is the same mechanism that then
produces more of them.

Three things changed:

- **The ring timer went to 60s.** Ring time is not billed, so the cost is campaign
  pace on genuinely unanswered numbers — and even that is usually less than the
  extra 30s, because an unanswered mobile gets an operator announcement at ~30–45s
  which the detector calls a machine and we hang up on. The value is chosen to
  clear the whole measured distribution with room for its tail, not to be generous.
- **`answer_lost` became a disposition.** Ask *who cleared the call*: a phone that
  genuinely rings out is cleared by our own timer or by the network, and **only a
  person can clear a call from the far end**. So a far-end hangup on a call we were
  never told was answered means somebody was holding that handset. It could not
  fire at all at 30s — we always cancelled first, and all 40 of the previous run's
  dials came back caller-or-network. *Raising the timer past how long a person will
  hold a silent line is what makes the failure observable at all.*
- **The report gained an answer-supervision section**, and it warns when the worst
  sample is within 5s of the ring timer. The number that matters is the **worst**
  one, not the median: that run's median of 13.2s looks perfectly healthy, and that
  is exactly what hid this for two campaigns. It compares against the ring timer
  from that run's own manifest, so a report read weeks later is not silently
  measured against whatever the code defaults to today.

None of this makes the route good. It stops the route's badness from being
invisible, and it stops us adding 30 seconds of our own to it.

### When the route stops completing calls, stop dialling

Three consecutive validation calls to a phone that was being answered every time:

```
19:13:08 initiated → 29.3s ringing → hangup cause=timeout · 0 media frames
19:17:30 initiated → 29.4s ringing → hangup cause=timeout · 0 media frames
19:21:08 initiated → 30.2s ringing → hangup cause=timeout · 0 media frames
```

Two calls to the same number minutes earlier had connected in 7–9 seconds. Nothing
in this program can influence whether a distant carrier completes a call from a
foreign number — and five calls from one foreign number to one mobile inside half an
hour, at 00:45 local time, is the exact pattern carrier spam mitigation exists to
stop.

What the code *can* do is notice. Hangup cause, hangup source and SIP cause now come
off the webhook onto the event record and into the report, so "three of nine dials
never connected" stops being a fact with no explanation attached — a phone that rang
out, a handset that rejected the call and a carrier that blocked it are three
different problems. A breaker stops the campaign when six dials in a row carry no
audio at all; cold calling has a genuinely high no-answer rate, so the bar sits
where an ordinary run will not reach it.

---

## Person or machine

### The opener must not be spoken to a recording

Premium carrier machine-detection is the only thing on the call that reliably
separates a voicemail greeting from a person saying hello. It got all six answered
calls in the first campaign right. What it is **not** is fast — verdicts landed at
**3.0, 3.1, 4.3, 4.9, 5.1 and 6.5 seconds** after pickup. So the opener can neither
wait for it nor ignore it. It ignored it, and pitched all three recordings.

The hard part is that on the first two seconds of audio the two are the same
signal:

| Call | | Shape |
|---|---|---|
| #4 | human | spoke 1,320ms straight, then quiet for 6.6s |
| #6 | machine | spoke 280ms, **paused 940ms**, carried on |
| #7 | machine | spoke 60ms, **paused 1,020ms**, carried on |
| #3 | machine | continuous, longest internal gap 80ms |
| #5 | human | silent for 3.1s, then spoke |

#6 and #7 walked straight through the old trigger: a short burst followed by a ~1s
pause is exactly a person saying "hello?" and waiting.

A validation call then showed the hardest sub-case. He answered with a **280ms
sound and waited 8.4 seconds** — the same 280ms, in the same position, as machine
#6. Nothing about the sound itself separates them. Requiring a whole greeting
pushed him to **2.8s of dead air**, which is exactly what an earlier prospect hung
up on. What separates them is the silence *after*: 8,440ms against 940ms and
1,020ms. Hence a fragment-then-quiet threshold set just past a recorded greeting's
breath and not one millisecond further.

**The errors are not symmetric, and the threshold is set from that.** Speaking too
early to a machine costs a couple of seconds of carrier time and the detector hangs
up anyway. Waiting too long on a person loses the lead.

So the decision fires on **positive evidence of a person and nothing else**:
the detector saying human; one whole utterance landed and gone quiet; one short
fragment and enough silence after it; or nothing said at all and the line quiet at
a hard timeout. Everything else is machine-shaped and the opener is *held* for the
detector, bounded at 3.5s when the line is quiet and 7s when it is not — silence is
expensive and speech is free, because speaking over someone who is talking is not
an improvement on waiting.

The tell that does the work: **a person who answers says one thing and waits for the
caller; a recorded greeting pauses for breath and starts again.** A second utterance
before the agent has spoken at all is the only cheap machine signal that survived
measurement.

A `not_sure` verdict is **not** a machine — it is the detector running out of
analysis time — so it *releases* the hold rather than extending it. Both silent
answerers on that run were real people.

The decision is a pure function precisely so those six calls can be replayed against
it in a test instead of argued about. All three machines are held past their
verdict; no human is delayed by a single tick.

### Transcript-based voicemail detection is deliberately near-useless

Personal voicemail is rare on the target route — what a cue list actually catches is
the *carrier* announcing an unreachable number. The detector is the real one; the
transcript scan is a backstop only, and **the two failure modes are not symmetric**:
missing a machine costs 40s of pitching a recording, while matching a live prospect
**hangs up on a real lead**, using ASR that rendered a common phrase as a French
loanword.

So it fires only when *all* of these hold: within 8s of the gate opening, no name
captured, no barge-in or resume yet, and ≥1.5s of continuous prospect speech — and
only on full operator sentence stems.

Phrases like "not available right now", "please try later", "switch off", "not
responding", "voicemail", "mailbox" and "press 1" were all **removed**. A prospect
saying *"I'm not available right now, call me later"* is a **callback** — the best
non-yes outcome on the list — and the old list hung up on him.

---

## Guardrails that had to be code

The pattern in all four: the prompt already said the right thing, and the model did
the other thing. **A prompt is a request.**

### "No" ends the call

The prompt used to say, on a refusal: *"just send the link anyway, it's free"*. On
the first live campaign the agent did exactly that — told "I don't need anything,
thank you", it said "oh sure, no problem", delivered the whole pitch, called the
send tool without ever getting a yes, and logged a delivery. **It was obeying its
instructions.**

Three layers now:

1. The playbook makes refusal the rule that outranks every stage.
2. A **transcript scanner** latches refusal independently of the model. The cue list
   is narrow on purpose — a bare "no" is *not* a cue, because the agent asks "do you
   have a website of your own?" and "no" there is the best-qualified lead on the
   list.
3. The send tool refuses once latched, and **dispatch refuses again** at the last
   gate before a real message reaches a real person — the refusal can arrive *after*
   the tool call, and on a live call it did.

A `suppressed` link status is the record of that.

### The agent must not hold a conversation with itself

The worst failure this system has produced, seen whole on a validation call:

```
19:05:05.3  PROSPECT  "that happens to me a lot too"
19:05:07.0  tool      send_demo_link
19:05:08.7  tool      capture_lead(name: "human")
19:05:10.0  tool      set_followup(meeting, "tomorrow evening")
19:05:11.1  tool      end_call(link_sent)
```

Four tool calls in 4.1 seconds, not one word from the far end between them. It asked
"and your name?", answered itself "ah, human", asked "shall I call tomorrow
evening?", answered itself "okay", and hung up — on a man still sitting there
listening. `human` would have gone into the results file, and into the follow-up
message's greeting.

**What caused it was the tool responses.** Each one was a *list of steps* —
send's said *say it's on its way, then ask their name, then ask when to talk again,
then call set_followup, only then end_call* — and the model worked through the list
without ever yielding the floor.

> **Tool responses are prompt, and a list of steps is a list the model will
> complete.**

They now name **one** thing to say and require silence after it. The step after that
is still named — a note that ends a turn without naming what follows is how the
agent came to hang up right after asking someone's name — but explicitly placed in a
LATER turn.

And a code guard, because the playbook already said STOP eleven times and was
ignored: a tool call is refused when the prospect has made no sound **and** produced
no transcript since the model's previous tool call. Then "the name they just gave
you", "the time they just agreed to" and "they just said yes" are all claims about
something that never happened. Tools in the *same* batch are fine — "yes send it, my
name's Alex" legitimately produces two. It is the next batch, arriving out of
silence, that is fiction.

It also clears the playout buffer on detection. This is one of the very few moments
where we know the audio in flight is wrong, and letting it play is letting the caller
listen to the agent invent their replies.

The hangup tool is deliberately exempt: ending the call is the agent's own decision,
and a call that has gone wrong is better ended than looped.

### What the model cannot see, it cannot repeat

The opener spends one fact out loud before the system instruction is ever read. On a
validation call it said *"I saw your 474 reviews"* and nine seconds later the agent
said *"4.8 rating, 474 reviews, great work"*. Hearing your own review count twice in
one call is the moment a caller stops sounding like a person.

The prompt already told it not to. It used the number anyway — not really
disobeying, just using what it was handed. So the per-lead research builder
**omits the spent figure entirely** and points that stage at the rating, the city or
the work instead.

Same reasoning that moved the refusal guard out of the prompt and into code.

### The gap line is per-lead and must never be a lie

46% of one list had *something* at a website URL, so a blanket "you don't have a
website" would be provably false on hundreds of calls. The line is derived from the
scraper's own classification:

| Situation | Leads | What the agent says |
|---|---|---|
| No website at all | 749 | "you don't have a website" |
| The "website" is a social profile | 595 | "that's actually a social page" |
| **Unknown** | 347 | **asks** — "do you have a website of your own?" |
| Site is down | 123 | "your website isn't loading at all" |
| Link-hub / old builder / HTTP-only / decent site | 161 | one specific line each |

The unknown bucket **asks instead of asserting**. Never assert a gap the data can't
back up — a wrong "fact" is caught in one sentence and the call is dead.

The classification always outranks the raw website cell: a blank cell must not
override an explicit classification, and that ordering bug is covered by a test.

### The opener must not be triggered blind

The model is given **no text cue** to start talking. The mic gate opens as the
opener *starts* playing, so the prospect's answer reaches the model while the opener
is still being spoken and the reply is ready the moment it ends.

An earlier version triggered the model *before* opening the gate. With no prospect
audio to respond to, it **invented their answer** — "yes, this is the studio, go
ahead" — and then replied to it. Two people talking on one call, one of them
imaginary.

### One idea per turn

The single biggest failure mode of a staged playbook is the model reciting all the
stages as one monologue, which on a real call is a hangup. The prompt fights this
explicitly with a worked wrong/right example and a hard two-sentence cap.

### The first line has to earn the next five seconds, and length is measured

The pre-recorded opener is the only scripted part of the call and the part with the
least room for error. On the first live campaign, two of three people who answered
were gone inside nine seconds.

The opener plays to completion — barge-in is deliberately off for it — so every
syllable is one the prospect cannot talk through. A CLI times a candidate line
through the real synthesiser before a stranger ever hears it:

| Line | Spoken |
|---|---|
| "Hello sir, am I speaking to X?" (the old one) | 3.5s |
| "Hello sir! I saw your 474 reviews — this is X, right?" | **5.1s** ← shipped |
| "…saw them — very impressive! This is X, right?" | 6.3s |
| "Hello sir! I saw X's 474 reviews — really nice work!" | 5.8s |

The explicit compliment is cut because **the number *is* the compliment** — nobody
reads out your review count except someone who went and looked — and 1.2s is the
difference between an opening and a monologue.

The first draft of that line read as tight on the page and measured **7.4 seconds**.
Syllable-counting is not a substitute for hearing the synthesiser say it.

It still costs +1.6s over the old line, and the trade is measured rather than
assumed: every call records how long the prospect spoke *during* the opener, so the
next run says how often and for how long the opener talks over the person it just
reached. A config flag reverts it without a rebuild.

The praise is also refused when the row cannot back one up — below a review or
follower floor the agent falls back to the plain confirm line rather than praising
someone for eleven reviews.

---

## Scraping at rate limits

### Rolling windows cannot be sized from a burst

Three successive numbers for one endpoint's limit, and the first two were both
wrong:

1. **Published guidance: ~200 requests/hour/IP.**
2. **A 22-request burst at 6s intervals ran clean** (21 ok, 1 not-found, 0 limits),
   implying ~477/hr. **Over-optimistic** — 22 requests is far too short a sample to
   reveal a rolling-window limit.
3. **The real run was limited after 228 calls** at ~8s spacing (≈500 req/hr).

The trap that made #2 misleading in practice: **~38% of profiles hit a server-side
schema bug and fall back to a second HTML request.** So one *lead* costs ~1.4
*requests*, and pacing computed per-lead silently ran ~40% hotter than intended.

> **Pace on requests, not on work items.**

### The penalty outlasts the pause

Once limited, the IP stayed blocked through a 10-minute drain and was refused on the
very first call after it. Backoff therefore **escalates** (15m → 30m → 60m → 2h) and
aborts after 6 consecutive limits — probing every 15 minutes just re-arms the block.

Aborting costs nothing, because unenriched leads keep their flag unset and a re-run
resumes exactly where it stopped. That resumability is what makes the aggressive
give-up affordable.

### Status codes have semantics worth determining empirically

| Code | Means | Therefore |
|---|---|---|
| 200 + null user | profile hidden or removed | not an error |
| **404** | the handle does not exist | **never guess handles** — only ever query one discovered from a real URL |
| **400** | a *persistent, profile-specific* server-side schema bug, confirmed unchanged across 6 consecutive retries — **not** rate limiting | needs an HTML fallback; retrying is wasted budget |
| 429 | rolling-window limit | escalating backoff, and expect it to outlast the pause |

Getting 400 wrong is expensive twice: you burn retry budget *and* you mis-attribute
the failure to rate limiting, which makes you slow down for no reason.

### The architecture follows from the scarcest resource

Enrichment is the bottleneck, so: **collect wide from the cheap source → score on
that data alone → enrich only the top slice.** Enrichment never runs on the whole
corpus; it runs on the leads that already earned a call.

The site-probe stage pays for itself twice: while checking health it also harvests
social handles, emails, messaging links and the platform from the same fetch.
Measured on a 200-site sample: **78 handles and 74 emails recovered** at no extra
request cost.

### Rate limits differ by host, so pacing is per-source

The primary listing source tolerated a full run at 2–4.5s spacing with **zero
blocks**; a marketplace returned HTTP 429 on profile fetches at 6 workers and needed
3, with a 20s+ backoff on a 429 rather than burning the retry budget on 4s sleeps.

Threading is per-source too: **~5 sites/sec at 12 workers** (7× the serial rate) and
**~8 profiles/sec at 6 workers**, while the primary listing source stays serial and
paced — it is the one that will actually block you.

---

## Concurrency and SQLite

Three things had to be true before concurrent stages were safe:

1. **The database must be off a network-backed filesystem.** WAL mode needs
   shared-memory sidecar files that a 9p/v9fs mount does not properly support, and
   the symptom is intermittent `database is locked` the moment two stages overlap.
   Measured after moving it to a local filesystem: **8,798 concurrent writes/sec, 0
   lock errors**, versus a hard crash before.

2. **Commit per row, not per batch.** Batching 50 updates behind one commit holds
   the write transaction open for the *whole batch*; at network speed that is
   minutes, and every other stage dies. Commits measured at ~8,800/sec — batching
   bought nothing and cost the run.

3. **A busy timeout is not sufficient on its own.** In WAL mode a connection that
   reads and then writes after another has committed gets an *immediate* snapshot
   conflict that the timeout does not wait out. A retry wrapper backs off and
   retries, and re-raises non-lock errors untouched.

Network fans out across threads but **only the main thread touches SQLite**, and
HTTP sessions are per-thread because the impersonating client is not thread-safe.

---

## Rendering

### Determinism is a performance feature

The scene never animates itself. A `seek(t)` function writes every animated property
as a pure function of timeline position, and the capture loop calls it frame by
frame. Nothing uses CSS animation, transition, `requestAnimationFrame` or `Date`.

Two consequences, and the second one is why it is worth the discipline:

- The same input always produces the same file.
- A slow machine produces the *same file* as a fast one, rather than dropping frames
  — which is what any real-time recorder would do.

And a third that pays for the whole design: `seek()` returns a hash of every style it
wrote. **Equal hash ⇒ identical pixels ⇒ re-send the previous encoded JPEG instead
of screenshotting.** ~36% of frames are never captured.

This is why the camera holds dead still through the conversation. A slow push-in
would be imperceptible at ~2px/sec and would change a transform on *every* frame,
defeating the whole optimisation. The cheapest-looking creative decision in the piece
is load-bearing.

Purity also lets one clip be sharded across workers by *timeline*, not by scene: a
worker starting at t=45s draws exactly the frames it would have drawn playing from
zero. Measured on a single-scene explainer: **100s wall clock for a 61.6s clip, ~37
fps aggregate.**

### From 2.2 fps to a batch that finishes in hours

| Change | Effect |
|---|---|
| Large-radius CSS blur → multi-stop radial gradients | **308ms → ~0 per frame.** The blur was re-rasterised every time ambient drift moved it: 74% of the entire frame cost, for two soft light pools a gradient renders identically. |
| Static background layers baked to a JPEG | ~44ms/frame → one blit. It *is* a static image; compositing it live re-evaluated it on every capture. |
| `backdrop-filter` removed from nav, composer and cards | ~17ms/frame. Nothing sits behind them worth blurring. |
| Frame-rate limit disabled in headless | Otherwise every screenshot is pinned at exactly 50ms regardless of page complexity. |
| Duplicate-frame elision | ~36% of frames never captured. |
| Encoder thread pool capped | Left to its default, x264 sizes its pool from the core count: ~24 threads and ~830MB per encoder. Six workers exhausted 8GB and drove the machine into swap. |
| GPU encode when available | Encoding moves to dedicated silicon, freeing every core for capture — which is the actual bottleneck. |

Quality was matched across encoders by measurement rather than by trusting the CRF
scale: against the source frames, x264 CRF 21 gave 7.26MB / 44.3dB PSNR and the
matching GPU point was CQ 28 (~7.5MB / ~43.4dB). At 2× zoom on the text they are
indistinguishable.

### The colour-range bug that nearly shipped in 2,000 files

Frames arrive from the headless browser as MJPEG, which is **full range** (0–255).
Encoding straight from that produced files tagged full-range with primaries and
transfer unset — **even with `-pix_fmt yuv420p` on the command line**. Any player
that ignores the range flag then reads 0–255 content as 16–235, crushing the dark
backdrop and blowing out the white bubbles, which is most of what the video is made
of.

The fix does the range conversion in an **explicit filter** rather than trusting the
encoder, then re-stamps the colour tags because scaling drops them:

```
-vf scale=in_range=full:out_range=limited,format=yuv420p,
    setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709
-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709
```

Verified by decoding back to RGB: black lands on 0, white on 255, mean difference
0.7%. A QA pass asserts the pixel format on every rendered file so it cannot come
back silently.

Two neighbouring decisions from the same measurement session:

- **30fps, not 60 or 24.** The motion is eased UI; below 30 the slow moves judder.
- **Grain is baked into the background.** Large flat gradients posterise into visible
  bands once the encoder quantises them, and a couple of levels of noise dithers the
  ramp away for far less bitrate than the quality bump that would otherwise be
  needed.
- **Subpixel antialiasing forced off.** It paints coloured fringes on glyph edges,
  and 4:2:0 chroma subsampling smears those into muddy haloes around every letter.

### Verify what a screenshot cannot show

A browser-driven verifier over a sample fails on: copy that ran outside the frame
(the stage's `overflow:hidden` crops it *silently*), a title that wrapped to two
lines, an element wider than the screen, a timestamp sitting on top of message text,
an unsubstituted template slot, any network request, any console error.

A separate pass checks the *rendered files* rather than the scenes: codec,
resolution, pixel format **and colour range**, fps, duration, size, and that the
metadata atom precedes the media data — without that, a phone on mobile data must
download the whole file before it plays.

And a contact-sheet tool, because **encoding 35 seconds of video to look at one
badly-placed bubble is 40 seconds of waiting; a six-up sheet is two.**

### 2,000 files in a folder is not reviewable

A gallery generator writes a filterable, searchable page with every video playable
inline. It also extracts a poster still per video, which is not decoration: a
`<video>` with `preload="metadata"` paints **nothing** — not even with a time
fragment, which needs media data the browser has not fetched — so without real
posters every tile is a black rectangle. With them, the video file is only attached
on first click, so scrolling the grid does not open thousands of files.

---

## Instrumentation

### The health check nobody thinks to write

Three circuit breakers already caught infrastructure failing *loudly*. None of them
caught the failure that has actually happened twice: everything connects, the call
runs, the results file fills in, and **the agent says nothing**.

- A VAD sensitivity setting left it deaf, and it said nothing for 33 seconds while
  the prospect asked "hello?" five times.
- An exhausted quota opened every call in silence.

Both look like ordinary hangups from the outside. `hung_up` is exactly what got
recorded. Nothing said "the agent is not speaking", because nothing measured it.

So every 10 dials the run asks one question of the calls that actually gave the
agent a chance to speak: **did it?** Below a 50% reply rate the campaign stops.

What counts as a chance is the whole design. Rings-out, machines and two-second
hangups are ordinary cold-calling outcomes and say nothing about whether the agent
works, so a call only qualifies at **≥8s connected AND ≥500ms of prospect speech AND
a disposition that isn't voicemail / no-answer / dial-failure**. A voicemail greeting
is several seconds of loud continuous "speech" that trips every duration test, and
the agent is *right* not to answer it.

The floor is 50%, not 100%: someone can talk over the opener and hang up before the
first turn lands, and that is a real call, not a fault.

The test replays a real campaign's nine calls through it — three rings-out, three
machines, two conversations — and asserts it stays **quiet**, then flips the two
conversations to silence and asserts it fires. **A guard that would have stopped a
working run is worse than no guard.**

Even when it passes it logs the numbers, because *"the agent replied on 9 of 9"* is
worth seeing in a run that produced no sales: it separates "the pitch isn't landing"
from "the agent isn't talking", which are the two explanations every bad campaign has
and which look identical from the results file.

### A results row says what a call was *for*; it does not say whether it *worked*

So the row also carries connected seconds, prospect speech milliseconds and agent
turn count. Those are what the health check reads, and they turn "did the agent talk
on this one?" from an afternoon with six recordings into a column.

### One JSON record per call, and a manifest on line one

The results file answers *what the outcome was*. It does not answer *why*.
Reconstructing the first live run meant reading a 53KB text log, correlating it
against a results file whose disposition column was itself wrong, and pulling six
recordings off the server to measure the audio by hand. Every finding was
recoverable and none of it was readable.

Each call now writes one record carrying everything it knew about itself: when it
was answered, **which rule released the opener and how long after pickup**, **what
the detector said and how late**, how much of the prospect's audio reached the
model, every reply latency, the whole transcript, and **what was promised versus
what was delivered**. Append-only and fsynced per record, so a campaign killed
mid-run loses nothing.

**The manifest on line one matters as much as the calls.** It pins every tunable
those calls ran under. A threshold that was right last week and wrong today is
invisible unless the file says which one was in force.

### The report is tested against a real run

Given the first campaign's nine real calls, the report independently reproduces the
hand-written teardown's findings: three dials that never connected, three recordings
pitched, nine fillers on one call, and a barge-in gate that let nothing through.

That is the actual test of an analysis tool — not that it runs, but that it finds
what a human found by hand, from the same evidence.

### Status codes are not the contract

The sidecar answers `202` whether a job is new or a duplicate. A dialer that only
checked the status code reported **seven deliveries backed by one job**. The link
status column now records what *actually* happened to the promise — queued,
duplicate, failed, suppressed — not what the agent said.

### Every promise needs a record of whether it was kept

- Sidecar unreachable → the lead is written to a failed-links file, so the promise
  can still be kept by hand.
- Email consent → appended to a pending file for **manual** sending. Nothing is sent
  automatically without a verified sending identity; blasting links from an
  unverified address burns the domain. The agent still takes the address on the
  call.
- Some numbers cannot receive messages at all. 19 of 2,000 leads were landlines; one
  turned up inside a nine-call run, the agent promised a message, and the sidecar
  returned `unreachable`. A line-type check now gates the ask, and the per-lead
  research tells the agent to take an email instead.

### A badly-timed run produces numbers that cannot be read

The campaign runner refuses to dial outside working hours or on weekends unless
explicitly overridden. The first campaign went out in the evening; three of six
connections were answering machines and nobody was in a mood to hear about websites.

None of that was the pitch's fault, **which is exactly the problem** — the hour is a
variable of the experiment, not a detail of it. The runner also refuses to reuse a
run tag, and refuses to start if the follow-up sidecar is not linked.
