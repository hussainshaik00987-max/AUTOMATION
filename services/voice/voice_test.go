package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// ---------------------------------------------------------------------------
// Clause aggregation
// ---------------------------------------------------------------------------

// The first clause of a turn must NOT wait for punctuation. Every natural
// conversational opener is under the prosody floor and carries no punctuation at
// all, so a punctuation rule waits out its whole ceiling on essentially every
// turn — which is the one gap on the call the caller actually perceives.
func TestFirstClauseEmitsAtAWordBoundaryNotPunctuation(t *testing.T) {
	a := newClauseAggregator()

	if got := a.push("Yeah so "); got != "" {
		t.Fatalf("emitted %q below the %d-char prosody floor", got, prosodyFloorChars)
	}
	got := a.push("I actually built ")
	if got == "" {
		t.Fatal("first clause never emitted — it is waiting for punctuation that is not coming")
	}
	if strings.ContainsAny(got, ".,!?;:") {
		t.Errorf("first clause %q broke on punctuation, not a word boundary", got)
	}
	if len(got) < prosodyFloorChars {
		t.Errorf("emitted %q (%d chars), under the %d-char prosody floor", got, len(got), prosodyFloorChars)
	}
}

// Later clauses are pipelined behind audio that is already playing, so their
// aggregation latency is hidden and punctuation is the right break.
func TestLaterClausesBreakOnPunctuation(t *testing.T) {
	a := newClauseAggregator()
	a.push("Yeah so I actually built ") // retires the fast path

	if got := a.push("you a demo site already"); got != "" {
		t.Fatalf("later clause %q emitted with no punctuation in sight", got)
	}
	got := a.push(", completely free.")
	if !strings.HasSuffix(strings.TrimSpace(got), ",") && !strings.HasSuffix(strings.TrimSpace(got), ".") {
		t.Errorf("later clause %q did not break on punctuation", got)
	}
}

// The tail of a turn has no boundary after it. If drain does not return it, the
// last thing the agent meant to say is simply never spoken.
func TestDrainReturnsTheUnterminatedTail(t *testing.T) {
	a := newClauseAggregator()
	a.push("Yeah so I built you a demo site, ")
	tail := a.drain()
	if !strings.Contains(tail, "demo site") && tail == "" {
		t.Fatalf("drain lost the buffered tail, got %q", tail)
	}
	if a.drain() != "" {
		t.Error("drain is not idempotent — the tail would be spoken twice")
	}
}

// A model that produces a long unpunctuated run must not stall the whole turn
// behind it.
func TestLongRunWithoutPunctuationStillEmits(t *testing.T) {
	a := newClauseAggregator()
	a.push("Yeah so I actually built ") // retire the fast path
	long := strings.Repeat("word ", 60)
	if got := a.push(long); got == "" {
		t.Fatalf("a %d-char unpunctuated run emitted nothing — the turn is stalled", len(long))
	}
}

func TestResetReArmsTheFirstClauseFastPath(t *testing.T) {
	a := newClauseAggregator()
	a.push("Yeah so I actually built ")
	a.drain()
	a.reset()
	if !a.first {
		t.Fatal("reset did not re-arm the fast path; every turn after the first pays the punctuation wait")
	}
}

// ---------------------------------------------------------------------------
// The interrupt path — the buffer the India build never had
// ---------------------------------------------------------------------------

// fakeEleven stands in for the ElevenLabs multi-context socket. It records the
// client messages it receives and emits audio for whichever context it is told
// to, on demand — including AFTER a close_context, which is exactly the race
// this test exists to pin.
type fakeEleven struct {
	srv  *httptest.Server
	conn chan *websocket.Conn
	got  chan map[string]any
}

func newFakeEleven(t *testing.T) *fakeEleven {
	t.Helper()
	f := &fakeEleven{
		conn: make(chan *websocket.Conn, 1),
		got:  make(chan map[string]any, 64),
	}
	up := websocket.Upgrader{}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		f.conn <- c
		for {
			var m map[string]any
			if err := c.ReadJSON(&m); err != nil {
				return
			}
			f.got <- m
		}
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeEleven) client(t *testing.T) *websocket.Conn {
	t.Helper()
	select {
	case c := <-f.conn:
		return c
	case <-time.After(3 * time.Second):
		t.Fatal("fake ElevenLabs never saw a connection")
		return nil
	}
}

