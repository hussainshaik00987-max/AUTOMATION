package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// Latency bench — measuring reply latency without phoning anyone.
//
// Per-turn dead air is what makes these calls feel broken, and it cannot be
// tuned from live calls: every dial costs money, wakes a stranger, and gives one
// noisy sample. So this replays a REAL prospect utterance — pulled from the
// prospect channel of an actual recording — through the REAL live session, with
// the real system instruction, tools and audio path, at REALTIME pace.
//
// Pacing matters more than it looks. Gemini's VAD decides the prospect has
// stopped talking after silenceDurationMs of quiet; blasting the utterance as
// fast as the socket allows would compress that window and report a latency no
// live call could reproduce. Frames go out one per 20ms, exactly as Telnyx
// delivers them, and silence keeps flowing afterwards — a real phone line never
// goes quiet, it sends quiet frames.
//
// What it reports is the number that matters to the person on the phone: from
// the last frame of their speech to the first frame of the agent's audio.
//
//	go run . -bench
//	go run . -bench -bench-trials 8 -bench-silence 100,200,300
// ---------------------------------------------------------------------------

type benchOpts struct {
	trials    int
	turns     int    // measured turns per session; turn 1 is the one the prospect judges
	utterance string // path to raw PCM16 mono 8kHz — the prospect's turn
	silences  []int  // silenceDurationMs values to sweep; empty = whatever config says
}

type benchResult struct {
	label    string
	setupMs  []float64
	ttftMs   []float64   // every measured turn
	byTurn   [][]float64 // ...split by turn index, so turn 1 can be read on its own
	failures int
}

func runBench(cfg Config, o benchOpts) {
	if o.trials <= 0 {
		o.trials = 5
	}
	pcm, err := os.ReadFile(o.utterance)
	if err != nil {
		fmt.Printf("cannot read utterance %q: %v\n", o.utterance, err)
		fmt.Println("expected raw PCM16 mono 8kHz — extract one from a call recording's prospect channel")
		return
	}
	// Telnyx hands us μ-law; the production path is μ-law → PCM16@16k. Convert to
	// μ-law first so the bench runs through the exact same codec chain as a call,
	// quantisation loss and all.
	mulaw := LinearToMulaw(pcm, 8000)
	frames := len(mulaw) / telnyxFrameBytes

	fmt.Println("──────────────────────────────────────────────────────────────")
	fmt.Println(" REPLY LATENCY BENCH — real audio, real session, no phone call")
	fmt.Printf(" utterance : %s (%d frames, %.2fs)\n", o.utterance, frames, float64(frames)*0.02)
	fmt.Printf(" brain     : %s   reasoning=%s   vad=%s\n", cfg.OpenAIRealtimeModel, cfg.ReasoningEffort, cfg.VADMode)
	fmt.Printf(" voice     : %s   %s\n", cfg.ElevenModel, cfg.ElevenVoiceID)
	fmt.Printf(" trials    : %d per setting\n", o.trials)
	fmt.Println("──────────────────────────────────────────────────────────────")

	sweep := o.silences
	if len(sweep) == 0 {
		sweep = []int{vadSilenceDurationMs}
	}

	var results []benchResult
	for _, sil := range sweep {
		orig := vadSilenceDurationMs
		vadSilenceDurationMs = sil
		r := benchResult{label: fmt.Sprintf("silenceDurationMs=%d", sil)}
		r.byTurn = make([][]float64, o.turns)
		for i := 0; i < o.trials; i++ {
			setup, turns, err := benchOnce(cfg, mulaw, o.turns)
			if err != nil {
				r.failures++
				fmt.Printf("  trial %d: %v\n", i+1, err)
				// A trial that got two turns and failed on the third still measured
				// two turns. Discarding them is how a bench comes back with nothing
				// at all and tells you less than the failure did.
				if len(turns) == 0 {
					continue
				}
			}
			r.setupMs = append(r.setupMs, setup)
			parts := make([]string, 0, len(turns))
			for ti, ms := range turns {
				r.ttftMs = append(r.ttftMs, ms)
				r.byTurn[ti] = append(r.byTurn[ti], ms)
				parts = append(parts, fmt.Sprintf("turn%d %6.0fms", ti+1, ms))
			}
			fmt.Printf("  [%s] trial %d: setup %6.0fms   %s\n", r.label, i+1, setup, strings.Join(parts, "   "))
			time.Sleep(1200 * time.Millisecond) // don't hammer the API
		}
		vadSilenceDurationMs = orig
		results = append(results, r)
	}

	fmt.Println("\n─── summary ─────────────────────────────────────────────────")
	fmt.Printf("%-26s %8s %8s %8s %8s %6s\n", "setting", "min", "median", "p90", "max", "fails")
	for _, r := range results {
		if len(r.ttftMs) == 0 {
			fmt.Printf("%-26s %8s %8s %8s %8s %6d\n", r.label, "-", "-", "-", "-", r.failures)
			continue
		}
		fmt.Printf("%-26s %7.0fms %7.0fms %7.0fms %7.0fms %6d\n",
			r.label, pct(r.ttftMs, 0), pct(r.ttftMs, 50), pct(r.ttftMs, 90), pct(r.ttftMs, 100), r.failures)
	}
	if o.turns > 1 {
		fmt.Println("\n─── by turn (is the FIRST reply slower?) ────────────────────")
		fmt.Printf("%-26s", "setting")
		for t := 0; t < o.turns; t++ {
			fmt.Printf(" %10s", fmt.Sprintf("turn %d", t+1))
		}
		fmt.Println()
		for _, r := range results {
			fmt.Printf("%-26s", r.label)
			for t := 0; t < o.turns; t++ {
				if len(r.byTurn[t]) == 0 {
					fmt.Printf(" %10s", "-")
					continue
				}
				fmt.Printf(" %9.0fms", pct(r.byTurn[t], 50))
			}
			fmt.Println()
		}
		fmt.Println("A turn-1 median well above turn 2 means the cost is first-inference")
		fmt.Println("prompt processing, which no VAD setting can fix.")
	}
	if len(results) > 0 && len(results[0].setupMs) > 0 {
		fmt.Printf("\nsession setup (connect→setupComplete): median %.0fms\n", pct(results[0].setupMs, 50))
	}
	fmt.Println("\nReply latency is measured from the LAST frame of the prospect's speech to")
	fmt.Println("the FIRST frame of SYNTHESISED audio — the silence they actually sit")
	fmt.Println("through, model and ElevenLabs together. A model-only number would be")
	fmt.Println("smaller and nobody would ever experience it.")
}

