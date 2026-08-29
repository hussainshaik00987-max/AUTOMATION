package main

import (
	"strings"
	"testing"
	"time"
)

// ms builds a UnixNano-style clock offset, so these tests read in the units the
// gate actually thinks in.
func ms(n int64) int64 { return n * int64(time.Millisecond) }

// speak feeds n frames of prospect speech to the gate at 20ms intervals ending
// at `end`, and returns whether any of them asked for a filler (none should —
// the gate only fires once they have STOPPED).
func speak(g *thinkingGate, end int64, n int, agentAudible, lastModelAudio int64) bool {
	fired := false
	for i := n; i > 0; i-- {
		now := end - ms(int64(i-1)*frameMillis)
		if g.observe(now, now, agentAudible, lastModelAudio, true) {
			fired = true
		}
	}
	return fired
}

// The case this exists for: they answer, the model is slow, and the line would
// otherwise be silent long enough for them to ask whether anyone is there.
func TestThinkingFillerFiresOnASlowTurn(t *testing.T) {
	var g thinkingGate
	base := ms(100_000)

	if speak(&g, base, 20, 0, 0) {
		t.Fatal("fired while the prospect was still talking")
	}
	// Still too soon — the answer may be seconds away or milliseconds away.
	if g.observe(base+ms(400), base, 0, 0, false) {
		t.Fatal("fired at 400ms, before thinkingFillerAfterMs")
	}
	if !g.observe(base+ms(int64(thinkingFillerAfterMs)+20), base, 0, 0, false) {
		t.Fatal("did not fire once the silence passed thinkingFillerAfterMs")
	}
}

// A long wait gets a second acknowledgement — but only after the first has
// finished playing AND a further full interval of silence, never back to back.
// One acknowledgement per turn, and no more, however long the wait runs. Two in
// a row was the old behaviour and it is what put nine "जी..."s into a single
// 87-second live call — the point at which a filler stops sounding like someone
// listening and starts sounding like a loop. The residual silence on a genuinely
// slow turn is deliberate.
func TestThinkingFillerNeverFillsTwiceInOneTurn(t *testing.T) {
	var g thinkingGate
	base := ms(100_000)
	after := int64(thinkingFillerAfterMs)
	speak(&g, base, 20, 0, 0)

	firstAt := base + ms(after+20)
	if !g.observe(firstAt, base, 0, 0, false) {
		t.Fatal("did not fire the first time")
	}
	// The filler is ~900ms of audio, so the caller can hear us until then.
	fillerEnds := firstAt + ms(900)
	if g.observe(firstAt+ms(200), base, fillerEnds, 0, false) {
		t.Fatal("fired again while the first filler was still playing")
	}
	// Silence long past a second full interval — still nothing.
	for _, wait := range []int64{after + 20, 2 * after, 4 * after} {
		if g.observe(fillerEnds+ms(wait), base, fillerEnds, 0, false) {
			t.Fatalf("fired a second time in one turn after %dms of further silence", wait)
		}
	}
}

// The cap: beyond it the filler stops reassuring anyone and the silence
// supervisor should be taking over instead.
func TestThinkingFillerStopsAtTheCap(t *testing.T) {
	var g thinkingGate
	base := ms(100_000)
	after := int64(thinkingFillerAfterMs)
	speak(&g, base, 20, 0, 0)

	audible := int64(0)
	fired := 0
	for i := int64(1); i <= 12; i++ {
		at := base + ms(i*(after+1200))
		if g.observe(at, base, audible, 0, false) {
			fired++
			audible = at + ms(900) // that filler now occupies the line
		}
	}
	if fired != thinkingMaxFillsPerTurn {
		t.Fatalf("fired %d times over a very long wait, want the cap of %d", fired, thinkingMaxFillsPerTurn)
	}
}

// While the caller can still hear the agent there is no dead air to fill, and
// interrupting our own sentence with "जी..." would be worse than the silence.
func TestThinkingFillerSilentWhileAgentIsAudible(t *testing.T) {
	var g thinkingGate
	base := ms(100_000)
	audibleUntil := base + ms(5000)

	speak(&g, base, 20, audibleUntil, 0)
	if g.observe(base+ms(int64(thinkingFillerAfterMs)+20), base, audibleUntil, 0, false) {
		t.Fatal("fired while the agent was still playing")
	}
	// Nor the instant the agent's audio drains: the silence is measured from the
	// last thing the caller HEARD, so firing here would bolt "जी..." onto the tail
	// of the agent's own sentence.
	if g.observe(audibleUntil+ms(20), base, audibleUntil, 0, false) {
		t.Fatal("fired immediately after the agent's own sentence ended")
	}
	// ...and fires once that silence has actually lasted a full interval.
	if !g.observe(audibleUntil+ms(int64(thinkingFillerAfterMs)+20), base, audibleUntil, 0, false) {
		t.Fatal("did not fire after a full interval of post-agent silence")
	}
}

// A line click or a breath is not a question, and answering it with "जी..."
// makes the agent look like it is hearing things.
func TestThinkingFillerIgnoresNoiseBlips(t *testing.T) {
	var g thinkingGate
	base := ms(100_000)

	speak(&g, base, thinkingMinSpeechFrames-1, 0, 0)
	if g.observe(base+ms(int64(thinkingFillerAfterMs)+500), base, 0, 0, false) {
		t.Fatalf("fired after only %d speech frames", thinkingMinSpeechFrames-1)
	}
}

