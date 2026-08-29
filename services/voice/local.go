package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------------------
// Local voice mode (-local): talk to the agent through your laptop's mic and
// speaker, with the real brain and the real ElevenLabs voice — same system
// instruction and tools as a live phone call. The PSTN/Telnyx leg is replaced by
// ffmpeg (mic capture) and ffplay (speaker playback); everything above it is
// identical to a dial.
//
// The audio path is deliberately the SAME μ-law 8kHz the phone network carries,
// not something nicer. A harness that rehearses on 24kHz studio audio tells you
// how the agent sounds in a room, which is not the question — the question is
// how it sounds down a telephone, and 8kHz μ-law is what does that to it.
//
// Audio rates:
//   mic   → 16 kHz PCM16 → μ-law 8 kHz → brain
//   voice → μ-law 8 kHz  → PCM16 16 kHz → speaker
// ---------------------------------------------------------------------------

const (
	localMicRate     = 16000
	localSpeakerRate = 16000
	localFrameMs     = 20
	localMicFrame    = localMicRate / 1000 * localFrameMs * 2 // bytes per 20ms mic frame (640)
)

func runLocal(cfg Config, prospectArg, micOverride string) {
	p := parseSimProspect(prospectArg)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	mic := micOverride
	if mic == "" {
		m, err := detectMic()
		if err != nil {
			fmt.Printf("could not auto-detect a microphone: %v\n", err)
			fmt.Println("List devices with:  ffmpeg -list_devices true -f dshow -i dummy")
			fmt.Println("Then pass it with:  go run . -local -mic \"<device name>\"")
			return
		}
		mic = m
	}

	fmt.Println("──────────────────────────────────────────────────────────────")
	fmt.Println(" Outbound agent — LOCAL VOICE mode (mic + speaker, no phone call)")
	fmt.Println("──────────────────────────────────────────────────────────────")
	fmt.Printf(" Prospect : %s — %s\n", orDash(p.Name), orDash(p.City))
	fmt.Printf(" Mic      : %s\n", mic)
	fmt.Println(" Just talk — the agent will call you. Press Ctrl+C to hang up.")
	fmt.Println("──────────────────────────────────────────────────────────────")

	// --- Speaker (ffplay reads raw PCM from stdin) ---
	spk, err := newSpeaker(localSpeakerRate)
	if err != nil {
		fmt.Printf("could not start speaker (ffplay): %v\n", err)
		return
	}
	defer spk.Close()

	// --- Microphone (ffmpeg dshow → raw PCM on stdout) ---
	micCmd := exec.CommandContext(ctx, "ffmpeg",
		"-hide_banner", "-loglevel", "error",
		"-f", "dshow", "-i", "audio="+mic,
		"-ar", strconv.Itoa(localMicRate), "-ac", "1", "-f", "s16le", "-")
	micOut, err := micCmd.StdoutPipe()
	if err != nil {
		fmt.Printf("mic pipe: %v\n", err)
		return
	}
	var micErr bytes.Buffer
	micCmd.Stderr = &micErr
	if err := micCmd.Start(); err != nil {
		fmt.Printf("could not start microphone (ffmpeg): %v\n", err)
		return
	}

	// --- Brain + voice: the same pair a real dial opens ---
	brain, err := ConnectBrain(ctx, cfg, buildCallInstruction(cfg, p, false, false))
	if err != nil {
		fmt.Printf("could not connect the brain: %v\n", err)
		return
	}
	defer brain.Close()

	voice, err := ConnectVoice(ctx, cfg)
	if err != nil {
		fmt.Printf("could not connect ElevenLabs: %v\n", err)
		return
	}
	defer voice.Close()

	d := &localDriver{cfg: cfg, brain: brain, voice: voice, spk: spk, ctx: ctx, cancel: cancel}

	if err := brain.SendText(buildKickoff(p)); err != nil {
		fmt.Printf("kickoff failed: %v\n", err)
		return
	}

	var wg sync.WaitGroup
	wg.Add(3)
	go func() { defer wg.Done(); d.captureLoop(micOut) }()
	go func() { defer wg.Done(); d.brainLoop() }()
	go func() { defer wg.Done(); d.voiceLoop() }()

	wg.Wait()

	if ctx.Err() != nil {
		// expected on hangup
	}
	if micErr.Len() > 0 {
		fmt.Printf("\n[ffmpeg mic: %s]\n", strings.TrimSpace(micErr.String()))
	}
	fmt.Println("\n[call ended]")
}

// localDriver carries the shared state of one local-voice "call".
type localDriver struct {
	cfg    Config
	brain  *BrainConn
	voice  *VoiceStream
	spk    *speaker
	ctx    context.Context
	cancel context.CancelFunc
}

// captureLoop reads 20ms mic frames, encodes them to the μ-law the phone
// network uses, and forwards them to the brain — the same bytes a Telnyx frame
// would carry.
func (d *localDriver) captureLoop(micOut io.Reader) {
	buf := make([]byte, localMicFrame)
	for {
		if d.ctx.Err() != nil {
			return
		}
		if _, err := io.ReadFull(micOut, buf); err != nil {
			d.cancel()
			return
		}

		mulaw := LinearToMulaw(buf, localMicRate)
		if err := d.brain.SendAudio(base64.StdEncoding.EncodeToString(mulaw)); err != nil {
			if d.ctx.Err() == nil {
				fmt.Printf("\n[brain send: %v]\n", err)
			}
			d.cancel()
			return
		}
	}
}

