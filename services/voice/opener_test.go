package main

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// The opener decision, replayed against the calls it was built from.
//
// Every trace below was measured off the FIRST LIVE CAMPAIGN's own dual-channel
// recordings — the prospect channel, framed at 20ms and thresholded at
// speechEnergyThreshold, exactly as the live agent sees it — together with the
// answering-machine verdict Telnyx returned for that same call. They are not
// invented shapes: they are the six calls that were actually answered on 20 Aug
// 2026, three of them recordings and three of them people.
//
// The old logic pitched all three recordings. What makes that hard to fix is
// visible in the numbers: on the first two seconds of audio, #6 and #7 (both
// machines) look exactly like #4 (a person saying hello). The separation is in
// the SHAPE — one utterance and then a wait, versus a fragment and a second
// utterance — and in AMD, which was right every time but took 3.0s to 6.5s.
// ---------------------------------------------------------------------------

// speechRun is one stretch of prospect audio above the speech floor.
type speechRun struct{ fromMs, toMs int }

// openerTrace replays one real call and reports what the opener would do.
type openerTrace struct {
	name  string
	runs  []speechRun // prospect speech, ms from the moment the call was answered
	amd   string      // "human" | "machine" | "" (never ruled)
	amdMs int         // when the verdict landed, ms after the answer
}

// play steps the trace at the same 100ms cadence as the live ticker and returns
// the first decision the opener makes, with the elapsed time it made it at.
func (tr openerTrace) play(t *testing.T) (reason string, atMs int) {
	t.Helper()
	const step = 100

	for now := 0; now <= 12000; now += step {
		st := openerState{elapsed: time.Duration(now) * time.Millisecond}

		// Cumulative speech, last speech instant and utterance count, exactly as
		// the inbound bridge accumulates them.
		lastSpeech := -1
		prevFrame := -1
		var spokeMs, curMs, longestMs int
		for _, r := range tr.runs {
			for f := r.fromMs; f < r.toMs; f += frameMillis {
				if f > now {
					break
				}
				spokeMs += frameMillis
				if prevFrame < 0 || f-prevFrame > int(preOpenerUtteranceGap/time.Millisecond) {
					st.utterances++
					curMs = 0
				}
				curMs += frameMillis
				if curMs > longestMs {
					longestMs = curMs
				}
				prevFrame, lastSpeech = f, f
			}
		}
		st.longest = time.Duration(longestMs) * time.Millisecond
		st.spoke = time.Duration(spokeMs) * time.Millisecond
		st.quiet = st.elapsed
		if lastSpeech >= 0 {
			st.quiet = time.Duration(now-lastSpeech) * time.Millisecond
		}

		if tr.amd != "" && now >= tr.amdMs {
			switch tr.amd {
			case "human":
				st.amd = amdHuman
			case "machine":
				st.amd = amdMachine
			}
		}

		if reason, decided := openerDecide(st); decided {
			return reason, now
		}
	}
	return "never", -1
}