// The model answered — nothing to cover, and the gate must rearm cleanly for the
// prospect's next turn rather than staying latched.
func TestThinkingFillerResetsWhenTheModelAnswers(t *testing.T) {
	var g thinkingGate
	base := ms(100_000)
	speak(&g, base, 20, 0, 0)

	// Agent audio lands before the threshold: this turn is closed.
	if g.observe(base+ms(300), base, 0, base+ms(250), false) {
		t.Fatal("fired even though the model had already started speaking")
	}
	if g.turnStartNano != 0 || g.fills != 0 || g.speechFrames != 0 {
		t.Fatalf("gate not rearmed after the model answered: %+v", g)
	}

	// A fresh prospect turn is treated on its own merits.
	next := base + ms(6000)
	speak(&g, next, 20, 0, base+ms(250))
	if !g.observe(next+ms(int64(thinkingFillerAfterMs)+20), next, 0, base+ms(250), false) {
		t.Fatal("did not fire on the following turn")
	}
}

// Silence with nobody having said anything is just an idle line — that belongs
// to the conversation supervisor, not here.
func TestThinkingFillerSilentWhenProspectNeverSpoke(t *testing.T) {
	var g thinkingGate
	base := ms(100_000)
	for i := 0; i < 200; i++ {
		if g.observe(base+ms(int64(i)*frameMillis), base, 0, 0, false) {
			t.Fatal("fired without the prospect ever speaking")
		}
	}
}

// Hearing the same clip twice is the moment a prospect stops hearing a person
// and starts hearing a recording — and "twice" does not mean "back to back".
//
// The first live call on this stack played the same 603ms acknowledgement four
// times in 162 seconds while never once repeating consecutively, which is
// exactly what a window of one guarantees. The caller heard a recording. So the
// picker excludes the last fillerNoRepeat picks, not just the previous one.
func TestThinkingFillerDoesNotRepeatInsideTheNoRepeatWindow(t *testing.T) {
	clips := [][]byte{{1}, {2}, {3}, {4}, {5}, {6}, {7}, {8}}
	thinkingFillers.Store(&thinkingPool{clips: clips})
	t.Cleanup(func() { thinkingFillers.Store(nil) })

	s := &Session{}

	var window []byte
	seen := map[byte]bool{}
	for i := 0; i < 400; i++ {
		got := s.pickThinkingFiller()
		if len(got) != 1 {
			t.Fatalf("pick %d returned %v, want one of the pool", i, got)
		}
		for _, w := range window {
			if w == got[0] {
				t.Fatalf("pick %d repeated clip %d inside the last %d picks — that is what the caller hears as a recording",
					i, got[0], fillerNoRepeat)
			}
		}
		window = append(window, got[0])
		if len(window) > fillerNoRepeat {
			window = window[1:]
		}
		seen[got[0]] = true
	}
	// ...and it still uses the whole pool, rather than ping-ponging between two.
	if len(seen) != len(clips) {
		t.Fatalf("only %d of %d clips were ever used — the rotation is not spreading", len(seen), len(clips))
	}
}

// A pool of one cannot alternate, and must not spin trying.
func TestThinkingFillerHandlesSingleClipPool(t *testing.T) {
	thinkingFillers.Store(&thinkingPool{clips: [][]byte{{7}}})
	t.Cleanup(func() { thinkingFillers.Store(nil) })

	s := &Session{}
	for i := 0; i < 5; i++ {
		if got := s.pickThinkingFiller(); len(got) != 1 || got[0] != 7 {
			t.Fatalf("pick %d = %v, want the only clip", i, got)
		}
	}
}

// The bank must be wider than the no-repeat window, or the constraint is
// unsatisfiable and the picker is FORCED to repeat — which is the failure the
// window exists to prevent.
func TestFillerBankIsWiderThanTheNoRepeatWindow(t *testing.T) {
	if len(thinkingLines) <= fillerNoRepeat {
		t.Fatalf("%d filler lines against a no-repeat window of %d — the window cannot be honoured",
			len(thinkingLines), fillerNoRepeat)
	}
}

// A filler that NAMES the wait makes the caller notice it and then wait for the
// thing it promised. The first live call's listener reported the agent "said one
// sec several times"; nothing here may promise time.
func TestFillersDoNotPromiseTime(t *testing.T) {
	banned := []string{"sec", "moment", "minute", "hold on", "just a", "bear with"}
	for _, line := range thinkingLines {
		low := strings.ToLower(line)
		for _, b := range banned {
			if strings.Contains(low, b) {
				t.Errorf("filler %q promises the caller a wait (%q) — it draws attention to the gap it is covering", line, b)
			}
		}
	}
}

// The threshold has to sit above ordinary turn-taking on THIS stack, or it fires
// on nearly every turn. These are the reply latencies measured on the first live
// call.
func TestFillerThresholdSitsAboveOrdinaryTurnTaking(t *testing.T) {
	measured := []int{1976, 2256, 1384, 3770, 1640, 1570, 1719, 1671, 5321}
	fired := 0
	for _, ms := range measured {
		if ms > thinkingFillerAfterMs {
			fired++
		}
	}
	if fired > len(measured)/2 {
		t.Errorf("threshold %dms fires on %d of %d measured turns — that is the tic, not coverage",
			thinkingFillerAfterMs, fired, len(measured))
	}
	if fired == 0 {
		t.Errorf("threshold %dms never fires, so the long turns (3.8s, 5.3s) play as dead air", thinkingFillerAfterMs)
	}
}