// benchOnce runs `turns` measured turns against one fresh session, replaying the
// same prospect utterance each time. Turn 1 is the one that decides whether the
// prospect stays on the call.
func benchOnce(cfg Config, mulaw []byte, turns int) (setupMs float64, ttfts []float64, err error) {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()

	// The same instruction a live call gets, so the model is in the same state:
	// opener already spoken, waiting on the prospect's answer.
	p := parseSimProspect("")
	t0 := time.Now()
	g, err := ConnectBrain(ctx, cfg, buildCallInstruction(cfg, p, true, true))
	if err != nil {
		return 0, nil, fmt.Errorf("connect: %w", err)
	}
	defer g.Close()
	setupMs = float64(time.Since(t0).Microseconds()) / 1000

	// The voice leg is part of what is being measured. The number that matters is
	// the silence the CALLER sits through, and on this stack that silence ends
	// when ElevenLabs returns its first chunk — not when the model returns its
	// first token. Measuring the model alone would report a latency nobody
	// experiences and would hide the entire synthesis leg.
	v, err := ConnectVoice(ctx, cfg)
	if err != nil {
		return setupMs, nil, fmt.Errorf("voice connect: %w", err)
	}
	defer v.Close()

	turnAudio := make(chan time.Time, 8)
	turnDone := make(chan struct{}, 8)
	readErr := make(chan error, 1)

	// brain reader: text deltas straight into the synthesiser, exactly as the
	// live bridge does it — same aggregator, same first-clause flush.
	go func() {
		agg := newClauseAggregator()
		first := true
		for {
			ev, err := g.ReadEvent()
			if err != nil {
				readErr <- err
				return
			}
			switch ev.Kind {
			case BrainTextDelta:
				if v.Current() == "" {
					v.Begin()
					agg.reset()
					first = true
				}
				if clause := agg.push(ev.Text); clause != "" {
					_ = v.Say(clause, first)
					first = false
				}
			case BrainTurnComplete:
				if clause := agg.drain(); clause != "" {
					_ = v.Say(clause, first)
				}
				_ = v.Finish()
				agg.reset()
				first = true
				select {
				case turnDone <- struct{}{}:
				default:
				}
			}
		}
	}()

	// voice reader: the first chunk of each turn is the moment the caller would
	// hear something.
	//
	// Keyed on the CONTEXT, not on a "have we started" flag. Each agent turn gets
	// its own ElevenLabs context, and a flag cleared by the final marker misses
	// turn two entirely when the marker arrives attached to audio rather than on
	// its own — which is exactly what it did.
	go func() {
		heard := ""
		for chunk := range v.Chunks() {
			if len(chunk.Mulaw) == 0 {
				continue
			}
			if chunk.ContextID != heard {
				heard = chunk.ContextID
				turnAudio <- time.Now()
			}
		}
	}()

	silence := make([]byte, telnyxFrameBytes)
	for i := range silence {
		silence[i] = mulawSilence
	}

	// Raw μ-law, exactly as a Telnyx frame arrives — the Realtime API takes
	// audio/pcmu, so there is nothing to convert.
	send := func(frame []byte) error {
		return g.SendAudio(base64.StdEncoding.EncodeToString(frame))
	}

	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()

	quiet := func(n int) error {
		for i := 0; i < n; i++ {
			<-ticker.C
			if err := send(silence); err != nil {
				return err
			}
		}
		return nil
	}

	// A real line is never silent — it sends quiet frames. Give the session a
	// moment of that before speaking, so VAD sees a normal floor rather than a
	// stream that begins mid-word.
	if err := quiet(25); err != nil {
		return setupMs, nil, fmt.Errorf("send silence: %w", err)
	}

	nFrames := len(mulaw) / telnyxFrameBytes
	for turn := 0; turn < turns; turn++ {
		// The prospect's turn, one frame per 20ms — pacing is the whole point.
		for i := 0; i < nFrames; i++ {
			<-ticker.C
			if err := send(mulaw[i*telnyxFrameBytes : (i+1)*telnyxFrameBytes]); err != nil {
				return setupMs, ttfts, fmt.Errorf("send speech: %w", err)
			}
		}
		lastSpeech := time.Now()

		// Hold the line open until the agent starts talking.
		deadline := time.After(20 * time.Second)
		got := false
		for !got {
			select {
			case t := <-turnAudio:
				ttfts = append(ttfts, float64(t.Sub(lastSpeech).Microseconds())/1000)
				got = true
			case e := <-readErr:
				return setupMs, ttfts, fmt.Errorf("brain closed: %w", e)
			case <-deadline:
				return setupMs, ttfts, fmt.Errorf("no agent audio within 20s on turn %d", turn+1)
			case <-ticker.C:
				if err := send(silence); err != nil {
					return setupMs, ttfts, fmt.Errorf("send trailing silence: %w", err)
				}
			}
		}

		if turn == turns-1 {
			break
		}
		// Let the agent finish before speaking again, otherwise the next turn is
		// measuring an interruption rather than a reply.
		waitDone := time.After(15 * time.Second)
		done := false
		for !done {
			select {
			case <-turnDone:
				done = true
			case e := <-readErr:
				return setupMs, ttfts, fmt.Errorf("brain closed: %w", e)
			case <-waitDone:
				done = true // it kept talking; measure the next turn anyway
			case <-ticker.C:
				if err := send(silence); err != nil {
					return setupMs, ttfts, fmt.Errorf("send gap silence: %w", err)
				}
			}
		}
		if err := quiet(20); err != nil { // a natural beat before replying
			return setupMs, ttfts, fmt.Errorf("send beat: %w", err)
		}
	}
	return setupMs, ttfts, nil
}

