package main

// Barge-in: deciding when the prospect has actually taken the floor.
//
// While the agent is speaking, inbound audio is one of three things — line echo
// of the agent's own voice, a back-channel ("हाँ", "अच्छा", "हूँ"), or a real
// interruption. Only the third should reach the model, because Gemini's VAD
// treats anything it receives as a reason to abandon the current turn.
//
// Loudness cannot separate the last two. Measured on the US-number →
// Indian-mobile route, the prospect's "हाँ" arrives at 1000–3999 — exactly where
// a real interruption arrives — so any threshold that lets one through lets the
// other through too. On a live call that cost eleven seconds: an acknowledging
// "हाँ" killed the agent's turn, the playout buffer was wiped, and the prospect
// said "हलो? हलो?" into silence while the same thing happened to the recovery.
//
// LENGTH separates them. A back-channel is ~200ms and stops; a real
// interruption keeps going. So a loud frame no longer passes on its own — it
// opens a run, and only a run that survives bargeInMinFrames is admitted.

// bargeDecision is what to do with one inbound frame that arrived while the
// live model's turn was still playing at the caller.
type bargeDecision int

const (
	// bargeHold: might be the start of an interruption, might be a back-channel.
	// Too early to tell — the frame is parked, not dropped.
	bargeHold bargeDecision = iota
	// bargeBackChannel: a run ended before it qualified. What was held was an
	// acknowledgement; the agent keeps the floor and finishes its sentence.
	bargeBackChannel
	// bargeEcho: a quiet frame with no run in progress — line silence or echo of
	// the agent's own voice. The common case, and deliberately not logged.
	bargeEcho
	// bargeQualified: a sustained interruption. The parked frames are released so
	// the model hears it from the first syllable rather than joining mid-word.
	bargeQualified
	// bargePass: already qualified earlier in this playback window — forward.
	bargePass
)

// bargeGate holds the state of one call's barge-in decision. It is driven from
// the single Telnyx→Gemini goroutine and needs no lock.
// bargeGapToleranceFrames is how much quiet may sit INSIDE a run without ending
// it: 5 frames = 100ms.
//
// Requiring strictly consecutive loud frames was too literal about how people
// talk. On a live call the prospect tried to break in eleven times in short
// bursts — "haan— nahi— suniye—" — and not one burst reached 360ms unbroken, so
// the agent kept the floor and talked over them for six straight seconds. Real
// speech has micro-pauses between words; only a gap long enough to be a genuine
// stop should end the run.
const bargeGapToleranceFrames = 5

type bargeGate struct {
	run   int      // frames above bargeInThreshold in this run
	quiet int      // consecutive quiet frames seen inside the current run
	open  bool     // a run has qualified; the prospect owns the rest of this window
	ring  [][]byte // frames withheld while the run is still unproven
}

// release ends the gating window — the agent has stopped being audible, so there
// is no turn left to protect. It returns any frames still held.
//
// Those frames MUST be forwarded, not dropped. They are the beginning of the
// prospect's next utterance: people habitually start answering over the tail of
// the agent's sentence, and a gate that discards what it was holding costs the
// model the first ~360ms of their reply — it hears "...साल से कर रहे हैं" and has
// to wait for more before it can respond. Dropping them was measured at 23
// discarded speech frames on one live call, all of them the openings of answers.
func (g *bargeGate) release() (held [][]byte) {
	held, g.ring = g.ring, nil
	g.run, g.quiet, g.open = 0, 0, false
	return held
}

// reset drops the gate's state without forwarding anything. Only for a call
// that is ending, where held audio has nowhere useful to go.
func (g *bargeGate) reset() {
	g.run, g.quiet, g.open, g.ring = 0, 0, false, nil
}

// admit classifies one inbound frame. On bargeQualified it returns the frames to
// forward, oldest first. held is how many frames the run had accumulated, for
// logging — meaningful on bargeQualified and bargeBackChannel.
//
// The caller must forward the returned frames in order; the gate keeps no copy.
func (g *bargeGate) admit(mulaw []byte, energy int) (d bargeDecision, release [][]byte, held int) {
	if g.open {
		return bargePass, nil, 0
	}

	// Copy on the way in either way: the caller's buffer is reused by the
	// websocket reader on the next frame, so parking the slice itself would hand
	// the model garbage. Quiet frames inside a run are kept too, so a released
	// utterance is continuous audio rather than speech with the gaps cut out.
	park := func() {
		chunk := make([]byte, len(mulaw))
		copy(chunk, mulaw)
		g.ring = append(g.ring, chunk)
	}

	if energy >= bargeInThreshold {
		park()
		g.run++
		g.quiet = 0
		if g.run < bargeInMinFrames {
			return bargeHold, nil, g.run
		}
		g.open = true
		release, g.ring = g.ring, nil
		return bargeQualified, release, g.run
	}

	// Quiet. Inside a run, a short gap is the space between words — keep going.
	if g.run > 0 {
		g.quiet++
		if g.quiet <= bargeGapToleranceFrames {
			park()
			return bargeHold, nil, g.run
		}
		held = g.run
		g.run, g.quiet, g.ring = 0, 0, nil
		return bargeBackChannel, nil, held
	}
	return bargeEcho, nil, 0
}