// The three recordings. Every one of these was pitched by the old logic; none of
// them may be pitched now — the opener has to still be held when AMD rules.
func TestOpenerHoldsUntilAMDRulesOnRealVoicemails(t *testing.T) {
	machines := []openerTrace{
		{
			// JP STUDIO — a continuous carrier/voicemail greeting. Never pauses for
			// as much as 100ms, so it never "settles"; the old blind hard timeout
			// spoke straight over it at 3.0s and AMD only ruled at 6.5s.
			name: "JP Studio — unbroken greeting",
			runs: []speechRun{{40, 240}, {300, 340}, {380, 1000}, {1020, 1100}, {1120, 1920},
				{1940, 2120}, {2140, 2160}, {2240, 2260}, {2280, 2340}, {2360, 2400},
				{2480, 2620}, {2640, 2680}, {2700, 2920}, {2940, 2980}, {3000, 3060},
				{3080, 3120}, {3140, 3420}, {3440, 3480}, {3640, 3960}, {4040, 4640},
				{4660, 4680}, {4960, 5080}, {5160, 5280}, {5320, 5360}, {5400, 5440},
				{5480, 5500}, {5520, 5540}, {5760, 5960}, {5980, 6000}, {6260, 6280}},
			amd: "machine", amdMs: 6464,
		},
		{
			// Weddings By Neeraj Kamra — a 280ms burst, a 940ms pause for breath,
			// then the rest of the greeting. The pause is what the old settle
			// trigger read as a person saying "हैलो?" and waiting.
			name: "Neeraj Kamra — greeting with a 940ms breath",
			runs: []speechRun{{80, 360}, {1300, 1360}, {1440, 1600}, {1660, 1960},
				{1980, 2300}, {2620, 2660}, {3320, 3520}},
			amd: "machine", amdMs: 4268,
		},
		{
			// MAA Animation — a 60ms blip, a 1,020ms pause, then the greeting. Same
			// shape, even less to go on.
			name: "MAA Animation — greeting with a 1,020ms breath",
			runs: []speechRun{{400, 460}, {1480, 1580}, {1860, 1880}, {1900, 1920},
				{2340, 2380}, {2400, 2520}, {2980, 3000}, {3020, 3320}, {3340, 3400},
				{4420, 4540}},
			amd: "machine", amdMs: 5123,
		},
	}

	for _, tr := range machines {
		t.Run(tr.name, func(t *testing.T) {
			reason, at := tr.play(t)
			if reason != "" {
				t.Errorf("opener released (%s) at %dms — this is a recording, and AMD ruled machine at %dms."+
					" The agent would have pitched %dms of a voicemail.", reason, at, tr.amdMs, tr.amdMs-at)
			}
		})
	}
}

// The three people. None of them may be delayed by the machine handling above —
// the whole point of the listen-first design is that a human never sits in dead
// air, and two of the three humans on this run hung up inside nine seconds.
func TestOpenerStillSpeaksPromptlyToRealPeople(t *testing.T) {
	humans := []struct {
		openerTrace
		wantReason string
		wantByMs   int
	}{
		{
			// Porur GK Studio — answered with a full 1.3s greeting, then went quiet
			// for 6.6 seconds waiting for the caller. The good case.
			openerTrace: openerTrace{
				name: "Porur GK — a whole greeting, then waits",
				runs: []speechRun{{440, 680}, {720, 820}, {860, 1040}, {1060, 1300},
					{1360, 1460}, {1500, 1760}},
				amd: "human", amdMs: 4901,
			},
			wantReason: "speech_settled", wantByMs: 2400,
		},
		{
			// Aperture House — picked up and said nothing for 3.1 seconds. The old
			// 3s timeout meant he heard silence until the exact moment he gave up
			// and started to speak; he hung up at 8.5s.
			openerTrace: openerTrace{
				name: "Aperture House — answered in silence",
				runs: []speechRun{{3140, 3440}},
				amd:  "human", amdMs: 3124,
			},
			wantReason: "timeout", wantByMs: 1600,
		},
		{
			// The Milan Studio — also a silent answer; AMD confirmed human at 3.0s.
			openerTrace: openerTrace{
				name: "Milan Studio — answered in silence",
				runs: []speechRun{{3200, 3600}},
				amd:  "human", amdMs: 3031,
			},
			wantReason: "timeout", wantByMs: 1600,
		},
		{
			// The validation call, 20 Aug 2026. He answered with a 280ms sound and
			// then waited in silence for 8.4 seconds.
			//
			// This is the case that caught the first version of this logic out. That
			// 280ms is BYTE FOR BYTE the shape of the two voicemails above — same
			// length, same position — so nothing about the sound itself can be used
			// to tell them apart, and requiring a full greeting pushed him to 2.8
			// seconds of dead air. 2.8s is what Aperture House hung up on.
			//
			// The separator is the silence AFTER: 8,440ms here against 940ms and
			// 1,020ms on the recordings.
			openerTrace: openerTrace{
				name: "validation call — a 280ms sound, then he waits",
				runs: []speechRun{{400, 680}},
				amd:  "human", amdMs: 3655,
			},
			wantReason: "fragment_settled", wantByMs: 1900,
		},
	}

	for _, tc := range humans {
		t.Run(tc.name, func(t *testing.T) {
			reason, at := tc.play(t)
			if reason != tc.wantReason {
				t.Errorf("opener reason = %q at %dms, want %q", reason, at, tc.wantReason)
			}
			if at < 0 || at > tc.wantByMs {
				t.Errorf("opener released at %dms, want no later than %dms — a person who has "+
					"answered is listening to silence for every one of those milliseconds", at, tc.wantByMs)
			}
		})
	}
}

