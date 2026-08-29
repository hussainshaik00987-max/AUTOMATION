package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// ---------------------------------------------------------------------------
// Brain — OpenAI Realtime, audio in, TEXT out
//
// The model hears the prospect's μ-law straight off the wire and answers in
// plain text. It never generates a single sample of audio: ElevenLabs does all
// of that (voice.go).
//
// That split is forced, not chosen. The opener has to be pre-synthesised to kill
// the dead air on pickup, and the opener must be the SAME voice as the rest of
// the call or the handoff is audible (opener.go). Since ElevenLabs is the voice,
// the brain has to emit text. Gemini's Live models cannot: the native-audio
// models are audio-only, and the half-cascade model that could do text-out was
// retired in December 2025 with no replacement.
//
// What the API still does for us, with output_modalities:["text"], is
// everything except synthesis — semantic turn detection, interruption handling,
// input transcription, function calling and conversation state. Verified live
// against gpt-realtime-2.1-mini rather than assumed:
//
//	audio.input.format {type: "audio/pcmu"}   accepted, echoed back
//	output_modalities  ["text"]               accepted
//	turn_detection     semantic_vad           accepted, interrupt_response honoured
//	text deltas        response.output_text.delta
//
// The pcmu part matters more than it looks: the phone network is 8kHz μ-law end
// to end, so accepting it directly deletes the inbound transcode AND resample
// that the Gemini build had to do on every single 20ms frame.
// ---------------------------------------------------------------------------

// BrainEventKind is the small vocabulary session.go actually reacts to. The
// Realtime API emits ~30 event types; all but a handful are noise for a phone
// call, and normalising here keeps the session loop readable.
type BrainEventKind int

const (
	BrainNothing      BrainEventKind = iota
	BrainTextDelta                   // a piece of what the agent is saying
	BrainTurnComplete                // the agent's turn is finished
	BrainInterrupted                 // the prospect started talking over it
	BrainProspectSaid                // prospect-side transcript (voicemail cues, refusals)
	BrainToolCalls                   // one or more function calls
	BrainError                       // the API rejected something
)

// BrainToolCall is one function call, already parsed.
type BrainToolCall struct {
	ID   string
	Name string
	Args map[string]any
}

// BrainEvent is one normalized thing that happened.
type BrainEvent struct {
	Kind   BrainEventKind
	Text   string // BrainTextDelta / BrainProspectSaid / BrainError
	Calls  []BrainToolCall
	RespID string
}

// BrainToolResult is one function's return value, on its way back to the model.
type BrainToolResult struct {
	ID       string
	Name     string
	Response map[string]any
}

// BrainConn is one call's Realtime session.
type BrainConn struct {
	ws  *websocket.Conn
	log *slog.Logger

	// writeMu serializes writes. Audio is written from the inbound-audio
	// goroutine while tool responses are written from the brain-reading
	// goroutine, and gorilla/websocket allows one concurrent writer.
	writeMu sync.Mutex

	// pending accumulates streamed function-call arguments by call id.
	pending map[string]*pendingCall

	// batch collects the function calls a single response produced, to be handed
	// over together when it closes.
	//
	// An earlier version returned each call the instant its arguments finished
	// streaming, on the assumption that acting sooner was faster. It is not, and
	// it is wrong: a function call ENDS the response that made it, so there is no
	// text still coming to be raced. What it did produce was
	// "Conversation already has an active response in progress" — a hard
	// rejection, after which the turn the tool result was owed simply never
	// happens and the caller sits in silence — and, when the output item landed
	// after the new response had already been asked for, the model calling the
	// same tool a second time because its first call still looked unanswered.
	//
	// Batching also gives fabricatedToolCall the shape it expects: several tools
	// from ONE response are a legitimate batch ("sure, send it, I'm Mike"), and
	// it is the NEXT batch arriving out of silence that is fiction.
	batch []BrainToolCall

	// responseActive is true between response.created and response.done, so a
	// caller that wants the model to speak can tell whether it already is.
	//
	// responseExpected covers the gap BEFORE that. turn_detection runs with
	// create_response, so the server makes a response of its own the moment it
	// commits the prospect's turn — but response.created does not arrive
	// instantly. In that window responseActive is still false, and anything that
	// asks for a response (the resume watchdog, the silence supervisor) gets a
	// SECOND one. On the first live call that is what made the agent deliver
	// "I went ahead and built you a demo site… would fifteen minutes next week
	// work?" twice in a row, word for word, to a real person.
	//
	// It is armed when the buffer is committed and disarmed when the response
	// actually shows up, so the whole span from "they stopped talking" to "it is
	// talking" is covered.
	stateMu          sync.Mutex
	responseActive   bool
	responseExpected bool
}

