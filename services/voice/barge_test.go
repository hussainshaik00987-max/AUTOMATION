package main

import "testing"

// loud is comfortably above bargeInThreshold; quiet is line silence. Real frames
// on this route cluster at 0–99 and 1000–3999, so these stand in for both.
const (
	loudEnergy  = 2000
	quietEnergy = 40
)

// frame is one 20ms μ-law frame. Contents don't matter to the gate — only that
// each frame is distinguishable, so the release order can be checked.
func frame(marker byte) []byte {
	b := make([]byte, 160)
	b[0] = marker
	return b
}

// A back-channel is the whole reason this gate exists: on a live call an
// acknowledging "हाँ" killed the agent's turn and cost eleven seconds of dead
// air. ~200ms of speech must leave the agent holding the floor.
func TestBargeGateWithholdsBackChannel(t *testing.T) {
	var g bargeGate
	const backChannelFrames = 10 // ~200ms, a "हाँ"

	for i := range backChannelFrames {
		d, release, _ := g.admit(frame(byte(i)), loudEnergy)
		if d != bargeHold {
			t.Fatalf("frame %d: decision = %v, want bargeHold", i, d)
		}
		if release != nil {
			t.Fatalf("frame %d: released %d frames while still unproven", i, len(release))
		}
	}

	// A short gap is the space between words, not the end of the utterance, so it
	// must NOT collapse the run on its own.
	for i := 0; i < bargeGapToleranceFrames; i++ {
		if d, _, _ := g.admit(frame(50), quietEnergy); d != bargeHold {
			t.Fatalf("quiet frame %d inside a run gave %v, want bargeHold", i, d)
		}
	}

	// Once the gap is genuinely long, the run collapses and nothing ever reaches
	// the model — the agent keeps the floor.
	d, release, held := g.admit(frame(99), quietEnergy)
	if d != bargeBackChannel {
		t.Fatalf("decision = %v, want bargeBackChannel", d)
	}
	if release != nil {
		t.Fatalf("released %d frames for a back-channel", len(release))
	}
	if held != backChannelFrames {
		t.Fatalf("held = %d, want %d", held, backChannelFrames)
	}
}

// Real speech has micro-pauses between words. A prospect breaking in with
// "haan— nahi— suniye—" must still be heard: on a live call eleven such bursts
// were each shorter than the floor, so the agent talked over them for six
// straight seconds and the prospect hung up.
func TestBargeGateAllowsBurstySpeechToInterrupt(t *testing.T) {
	var g bargeGate
	qualified := false

	// Bursts of 6 loud frames separated by gaps shorter than the tolerance.
	for burst := 0; burst < 5 && !qualified; burst++ {
		for i := 0; i < 6; i++ {
			d, release, _ := g.admit(frame(byte(burst*10+i)), loudEnergy)
			if d == bargeQualified {
				qualified = true
				if len(release) < bargeInMinFrames {
					t.Fatalf("released only %d frames — the gaps were dropped from the utterance", len(release))
				}
				break
			}
		}
		if qualified {
			break
		}
		for i := 0; i < bargeGapToleranceFrames; i++ {
			g.admit(frame(200), quietEnergy)
		}
	}
	if !qualified {
		t.Fatal("bursty speech never qualified — a stuttering prospect cannot interrupt at all")
	}
}

