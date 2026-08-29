package main

import (
	"context"
	"log/slog"
	"math/rand"
	"sync"
	"sync/atomic"
	"time"
)

// ---------------------------------------------------------------------------
// Thinking fillers — what a person does while they work out what to say.
//
// Measured on live calls, the model takes 0.8–3.9s to start answering. Some of
// that is reducible and some is not, but ALL of it is currently delivered to the
// prospect as pure silence, and silence on a phone call does not read as
// "thinking" — it reads as "the line dropped". That is what produced the
// "हलो? हलो?" calls: the prospect answers, hears nothing, and assumes the
// call is broken. They are not reacting to the delay, they are reacting to the
// absence of any sign of life.
//
// A human never does this. Asked something they need a moment for, they say
// "जी..." or "हम्म..." — a sound that means "I heard you, I'm on it". So does
// the agent now: if the prospect stops talking and nothing has come back within
// thinkingFillerAfterMs is how long a gap must run before the agent makes a
// sound to hold the floor.
//
// It is a threshold on a DISTRIBUTION, not an independently tunable number, and
// it has now been set wrong three times by reading it off a median. It was
// inherited here at 1600ms from a stack whose reply latency ran ~1.0s. This
// stack's does not. Measured on the first live call, every reply latency:
//
//	1976  2256  1384  3770  1640  1570  1719  1671  5321 ms
//
// Median ~1.7s. At a 1600ms threshold that fires on SEVEN of nine turns, which
// is the "one sec" tic the caller reported — a filler in front of very nearly
// every sentence, each one landing a few hundred milliseconds before an answer
// that was already on its way, and delaying it by its own length.
//
// A filler only helps if it starts before the answer would have. Set above the
// bulk of the distribution so it covers the genuinely long turns (3.8s, 5.3s)
// and stays silent through ordinary turn-taking, which nobody wonders about.
//
// **This number is not the fix for slow turns.** If the median moves, re-derive
// it from `-report`'s reply-latency percentiles rather than nudging it here.
var thinkingFillerAfterMs = 2600

// thinkingMinSpeechFrames is how much the prospect must actually have said
// before a silence counts as "they are waiting on us". 8 frames = 160ms, enough
// to exclude a line click or a cough but not a one-word "हाँ".
const thinkingMinSpeechFrames = 8

// thinkingLines are non-committal acknowledgements — they say "I'm listening"
// and commit to nothing, so they fit in front of ANY answer the model then
// gives. Anything with content ("Right, so what you'd want is—") would sometimes
// contradict the sentence it precedes.
//
// They are real words, never a hum. A non-lexical "mmm" is the one that gives
// the game away: a real word carries the speaker's own prosody, while a
// synthesised hum is just a tone and reads as a recording.
//
// **"Sure, one sec..." was removed after the first live call on this stack.**
// The listener's report was that the agent "said one sec several times", and the
// log agrees: eight fillers in a 162-second call. Two separate faults produced
// that, and only one of them is the threshold.
//
// The other is the wording. A filler that NAMES the wait — "one sec", "just a
// moment", "hold on" — makes the caller notice the wait and then wait for the
// thing it promised. A filler that merely holds the floor does not. Every line
// here is now a sound a person makes while still assembling the sentence, not a
// request for time.
//
// Eight, because the no-repeat window below needs more entries than the window
// is wide, or the constraint is unsatisfiable and the bank is FORCED to repeat.
var thinkingLines = []string{
	"Uh...",
	"So...",
	"Yeah, so...",
	"Right...",
	"Okay so...",
	"Hmm, okay...",
	"Let me see...",
	"Right, so...",
}

// thinkingFillers holds the synthesised pool, shared by every call in the run.
// Populated once at startup (see WarmThinkingFillers) and read without locking.
type thinkingPool struct {
	clips [][]byte
}

var thinkingFillers atomic.Pointer[thinkingPool]