// brainLoop turns the model's text into speech and handles tool calls.
func (d *localDriver) brainLoop() {
	endCallPending := false
	agg := newClauseAggregator()
	first := true

	speak := func(text string) {
		if text == "" {
			return
		}
		if err := d.voice.Say(text, first); err != nil {
			fmt.Printf("\n[voice say: %v]\n", err)
			return
		}
		fmt.Printf("\nAGENT: %s\n", text)
		first = false
	}

	for {
		ev, err := d.brain.ReadEvent()
		if err != nil {
			if d.ctx.Err() == nil {
				fmt.Printf("\n[brain closed: %v]\n", err)
			}
			d.cancel()
			return
		}

		switch ev.Kind {
		case BrainProspectSaid:
			fmt.Printf("\nYOU: %s\n", ev.Text)

		case BrainTextDelta:
			if d.voice.Current() == "" {
				d.voice.Begin()
				agg.reset()
				first = true
			}
			speak(agg.push(ev.Text))

		case BrainToolCalls:
			d.handleToolCalls(ev.Calls, &endCallPending)

		case BrainInterrupted:
			// Barge-in: stop synthesising AND silence what is already queued.
			d.voice.Interrupt()
			d.spk.Flush()
			agg.reset()
			first = true

		case BrainTurnComplete:
			speak(agg.drain())
			_ = d.voice.Finish()
			agg.reset()
			first = true
			if endCallPending {
				endCallPending = false
				go func() {
					time.Sleep(3 * time.Second) // let the goodbye finish playing
					d.cancel()
				}()
			}

		case BrainError:
			fmt.Printf("\n[brain error: %s]\n", ev.Text)
		}
	}
}

// voiceLoop plays synthesised audio out of the laptop speaker.
func (d *localDriver) voiceLoop() {
	for {
		select {
		case <-d.ctx.Done():
			return
		case chunk, ok := <-d.voice.Chunks():
			if !ok {
				return
			}
			if len(chunk.Mulaw) == 0 {
				continue
			}
			d.spk.Write(MulawToLinear16k(chunk.Mulaw))
		}
	}
}

func (d *localDriver) handleToolCalls(calls []BrainToolCall, endCallPending *bool) {
	var responses []BrainToolResult
	for _, c := range calls {
		fmt.Printf("\n   ⚙  %s\n", formatToolCall(c))
		out := map[string]any{"output": "ok"}
		switch c.Name {
		case "book_meeting":
			// Don't actually create a calendar event during a local mic test —
			// just acknowledge so the agent confirms and wraps up.
			fmt.Println("   📅  [meeting would be booked on Google Calendar here]")
			out = map[string]any{"booked": true, "when": "as agreed", "note": "invite emailed"}
		case "end_call":
			*endCallPending = true
		}
		responses = append(responses, BrainToolResult{ID: c.ID, Name: c.Name, Response: out})
	}
	if len(responses) > 0 {
		_ = d.brain.SendToolResponse(responses)
	}
}

// ---------------------------------------------------------------------------
// Audio helpers
// ---------------------------------------------------------------------------

// speaker wraps an ffplay process reading raw PCM16 mono from stdin. Flush kills
// and restarts it so barge-in silences buffered audio instantly.
type speaker struct {
	mu     sync.Mutex
	rate   int
	cmd    *exec.Cmd
	in     io.WriteCloser
	closed bool
}

func newSpeaker(rate int) (*speaker, error) {
	s := &speaker{rate: rate}
	if err := s.start(); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *speaker) start() error {
	cmd := exec.Command("ffplay",
		"-hide_banner", "-loglevel", "quiet", "-nodisp", "-autoexit",
		"-f", "s16le", "-ar", strconv.Itoa(s.rate), "-ac", "1", "-i", "-")
	in, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	s.cmd, s.in = cmd, in
	return nil
}

func (s *speaker) Write(b []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.in != nil {
		_, _ = s.in.Write(b)
	}
}

func (s *speaker) Flush() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.stopLocked()
	if !s.closed {
		_ = s.start()
	}
}

func (s *speaker) Close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closed = true
	s.stopLocked()
}

func (s *speaker) stopLocked() {
	if s.in != nil {
		_ = s.in.Close()
		s.in = nil
	}
	if s.cmd != nil && s.cmd.Process != nil {
		_ = s.cmd.Process.Kill()
		_ = s.cmd.Wait()
		s.cmd = nil
	}
}

// detectMic returns the first dshow audio (microphone) device name ffmpeg reports.
func detectMic() (string, error) {
	cmd := exec.Command("ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy")
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	_ = cmd.Run() // always exits non-zero; we only want the device listing

	for _, ln := range strings.Split(stderr.String(), "\n") {
		if !strings.Contains(ln, "(audio)") {
			continue
		}
		a := strings.Index(ln, "\"")
		if a < 0 {
			continue
		}
		b := strings.Index(ln[a+1:], "\"")
		if b < 0 {
			continue
		}
		return ln[a+1 : a+1+b], nil
	}
	return "", fmt.Errorf("no dshow audio device found")
}

// resamplePCM16 converts mono PCM16-LE between sample rates by nearest-neighbour.
// In practice Gemini emits 24 kHz, matching the speaker, so this is usually a no-op.
func resamplePCM16(pcm []byte, from, to int) []byte {
	if from == to || from <= 0 {
		return pcm
	}
	inN := len(pcm) / 2
	if inN == 0 {
		return nil
	}
	outN := inN * to / from
	out := make([]byte, outN*2)
	for i := range outN {
		j := i * from / to
		if j >= inN {
			j = inN - 1
		}
		copy(out[i*2:i*2+2], pcm[j*2:j*2+2])
	}
	return out
}