type pendingCall struct {
	name string
	args string
}

// brainConsecutiveFails counts connect failures in a row across the whole
// campaign (reset to 0 on any success). The campaign loop stops dialling once it
// crosses brainFailLimit, so we never burn leads while the API is down.
var brainConsecutiveFails atomic.Int32

const brainFailLimit int32 = 3

func brainKeys(cfg Config) []string {
	keys := []string{cfg.OpenAIAPIKey}
	if cfg.OpenAIAPIKeyBackup != "" {
		keys = append(keys, cfg.OpenAIAPIKeyBackup)
	}
	return keys
}

// brainHost is a var so the protocol tests can stand in for OpenAI.
var (
	brainHost   = "api.openai.com"
	brainScheme = "wss"
)

// ConnectBrain opens a Realtime session for a real call with a per-call system
// instruction (playbook + this prospect's research + opening behaviour). No
// kickoff turn is sent, so the agent waits for the prospect to speak first.
func ConnectBrain(ctx context.Context, cfg Config, instructions string) (*BrainConn, error) {
	dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second}
	url := fmt.Sprintf("%s://%s/v1/realtime?model=%s", brainScheme, brainHost, cfg.OpenAIRealtimeModel)

	var lastErr error
	for i, key := range brainKeys(cfg) {
		hdr := http.Header{}
		hdr.Set("Authorization", "Bearer "+key)

		ws, _, err := dialer.DialContext(ctx, url, hdr)
		if err != nil {
			lastErr = fmt.Errorf("brain dial: %w", err)
			continue
		}
		b := &BrainConn{
			ws:      ws,
			log:     slog.With("component", "brain"),
			pending: map[string]*pendingCall{},
		}
		if err := b.configure(cfg, instructions); err != nil {
			ws.Close()
			lastErr = err
			continue
		}
		if i > 0 {
			b.log.Warn("connected on BACKUP OpenAI key (primary failed)")
		}
		brainConsecutiveFails.Store(0)
		b.log.Info("session ready", "model", cfg.OpenAIRealtimeModel)
		return b, nil
	}

	// Only count it as an API failure if the call is still live — a cancelled
	// context means the call already ended (e.g. voicemail hangup), not an outage.
	if ctx.Err() == nil {
		brainConsecutiveFails.Add(1)
	}
	return nil, fmt.Errorf("brain connect failed on all keys: %w", lastErr)
}

// vadSilenceDurationMs is retained from the Gemini build as the server_vad
// fallback window. semantic_vad does not take one — it decides from the words,
// not from a stopwatch — which is why it is the default here: on a cold call
// people trail off mid-sentence and a fixed timer answers half a question.
// 200ms was far too short. OpenAI's own default is 500, and at 200 the server
// commits a turn after a fifth of a second of quiet — which on a real call means
// two things at once: the prospect gets chopped off mid-sentence, and every blip
// of line noise between their words becomes a TURN of its own. A live call to a
// noisy Indian mobile came back with the prospect's side transcribed as
// "येनो" / "कोर." / "येगना" / "एक जुए" — twenty fragments where he had said a
// handful of sentences, each one committed as a separate turn and each one
// cancelling whatever the agent had started to say.
var vadSilenceDurationMs = 500

