package main

import (
	"log/slog"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// The failure these tests pin down, in the order it was found:
//
// 21 Aug 2026, two controlled dials to a phone that was picked up on the first
// ring. Both came back hangup_cause=timeout · sip 487 · zero media frames, and
// nginx confirms Telnyx sent exactly three webhooks per call — initiated,
// hangup, streaming.stopped — and never once call.answered. A person held a live
// handset to their ear through half a minute of silence, and results.csv
// recorded "no_answer", which is the same row a phone nobody was near produces.
//
// The same day's campaign log explained the scale of it: of live2's 40 dials,
// the 21 that DID connect had their answer signal arrive between 5.5s and 29.2s
// after the dial — against a 30s ring timer. A distribution whose largest sample
// lands 0.8s inside the cutoff is a distribution being clipped, and the 19 dials
// filed as "rang out" are where the clipped ones went.
// ---------------------------------------------------------------------------

// answerLost is the only thing that separates "nobody picked up" from "somebody
// picked up and we were never told". Getting it wrong in either direction is
// expensive: miss it and a broken route reads as a quiet lead list; fire it
// spuriously and an ordinary no-answer rate reads as an outage.
func TestAnswerLostOnlyWhenTheFarEndClearedAnUnansweredCall(t *testing.T) {
	cases := []struct {
		name     string
		answered bool
		source   string
		want     bool
	}{
		// The failure itself. Only a person can clear a call from the far end,
		// so a callee hangup on a call we were never told was answered means
		// somebody was holding that handset.
		{"picked up and hung up on silence", false, "callee", true},

		// Our own ring timer expiring. This is what 16 of live2's 40 dials
		// reported, because at 30s we always cancelled first — which is exactly
		// why the failure stayed invisible across two campaigns.
		{"our ring timer fired", false, "caller", false},

		// The carrier gave up and did not say who. Not evidence of a person.
		{"network gave up", false, "unknown", false},

		// No hangup webhook arrived before the session finished. The safe
		// default is the ordinary outcome, never the alarming one.
		{"no cause reported", false, "", false},

		// An ordinary completed call: the prospect hangs up at the end. This is
		// the common case and must never be mistaken for the failure — it
		// carries the same hangup_source, and only `answered` separates them.
		{"ordinary end of a real conversation", true, "callee", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := Session{log: slog.Default()}
			if tc.answered {
				s.markAnswered()
			}
			s.hangupSource.Store(tc.source)
			if got := s.answerLost(); got != tc.want {
				t.Errorf("answerLost() = %v, want %v (answered=%v source=%q)",
					got, tc.want, tc.answered, tc.source)
			}
		})
	}
}

// answer_lost is a ROUTE fault, not a lead outcome, and the two circuit breakers
// have to treat it that way: it must not count as a chance for the agent to
// speak, because there was no media path at all, and it must count toward the
// dead-dial breaker, because it is the strongest evidence a route can give that
// it has stopped working — a person answered and the network never said so.
func TestAnswerLostCountsAsARouteFaultAndNotAsAgentSilence(t *testing.T) {
	r := Result{Disposition: "answer_lost", ConnectedSec: 0, ProspectSpeechMs: 0, AgentTurns: 0}

	if !deadDial(r) {
		t.Error("answer_lost must count toward routeFailLimit — it is a dial that carried no audio")
	}

	var h campaignHealth
	h.record(r)
	if h.chances != 0 {
		t.Errorf("answer_lost gave the agent %d chance(s) to speak; with no media path it gave it none", h.chances)
	}
}

// liveTwoAnswerLatencies is every answer-supervision delay live2 actually
// measured, in milliseconds from the dial. Five of these are answering machines,
// and a machine does not take eleven seconds to pick up — so most of this number
// is transit, not somebody reaching for their phone.
func liveTwoAnswerLatencies() []int64 {
	return []int64{5503, 7807, 8789, 8971, 9458, 9808, 10843, 10883, 11490, 11848,
		13174, 13580, 14116, 15950, 16001, 16254, 19653, 19993, 20097, 20609, 29201}
}

