package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/gorilla/websocket"
)

// ---------------------------------------------------------------------------
// Voice — ElevenLabs Flash v2.5
//
// Everything the prospect hears comes from here: the pre-synthesised opener, the
// thinking fillers, and every live turn. That is not a preference, it is a
// constraint the India build paid for on a real call (see opener.go): the engine
// that speaks the opener MUST be the engine that speaks the rest, or the caller
// hears the call hand off between two different people mid-sentence.
//
// Audio is requested as `ulaw_8000` — 8kHz μ-law, which is exactly what the
// telephone network carries and exactly what Telnyx wants on the wire. So there
// is no transcode on the outbound path at all: bytes off the ElevenLabs socket
// go into a Telnyx media frame unmodified.
//
// Two entry points, one voice:
//
//	elevenSynth   one-shot HTTP, for audio prepared before the call (openers,
//	              fillers). Off the critical path, so a plain POST is right.
//	VoiceStream   the multi-context WebSocket, for live turns. One socket per
//	              call, opened while the phone is still ringing — cold TLS is
//	              100–200ms and must never land inside a conversation.
// ---------------------------------------------------------------------------

// elevenHost and voiceScheme are vars, not consts, so the protocol tests can
// point the client at a local server standing in for ElevenLabs. Nothing in the
// running program reassigns either.
var (
	elevenHost  = "api.elevenlabs.io"
	voiceScheme = "wss"
)

const (
	// ulaw_8000 is the format the phone network already speaks. Anything else
	// means transcoding on the hot path for no benefit.
	elevenOutputFormat = "ulaw_8000"

	// The socket is opened at dial time and must survive the whole call. The
	// default inactivity timeout is 20s — long silences on a cold call are
	// routine and would close it mid-conversation. 180 is the documented max.
	elevenInactivityTimeout = 180
)

// ---------------------------------------------------------------------------
// One-shot synthesis (openers, fillers)
// ---------------------------------------------------------------------------

