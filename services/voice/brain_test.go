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

// fakeBrain stands in for the Realtime socket: it records what the client sends
// and plays back whatever event script a test hands it.
type fakeBrain struct {
	srv  *httptest.Server
	conn chan *websocket.Conn
	got  chan map[string]any
}

func newFakeBrain(t *testing.T) *fakeBrain {
	t.Helper()
	f := &fakeBrain{conn: make(chan *websocket.Conn, 1), got: make(chan map[string]any, 64)}
	up := websocket.Upgrader{}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		// The client blocks on session.updated before it will return.
		go func() {
			for {
				var m map[string]any
				if err := c.ReadJSON(&m); err != nil {
					return
				}
				f.got <- m
				if m["type"] == "session.update" {
					_ = c.WriteJSON(map[string]any{
						"type":    "session.updated",
						"session": map[string]any{"output_modalities": []string{"text"}},
					})
				}
			}
		}()
		f.conn <- c
		<-r.Context().Done()
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeBrain) client(t *testing.T) *websocket.Conn {
	t.Helper()
	select {
	case c := <-f.conn:
		return c
	case <-time.After(3 * time.Second):
		t.Fatal("fake Realtime server never saw a connection")
		return nil
	}
}

func dialFakeBrain(t *testing.T, f *fakeBrain) *BrainConn {
	t.Helper()
	oldHost, oldScheme := brainHost, brainScheme
	brainHost = strings.TrimPrefix(f.srv.URL, "http://")
	brainScheme = "ws"
	t.Cleanup(func() { brainHost, brainScheme = oldHost, oldScheme })

	b, err := ConnectBrain(context.Background(), Config{
		OpenAIAPIKey: "test", OpenAIRealtimeModel: "test-model",
		VADMode: "semantic_vad", VADEagerness: "auto", ReasoningEffort: "minimal",
	}, "playbook")
	if err != nil {
		t.Fatalf("ConnectBrain: %v", err)
	}
	t.Cleanup(b.Close)
	return b
}

// The session MUST come up text-only. If it does not, the model generates audio
// of its own on top of ElevenLabs and the caller hears two voices at once — so
// that is a connect failure, not something to discover mid-call.
func TestConnectRefusesASessionThatIsNotTextOnly(t *testing.T) {
	f := &fakeBrain{conn: make(chan *websocket.Conn, 1), got: make(chan map[string]any, 8)}
	up := websocket.Upgrader{}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, _ := up.Upgrade(w, r, nil)
		if c == nil {
			return
		}
		var m map[string]any
		_ = c.ReadJSON(&m)
		// The server quietly keeps audio on.
		_ = c.WriteJSON(map[string]any{
			"type":    "session.updated",
			"session": map[string]any{"output_modalities": []string{"audio"}},
		})
		<-r.Context().Done()
	}))
	defer f.srv.Close()

	oldHost, oldScheme := brainHost, brainScheme
	brainHost = strings.TrimPrefix(f.srv.URL, "http://")
	brainScheme = "ws"
	defer func() { brainHost, brainScheme = oldHost, oldScheme }()

	_, err := ConnectBrain(context.Background(), Config{
		OpenAIAPIKey: "test", OpenAIRealtimeModel: "m", VADMode: "semantic_vad",
	}, "playbook")
	if err == nil {
		t.Fatal("connected to a session that will emit audio — the caller would hear two voices")
	}
	if !strings.Contains(err.Error(), "text-only") {
		t.Errorf("error does not say what is actually wrong: %v", err)
	}
}

