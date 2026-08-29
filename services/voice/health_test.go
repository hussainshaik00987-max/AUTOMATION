package main

import (
	"io"
	"log/slog"
	"testing"
)

func quietLog() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, &slog.HandlerOptions{Level: slog.LevelError + 1}))
}

// A call where somebody spoke to the agent and the line stayed up long enough
// for a reply to be due.
func chance(agentTurns int) Result {
	return Result{
		ConnectedSec:     30,
		ProspectSpeechMs: 2000,
		AgentTurns:       agentTurns,
	}
}

// The failure this exists to catch: the agent has gone silent — a bad VAD
// setting, an exhausted quota, a dead key — and every call looks like an
// ordinary hangup from the outside.
func TestHealthStopsWhenTheAgentNeverReplies(t *testing.T) {
	var h campaignHealth
	for i := 0; i < healthCheckEvery; i++ {
		h.record(chance(0))
	}
	if !h.due() {
		t.Fatalf("after %d dials the check should be due", healthCheckEvery)
	}
	stop, reason := h.check(quietLog())
	if !stop {
		t.Fatal("ten calls where nobody got a reply did not stop the campaign")
	}
	if reason == "" {
		t.Fatal("stopped without saying why")
	}
}

// ...and must not fire on a healthy run, however badly it is selling.
func TestHealthAllowsAConversingAgent(t *testing.T) {
	var h campaignHealth
	for i := 0; i < healthCheckEvery; i++ {
		h.record(chance(3))
	}
	if stop, _ := h.check(quietLog()); stop {
		t.Fatal("stopped a run where the agent replied on every call")
	}
}

// Rings-out, machines and two-second hangups are normal cold-calling outcomes
// and say nothing about whether the agent works. Counting them would stop a
// perfectly healthy campaign on a bad hour — which is exactly what the first
// live run looked like: 9 dials, 3 never connected, 3 machines.
func TestHealthIgnoresCallsThatGaveNoChanceToSpeak(t *testing.T) {
	var h campaignHealth
	neverAnswered := Result{ConnectedSec: 0, ProspectSpeechMs: 0, AgentTurns: 0}
	machine := Result{ConnectedSec: 6, ProspectSpeechMs: 3000, AgentTurns: 0}
	instantHangup := Result{ConnectedSec: 3, ProspectSpeechMs: 200, AgentTurns: 0}

	for i := 0; i < 4; i++ {
		h.record(neverAnswered)
		h.record(machine)
		h.record(instantHangup)
	}
	if h.chances != 0 {
		t.Fatalf("chances = %d, want 0 — none of these gave the agent an opportunity", h.chances)
	}
	if stop, _ := h.check(quietLog()); stop {
		t.Fatal("stopped a run consisting entirely of rings-out and machines")
	}
}

// The realistic mixed case: a bad hour AND a working agent. The agent replied
// every time it could; the rest never connected. That must not stop.
func TestHealthSurvivesABadHourWithAWorkingAgent(t *testing.T) {
	var h campaignHealth
	h.record(chance(4))
	h.record(chance(2))
	for i := 0; i < 8; i++ {
		h.record(Result{ConnectedSec: 0}) // rang out
	}
	if stop, _ := h.check(quietLog()); stop {
		t.Fatal("stopped although the agent replied on both calls it could")
	}
}

// A partial failure is still a failure: most people who spoke got nothing back.
func TestHealthStopsBelowTheFloor(t *testing.T) {
	var h campaignHealth
	h.record(chance(1)) // replied
	for i := 0; i < 4; i++ {
		h.record(chance(0)) // silent
	}
	if got := h.rate(); got >= healthMinReplyRate {
		t.Fatalf("rate = %.2f, expected below the %.2f floor for this fixture", got, healthMinReplyRate)
	}
	if stop, _ := h.check(quietLog()); !stop {
		t.Fatal("a 20% reply rate did not stop the campaign")
	}
}