// pct returns the p-th percentile (0 = min, 100 = max) of a sample.
func pct(xs []float64, p int) float64 {
	s := append([]float64(nil), xs...)
	sort.Float64s(s)
	if len(s) == 0 {
		return 0
	}
	i := p * (len(s) - 1) / 100
	return s[i]
}

// parseBenchSilences turns "100,200,300" into the values to sweep.
func parseBenchSilences(s string) []int {
	var out []int
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if n, err := strconv.Atoi(part); err == nil && n >= 0 {
			out = append(out, n)
		}
	}
	return out
}

// ---------------------------------------------------------------------------
// TTS bench — how fast does the voice start?
// ---------------------------------------------------------------------------

// runTTSBench times the two ElevenLabs paths from wherever this runs.
//
// They answer different questions. The one-shot POST prepares openers and
// fillers ahead of the dial, so it is never on the critical path and only has to
// keep up with prefetch. The streaming socket IS the critical path: its
// time-to-first-chunk is a term in every single reply the prospect waits through.
func runTTSBench(cfg Config, trials int) {
	if trials <= 0 {
		trials = 3
	}
	line := "Yeah so I actually built you a demo site already, it's free either way."
	fmt.Println("──────────────────────────────────────────────────────────────")
	fmt.Printf(" ELEVENLABS BENCH — %d trials · %s · voice %s\n", trials, cfg.ElevenModel, cfg.ElevenVoiceID)
	fmt.Println("──────────────────────────────────────────────────────────────")

	fmt.Println("\n  one-shot POST (openers, fillers — off the critical path)")
	var oneShot []float64
	for i := 0; i < trials; i++ {
		ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
		t0 := time.Now()
		audio, err := elevenSynth(ctx, cfg, line)
		cancel()
		ms := float64(time.Since(t0).Microseconds()) / 1000
		if err != nil {
			fmt.Printf("    trial %d: FAILED %v\n", i+1, err)
			continue
		}
		oneShot = append(oneShot, ms)
		fmt.Printf("    trial %d: %6.0fms  (%d bytes = %.2fs of audio)\n",
			i+1, ms, len(audio), float64(len(audio))*0.000125)
	}

	fmt.Println("\n  streaming socket, time to FIRST chunk (this one is on the critical path)")
	var ttfb []float64
	for i := 0; i < trials; i++ {
		ms, err := benchVoiceFirstChunk(cfg, line)
		if err != nil {
			fmt.Printf("    trial %d: FAILED %v\n", i+1, err)
			continue
		}
		ttfb = append(ttfb, ms)
		fmt.Printf("    trial %d: %6.0fms\n", i+1, ms)
	}

	fmt.Println("\n─── summary ─────────────────────────────────────────────────")
	if len(oneShot) > 0 {
		fmt.Printf("  one-shot  : median %6.0fms   min %6.0fms\n", pct(oneShot, 50), pct(oneShot, 0))
	}
	if len(ttfb) > 0 {
		fmt.Printf("  first chunk: median %6.0fms  min %6.0fms\n", pct(ttfb, 50), pct(ttfb, 0))
		fmt.Println("\n  This is the part of every reply that belongs to the voice. If it is far")
		fmt.Println("  above the ~75-150ms Flash is capable of, the distance between this box")
		fmt.Println("  and ElevenLabs is the cause, not the model.")
	}
}

