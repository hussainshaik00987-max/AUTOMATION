package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The report is only worth having if it would have told you what the first live
// campaign's teardown took a day of log-reading, CSV-correcting and audio
// measurement to establish. So it is tested against that run's real numbers.
//
// live1, 20 Aug 2026: nine dials. Three never connected (zero media frames) and
// were logged as "hung_up" as though a person had rejected the call. Three were
// answering machines, and the agent delivered part of its pitch to every one of
// them before AMD ruled. Three were people, one of whom was sent a link after
// refusing twice.
func liveOneEvents() []CallEvent {
	return []CallEvent{
		{Type: "call", Seq: 1, Business: "Genda Phool Events", Disposition: "no_answer",
			AnsweredMs: -1, TalkMs: -1, OpenerAfterMs: -1, AMDAfterMs: -1, FramesFromTelnyx: 0},
		{Type: "call", Seq: 2, Business: "Fotuwala Studio", Disposition: "no_answer",
			AnsweredMs: -1, TalkMs: -1, OpenerAfterMs: -1, AMDAfterMs: -1, FramesFromTelnyx: 0},
		{Type: "call", Seq: 3, Business: "JP STUDIO", Disposition: "voicemail", AMD: "machine",
			AnsweredMs: 8000, TalkMs: 6464, FramesFromTelnyx: 316, FramesWithSpeech: 220,
			OpenerReason: "timeout", OpenerAfterMs: 3015, AMDAfterMs: 6464},
		{Type: "call", Seq: 4, Business: "Porur GK studio", Disposition: "link_sent", AMD: "human_residence",
			AnsweredMs: 9000, TalkMs: 87789, FramesFromTelnyx: 4329, FramesWithSpeech: 504,
			OpenerReason: "speech_settled", OpenerAfterMs: 2895, AMDAfterMs: 4901,
			LinkChannel: "whatsapp", LinkStatus: "queued", Refused: true, Landline: true,
			ReplyLatenciesMs: []int64{1002, 1729, 16, 240, 1478}, ThinkingFills: 9, Turns: 22},
		{Type: "call", Seq: 5, Business: "Aperture House", Disposition: "hung_up", AMD: "human_residence",
			AnsweredMs: 7000, TalkMs: 9710, FramesFromTelnyx: 423, FramesWithSpeech: 15,
			OpenerReason: "timeout", OpenerAfterMs: 3021, AMDAfterMs: 3124},
		{Type: "call", Seq: 6, Business: "Weddings By Neeraj Kamra", Disposition: "voicemail", AMD: "machine",
			AnsweredMs: 8000, TalkMs: 5489, FramesFromTelnyx: 211, FramesWithSpeech: 99,
			OpenerReason: "speech_settled", OpenerAfterMs: 1519, AMDAfterMs: 4268},
		{Type: "call", Seq: 7, Business: "MAA ANIMATION", Disposition: "voicemail", AMD: "machine",
			AnsweredMs: 8000, TalkMs: 6301, FramesFromTelnyx: 249, FramesWithSpeech: 57,
			OpenerReason: "speech_settled", OpenerAfterMs: 1661, AMDAfterMs: 5123},
		{Type: "call", Seq: 8, Business: "MBphotography", Disposition: "no_answer",
			AnsweredMs: -1, TalkMs: -1, OpenerAfterMs: -1, AMDAfterMs: -1, FramesFromTelnyx: 0},
		{Type: "call", Seq: 9, Business: "The Milan Studio", Disposition: "incomplete", AMD: "human_residence",
			AnsweredMs: 9000, TalkMs: 29599, FramesFromTelnyx: 1461, FramesWithSpeech: 159,
			OpenerReason: "timeout", OpenerAfterMs: 3004, AMDAfterMs: 3031,
			BargeIns: 0, BackChannels: 11, ReplyLatenciesMs: []int64{155}, Turns: 6},
	}
}

func writeRun(t *testing.T, calls []CallEvent) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "run.jsonl")
	rl, err := OpenRunLog(path, "live1")
	if err != nil {
		t.Fatalf("open run log: %v", err)
	}
	rl.Manifest(Config{ProspectsFile: "test_wedding.csv", FromNumber: "+19103172041",
		OpenAIRealtimeModel: "gpt-realtime-2.1-mini"}, 85, 6, nil)
	for _, c := range calls {
		rl.Call(c)
	}
	rl.Close()
	return path
}

func TestReportReproducesTheFirstCampaignsFindings(t *testing.T) {
	path := writeRun(t, liveOneEvents())
	rep, err := readRunFile(path)
	if err != nil {
		t.Fatalf("read run file: %v", err)
	}
	if len(rep.calls) != 9 {
		t.Fatalf("read %d calls, want 9", len(rep.calls))
	}
	out := rep.Render(true)

	// The finding that cost the most: the agent spoke to all three recordings.
	// This is the single number the opener change exists to move, so the report
	// has to state it in as many words rather than leave it to be inferred.
	if !strings.Contains(out, "SPOKE TO A RECORDING on 3 call(s)") {
		t.Errorf("report does not surface that three machines were pitched:\n%s", out)
	}
	// Three dials never connected at all. Calling those "hung up" is what made
	// every answer-rate number derived from that run wrong.
	if !strings.Contains(out, "never connected             3") {
		t.Errorf("report does not separate calls that never connected:\n%s", out)
	}
	if !strings.Contains(out, "a person answered           3") {
		t.Errorf("report miscounts the calls a person actually answered:\n%s", out)
	}
	// Nine fillers on one call is what made the agent sound like a tic.
	if !strings.Contains(out, "sound like a tic") {
		t.Errorf("report does not flag the filler density:\n%s", out)
	}
	// Eleven back-channels withheld and not one interruption allowed is the
	// six-second monologue, seen from the gate's side.
	if !strings.Contains(out, "nothing got through the gate") {
		t.Errorf("report does not flag that the prospect could never interrupt:\n%s", out)
	}
}

// A run killed mid-write must still be readable. The campaign is a long-running
// process that gets stopped by signals, operators and the box itself, and the
// tail of the file is exactly the part worth reading afterwards.
func TestReportSurvivesATruncatedFile(t *testing.T) {
	path := writeRun(t, liveOneEvents())
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	// Chop the last record in half, the way a kill mid-write would.
	if err := os.WriteFile(path, raw[:len(raw)-40], 0o644); err != nil {
		t.Fatal(err)
	}
	rep, err := readRunFile(path)
	if err != nil {
		t.Fatalf("a truncated run file must still read: %v", err)
	}
	if len(rep.calls) != 8 {
		t.Fatalf("recovered %d calls from a truncated file, want the 8 complete ones", len(rep.calls))
	}
	_ = rep.Render(true) // must not panic on a partial run
}