// elevenSynth renders one line to μ-law 8kHz, ready to hand straight to Telnyx.
//
// This replaces the India build's generateOpenerMulaw, which had to open a whole
// Gemini WebSocket session per lead for the sole purpose of matching the live
// model's voice. Now that ElevenLabs speaks the live call too, a plain POST
// matches it exactly — same voice id, same model, same settings.
func elevenSynth(ctx context.Context, cfg Config, line string) ([]byte, error) {
	if cfg.ElevenAPIKey == "" {
		return nil, fmt.Errorf("ELEVENLABS_API_KEY not set")
	}
	if strings.TrimSpace(line) == "" {
		return nil, fmt.Errorf("nothing to synthesise")
	}

	body, err := json.Marshal(map[string]any{
		"text":           line,
		"model_id":       cfg.ElevenModel,
		"voice_settings": elevenVoiceSettings(cfg),
	})
	if err != nil {
		return nil, err
	}

	u := fmt.Sprintf("https://%s/v1/text-to-speech/%s?output_format=%s",
		elevenHost, url.PathEscape(cfg.ElevenVoiceID), elevenOutputFormat)
	req, err := http.NewRequestWithContext(ctx, "POST", u, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("xi-api-key", cfg.ElevenAPIKey)

	resp, err := (&http.Client{Timeout: 25 * time.Second}).Do(req)
	if err != nil {
		return nil, fmt.Errorf("elevenlabs synth: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return nil, fmt.Errorf("elevenlabs synth: %d %s", resp.StatusCode, strings.TrimSpace(string(detail)))
	}

	mulaw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("elevenlabs synth read: %w", err)
	}
	if len(mulaw) == 0 {
		return nil, fmt.Errorf("elevenlabs returned no audio")
	}
	return mulaw, nil
}

// mulawSecondsPerByte — μ-law at 8kHz is one byte per sample.
const mulawSecondsPerByte = 1.0 / 8000.0

// MulawSeconds is how long a μ-law buffer takes to play.
func MulawSeconds(mulaw []byte) float64 {
	return float64(len(mulaw)) * mulawSecondsPerByte
}

// openerMaxSeconds is how long a pre-recorded opener may run.
//
// The opener is exempt from barge-in and plays to completion, so every syllable
// is one the prospect cannot talk through. Past about five and a half seconds it
// stops being an opening and starts being a monologue.
const openerMaxSeconds = 5.5

// elevenSynthBounded synthesises a line and keeps the shortest take that fits.
//
// This exists because SYNTHESIS LENGTH IS NOT DETERMINISTIC, which was measured
// rather than assumed. The identical opener, sent five times to the same voice
// and model on 21 Aug 2026, came back at:
//
//	6.27s   3.62s   3.11s   4.74s   3.34s
//
// A two-fold spread, on a line that is exempt from barge-in. Timing an opener
// once with `-say` and shipping it tells you what ONE take did, and the take
// that reaches a real prospect is a different one. Pre-synthesis is what makes
// this fixable at all: the audio is generated during the previous call, so there
// is time to look at what came back and ask for another if it runs long.
//
// It returns the shortest take rather than failing, because a long opener is
// worse than a short one but far better than none.
func elevenSynthBounded(ctx context.Context, cfg Config, line string, maxSeconds float64, tries int) ([]byte, error) {
	var best []byte
	var lastErr error
	for i := 0; i < tries; i++ {
		audio, err := elevenSynth(ctx, cfg, line)
		if err != nil {
			lastErr = err
			continue
		}
		if MulawSeconds(audio) <= maxSeconds {
			if i > 0 {
				slog.Debug("opener re-synthesised to fit", "takes", i+1,
					"seconds", MulawSeconds(audio), "ceiling", maxSeconds)
			}
			return audio, nil
		}
		if best == nil || len(audio) < len(best) {
			best = audio
		}
		if ctx.Err() != nil {
			break
		}
	}
	if best != nil {
		slog.Warn("every take of this line ran past the opener ceiling — using the shortest",
			"line", line, "seconds", MulawSeconds(best), "ceiling", maxSeconds, "takes", tries)
		return best, nil
	}
	return nil, lastErr
}

// elevenVoiceSettings is the one place the voice's character is decided, so the
// opener and the live turns cannot drift apart.
//
// Stability is deliberately mid-low: a cold call wants a person, not a
// newsreader, and the India teardown is emphatic that the moment the caller
// stops sounding human is the moment the call is over. Speed stays at 1.0 —
// speeding a sales pitch up is how it starts sounding like a robocall.
func elevenVoiceSettings(cfg Config) map[string]any {
	return map[string]any{
		"stability":         cfg.ElevenStability,
		"similarity_boost":  cfg.ElevenSimilarity,
		"speed":             cfg.ElevenSpeed,
		"use_speaker_boost": false,
	}
}

// ---------------------------------------------------------------------------
// Clause aggregation — the text→speech boundary
// ---------------------------------------------------------------------------

// prosodyFloorChars is the shortest fragment worth synthesising on its own.
// Below it the model has no material to place a sentence contour on and the
// result is clipped and robotic.
const prosodyFloorChars = 12

// clauseMaxChars forces an emit even with no boundary in sight, so a model that
// produces a long unpunctuated run doesn't stall the whole turn behind it.
const clauseMaxChars = 180

// clauseAggregator turns a stream of model text deltas into speakable units.
//
// Raw token deltas must never go straight to the synthesiser: they arrive a word
// or two at a time and each one becomes its own audio request, which destroys
// prosody and multiplies latency.
//
// The rule for the FIRST clause of a turn is different from the rest, and that
// difference is measured rather than assumed. Waiting for punctuation costs the
// whole opening of the reply — and every natural conversational opener ("Sure,"
// "Okay so", "Right", "Yeah —") is under the 12-character prosody floor, so no
// early boundary exists and the aggregator waits out its full ceiling on
// essentially every turn. Measured in the Python prototype: 65ms waiting for
// punctuation against 25ms of budget. Emitting at the first WORD boundary past
// the prosody floor instead measured 16ms with the floor still enforced.
//
// Later clauses keep the punctuation rule. They are pipelined behind audio that
// is already playing, so their aggregation latency is hidden, and punctuation is
// where the prosody actually wants to break.
type clauseAggregator struct {
	buf   strings.Builder
	first bool // still waiting to emit the first clause of this turn
}

func newClauseAggregator() *clauseAggregator {
	return &clauseAggregator{first: true}
}

// reset starts a new turn. The first-clause fast path re-arms.
func (a *clauseAggregator) reset() {
	a.buf.Reset()
	a.first = true
}

// push adds a model delta and returns whatever is now ready to speak ("" if
// nothing is).
func (a *clauseAggregator) push(delta string) string {
	if delta == "" {
		return ""
	}
	a.buf.WriteString(delta)
	s := a.buf.String()

	if len(s) >= clauseMaxChars {
		if cut := lastBoundary(s, " "); cut > 0 {
			return a.take(cut)
		}
		return a.take(len(s))
	}

	if a.first {
		// Fast path: first word boundary at or past the prosody floor.
		if len(s) < prosodyFloorChars {
			return ""
		}
		if cut := firstIndexFrom(s, prosodyFloorChars, ' '); cut > 0 {
			return a.take(cut)
		}
		return ""
	}

	// Later clauses: break on punctuation, and only past the prosody floor so a
	// stray "Mr." doesn't become its own utterance.
	if cut := lastPunctuation(s); cut >= prosodyFloorChars {
		return a.take(cut)
	}
	return ""
}

// drain returns everything still buffered. Called when the model's turn ends —
// the last clause has no boundary after it and would otherwise never be spoken.
func (a *clauseAggregator) drain() string {
	s := strings.TrimSpace(a.buf.String())
	a.buf.Reset()
	if s != "" {
		a.first = false
	}
	return s
}

// take emits s[:n], keeps the remainder, and retires the first-clause fast path.
func (a *clauseAggregator) take(n int) string {
	s := a.buf.String()
	out := strings.TrimSpace(s[:n])
	rest := s[n:]
	a.buf.Reset()
	a.buf.WriteString(rest)
	if out == "" {
		return ""
	}
	a.first = false
	return out
}

// firstIndexFrom returns the index just past the first occurrence of c at or
// after start, or -1. Scanning bytes is safe here because c is always ASCII and
// a UTF-8 continuation byte can never equal an ASCII one — so a byte match is
// always a real character boundary, even if `start` lands mid-rune.
func firstIndexFrom(s string, start int, c byte) int {
	for i := start; i < len(s); i++ {
		if s[i] == c {
			return i + 1
		}
	}
	return -1
}

// lastPunctuation returns the byte index just past the last clause-ending mark.
// It walks runes, not bytes, because an em-dash is three bytes and a byte scan
// would both miss it and be able to return an index inside a character.
func lastPunctuation(s string) int {
	last := -1
	for i, r := range s {
		switch r {
		case '.', '!', '?', ',', ';', ':', '—', '–':
			last = i + utf8.RuneLen(r)
		}
	}
	return last
}

// lastBoundary returns the index just past the last occurrence of sep, or -1.
func lastBoundary(s, sep string) int {
	if i := strings.LastIndex(s, sep); i > 0 {
		return i + len(sep)
	}
	return -1
}

// ---------------------------------------------------------------------------
// VoiceStream — the live multi-context WebSocket
// ---------------------------------------------------------------------------

// VoiceChunk is one piece of synthesised audio, already μ-law 8kHz and tagged
// with the context that produced it.
//
// The tag is the whole point. When the prospect interrupts we close the context,
// but audio for it may already be in flight — on the socket, in a kernel buffer,
// anywhere. Without the tag there is no way to tell that audio apart from the
// NEXT turn's, and the prospect hears the sentence they just interrupted play
// out on top of the answer to their interruption.
type VoiceChunk struct {
	ContextID string
	Mulaw     []byte
	Final     bool
}

// VoiceStream is one call's connection to ElevenLabs. It is opened at dial time,
// lives for the whole call, and carries one context per agent turn.
type VoiceStream struct {
	cfg Config
	log *slog.Logger
	ws  *websocket.Conn

	// writeMu serializes writes. Text is written from the brain-reading
	// goroutine while Interrupt() is written from the inbound-audio goroutine,
	// and gorilla/websocket allows exactly one concurrent writer.
	writeMu sync.Mutex

	mu      sync.Mutex
	current string          // context id of the turn being spoken; "" = none open
	dead    map[string]bool // contexts closed by an interruption — drop their audio
	turn    int

	chunks chan VoiceChunk
	closed chan struct{}
	once   sync.Once
}

// ConnectVoice opens the ElevenLabs socket for one call.
//
// Called at DIAL time, not on answer. The handshake plus TLS is 100–200ms, which
// is affordable while the phone is ringing and is not affordable in the gap
// after somebody says "hello".
func ConnectVoice(ctx context.Context, cfg Config) (*VoiceStream, error) {
	if cfg.ElevenAPIKey == "" {
		return nil, fmt.Errorf("ELEVENLABS_API_KEY not set")
	}

	q := url.Values{}
	q.Set("model_id", cfg.ElevenModel)
	q.Set("output_format", elevenOutputFormat)
	q.Set("inactivity_timeout", fmt.Sprint(elevenInactivityTimeout))
	// auto_mode is deliberately OFF. It disables the chunk schedule and every
	// buffer, which is the right trade only when whole sentences are sent — and
	// the aggregator above sends clauses, some of them mid-sentence. Instead the
	// chunk schedule stays on and we force generation with an explicit flush at
	// the two moments that matter: the first clause of a turn, and the end of it.
	q.Set("auto_mode", "false")
	q.Set("apply_text_normalization", "auto")

	u := url.URL{
		Scheme:   voiceScheme,
		Host:     elevenHost,
		Path:     fmt.Sprintf("/v1/text-to-speech/%s/multi-stream-input", cfg.ElevenVoiceID),
		RawQuery: q.Encode(),
	}

	dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second}
	hdr := http.Header{}
	hdr.Set("xi-api-key", cfg.ElevenAPIKey)

	ws, resp, err := dialer.DialContext(ctx, u.String(), hdr)
	if err != nil {
		if resp != nil {
			detail, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
			return nil, fmt.Errorf("elevenlabs dial: %w (%d %s)", err, resp.StatusCode, strings.TrimSpace(string(detail)))
		}
		return nil, fmt.Errorf("elevenlabs dial: %w", err)
	}

	v := &VoiceStream{
		cfg:    cfg,
		log:    slog.With("component", "voice"),
		ws:     ws,
		dead:   make(map[string]bool),
		chunks: make(chan VoiceChunk, 256),
		closed: make(chan struct{}),
	}
	go v.read()
	return v, nil
}