// WarmThinkingFillers synthesises the acknowledgement pool once, before the
// campaign starts. They are generic — no lead's name in them — so unlike openers
// they are made once and reused for every call in the run.
//
// Synthesis goes through the same ElevenLabs voice as the live call for the same
// reason openers do: a different voice saying "uh..." immediately before the
// agent's own sentence is two people on one call.
func WarmThinkingFillers(ctx context.Context, cfg Config) {
	if cfg.ElevenAPIKey == "" || thinkingFillerAfterMs <= 0 {
		return
	}
	log := slog.With("component", "thinking-fillers")
	pool := &thinkingPool{}

	// Synthesised a few at a time. Done strictly in order this is a noticeable
	// pause between the operator pressing start and the first lead being dialled —
	// but firing them all at once risks the provider's per-minute rate limit, and the failures land on
	// whatever else is synthesising at the same moment. On the first run of this
	// code that was the GENERIC OPENER, which is the fallback greeting every call
	// depends on: the fillers came up and the thing they were queued behind did
	// not. Two at a time is fast enough and leaves the quota headroom the openers
	// need.
	sem := make(chan struct{}, fillerSynthesisConcurrency)
	gen := func(lines []string) [][]byte {
		out := make([][]byte, len(lines))
		var wg sync.WaitGroup
		for i, line := range lines {
			wg.Add(1)
			go func(i int, line string) {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()
				audio, err := synthesiseWithRetry(ctx, cfg, line, log)
				if err != nil {
					log.Warn("filler synthesis failed — skipping this one", "line", line, "error", err)
					return
				}
				out[i] = audio
			}(i, line)
		}
		wg.Wait()
		kept := out[:0]
		for _, a := range out {
			if len(a) > 0 {
				kept = append(kept, a)
			}
		}
		return kept
	}

	pool.clips = gen(thinkingLines)

	if len(pool.clips) == 0 {
		log.Warn("no thinking fillers available — slow turns will play as silence")
		return
	}
	thinkingFillers.Store(pool)
	log.Info("thinking fillers ready", "clips", len(pool.clips), "after_ms", thinkingFillerAfterMs)
}

// fillerSynthesisConcurrency bounds how many synthesis sessions run at once.
// The provider rejects the surplus with a quota error rather than queueing it, and a
// rejection here is silent until a call needs the audio.
const fillerSynthesisConcurrency = 2

// fillerNoRepeat is how many recent acknowledgements are excluded from the next
// pick. Four, against a bank of eight: wide enough that a caller does not hear
// the same sound twice inside a couple of minutes, narrow enough that the
// constraint stays satisfiable.
const fillerNoRepeat = 4

// synthesiseWithRetry rides out the transient quota rejections that come from
// running synthesis alongside the opener prefetch pool. These are per-minute
// limits, not a hard cap, so a short backoff clears them — and a filler that
// fails is not fatal, it just means slow turns play as silence again.
func synthesiseWithRetry(ctx context.Context, cfg Config, line string, log *slog.Logger) ([]byte, error) {
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(time.Duration(attempt) * 2 * time.Second):
			}
		}
		audio, err := elevenSynth(ctx, cfg, line)
		if err == nil {
			return audio, nil
		}
		lastErr = err
		log.Debug("filler synthesis attempt failed — retrying", "line", line, "attempt", attempt+1, "error", err)
	}
	return nil, lastErr
}