// A real interruption must get through — and must reach the model from its FIRST
// syllable, or Gemini answers a question it only half heard.
func TestBargeGateReleasesSustainedInterruption(t *testing.T) {
	var g bargeGate

	for i := range bargeInMinFrames - 1 {
		if d, _, _ := g.admit(frame(byte(i)), loudEnergy); d != bargeHold {
			t.Fatalf("frame %d: decision = %v, want bargeHold", i, d)
		}
	}

	d, release, held := g.admit(frame(byte(bargeInMinFrames-1)), loudEnergy)
	if d != bargeQualified {
		t.Fatalf("decision = %v, want bargeQualified", d)
	}
	if held != bargeInMinFrames {
		t.Fatalf("held = %d, want %d", held, bargeInMinFrames)
	}
	if len(release) != bargeInMinFrames {
		t.Fatalf("released %d frames, want all %d held", len(release), bargeInMinFrames)
	}
	// Oldest first: the model must hear the interruption in the order it was said.
	for i, f := range release {
		if f[0] != byte(i) {
			t.Fatalf("release[%d] starts with %d, want %d — held audio is out of order", i, f[0], i)
		}
	}

	// Once qualified the prospect owns the rest of the window, quiet tail included,
	// so trailing frames are not re-gated mid-sentence.
	if d, _, _ := g.admit(frame(200), quietEnergy); d != bargePass {
		t.Fatalf("after qualifying, decision = %v, want bargePass", d)
	}
}

// Frames are copied on the way in. The websocket reader reuses its buffer, so a
// gate that parked the caller's slice would hand Gemini whatever arrived next.
func TestBargeGateCopiesHeldFrames(t *testing.T) {
	var g bargeGate
	buf := frame(1)
	for range bargeInMinFrames - 1 {
		g.admit(buf, loudEnergy)
		buf[0]++ // simulate the reader overwriting its buffer in place
	}
	_, release, _ := g.admit(buf, loudEnergy)
	if len(release) < 2 || release[0][0] == release[1][0] {
		t.Fatal("held frames alias the caller's buffer — they must be copied")
	}
}

// A back-channel run that collapses must not leave the gate primed, or the next
// turn gets interrupted by fewer frames than the floor requires.
func TestBargeGateResetClearsRun(t *testing.T) {
	var g bargeGate
	for range bargeInMinFrames - 1 {
		g.admit(frame(1), loudEnergy)
	}
	g.reset()
	if d, _, _ := g.admit(frame(2), loudEnergy); d != bargeHold {
		t.Fatalf("after reset, one loud frame gave %v — the run survived the reset", d)
	}
}

// Silence during the agent's turn is the common case and must stay cheap and
// unlogged: it is neither a back-channel nor an interruption.
func TestBargeGateQuietFrameIsEcho(t *testing.T) {
	var g bargeGate
	if d, _, held := g.admit(frame(1), quietEnergy); d != bargeEcho || held != 0 {
		t.Fatalf("quiet frame gave (%v, held=%d), want (bargeEcho, 0)", d, held)
	}
}

// Frames held back while the agent was finishing must be FORWARDED when it goes
// quiet, never dropped. People start answering over the tail of a sentence; a
// gate that discards what it was holding costs the model the first syllables of
// the reply and it sits waiting for a prospect who has already spoken.
func TestBargeGateReleaseForwardsHeldFrames(t *testing.T) {
	var g bargeGate
	const held = 5 // a run that never qualified, still parked
	for i := range held {
		if d, _, _ := g.admit(frame(byte(i)), loudEnergy); d != bargeHold {
			t.Fatalf("frame %d: decision = %v, want bargeHold", i, d)
		}
	}

	out := g.release()
	if len(out) != held {
		t.Fatalf("release returned %d frames, want the %d held", len(out), held)
	}
	for i, f := range out {
		if f[0] != byte(i) {
			t.Fatalf("release[%d] starts with %d, want %d — released out of order", i, f[0], i)
		}
	}
	// The ring is emptied by the release, not merely copied out.
	if again := g.release(); len(again) != 0 {
		t.Fatalf("second release returned %d frames — the ring was not cleared", len(again))
	}
	// ...and the run counter went with it, so the next turn gets the full floor
	// rather than qualifying early on leftovers.
	if d, _, held := g.admit(frame(99), loudEnergy); d != bargeHold || held != 1 {
		t.Fatalf("after release, one loud frame gave (%v, held=%d), want (bargeHold, 1)", d, held)
	}
}