// sendAudio emits one base64 μ-law chunk on the given context.
func (f *fakeEleven) sendAudio(t *testing.T, c *websocket.Conn, ctxID string, final bool) {
	t.Helper()
	// "////" decodes to four μ-law bytes; the content is irrelevant, the routing
	// is the point.
	if err := c.WriteJSON(map[string]any{
		"contextId": ctxID, "audio": "////", "is_final": final,
	}); err != nil {
		t.Fatalf("fake server write: %v", err)
	}
}

func dialFake(t *testing.T, f *fakeEleven) *VoiceStream {
	t.Helper()
	old := elevenHost
	elevenHost = strings.TrimPrefix(f.srv.URL, "http://")
	t.Cleanup(func() { elevenHost = old })

	// ConnectVoice builds a wss:// URL; point it at the plain-HTTP test server.
	oldDial := voiceScheme
	voiceScheme = "ws"
	t.Cleanup(func() { voiceScheme = oldDial })

	v, err := ConnectVoice(context.Background(), Config{
		ElevenAPIKey: "test", ElevenVoiceID: "voice", ElevenModel: "eleven_flash_v2_5",
	})
	if err != nil {
		t.Fatalf("ConnectVoice: %v", err)
	}
	t.Cleanup(v.Close)
	return v
}

