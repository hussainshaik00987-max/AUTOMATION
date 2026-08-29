package main

import "log/slog"

// ---------------------------------------------------------------------------
// Campaign health — is the agent actually holding conversations?
//
// A disposition says what a call was FOR. It does not say whether the agent
// worked. The agent has been silently broken twice in this project's history and
// both times the campaign would have kept dialling strangers:
//
//   - a VAD sensitivity left it deaf, and it said nothing for 33 seconds while
//     the prospect asked "हेलो?" five times;
//   - a filler pool exhausted Gemini's quota and calls opened in silence.
//
// From the outside both look like ordinary hangups. `hung_up` is exactly what
// results.csv recorded. Nothing in the run said "the agent is not speaking",
// because nothing was measuring it.
//
// So the campaign now checks itself. Every healthCheckEvery dials it asks one
// question of the calls that actually gave the agent a chance to talk: did it?
// If the answer is no often enough, the run stops rather than burning the list.
// ---------------------------------------------------------------------------

// healthCheckEvery is how many dials pass between checks.
const healthCheckEvery = 10

// A call only counts toward the health verdict if the agent genuinely had an
// opportunity to speak. Rings-out, machines and two-second hangups are normal
// outcomes and say nothing about whether the agent is alive, so they are
// excluded — counting them would make a bad hour look like a broken build.
const (
	healthMinConnectedSec = 8   // the line was up long enough for a reply to be due
	healthMinSpeechMs     = 500 // ...and the prospect actually said something to it
)

// healthMinReplyRate is the floor. Below this, across a full check window, the
// agent is not conversing and the run stops.
//
// It is not 1.0: a prospect can talk over the opener and hang up before the
// model's first turn lands, and that is a real call, not a fault. It is not near
// zero either — anything under half means most people who spoke got nothing back.
const healthMinReplyRate = 0.5

// campaignHealth tallies how often the agent replied when it had the chance.
type campaignHealth struct {
	dials       int
	chances     int // calls where a reply was genuinely due
	replies     int // ...of which the live model actually spoke
	lastChecked int // dial count at the previous check
}

// record folds one finished call into the tally.
func (h *campaignHealth) record(r Result) {
	h.dials++

	// Outcomes where the agent staying silent is CORRECT. A voicemail greeting is
	// several seconds of loud, continuous "speech" that trips every duration test
	// below, and the agent is right not to answer it — AMD said machine and the
	// call was hung up. Counting those would make a run full of answering
	// machines look like a broken agent, which is precisely the shape of an
	// evening campaign.
	switch r.Disposition {
	case "voicemail", "no_answer", "dial_failed", "answer_lost":
		// answer_lost belongs here for the same reason: no media path was ever
		// opened, so the agent had no way to speak and its silence is not evidence
		// of anything. It is a ROUTE fault and routeFailLimit is what catches it.
		return
	}

	if r.ConnectedSec < healthMinConnectedSec || r.ProspectSpeechMs < healthMinSpeechMs {
		return // not an opportunity — says nothing either way
	}
	h.chances++
	if r.AgentTurns > 0 {
		h.replies++
	}
}

// due reports whether it is time to run a check.
func (h *campaignHealth) due() bool {
	return h.dials-h.lastChecked >= healthCheckEvery
}

// rate is the share of opportunities the agent actually answered.
func (h *campaignHealth) rate() float64 {
	if h.chances == 0 {
		return 1 // nothing to judge on; not a failure
	}
	return float64(h.replies) / float64(h.chances)
}

// check evaluates the window and reports whether the campaign should stop, with
// the reason. It always logs the numbers, because "the agent replied on 9 of 9"
// is worth seeing in the log of a run that produced no sales — it separates
// "the pitch isn't landing" from "the agent isn't talking", which are the two
// explanations a bad campaign always has and which look identical from the CSV.
func (h *campaignHealth) check(log *slog.Logger) (stop bool, reason string) {
	h.lastChecked = h.dials

	if h.chances == 0 {
		log.Warn("health check: no call yet gave the agent a chance to speak",
			"dials", h.dials,
			"note", "every call so far rang out, hit a machine, or ended within "+
				itoa(healthMinConnectedSec)+"s — nothing to judge the agent on yet")
		return false, ""
	}

	rate := h.rate()
	fields := []any{
		"dials", h.dials,
		"calls_where_a_reply_was_due", h.chances,
		"calls_the_agent_replied_on", h.replies,
		"reply_rate", int(rate * 100),
		"floor", int(healthMinReplyRate * 100),
	}

	if rate >= healthMinReplyRate {
		log.Info("health check: the agent is holding conversations", fields...)
		return false, ""
	}

	log.Error("HEALTH CHECK FAILED — the agent is not replying to people who speak to it", fields...)
	return true, "agent replied on only " + itoa(h.replies) + " of " + itoa(h.chances) +
		" calls where someone spoke to it and the line stayed up"
}

// itoa avoids dragging strconv in for three call sites.
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}