// Chunks yields synthesised audio in order. It closes when the socket does.
func (v *VoiceStream) Chunks() <-chan VoiceChunk { return v.chunks }

// Begin opens a context for a new agent turn and returns its id. Any context
// still open is closed first — the model only ever has one floor to hold.
func (v *VoiceStream) Begin() string {
	v.mu.Lock()
	if v.current != "" {
		prev := v.current
		v.current = ""
		v.mu.Unlock()
		v.closeContext(prev, false)
		v.mu.Lock()
	}
	v.turn++
	id := fmt.Sprintf("t%d", v.turn)
	v.current = id
	v.mu.Unlock()
	return id
}

// Current returns the context being spoken, or "" if the agent has the floor
// but has not started a turn.
func (v *VoiceStream) Current() string {
	v.mu.Lock()
	defer v.mu.Unlock()
	return v.current
}

// Say sends one speakable clause into the current turn. first must be true for
// the opening clause of the turn: it is followed by an explicit flush, because
// that clause is the only one whose latency the caller can actually perceive —
// every later one is pipelined behind audio that is already playing.
func (v *VoiceStream) Say(text string, first bool) error {
	if strings.TrimSpace(text) == "" {
		return nil
	}
	id := v.Current()
	if id == "" {
		id = v.Begin()
	}

	msg := map[string]any{"text": text + " ", "context_id": id}
	if first {
		// voice_settings and the chunk schedule are only read from the first
		// message of a context.
		msg["voice_settings"] = elevenVoiceSettings(v.cfg)
		msg["generation_config"] = map[string]any{
			"chunk_length_schedule": []int{120, 160, 250, 290},
		}
	}
	if err := v.write(msg); err != nil {
		return err
	}
	if first {
		return v.write(map[string]any{"context_id": id, "flush": true})
	}
	return nil
}