// The whole reason chunks carry a context id. On barge-in we close the context,
// but audio for it is already being made and keeps arriving. If that audio is
// played, the prospect hears the sentence they just interrupted finish on top of
// the answer to their interruption.
func TestInterruptedTurnsAudioIsDiscardedNotPlayed(t *testing.T) {
	f := newFakeEleven(t)
	v := dialFake(t, f)
	srv := f.client(t)

	id := v.Begin()
	if err := v.Say("Yeah so I built you a demo site", true); err != nil {
		t.Fatalf("Say: %v", err)
	}
	f.sendAudio(t, srv, id, false)

	// One chunk from the live turn should arrive.
	select {
	case c := <-v.Chunks():
		if c.ContextID != id || len(c.Mulaw) == 0 {
			t.Fatalf("expected audio on %s, got %+v", id, c)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("no audio arrived for the live turn")
	}

	// The prospect interrupts.
	v.Interrupt()

	// ElevenLabs was mid-sentence; more audio for the dead context arrives.
	f.sendAudio(t, srv, id, false)
	f.sendAudio(t, srv, id, true)

	// A new turn starts and speaks.
	id2 := v.Begin()
	if id2 == id {
		t.Fatal("Begin reused the interrupted context id")
	}
	f.sendAudio(t, srv, id2, false)

	select {
	case c := <-v.Chunks():
		if c.ContextID == id {
			t.Fatal("audio from the INTERRUPTED turn reached the caller — it would play over the reply to their interruption")
		}
		if c.ContextID != id2 {
			t.Fatalf("unexpected context %q", c.ContextID)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("the new turn's audio never arrived")
	}
}

// Closing the context is what stops ElevenLabs generating (and billing for) a
// sentence nobody will hear. Marking it dead only handles what is already in
// flight.
func TestInterruptSendsCloseContext(t *testing.T) {
	f := newFakeEleven(t)
	v := dialFake(t, f)
	f.client(t)

	id := v.Begin()
	_ = v.Say("Yeah so I built you a demo site", true)
	v.Interrupt()

	deadline := time.After(3 * time.Second)
	for {
		select {
		case m := <-f.got:
			if m["close_context"] == true && m["context_id"] == id {
				return // what we came for
			}
		case <-deadline:
			t.Fatal("Interrupt never sent close_context — ElevenLabs keeps synthesising the abandoned sentence")
		}
	}
}

// The first clause of a turn is the only one whose latency the caller perceives.
// It must be flushed rather than left to the chunk schedule.
func TestFirstClauseIsFlushedImmediately(t *testing.T) {
	f := newFakeEleven(t)
	v := dialFake(t, f)
	f.client(t)

	v.Begin()
	_ = v.Say("Yeah so I built you a demo site", true)

	sawText, sawFlush := false, false
	deadline := time.After(3 * time.Second)
	for !sawFlush {
		select {
		case m := <-f.got:
			if _, ok := m["text"]; ok {
				sawText = true
				if _, ok := m["voice_settings"]; !ok {
					t.Error("first message of a context carries no voice_settings — the turn would use defaults, not the call's voice")
				}
			}
			if m["flush"] == true {
				sawFlush = true
			}
		case <-deadline:
			t.Fatalf("first clause was not flushed (text seen: %v) — it waits on the chunk schedule and the caller hears the gap", sawText)
		}
	}
}

// ---------------------------------------------------------------------------
// Bounded opener synthesis
// ---------------------------------------------------------------------------

// Synthesis length is not deterministic — the same opener measured 3.11s to
// 6.27s across five takes of the identical line. The opener is exempt from
// barge-in and plays to completion, so a long take is time the prospect cannot
// talk through. Prefetch happens during the previous call, so there is room to
// look at what came back and ask again.
func TestMulawSecondsMatchesTheWire(t *testing.T) {
	// μ-law at 8kHz is one byte per sample: 8000 bytes is exactly one second.
	if got := MulawSeconds(make([]byte, 8000)); got != 1.0 {
		t.Errorf("MulawSeconds(8000 bytes) = %v, want 1.0 — every length check is wrong if this is", got)
	}
	if got := MulawSeconds(nil); got != 0 {
		t.Errorf("MulawSeconds(nil) = %v, want 0", got)
	}
}

// The ceiling has to sit where a pre-recorded opener stops being an opening.
// This pins it against the lines actually measured through the shipped voice, so
// a well-meaning tweak cannot quietly admit a seven-second monologue.
func TestOpenerCeilingRejectsTheLinesThatWereMeasuredTooLong(t *testing.T) {
	measured := map[string]float64{
		"Hi there — is this X?":                          2.09,
		"Hi! I saw your 214 Google reviews — is this X?": 3.62,
		"Hi! AI assistant here. I saw your 214 …":        4.32,
		"…on there, that's a lot — is this X?":           4.69,
	}
	for line, secs := range measured {
		if secs > openerMaxSeconds {
			t.Errorf("%q measured %.2fs and would now be rejected — it was shipped", line, secs)
		}
	}
	// The wording that named the company alongside the disclosure.
	if 7.34 <= openerMaxSeconds {
		t.Errorf("openerMaxSeconds = %v admits the 7.34s opener that -say flagged", openerMaxSeconds)
	}
}

// A finished context must be RELEASED, not just flushed.
//
// This is the bug that makes an agent say its first line and then go silent for
// the rest of the call while every other signal looks healthy — the model keeps
// replying, tools keep firing, the transcript keeps filling, and no audio ever
// reaches the caller again. `-bench` found it on turn two.
func TestFinishReleasesTheContextSoTheNextTurnGetsAFreshOne(t *testing.T) {
	f := newFakeEleven(t)
	v := dialFake(t, f)
	f.client(t)

	first := v.Begin()
	_ = v.Say("Yeah so I built you a demo site", true)
	if err := v.Finish(); err != nil {
		t.Fatalf("Finish: %v", err)
	}

	if cur := v.Current(); cur != "" {
		t.Fatalf("Current() = %q after Finish — the next turn would speak into a finished context and produce no audio", cur)
	}

	second := v.Begin()
	if second == first {
		t.Fatalf("Begin() reused the finished context %q", first)
	}
}

// Finish must also close the context. Five is the concurrent limit, so a call
// that leaks one per turn runs out partway through the conversation.
func TestFinishClosesTheContext(t *testing.T) {
	f := newFakeEleven(t)
	v := dialFake(t, f)
	f.client(t)

	id := v.Begin()
	_ = v.Say("Yeah so I built you a demo site", true)
	_ = v.Finish()

	sawFlush, sawClose := false, false
	deadline := time.After(3 * time.Second)
	for !sawClose {
		select {
		case m := <-f.got:
			if m["context_id"] != id {
				continue
			}
			if m["flush"] == true {
				sawFlush = true
			}
			if m["close_context"] == true {
				if !sawFlush {
					t.Error("context closed before the last clause was flushed — the tail of the turn is never spoken")
				}
				sawClose = true
			}
		case <-deadline:
			t.Fatal("Finish never closed the context — a call leaks one per turn against a limit of five")
		}
	}
}