func (b *BrainConn) configure(cfg Config, instructions string) error {
	turn := map[string]any{
		"type": "semantic_vad",
		// The server cancels the in-flight response the moment the prospect
		// speaks. Our own bargeGate (barge.go) decides what even reaches this
		// VAD, so it can afford to be eager.
		"interrupt_response": true,
		"create_response":    true,
		"eagerness":          cfg.VADEagerness,
	}
	if cfg.VADMode == "server_vad" {
		silence := vadSilenceDurationMs
		if cfg.VADSilenceMs > 0 {
			silence = cfg.VADSilenceMs
		}
		sv := map[string]any{
			"type":                "server_vad",
			"interrupt_response":  true,
			"create_response":     true,
			"silence_duration_ms": silence,
			"prefix_padding_ms":   300,
		}
		// The loudness bar the VAD calls speech. Left unset the server uses 0.5,
		// and on this route that is what let ROOM NOISE take the floor: the agent
		// would start a sentence, the prospect's line hiss would cross 0.5, the
		// server would cancel the response under interrupt_response, and the
		// caller heard nothing at all. The run log shows that as five consecutive
		// "turn cut short … heard_ms=0" — five turns generated, none of them
		// audible, twenty-six seconds of silence on a live call.
		//
		// There is no good universal value: it depends on the carrier and the
		// handset, which is why it is a knob and why the per-call loudness
		// histogram is logged to set it from.
		if cfg.VADThreshold > 0 {
			sv["threshold"] = cfg.VADThreshold
		}
		turn = sv
	}

	session := map[string]any{
		"type":              "realtime",
		"output_modalities": []string{"text"},
		"instructions":      instructions,
		"audio": map[string]any{
			"input": map[string]any{
				// The phone network's own codec. No transcode, no resample.
				"format":         map[string]any{"type": "audio/pcmu"},
				"turn_detection": turn,
				// The prospect's words feed the refusal latch and the voicemail-cue
				// scan, both of which run independently of the model's judgement.
				//
				// The LANGUAGE is pinned. Left to auto-detect, short telephone
				// utterances get assigned to whatever language the acoustics happen
				// to favour: a live call transcribed a plain "no" as Azerbaijani
				// ("Yox, təəssüf."), "yeah" as "Jo." and another as "Çox". Both
				// guards that read this are English substring matches, so a
				// mis-detected language does not merely look untidy in the
				// transcript — it silently disarms the refusal latch, and a
				// prospect saying "not interested" stops being heard as one.
				"transcription": map[string]any{
					"model":    cfg.TranscribeModel,
					"language": cfg.TranscribeLanguage,
				},
			},
		},
		"tools":       brainToolDecls(),
		"tool_choice": "auto",
	}

	// The direct analogue of the Gemini build's thinkingBudget:0. A cold call is
	// won or lost in the first two seconds of silence and the model's job here is
	// to follow a six-stage script, not to deliberate — so the deliberation pass
	// is bought down to the floor. Raise it only with a -bench number in hand.
	if cfg.ReasoningEffort != "" {
		session["reasoning"] = map[string]any{"effort": cfg.ReasoningEffort}
	}

	if err := b.write(map[string]any{"type": "session.update", "session": session}); err != nil {
		return fmt.Errorf("session.update: %w", err)
	}
	return b.awaitSessionUpdated()
}

// awaitSessionUpdated blocks until the server confirms the configuration, so a
// rejected session fails at connect rather than silently running with defaults —
// which for this agent would mean an audio-emitting model and a caller hearing
// two voices at once.
func (b *BrainConn) awaitSessionUpdated() error {
	_ = b.ws.SetReadDeadline(time.Now().Add(15 * time.Second))
	defer b.ws.SetReadDeadline(time.Time{})

	for {
		_, raw, err := b.ws.ReadMessage()
		if err != nil {
			return fmt.Errorf("brain setup read: %w", err)
		}
		var ev struct {
			Type    string                    `json:"type"`
			Error   *struct{ Message string } `json:"error"`
			Session *struct {
				OutputModalities []string `json:"output_modalities"`
			} `json:"session"`
		}
		if json.Unmarshal(raw, &ev) != nil {
			continue
		}
		switch ev.Type {
		case "session.updated":
			if ev.Session != nil && len(ev.Session.OutputModalities) == 1 && ev.Session.OutputModalities[0] == "text" {
				return nil
			}
			return fmt.Errorf("brain refused text-only output; it would generate audio over ElevenLabs")
		case "error":
			msg := ""
			if ev.Error != nil {
				msg = ev.Error.Message
			}
			return fmt.Errorf("brain setup rejected: %s", msg)
		}
	}
}

// SendAudio forwards one μ-law frame. b64 is base64 of the raw μ-law bytes,
// exactly as they arrived from Telnyx — there is no transcode on this path.
func (b *BrainConn) SendAudio(b64 string) error {
	return b.write(map[string]any{"type": "input_audio_buffer.append", "audio": b64})
}

// SendText injects a user turn. Used by the offline harnesses and by the
// resume watchdog, never by the live audio path.
func (b *BrainConn) SendText(text string) error {
	if err := b.write(map[string]any{
		"type": "conversation.item.create",
		"item": map[string]any{
			"type": "message", "role": "user",
			"content": []any{map[string]any{"type": "input_text", "text": text}},
		},
	}); err != nil {
		return err
	}
	return b.requestResponse()
}