// Finish ends the agent's turn: it flushes the remaining text so the last clause
// — which has no boundary after it — is actually spoken, closes the context, and
// RELEASES it so the next turn opens a fresh one.
//
// The release is the part that matters and it was missing. Flushing alone leaves
// `current` pointing at a context that has already been finalised, so every turn
// after the first sends its text into a closed context and produces no audio at
// all: the agent says one thing and is then mute for the rest of the call, while
// every other signal — model replying, tools firing, transcript filling — looks
// completely healthy.
//
// It was `-bench` that found it, on turn two, which is the entire argument for
// measuring more than one turn.
func (v *VoiceStream) Finish() error {
	v.mu.Lock()
	id := v.current
	v.current = ""
	v.mu.Unlock()
	if id == "" {
		return nil
	}
	if err := v.write(map[string]any{"context_id": id, "flush": true}); err != nil {
		return err
	}
	// Not marked dead: this turn's audio is wanted, it just has no more text
	// coming. Closing frees one of the five concurrent context slots.
	return v.write(map[string]any{"context_id": id, "close_context": true})
}

// Interrupt abandons the turn being spoken.
//
// This is the first of the THREE buffers a barge-in has to clear, and it is the
// one the India build never had. Skipping it means ElevenLabs carries on
// synthesising a sentence nobody will ever hear — billed for, and worse, still
// arriving on the socket afterwards, where it plays on top of the answer to the
// interruption. Marking the context dead is what makes the in-flight audio
// discardable; closing it is what stops more being made.
func (v *VoiceStream) Interrupt() {
	v.mu.Lock()
	id := v.current
	v.current = ""
	v.mu.Unlock()
	if id == "" {
		return
	}
	v.closeContext(id, true)
}