// AMD is not always decisive. "not_sure" and "silence" mean Telnyx ran out of
// analysis time, NOT that a machine answered — treating them as a machine would
// hang up on real people, and continuing to hold would leave someone who has
// already picked up listening to nothing.
func TestOpenerReleasesOnInconclusiveAMD(t *testing.T) {
	st := openerState{
		elapsed:    2 * time.Second,
		spoke:      200 * time.Millisecond, // a fragment — machine-shaped
		quiet:      1500 * time.Millisecond,
		utterances: 2,
		amd:        amdUnsure,
	}
	reason, decided := openerDecide(st)
	if !decided || reason != "amd_inconclusive" {
		t.Fatalf("openerDecide = (%q, %v), want an immediate amd_inconclusive release", reason, decided)
	}
}

// The hold must always end. If AMD never returns a verdict at all — a webhook
// lost, a leg Telnyx never analysed — the agent has to open anyway rather than
// sit on a live call forever.
func TestOpenerHoldAlwaysExpires(t *testing.T) {
	// Machine-shaped and still talking: the longest hold there is.
	st := openerState{elapsed: openerHoldEvidence, spoke: 3 * time.Second,
		quiet: 20 * time.Millisecond, utterances: 3, amd: amdPending}
	if reason, decided := openerDecide(st); !decided || reason != "amd_hold_expired" {
		t.Fatalf("openerDecide = (%q, %v) at the evidence deadline, want amd_hold_expired", reason, decided)
	}
	// One short fragment and then quiet, with no second utterance to convict it:
	// the SHORT deadline, because silence on a live call is the expensive kind of
	// waiting and there is no evidence here worth waiting on.
	st = openerState{elapsed: openerHoldQuick, spoke: 400 * time.Millisecond,
		quiet: 900 * time.Millisecond, utterances: 1, amd: amdPending}
	if reason, decided := openerDecide(st); !decided || reason != "amd_hold_expired" {
		t.Fatalf("openerDecide = (%q, %v) at the quick deadline, want amd_hold_expired", reason, decided)
	}
}

// A verdict of "machine" must stop the opener dead, whatever the audio looked
// like. This is the one decision that ends with the agent saying nothing at all.
func TestOpenerNeverSpeaksOnceAMDSaysMachine(t *testing.T) {
	st := openerState{elapsed: 5 * time.Second, spoke: 3 * time.Second,
		quiet: 2 * time.Second, utterances: 1, amd: amdMachine}
	reason, decided := openerDecide(st)
	if !decided || reason != "" {
		t.Fatalf("openerDecide = (%q, %v), want a silent stop", reason, decided)
	}
}