func answerRun(t *testing.T, ring time.Duration, latencies []int64, extra ...CallEvent) *runReport {
	t.Helper()
	path := filepath.Join(t.TempDir(), "run.jsonl")
	rl, err := OpenRunLog(path, "live2")
	if err != nil {
		t.Fatalf("open run log: %v", err)
	}
	rl.Manifest(Config{ProspectsFile: "wedding_run2.csv", FromNumber: "+19103172041",
		RingTimeout: ring}, 40, 40, nil)
	for i, ms := range latencies {
		rl.Call(CallEvent{Type: "call", Seq: i + 1, Business: "lead", Disposition: "hung_up",
			AnsweredMs: ms, TalkMs: 20000, FramesFromTelnyx: 400, OpenerAfterMs: 1500, AMDAfterMs: 4000})
	}
	for _, c := range extra {
		rl.Call(c)
	}
	rl.Close()
	rep, err := readRunFile(path)
	if err != nil {
		t.Fatalf("read run file: %v", err)
	}
	return rep
}

// The report has to say that the ring timer is deciding the campaign's answer
// rate. On live2's real numbers it had 0.8s of headroom and nothing said so —
// the run simply looked like an ordinary Indian no-answer rate.
func TestReportWarnsWhenTheRingTimerIsClippingAnswers(t *testing.T) {
	out := answerRun(t, 30*time.Second, liveTwoAnswerLatencies()).Render(false)

	if !strings.Contains(out, "ANSWER SUPERVISION") {
		t.Fatalf("report says nothing about how late answers arrive:\n%s", out)
	}
	// The worst sample against the timer is the number that matters. A median of
	// 13.2s looks perfectly healthy, and that is what hid this.
	if !strings.Contains(out, "worst 29.2s") {
		t.Errorf("report does not surface the worst answer delay:\n%s", out)
	}
	if !strings.Contains(out, "CUT OFF by the ring timer") {
		t.Errorf("report does not warn that answers are being clipped:\n%s", out)
	}
	if !strings.Contains(out, "0.8s of headroom") {
		t.Errorf("report does not quantify how little headroom is left:\n%s", out)
	}
}

// And it has to go quiet once the timer clears the distribution, or the warning
// becomes wallpaper. 60s against a 29.2s worst case is the shipped setting, and
// it must not shout.
func TestReportStaysQuietOnceTheRingTimerClearsTheDistribution(t *testing.T) {
	out := answerRun(t, 60*time.Second, liveTwoAnswerLatencies()).Render(false)

	if !strings.Contains(out, "ANSWER SUPERVISION") {
		t.Fatalf("the distribution is worth printing even when it is healthy:\n%s", out)
	}
	if strings.Contains(out, "CUT OFF by the ring timer") {
		t.Errorf("warned about clipping with 30.8s of headroom — a guard that cries wolf gets ignored:\n%s", out)
	}
}

// A call the prospect answered and hung up on is the failure caught red-handed,
// and it deserves its own line rather than being folded into the no-answers.
func TestReportCallsOutPeopleWhoAnsweredAndHeardSilence(t *testing.T) {
	lost := CallEvent{Type: "call", Seq: 99, Business: "Fairlight Studio", Disposition: "answer_lost",
		AnsweredMs: -1, TalkMs: -1, OpenerAfterMs: -1, AMDAfterMs: -1, FramesFromTelnyx: 0,
		HangupCause: "normal_clearing", HangupSource: "callee"}
	out := answerRun(t, 60*time.Second, liveTwoAnswerLatencies(), lost).Render(false)

	if !strings.Contains(out, "PICKED UP AND HUNG UP") {
		t.Errorf("report buries the one call that proves the route is dropping answers:\n%s", out)
	}
	if !strings.Contains(out, "The route, not the list") {
		t.Errorf("report does not say whose fault it is, which is the whole point:\n%s", out)
	}
}

// The report must compare against the ring timer THAT RUN used, not against
// whatever the code defaults to today, or a report read weeks later is silently
// measured against the wrong number.
func TestRingTimeoutComesFromTheRunsOwnManifest(t *testing.T) {
	if got := ringTimeoutMs(RunManifest{Tuning: map[string]any{"ring_timeout": "45s"}}); got != 45_000 {
		t.Errorf("ringTimeoutMs = %d, want 45000", got)
	}
	// An old run whose manifest predates the field must not be reported against
	// a fabricated timer.
	if got := ringTimeoutMs(RunManifest{Tuning: map[string]any{}}); got != 0 {
		t.Errorf("ringTimeoutMs with no manifest entry = %d, want 0", got)
	}
	if got := ringTimeoutMs(RunManifest{Tuning: map[string]any{"ring_timeout": "not a duration"}}); got != 0 {
		t.Errorf("ringTimeoutMs on junk = %d, want 0", got)
	}
}
