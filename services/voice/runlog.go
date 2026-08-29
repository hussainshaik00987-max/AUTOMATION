package main

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"runtime/debug"
	"sort"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------------------
// Run log — the record of what actually happened, written as it happens.
//
// results.csv answers "what was the outcome". It does not answer the question
// that actually comes up after a campaign, which is "why". Reconstructing the
// first live run meant reading a 53,000-byte text log, correlating it by
// timestamp against a CSV whose disposition column was itself wrong, and pulling
// six recordings off the box to measure the audio by hand. Every finding in that
// teardown was recoverable, but none of it was READABLE.
//
// So each call also emits one JSON object carrying everything that call knew
// about itself: when it was answered, which rule released the opener and how
// long after pickup, what answering-machine detection said and how late, how
// much of the prospect's audio reached the model, every reply latency, the whole
// transcript, and what was promised and delivered. The file is append-only JSONL
// so a crash mid-campaign loses nothing, and `-report` turns it back into a
// funnel and a set of distributions without anyone opening a log.
//
// The manifest at the top of the file matters as much as the calls: it records
// the exact tuning every one of those calls ran under. A number that was right
// last week and wrong today is invisible unless the file says which number was
// in force.
// ---------------------------------------------------------------------------

// RunLog appends newline-delimited JSON records for one campaign run.
type RunLog struct {
	mu   sync.Mutex
	f    *os.File
	tag  string
	path string
}

// runLog is the process-wide run log. Nil outside a campaign (the -probe,
// -bench and -local harnesses do not open one), and every call site tolerates
// that — a missing run log must never be able to interrupt a live call.
var runLog *RunLog

// OpenRunLog creates (or appends to) the run's event file.
func OpenRunLog(path, tag string) (*RunLog, error) {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, err
	}
	return &RunLog{f: f, tag: tag, path: path}, nil
}