// ---------------------------------------------------------------------------
// The silent hangup — a live human, killed by the gate that exists to protect
// him. Call meera1, 22 Aug 2026, +91 90000 00001.
//
// He picked up, said "हैलो?", heard nothing, and said it again, and again. Each
// one was ~380ms — under openerMinUtterance — so the settled-speech rule never
// fired; he kept talking, so `quiet` never reached openerFragmentQuiet and the
// fragment rule never fired either. The call fell through to "wait for AMD", and
// AMD, listening to a voice repeating "hello?" with pauses, returned `machine`
// at 5,480ms. The agent hung up without ever making a sound.
//
// The loop is self-inflicted: the agent's own silence is what makes a person say
// hello repeatedly, and saying hello repeatedly is what gets him classified as a
// machine.
//
// The veto runs at AMD time rather than before it, so this table is checked at
// each call's own AMD verdict — the moment the decision is actually made.
func TestHumanShapeOverridesAMD(t *testing.T) {
	cases := []struct {
		name      string
		utt       int64
		spokeMs   int64
		longestMs int64
		wantHuman bool
	}{
		// The three real voicemails. None may be vetoed.
		{"JP Studio — one unbroken 4.5s greeting", 1, 4460, 4460, false},
		{"Neeraj Kamra — greeting with a 940ms breath", 4, 1360, 840, false},
		{"MAA Animation — greeting chopped into fragments", 5, 860, 380, false},

		// The live humans this has hung up on. Both must be vetoed.
		{"meera1 — a person saying हैलो? into dead air", 3, 1140, 380, true},
		// wa_human_test2: picked up, said "hello", and was hung up on at 4.38s.
		// 2.4s of speech in short bursts — the case the old upper bound on
		// `spoke` excluded, so talking MORE made him look more like a machine.
		{"wa_human_test2 — he answered and said hello, twice", 2, 2400, 900, true},
		{"wa_human_test2 — same call, if the bursts were shorter", 4, 2400, 700, true},

		// A recorded greeting followed by the beep: one long run, then a blip.
		// Long total speech must NOT be enough on its own to call it a person.
		{"greeting then beep — long unbroken run", 2, 3800, 3400, false},

		// Guards on the edges.
		{"answered in silence — nothing to judge", 0, 0, 0, false},
		{"one short sound and a wait — the fragment case", 1, 280, 280, false},
	}

	for _, tc := range cases {
		s := &Session{}
		s.preOpenerUtterances.Store(tc.utt)
		s.speechMillis.Store(tc.spokeMs)
		s.longestUtteranceMs.Store(tc.longestMs)

		if got := s.looksHuman(); got != tc.wantHuman {
			mean := int64(0)
			if tc.utt > 0 {
				mean = tc.spokeMs / tc.utt
			}
			if tc.wantHuman {
				t.Errorf("%s: NOT vetoed (utt=%d spoke=%d longest=%d mean=%d) — the agent hangs up on a live person",
					tc.name, tc.utt, tc.spokeMs, tc.longestMs, mean)
			} else {
				t.Errorf("%s: vetoed (utt=%d spoke=%d longest=%d mean=%d) — the agent would pitch a recording",
					tc.name, tc.utt, tc.spokeMs, tc.longestMs, mean)
			}
		}
	}
}

// A person who says one short "हैलो?" and then holds the phone waiting is the
// shape fragment_settled already speaks to at ~1.5s. AMD ruling "machine" at
// 4.4s must not then hang up on them mid-sentence.
func TestBriefHelloThenWaitingIsAPerson(t *testing.T) {
	cases := []struct {
		name      string
		spokeMs   int64
		quietFor  time.Duration
		wantHuman bool
	}{
		{"said हैलो? 1.5s ago and has waited since", 280, 1500 * time.Millisecond, true},
		{"said हैलो? and is still mid-word", 280, 100 * time.Millisecond, false},
		{"never made a sound", 0, 0, false},
	}

	for _, tc := range cases {
		s := &Session{}
		s.preOpenerUtterances.Store(1)
		s.speechMillis.Store(tc.spokeMs)
		s.longestUtteranceMs.Store(tc.spokeMs)
		if tc.spokeMs > 0 {
			s.lastSpeechNano.Store(time.Now().Add(-tc.quietFor).UnixNano())
		}

		if got := s.looksHuman(); got != tc.wantHuman {
			t.Errorf("%s: looksHuman() = %v, want %v", tc.name, got, tc.wantHuman)
		}
	}
}

// A recorded greeting still talking must never match the brief-hello clause,
// however short the burst it is currently in the middle of.
func TestRecordingStillTalkingIsNotAPerson(t *testing.T) {
	s := &Session{}
	s.preOpenerUtterances.Store(5)
	s.speechMillis.Store(860) // MAA Animation
	s.longestUtteranceMs.Store(380)
	s.lastSpeechNano.Store(time.Now().Add(-2 * time.Second).UnixNano())

	if s.looksHuman() {
		t.Error("MAA Animation recording vetoed as a person — the agent would pitch a machine")
	}
}

// Once the veto has fired, the opener must actually be released — otherwise the
// call is saved from the hangup only to sit in silence.
func TestVetoReleasesTheOpener(t *testing.T) {
	st := openerState{
		elapsed:       5500 * time.Millisecond,
		spoke:         1140 * time.Millisecond,
		utterances:    3,
		longest:       380 * time.Millisecond,
		amd:           amdMachine,
		humanEvidence: true,
	}
	reason, decided := openerDecide(st)
	if !decided || reason == "" {
		t.Fatalf("veto set but the opener stays silent: reason=%q decided=%v", reason, decided)
	}
	if reason != "amd_overridden_human" {
		t.Errorf("reason = %q, want amd_overridden_human", reason)
	}
}