// The session must be configured for the phone network's own codec. If this
// silently reverts to pcm16, every inbound frame is misinterpreted and the model
// hears noise.
func TestSessionAsksForPcmuAndTextOnly(t *testing.T) {
	f := newFakeBrain(t)
	dialFakeBrain(t, f)

	m := <-f.got
	sess, _ := m["session"].(map[string]any)
	if sess == nil {
		t.Fatal("no session in session.update")
	}
	mods, _ := sess["output_modalities"].([]any)
	if len(mods) != 1 || mods[0] != "text" {
		t.Errorf("output_modalities = %v, want [text]", mods)
	}
	audio, _ := sess["audio"].(map[string]any)
	in, _ := audio["input"].(map[string]any)
	format, _ := in["format"].(map[string]any)
	if format["type"] != "audio/pcmu" {
		t.Errorf("input format = %v, want audio/pcmu — anything else means a transcode on every frame", format["type"])
	}
	if _, ok := sess["tools"]; !ok {
		t.Error("no tools registered — the agent could never book anything")
	}
}

// A function call ENDS the response that produced it. Answering it before the
// response closes is rejected outright ("Conversation already has an active
// response in progress"), the turn the tool result was owed never happens, and
// the caller sits in silence. So calls are handed over at response.done.
func TestToolCallsArriveAsOneBatchWhenTheResponseCloses(t *testing.T) {
	f := newFakeBrain(t)
	b := dialFakeBrain(t, f)
	srv := f.client(t)
	<-f.got // the session.update

	send := func(m map[string]any) {
		if err := srv.WriteJSON(m); err != nil {
			t.Fatalf("server write: %v", err)
		}
	}
	send(map[string]any{"type": "response.created"})
	send(map[string]any{
		"type": "response.function_call_arguments.done",
		"call_id": "c1", "name": "capture_lead", "arguments": `{"name":"Mike"}`,
	})
	send(map[string]any{
		"type": "response.function_call_arguments.done",
		"call_id": "c2", "name": "book_meeting", "arguments": `{"attendee_email":"m@x.co"}`,
	})

	// Nothing may surface yet.
	done := make(chan BrainEvent, 1)
	go func() {
		ev, err := b.ReadEvent()
		if err == nil {
			done <- ev
		}
	}()
	select {
	case ev := <-done:
		t.Fatalf("a tool call surfaced before the response closed: %+v", ev)
	case <-time.After(300 * time.Millisecond):
	}

	send(map[string]any{"type": "response.done", "response": map[string]any{"id": "r1"}})

	select {
	case ev := <-done:
		if ev.Kind != BrainToolCalls {
			t.Fatalf("kind = %v, want BrainToolCalls", ev.Kind)
		}
		if len(ev.Calls) != 2 {
			t.Fatalf("got %d calls, want both of them in ONE batch — fabricatedToolCall judges a batch, not a call", len(ev.Calls))
		}
		if ev.Calls[0].Name != "capture_lead" || ev.Calls[1].Name != "book_meeting" {
			t.Errorf("calls out of order or wrong: %+v", ev.Calls)
		}
		if ev.Calls[0].Args["name"] != "Mike" {
			t.Errorf("arguments not parsed: %+v", ev.Calls[0].Args)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("the batch never arrived after response.done")
	}
}

// A response.done that carried no tool call is an ordinary end of turn.
func TestResponseDoneWithoutToolsIsATurnBoundary(t *testing.T) {
	f := newFakeBrain(t)
	b := dialFakeBrain(t, f)
	srv := f.client(t)
	<-f.got

	_ = srv.WriteJSON(map[string]any{"type": "response.created"})
	_ = srv.WriteJSON(map[string]any{"type": "response.output_text.delta", "delta": "Yeah so "})
	_ = srv.WriteJSON(map[string]any{"type": "response.done", "response": map[string]any{"id": "r1"}})

	if ev, err := b.ReadEvent(); err != nil || ev.Kind != BrainTextDelta || ev.Text != "Yeah so " {
		t.Fatalf("first event = %+v (%v), want the text delta", ev, err)
	}
	if ev, err := b.ReadEvent(); err != nil || ev.Kind != BrainTurnComplete {
		t.Fatalf("second event = %+v (%v), want BrainTurnComplete", ev, err)
	}
}

// Asking for a response while one is already streaming is a hard rejection, and
// the turn it was meant to produce never happens. The agent is about to speak
// anyway, so the right answer is to skip.
func TestRequestResponseIsSkippedWhileOneIsStreaming(t *testing.T) {
	f := newFakeBrain(t)
	b := dialFakeBrain(t, f)
	srv := f.client(t)
	<-f.got

	_ = srv.WriteJSON(map[string]any{"type": "response.created"})
	if ev, _ := readWithTimeout(t, b); ev.Kind != BrainNothing {
		// response.created is swallowed; drain until the state has been applied.
		_ = ev
	}

	if err := b.SendText("[a system cue]"); err != nil {
		t.Fatalf("SendText: %v", err)
	}

	deadline := time.After(1500 * time.Millisecond)
	for {
		select {
		case m := <-f.got:
			if m["type"] == "response.create" {
				t.Fatal("asked for a response while one was still streaming — the API rejects this and the turn is lost")
			}
		case <-deadline:
			return // nothing improper was sent
		}
	}
}

// readWithTimeout runs ReadEvent with a deadline so a test can advance the
// connection's state without hanging when no event is due.
func readWithTimeout(t *testing.T, b *BrainConn) (BrainEvent, error) {
	t.Helper()
	type res struct {
		ev  BrainEvent
		err error
	}
	ch := make(chan res, 1)
	go func() {
		ev, err := b.ReadEvent()
		ch <- res{ev, err}
	}()
	select {
	case r := <-ch:
		return r.ev, r.err
	case <-time.After(500 * time.Millisecond):
		return BrainEvent{Kind: BrainNothing}, nil
	}
}

// turn_detection runs with create_response, so the server answers the prospect's
// turn by itself. Between the commit and response.created, responseActive is
// still false — and anything that asks for a response in that window gets a
// SECOND one. On the first live call that made the agent deliver its whole
// Stage 5 pitch twice, word for word, to a real person.
func TestNoSecondResponseWhileTheServerIsAboutToMakeOne(t *testing.T) {
	f := newFakeBrain(t)
	b := dialFakeBrain(t, f)
	srv := f.client(t)
	<-f.got // session.update

	// The prospect stops talking; the server commits and will answer on its own.
	_ = srv.WriteJSON(map[string]any{"type": "input_audio_buffer.committed"})
	readWithTimeout(t, b) // let the client observe it

	// The silence supervisor fires in exactly this window.
	if err := b.SendText("[system cue: are they still there?]"); err != nil {
		t.Fatalf("SendText: %v", err)
	}

	deadline := time.After(1500 * time.Millisecond)
	for {
		select {
		case m := <-f.got:
			if m["type"] == "response.create" {
				t.Fatal("asked for a response while the server was already about to make one — the caller hears the same sentence twice")
			}
		case <-deadline:
			return
		}
	}
}

// ...but once that response has been and gone, the next request must go through,
// or the agent falls permanently silent after its first server-driven turn.
func TestResponseRequestWorksAgainAfterTheTurnCompletes(t *testing.T) {
	f := newFakeBrain(t)
	b := dialFakeBrain(t, f)
	srv := f.client(t)
	<-f.got

	_ = srv.WriteJSON(map[string]any{"type": "input_audio_buffer.committed"})
	readWithTimeout(t, b)
	_ = srv.WriteJSON(map[string]any{"type": "response.created"})
	readWithTimeout(t, b)
	_ = srv.WriteJSON(map[string]any{"type": "response.done", "response": map[string]any{"id": "r1"}})
	readWithTimeout(t, b)

	if err := b.SendText("[system cue]"); err != nil {
		t.Fatalf("SendText: %v", err)
	}
	deadline := time.After(3 * time.Second)
	for {
		select {
		case m := <-f.got:
			if m["type"] == "response.create" {
				return // what we came for
			}
		case <-deadline:
			t.Fatal("the agent can no longer ask the model to speak — it would go silent for the rest of the call")
		}
	}
}