// SendToolResponse returns one or more function results and asks the model to
// carry on. The follow-up response.create is required: without it the model has
// the answer and says nothing, which on a phone call is dead air.
func (b *BrainConn) SendToolResponse(results []BrainToolResult) error {
	for _, r := range results {
		payload, err := json.Marshal(r.Response)
		if err != nil {
			payload = []byte(`{}`)
		}
		if err := b.write(map[string]any{
			"type": "conversation.item.create",
			"item": map[string]any{
				"type":    "function_call_output",
				"call_id": r.ID,
				"output":  string(payload),
			},
		}); err != nil {
			return err
		}
	}
	// The output items are accepted at any time; the request for a new response
	// is not.
	return b.requestResponse()
}

// requestResponse asks the model to speak, unless it already is.
//
// "Conversation already has an active response in progress" is a hard rejection,
// not a warning: the turn it was meant to produce simply never happens, and on a
// phone call that is dead air. Every caller that wants the model to speak goes
// through here — tool results, the resume watchdog, the silence supervisor, the
// harnesses — so there is one place that knows whether the socket is busy.
//
// Skipping is the right answer rather than queueing. If a response is already
// streaming, the agent is about to talk anyway, and what the caller wanted was
// for it to say SOMETHING.
func (b *BrainConn) requestResponse() error {
	// Claim the slot under the SAME lock that reads it. Checking and then
	// releasing left a window that two callers could both walk through — the
	// tool-result path and the resume watchdog firing together — and the second
	// response.create came back "Conversation already has an active response in
	// progress", which is a hard rejection: that turn never happens, and on a
	// phone call a turn that never happens is dead air.
	//
	// responseExpected is what response.created / response.done clear, so this
	// claim is released by the same events that would have cleared it anyway.
	b.stateMu.Lock()
	if b.responseActive || b.responseExpected {
		b.stateMu.Unlock()
		return nil
	}
	b.responseExpected = true
	b.stateMu.Unlock()

	if err := b.write(map[string]any{"type": "response.create"}); err != nil {
		// The request never made it out, so nothing is coming to clear the claim.
		// Leaving it set would wedge the connection silent for the rest of the call.
		b.stateMu.Lock()
		b.responseExpected = false
		b.stateMu.Unlock()
		return err
	}
	return nil
}

// Cancel abandons the response in flight. semantic_vad with interrupt_response
// already does this when the prospect speaks; this is for the cases we decide
// ourselves, such as a fabricated tool call.
func (b *BrainConn) Cancel() error {
	return b.write(map[string]any{"type": "response.cancel"})
}

func (b *BrainConn) Close() {
	_ = b.ws.Close()
}

func (b *BrainConn) write(msg map[string]any) error {
	b.writeMu.Lock()
	defer b.writeMu.Unlock()
	return b.ws.WriteJSON(msg)
}

// realtimeEvent is the subset of the wire format this agent reads.
type realtimeEvent struct {
	Type       string `json:"type"`
	Delta      string `json:"delta"`
	Transcript string `json:"transcript"`
	CallID     string `json:"call_id"`
	Name       string `json:"name"`
	Arguments  string `json:"arguments"`
	ItemID     string `json:"item_id"`
	ResponseID string `json:"response_id"`
	Error      *struct {
		Message string `json:"message"`
		Code    string `json:"code"`
	} `json:"error"`
	Response *struct {
		ID     string `json:"id"`
		Status string `json:"status"`
		Output []struct {
			Type      string `json:"type"`
			Name      string `json:"name"`
			CallID    string `json:"call_id"`
			Arguments string `json:"arguments"`
		} `json:"output"`
	} `json:"response"`
}