// benchVoiceFirstChunk opens a real socket, says one line, and reports how long
// until the first audio comes back — the connection handshake excluded, since on
// a live call it happens while the phone is ringing.
func benchVoiceFirstChunk(cfg Config, line string) (float64, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	v, err := ConnectVoice(ctx, cfg)
	if err != nil {
		return 0, err
	}
	defer v.Close()

	v.Begin()
	t0 := time.Now()
	if err := v.Say(line, true); err != nil {
		return 0, err
	}
	select {
	case chunk, ok := <-v.Chunks():
		if !ok {
			return 0, fmt.Errorf("socket closed with no audio")
		}
		_ = chunk
		return float64(time.Since(t0).Microseconds()) / 1000, nil
	case <-ctx.Done():
		return 0, fmt.Errorf("no audio within 30s")
	}
}

// mulawSilence is the μ-law encoding of PCM zero — what a quiet phone line sends.
var mulawSilence = func() byte {
	var z [2]byte
	binary.LittleEndian.PutUint16(z[:], 0)
	out := LinearToMulaw(z[:], 8000)
	if len(out) == 0 {
		return 0xFF
	}
	return out[0]
}()

// openaiTTSProbe times one synthesis through OpenAI's speech endpoint and
// returns the audio size. PCM is requested rather than MP3 so nothing is spent
// on container encoding — the fairest comparison against Gemini, which also
// returns raw samples.
func openaiTTSProbe(ctx context.Context, key string) (int, error) {
	model := strings.TrimSpace(os.Getenv("OPENAI_TTS_MODEL"))
	if model == "" {
		model = "gpt-4o-mini-tts"
	}
	voice := strings.TrimSpace(os.Getenv("OPENAI_TTS_VOICE"))
	if voice == "" {
		voice = "ash"
	}
	body, _ := json.Marshal(map[string]any{
		"model":           model,
		"voice":           voice,
		"input":           "Uh...",
		"response_format": "pcm",
	})
	req, err := http.NewRequestWithContext(ctx, "POST", "https://api.openai.com/v1/audio/speech", bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	audio, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, err
	}
	if resp.StatusCode >= 300 {
		return 0, fmt.Errorf("%d %s", resp.StatusCode, strings.TrimSpace(string(audio))[:min(200, len(audio))])
	}
	return len(audio), nil
}