func (v *VoiceStream) closeContext(id string, discard bool) {
	if discard {
		v.mu.Lock()
		v.dead[id] = true
		v.mu.Unlock()
	}
	if err := v.write(map[string]any{"context_id": id, "close_context": true}); err != nil {
		v.log.Debug("close_context failed", "context", id, "error", err)
	}
}

// Close tears the socket down at the end of the call.
func (v *VoiceStream) Close() {
	v.once.Do(func() {
		_ = v.write(map[string]any{"close_socket": true})
		close(v.closed)
		_ = v.ws.Close()
	})
}

func (v *VoiceStream) write(msg map[string]any) error {
	select {
	case <-v.closed:
		return fmt.Errorf("voice stream closed")
	default:
	}
	v.writeMu.Lock()
	defer v.writeMu.Unlock()
	_ = v.ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
	return v.ws.WriteJSON(msg)
}

// read pumps the socket into Chunks, dropping anything belonging to a context an
// interruption has already killed.
func (v *VoiceStream) read() {
	defer close(v.chunks)
	for {
		var resp struct {
			ContextID string `json:"contextId"`
			Audio     string `json:"audio"`
			IsFinal   bool   `json:"is_final"`
			Error     string `json:"error"`
			Message   string `json:"message"`
		}
		if err := v.ws.ReadJSON(&resp); err != nil {
			select {
			case <-v.closed:
			default:
				v.log.Info("voice ws closed", "error", err)
			}
			return
		}
		if resp.Error != "" || resp.Message != "" {
			v.log.Warn("elevenlabs error frame", "error", resp.Error, "message", resp.Message)
			continue
		}
		if resp.Audio == "" {
			if resp.IsFinal {
				v.chunks <- VoiceChunk{ContextID: resp.ContextID, Final: true}
			}
			continue
		}

		v.mu.Lock()
		dead := v.dead[resp.ContextID]
		v.mu.Unlock()
		if dead {
			// The prospect interrupted this turn. This audio was already being
			// made when we said stop; playing it would be the agent finishing a
			// sentence they have moved on from.
			continue
		}

		mulaw, err := base64.StdEncoding.DecodeString(resp.Audio)
		if err != nil || len(mulaw) == 0 {
			continue
		}
		select {
		case v.chunks <- VoiceChunk{ContextID: resp.ContextID, Mulaw: mulaw, Final: resp.IsFinal}:
		case <-v.closed:
			return
		}
	}
}