func (r *RunLog) write(kind string, v any) {
	if r == nil {
		return
	}
	b, err := json.Marshal(v)
	if err != nil {
		slog.Warn("run log: could not encode record", "kind", kind, "error", err)
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, err := r.f.Write(append(b, '\n')); err != nil {
		slog.Warn("run log: write failed", "kind", kind, "error", err)
		return
	}
	// Flushed per record on purpose. A campaign is a long-running process that
	// can be killed at any point — by a signal, by the operator, by the box — and
	// a buffered tail is exactly the part of the run you most want to read
	// afterwards.
	_ = r.f.Sync()
}

func (r *RunLog) Close() {
	if r == nil {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	_ = r.f.Close()
}

// ---------------------------------------------------------------------------
// Manifest — the settings every call in this run ran under
// ---------------------------------------------------------------------------

type RunManifest struct {
	Type       string `json:"type"`
	Run        string `json:"run"`
	StartedAt  string `json:"started_at"`
	StartedIST string `json:"started_ist"`
	GoVersion  string `json:"go_version"`
	Revision   string `json:"vcs_revision,omitempty"`
	Built      string `json:"vcs_time,omitempty"`

	LeadsFile  string `json:"leads_file"`
	LeadCount  int    `json:"lead_count"`
	ResultsCSV string `json:"results_csv"`

	FromNumber  string `json:"from_number"`
	BrainModel  string `json:"brain_model"`
	VoiceModel  string `json:"voice_model"`
	VoiceID     string `json:"voice_id"`
	Reasoning   string `json:"reasoning_effort"`
	VAD         string `json:"vad_mode"`
	MaxAnswered int    `json:"max_answered"`

	// Every tunable that changes how a call sounds or is judged. When a run reads
	// differently from the last one, this is the first place to look.
	Tuning map[string]any `json:"tuning"`

	// Preflight facts that decide whether the run's promises can be kept at all.
	Preflight map[string]any `json:"preflight,omitempty"`
}

func (r *RunLog) Manifest(cfg Config, leads int, maxAnswered int, preflight map[string]any) {
	if r == nil {
		return
	}
	ist := time.FixedZone("IST", 5*3600+1800)
	_ = ist
	m := RunManifest{
		Type:       "manifest",
		Run:        r.tag,
		StartedAt:  time.Now().UTC().Format(time.RFC3339),
		StartedIST: time.Now().In(ist).Format("Mon 02 Jan 2006 15:04 MST"),
		LeadsFile:  cfg.ProspectsFile,
		LeadCount:  leads,
		ResultsCSV: cfg.ResultsFile,

		FromNumber:  cfg.FromNumber,
		BrainModel:  cfg.OpenAIRealtimeModel,
		VoiceModel:  cfg.ElevenModel,
		VoiceID:     cfg.ElevenVoiceID,
		Reasoning:   cfg.ReasoningEffort,
		VAD:         cfg.VADMode,
		MaxAnswered: maxAnswered,

		Tuning: map[string]any{
			"opener_style":               openerStyle,
			"opener_hard_timeout_ms":     openerHardTimeout.Milliseconds(),
			"opener_min_listen_ms":       openerMinListen.Milliseconds(),
			"opener_settle_quiet_ms":     openerSettleQuiet.Milliseconds(),
			"opener_min_utterance_ms":    openerMinUtterance.Milliseconds(),
			"opener_utterance_gap_ms":    preOpenerUtteranceGap.Milliseconds(),
			"opener_hold_quick_ms":       openerHoldQuick.Milliseconds(),
			"opener_hold_evidence_ms":    openerHoldEvidence.Milliseconds(),
			"speech_energy_threshold":    speechEnergyThreshold,
			"barge_in_threshold":         bargeInThreshold,
			"barge_in_min_frames":        bargeInMinFrames,
			"barge_gap_tolerance_frames": bargeGapToleranceFrames,
			"vad_silence_ms":             vadSilenceDurationMs,
			"thinking_filler_after_ms":   thinkingFillerAfterMs,
			"thinking_max_fills_turn":    thinkingMaxFillsPerTurn,
			"stream_before_dial":         cfg.StreamBeforeDial,
			"anchorsite":                 cfg.AnchorSite,
			"ring_timeout":               cfg.RingTimeout.String(),
			"call_timeout":               cfg.CallTimeout.String(),
			"delay_between":              cfg.DelayBetween.String(),
		},
		Preflight: preflight,
	}
	if bi, ok := debug.ReadBuildInfo(); ok {
		m.GoVersion = bi.GoVersion
		for _, s := range bi.Settings {
			switch s.Key {
			case "vcs.revision":
				m.Revision = s.Value
			case "vcs.time":
				m.Built = s.Value
			}
		}
	}
	r.write("manifest", m)
}

// ---------------------------------------------------------------------------
// One call
// ---------------------------------------------------------------------------

// CallEvent is everything one call knew about itself. Field names are stable —
// `-report` and anything else reading these files depends on them.
type CallEvent struct {
	Type string `json:"type"`
	Run  string `json:"run"`
	Seq  int    `json:"seq"`

	At       string `json:"at"`
	CallID   string `json:"call_id"`
	Phone    string `json:"phone"`
	Business string `json:"business"`
	City     string `json:"city"`
	Vertical string `json:"vertical"`
	Rating   string `json:"rating"`
	Reviews  string `json:"reviews"`
	Landline bool   `json:"landline"`

	// Timings. answered_ms is measured from the dial; everything else from the
	// answer, because that is the instant the prospect starts judging the call.
	AnsweredMs int64 `json:"answered_ms"` // -1 = never answered
	DurationMs int64 `json:"duration_ms"` // dial → session close
	TalkMs     int64 `json:"talk_ms"`     // answer → session close, -1 if never answered

	// The opening. "why did it speak when it did" is the single most useful thing
	// on this record: an opener released on `timeout` for a call AMD later called
	// a machine is the agent pitching a recording, and nothing in results.csv
	// shows it.
	OpenerReason   string `json:"opener_reason"`
	OpenerAfterMs  int64  `json:"opener_after_ms"` // from answer, -1 = never opened
	OpenerStyle    string `json:"opener_style"`
	OpenerLine     string `json:"opener_line"`
	OpenerPersonal bool   `json:"opener_personalised"`
	OpenerAudioMs  int64  `json:"opener_audio_ms"`
	// How much the prospect said WHILE the pre-recorded opener was still playing.
	// The opener is exempt from barge-in and plays to the end, so this is the
	// measure of how much it talks over people — the number that decides whether
	// a longer opening line is affordable.
	SpokeDuringOpenerMs int64 `json:"spoke_during_opener_ms"`

	// Answering-machine detection, and how late it was. The opener's hold
	// deadlines are set from this distribution, so it must be re-measured every
	// run rather than trusted from a constant.
	AMD        string `json:"amd"`
	AMDAfterMs int64  `json:"amd_after_ms"` // from answer, -1 = no verdict

	// Why the carrier says the call ended. On a dial that never connected this is
	// the only thing that separates "they were busy" from "the network refused
	// the call", and those are not the same problem.
	HangupCause  string `json:"hangup_cause"`
	HangupSource string `json:"hangup_source"`

	FramesFromTelnyx  int64    `json:"frames_from_telnyx"`
	FramesWithSpeech  int64    `json:"frames_with_speech"`
	FramesToModel     int64    `json:"frames_to_model"`
	SpeechEatenByGate int64    `json:"speech_frames_eaten_by_gate"`
	EnergyHist        [8]int64 `json:"energy_hist"`

	BargeIns            int64 `json:"barge_ins"`
	BackChannels        int64 `json:"back_channels"`
	ThinkingFills       int64 `json:"thinking_fillers"`
	ResumePrompts       int64 `json:"resume_prompts"`
	PreOpenerUtterances int64 `json:"pre_opener_utterances"`

	ReplyLatenciesMs []int64  `json:"reply_latencies_ms"`
	Turns            int      `json:"turns"`
	Transcript       []string `json:"transcript"`

	Disposition  string `json:"disposition"`
	HangupBy     string `json:"hangup_by"`
	Refused      bool   `json:"refused"`
	OwnerName    string `json:"owner_name"`
	Language     string `json:"language"`
	LinkChannel  string `json:"link_channel"`
	LinkStatus   string `json:"link_status"`
	DemoURL      string `json:"demo_url"`
	FollowupKind string `json:"followup_kind"`
	FollowupWhen string `json:"followup_when"`
}

func (r *RunLog) Call(ev CallEvent) {
	if r == nil {
		return
	}
	ev.Type = "call"
	ev.Run = r.tag
	r.write("call", ev)
}

// WhatsAppSample is one reading of the sidecar's own counters, taken at the
// start of the run, periodically through it, and at the end.
//
// This account is the fragile part of the whole system: an unofficial client
// messaging strangers, linked to a real personal number, one ban away from
// costing every remaining lead on the list. Sampling it into the same file as
// the calls is what makes "the unreachable count rose during this run" a thing
// you can see rather than a thing you would have to already suspect.
type WhatsAppSample struct {
	Type string `json:"type"`
	Run  string `json:"run"`
	At   string `json:"at"`
	When string `json:"when"` // "start" | "lead-25" | "end"

	Reachable   bool   `json:"reachable"`
	Connected   bool   `json:"connected"`
	State       string `json:"state"`
	Detail      string `json:"detail,omitempty"`
	Total       int    `json:"total"`
	Queued      int    `json:"queued"`
	Pinged      int    `json:"pinged"`
	Sent        int    `json:"sent"`
	Declined    int    `json:"declined"`
	Unreachable int    `json:"unreachable"`
	Failed      int    `json:"failed"`
	SentToday   int    `json:"sent_today"`
	DailyCap    int    `json:"daily_cap"`
}

func (r *RunLog) WhatsApp(s WhatsAppSample) {
	if r == nil {
		return
	}
	s.Type = "whatsapp"
	s.Run = r.tag
	s.At = time.Now().UTC().Format(time.RFC3339)
	r.write("whatsapp", s)
}

// ---------------------------------------------------------------------------
// End-of-run summary
// ---------------------------------------------------------------------------

// Summarise folds the run's own event file back into the funnel and the
// distributions, logs it, and appends it as the last record. Printing it at the
// end of the campaign means the answer to "how did that go?" is on the screen
// the operator is already looking at, not in a file they have to know to open.
func (r *RunLog) Summarise() string {
	if r == nil {
		return ""
	}
	r.mu.Lock()
	path := r.path
	r.mu.Unlock()

	rep, err := readRunFile(path)
	if err != nil {
		slog.Warn("could not summarise the run", "file", path, "error", err)
		return ""
	}
	text := rep.Render(false)
	r.write("summary", map[string]any{
		"type": "summary", "run": r.tag,
		"at":   time.Now().UTC().Format(time.RFC3339),
		"text": text,
	})
	return text
}

// percentile returns the p-th percentile of an already-sortable slice. Nearest
// rank, which is the right definition for a handful of samples — interpolating
// between two of five measurements invents precision that isn't there.
func percentile(v []int64, p float64) int64 {
	if len(v) == 0 {
		return 0
	}
	s := append([]int64(nil), v...)
	sort.Slice(s, func(i, j int) bool { return s[i] < s[j] })
	i := int(float64(len(s)-1) * p)
	return s[i]
}

// histogram renders a small labelled distribution as a text bar chart.
func histogram(counts map[string]int, order []string, width int) string {
	max := 0
	for _, k := range order {
		if counts[k] > max {
			max = counts[k]
		}
	}
	var b strings.Builder
	for _, k := range order {
		n := counts[k]
		bar := 0
		if max > 0 {
			bar = n * width / max
		}
		fmt.Fprintf(&b, "    %-22s %3d  %s\n", k, n, strings.Repeat("█", bar))
	}
	return b.String()
}