// ReadEvent blocks for the next thing worth reacting to. Events the session
// doesn't act on are swallowed here rather than in the call loop.
func (b *BrainConn) ReadEvent() (BrainEvent, error) {
	for {
		_, raw, err := b.ws.ReadMessage()
		if err != nil {
			return BrainEvent{}, err
		}
		var ev realtimeEvent
		if json.Unmarshal(raw, &ev) != nil {
			continue
		}

		switch ev.Type {
		case "input_audio_buffer.committed", "input_audio_buffer.speech_stopped":
			// The server has taken the prospect's turn and will answer it itself.
			b.stateMu.Lock()
			b.responseExpected = true
			b.stateMu.Unlock()

		case "response.created":
			b.stateMu.Lock()
			b.responseActive = true
			b.responseExpected = false
			b.stateMu.Unlock()

		case "response.output_text.delta":
			if ev.Delta != "" {
				return BrainEvent{Kind: BrainTextDelta, Text: ev.Delta, RespID: ev.ResponseID}, nil
			}

		case "input_audio_buffer.speech_started":
			// The prospect took the floor. With interrupt_response the server has
			// already cancelled whatever it was saying; the session still has to
			// tear down the audio in flight downstream of it.
			return BrainEvent{Kind: BrainInterrupted}, nil

		case "conversation.item.input_audio_transcription.completed":
			if ev.Transcript != "" {
				return BrainEvent{Kind: BrainProspectSaid, Text: ev.Transcript}, nil
			}

		case "response.function_call_arguments.delta":
			p := b.pending[ev.CallID]
			if p == nil {
				p = &pendingCall{name: ev.Name}
				b.pending[ev.CallID] = p
			}
			p.args += ev.Delta

		case "response.function_call_arguments.done":
			name := ev.Name
			args := ev.Arguments
			if p := b.pending[ev.CallID]; p != nil {
				if name == "" {
					name = p.name
				}
				if args == "" {
					args = p.args
				}
				delete(b.pending, ev.CallID)
			}
			if call, ok := parseToolCall(ev.CallID, name, args); ok {
				b.batch = append(b.batch, call)
			}

		case "response.done":
			b.stateMu.Lock()
			b.responseActive = false
			b.responseExpected = false
			b.stateMu.Unlock()

			// Some calls only ever appear in the response's output list — pick up
			// anything the streamed events missed, without double-counting.
			if ev.Response != nil {
				for _, o := range ev.Response.Output {
					if o.Type != "function_call" || o.CallID == "" {
						continue
					}
					seen := false
					for _, c := range b.batch {
						if c.ID == o.CallID {
							seen = true
							break
						}
					}
					if seen {
						continue
					}
					if call, ok := parseToolCall(o.CallID, o.Name, o.Arguments); ok {
						b.batch = append(b.batch, call)
					}
				}
			}

			if len(b.batch) > 0 {
				calls := b.batch
				b.batch = nil
				// NOT a turn boundary. The agent has not finished speaking — it has
				// asked a question and is waiting for an answer, and the turn ends
				// when the response its tool result produces ends.
				return BrainEvent{Kind: BrainToolCalls, Calls: calls}, nil
			}
			return BrainEvent{Kind: BrainTurnComplete, RespID: responseID(ev)}, nil

		case "response.cancelled", "response.incomplete":
			// A turn the prospect interrupted, or one the server gave up on.
			// Either way nothing more is coming, so the flag must clear or every
			// later tool result would wait forever for a response that already
			// ended.
			b.stateMu.Lock()
			b.responseActive = false
			b.responseExpected = false
			b.stateMu.Unlock()
			b.batch = nil

		case "error", "response.failed":
			msg, code := "", ""
			if ev.Error != nil {
				msg, code = ev.Error.Message, ev.Error.Code
			}

			// "Conversation already has an active response" is a rejection of the
			// request we just made, NOT the end of the response already running.
			// Clearing the flags here would be a lie: the other response is still
			// streaming, and the next caller would see an idle socket, send another
			// response.create, and be rejected again — a loop that produces one
			// warning per attempt and no speech at all.
			//
			// The response actually in flight will clear these itself when it
			// reaches response.done, cancelled or failed.
			if isActiveResponseRejection(msg, code) {
				b.stateMu.Lock()
				b.responseActive = true
				b.responseExpected = false
				b.stateMu.Unlock()
				continue
			}

			b.stateMu.Lock()
			b.responseActive = false
			b.responseExpected = false
			b.stateMu.Unlock()
			b.batch = nil
			return BrainEvent{Kind: BrainError, Text: msg}, nil
		}
	}
}

// isActiveResponseRejection reports whether an error event is the server saying
// "you asked for a response while one was already running" rather than a real
// failure. It is benign — something IS about to be said — so it must not be
// surfaced as a call-level error or used to reset the response state.
func isActiveResponseRejection(msg, code string) bool {
	if strings.EqualFold(code, "conversation_already_has_active_response") {
		return true
	}
	return strings.Contains(strings.ToLower(msg), "already has an active response")
}

func responseID(ev realtimeEvent) string {
	if ev.Response != nil {
		return ev.Response.ID
	}
	return ev.ResponseID
}

func parseToolCall(id, name, args string) (BrainToolCall, bool) {
	if id == "" || name == "" {
		return BrainToolCall{}, false
	}
	parsed := map[string]any{}
	if args != "" {
		// A malformed argument blob is not a reason to drop the call — the tool
		// handlers all treat a missing field as absent, and losing an end_call
		// because its JSON was truncated leaves the line open.
		_ = json.Unmarshal([]byte(args), &parsed)
	}
	return BrainToolCall{ID: id, Name: name, Args: parsed}, true
}