// pickThinkingFiller returns one acknowledgement, or nil if none were
// synthesised.
//
// It never returns the same clip twice running. Random choice from five still
// repeats about one time in five, and a repeat is precisely the moment a
// prospect stops hearing a person and starts hearing a recording — the cost of
// avoiding it is one retry.
func (s *Session) pickThinkingFiller() []byte {
	pool := thinkingFillers.Load()
	if pool == nil {
		return nil
	}
	lines := pool.clips
	if len(lines) == 0 {
		return nil
	}
	if len(lines) == 1 {
		return lines[0]
	}
	// A window of one is not enough at the rate these actually fire. The live
	// call played the same 603ms clip four times in under two minutes while never
	// once repeating BACK TO BACK — which is precisely what "avoid the previous
	// one" guarantees and precisely what the caller hears as a recording.
	//
	// recentFillers keeps the last fillerNoRepeat picks and excludes all of them.
	s.fillerMu.Lock()
	defer s.fillerMu.Unlock()

	allowed := make([]int, 0, len(lines))
	for i := range lines {
		recent := false
		for _, r := range s.recentFillers {
			if r == i {
				recent = true
				break
			}
		}
		if !recent {
			allowed = append(allowed, i)
		}
	}
	if len(allowed) == 0 {
		// More fills than the bank can cover without repeating. Fall back to
		// anything but the immediately previous one.
		for i := range lines {
			if len(s.recentFillers) == 0 || i != s.recentFillers[len(s.recentFillers)-1] {
				allowed = append(allowed, i)
			}
		}
	}
	i := allowed[rand.Intn(len(allowed))]

	s.recentFillers = append(s.recentFillers, i)
	if len(s.recentFillers) > fillerNoRepeat {
		s.recentFillers = s.recentFillers[1:]
	}
	return lines[i]
}

// thinkingMaxFillsPerTurn bounds how many holding sounds one wait may get.
//
// This was 2, on the reasoning that one ~0.9s filler covers only the first ~1.7s
// of a wait while the slow turns run to 2.6s. The live call disproved it: with
// real reply latency the second one fired on most turns, and nine
// acknowledgements landed inside 87 seconds. Two in a row is not what makes a
// person sound present — one does, and the second is what makes them sound like
// a loop.
//
// So: one per turn. The residual silence on a genuinely slow turn is real, and
// it is the price of not sounding like a recording. Reply latency is on the
// per-call event record next to the filler count, so if that trade ever stops
// paying it will be visible rather than argued.
const thinkingMaxFillsPerTurn = 1

// thinkingGate decides, per prospect turn, whether the agent should make a
// holding sound. Driven from the Telnyx→Gemini loop, which sees every 20ms
// frame, so it can react far faster than the 500ms conversation supervisor.
//
// It fires only while the agent owes them an answer, and at most
// thinkingMaxFillsPerTurn times per turn.
type thinkingGate struct {
	turnStartNano int64 // when the prospect began this turn; 0 = not talking
	speechFrames  int   // how much they've actually said in it
	fills         int   // acknowledgements played in this turn
}

// observe updates the gate with one inbound frame and reports whether to play a
// filler right now. now and lastSpeech are UnixNano.
func (g *thinkingGate) observe(now, lastSpeech, agentAudible, lastModelAudio int64, isSpeech bool) bool {
	if isSpeech {
		if g.turnStartNano == 0 {
			g.turnStartNano = now
		}
		g.speechFrames++
	}

	// The agent has answered — this turn is closed, whatever happened in it.
	if g.turnStartNano != 0 && lastModelAudio > g.turnStartNano {
		*g = thinkingGate{}
		return false
	}

	// The silence is measured from the last thing the prospect HEARD or SAID —
	// not simply from when they stopped. After a filler has played, that is the
	// end of the filler, so a second one waits a full interval rather than
	// treading on the heels of the first.
	quietSince := lastSpeech
	if agentAudible > quietSince {
		quietSince = agentAudible
	}

	switch {
	case g.turnStartNano == 0, g.fills >= thinkingMaxFillsPerTurn:
		return false
	case g.speechFrames < thinkingMinSpeechFrames:
		return false // a click or a breath, not a question
	case now < agentAudible:
		return false // they can still hear us; the floor is not empty
	case now-quietSince < int64(thinkingFillerAfterMs)*1e6:
		return false // too soon — the answer may yet arrive on its own
	}
	g.fills++
	return true
}