// One prospect talking over the opener and hanging up before the model's first
// turn is a real call, not a fault — the floor is deliberately not 100%.
func TestHealthToleratesASingleUnansweredChance(t *testing.T) {
	var h campaignHealth
	for i := 0; i < 9; i++ {
		h.record(chance(2))
	}
	h.record(chance(0))
	if stop, _ := h.check(quietLog()); stop {
		t.Fatal("stopped over one call out of ten where the reply didn't land")
	}
}

// The window advances, so a second check covers the NEXT ten dials rather than
// re-judging the whole run from the top.
func TestHealthChecksEachWindowOnce(t *testing.T) {
	var h campaignHealth
	for i := 0; i < healthCheckEvery; i++ {
		h.record(chance(2))
	}
	if !h.due() {
		t.Fatal("first window should be due")
	}
	h.check(quietLog())
	if h.due() {
		t.Fatal("still due immediately after a check — the window did not advance")
	}
	for i := 0; i < healthCheckEvery-1; i++ {
		h.record(chance(2))
	}
	if h.due() {
		t.Fatal("due one dial early")
	}
	h.record(chance(2))
	if !h.due() {
		t.Fatal("second window did not come due")
	}
}

// Replayed from the first live campaign (20 Aug 2026, live1.jsonl/results_live1.csv).
// The agent WAS working that night — it just met three rings-out, three answering
// machines and a bad hour. The guard must not confuse that with breakage, or it
// would stop every evening run before it produced any data.
func TestHealthDoesNotFireOnTheFirstLiveCampaign(t *testing.T) {
	var h campaignHealth
	live1 := []Result{
		{Disposition: "no_answer", ConnectedSec: 0},                                          // Genda Phool  — rang out
		{Disposition: "no_answer", ConnectedSec: 0},                                          // Fotuwala     — rang out
		{Disposition: "voicemail", ConnectedSec: 8, ProspectSpeechMs: 4400},                  // JP Studio    — greeting
		{Disposition: "link_sent", ConnectedSec: 87, ProspectSpeechMs: 10080, AgentTurns: 6}, // Porur GK
		{Disposition: "hung_up", ConnectedSec: 8, ProspectSpeechMs: 300},                     // Aperture House      — gone at 8.5s
		{Disposition: "voicemail", ConnectedSec: 6, ProspectSpeechMs: 1980},                  // Neeraj Kamra
		{Disposition: "voicemail", ConnectedSec: 6, ProspectSpeechMs: 1140},                  // MAA Animation
		{Disposition: "no_answer", ConnectedSec: 0},                                          // MBphotography — rang out
		{Disposition: "incomplete", ConnectedSec: 29, ProspectSpeechMs: 3180, AgentTurns: 2}, // Milan Studio
	}
	for _, r := range live1 {
		h.record(r)
	}

	// Only the two real conversations should be judged.
	if h.chances != 2 {
		t.Fatalf("chances = %d, want 2 (Porur GK and Milan Studio) — check the exclusions", h.chances)
	}
	if h.replies != 2 {
		t.Fatalf("replies = %d, want 2 — the agent spoke on both", h.replies)
	}
	if stop, reason := h.check(quietLog()); stop {
		t.Fatalf("would have stopped the first live campaign: %s", reason)
	}
}

// The same night, had the agent been broken: identical dials, identical
// answering machines, but silence on the two calls where someone spoke to it.
// That must stop.
func TestHealthFiresOnTheSameNightWithASilentAgent(t *testing.T) {
	var h campaignHealth
	for _, r := range []Result{
		{Disposition: "no_answer", ConnectedSec: 0},
		{Disposition: "voicemail", ConnectedSec: 8, ProspectSpeechMs: 4400},
		{Disposition: "hung_up", ConnectedSec: 87, ProspectSpeechMs: 10080, AgentTurns: 0},
		{Disposition: "voicemail", ConnectedSec: 6, ProspectSpeechMs: 1980},
		{Disposition: "hung_up", ConnectedSec: 29, ProspectSpeechMs: 3180, AgentTurns: 0},
	} {
		h.record(r)
	}
	if stop, _ := h.check(quietLog()); !stop {
		t.Fatal("an agent that said nothing to either person who spoke to it was not caught")
	}
}
