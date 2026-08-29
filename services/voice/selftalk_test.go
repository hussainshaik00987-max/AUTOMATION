package main

import (
	"log/slog"
	"os"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// The agent must not hold a conversation with itself.
//
// Replayed from the validation call of 20 Aug 2026, which is the only place this
// has been seen whole:
//
//	19:05:05.3  PROSPECT  "बहुत होता है मेरे साथ भी"
//	19:05:07.0  tool      send_demo_link
//	19:05:08.7  tool      capture_lead(name: "human")
//	19:05:10.0  tool      set_followup(meeting, "kal shaam")
//	19:05:11.1  tool      end_call(link_sent)
//
// Four tool calls in 4.1 seconds and not one word from the far end between them.
// It asked "और आपका नाम?", answered itself "अच्छा human", asked "कल शाम को call
// करूँ?", answered itself "ठीक है", and hung up — on a man who was still sitting
// there listening. "human" and "kal shaam" would both have been written to
// results.csv, and "human" into the WhatsApp greeting.
//
// The playbook says STOP, in that word, eleven times. It was ignored. A prompt
// is a request.
// ---------------------------------------------------------------------------

func nano(t time.Time) int64 { return t.UnixNano() }

// newSelfTalkSession builds a Session far enough to exercise the guard without a
// live call: no sockets, no Gemini, no Telnyx.
func newSelfTalkSession() *Session {
	return &Session{log: slog.Default()}
}

func TestFabricatedToolCallDetectsSelfConversation(t *testing.T) {
	s := newSelfTalkSession()
	now := time.Now()

	// 19:05:05.3 — the prospect speaks, and the model answers with its first tool
	// call (send_demo_link at 19:05:07.0). Legitimate: there is nothing before it
	// to have talked over.
	s.lastSpeechNano.Store(nano(now.Add(-6 * time.Second)))
	if s.fabricatedToolCall() {
		t.Fatal("the first tool call of a call can never be fabricated")
	}
	// That batch happened 1.7s ago; this is capture_lead(name: "human") arriving
	// now, with nobody having said a word in between.
	s.lastToolBatchNano.Store(nano(now.Add(-1700 * time.Millisecond)))
	if !s.fabricatedToolCall() {
		t.Fatal("a tool call with no prospect speech since the last one is the model talking to itself")
	}
}

func TestFabricatedToolCallAllowsARealReply(t *testing.T) {
	s := newSelfTalkSession()
	now := time.Now()

	s.lastToolBatchNano.Store(nano(now.Add(-4 * time.Second)))
	// They actually answered — "haan, Rakesh" — before the next tool call.
	s.lastSpeechNano.Store(nano(now.Add(-2 * time.Second)))
	if s.fabricatedToolCall() {
		t.Fatal("a tool call after a real reply must be allowed")
	}
}

// Speech energy is not the only evidence a person spoke. The floor is tuned per
// route and a quiet caller on an odd carrier can fall under it; transcription
// arriving is separate proof, and either one is enough. Rejecting a genuine name
// because the caller was softly spoken would be its own bug.
func TestFabricatedToolCallAcceptsTranscriptAsEvidence(t *testing.T) {
	s := newSelfTalkSession()
	now := time.Now()

	s.lastToolBatchNano.Store(nano(now.Add(-4 * time.Second)))
	s.lastProspectTextNano.Store(nano(now.Add(-2 * time.Second)))
	if s.fabricatedToolCall() {
		t.Fatal("a transcribed prospect utterance is evidence a person spoke, even below the energy floor")
	}
}

// "human" reached capture_lead on a live call, from a question the model had
// asked itself seconds earlier. Even with the guard above, a name the model
// reaches for when nobody answered must not survive — it goes into the calendar
// invite, where "Hi human" is worse than no name at all.
func TestCleanPersonNameRejectsWhatTheModelInvents(t *testing.T) {
	for _, invented := range []string{"human", "Human", "person", "caller", "prospect",
		"user", "hello", "hey", "yes", "yeah", "ok", "sure", "speaking", "owner"} {
		if got := cleanPersonName(invented, "Miller Roofing"); got != "" {
			t.Errorf("cleanPersonName(%q) = %q, want it rejected — nobody is called that", invented, got)
		}
	}
	// The observed failure mode, in both directions: the business name handed
	// straight back as the owner's name.
	for _, biz := range []string{"Miller", "miller", "Roofing", "ROOFING"} {
		if got := cleanPersonName(biz, "Miller Roofing"); got != "" {
			t.Errorf("cleanPersonName(%q) = %q — that is the business name, not a person", biz, got)
		}
	}
	// Real names must still come through.
	for _, real := range []string{"Mike", "danielle", "Christopher", "Jose"} {
		if got := cleanPersonName(real, "Miller Roofing"); got == "" {
			t.Errorf("cleanPersonName(%q) rejected a real first name", real)
		}
	}
}

// The tool responses are what actually drove the monologue: each one was a list
// of steps, and the model worked through the list without stopping. They must
// name ONE thing to say and then require silence.
func TestToolNotesDoNotChainSteps(t *testing.T) {
	// The send_demo_link note is the worst offender historically — it used to
	// carry four steps ending in end_call.
	// book_meeting is not in this map because it is not in this build: there is
	// no meeting to book on these calls and no calendar behind it. Saying yes to
	// the link is the whole win.
	notes := map[string]string{
		// The send_demo_link note deliberately contains no sentence to say — one
		// used to be quoted there and the model recited it on top of its own
		// version. What must survive is the stop, and the ban on the past tense.
		"send_demo_link": "in the future or present-continuous tense, NEVER the past",
		"capture_lead":   "Say ONE short warm line using it, then STOP TALKING and WAIT",
		"set_language":   "Say nothing about the language itself",
	}
	src := readSourceFile(t, "session.go")
	for tool, want := range notes {
		if !contains(src, want) {
			t.Errorf("%s's tool note no longer requires a single line and a stop (looking for %q)", tool, want)
		}
	}
	// The specific chain that produced the four-tool monologue must not return.
	for _, chain := range []string{
		"Then, if you don't already have it, ask their name",
		"you still owe Stage 7: ask when to talk again",
		"then ask their name, then ask when",
	} {
		if contains(src, chain) {
			t.Errorf("a tool note still chains steps in one turn: %q", chain)
		}
	}
}

func readSourceFile(t *testing.T, name string) string {
	t.Helper()
	b, err := os.ReadFile(name)
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	return string(b)
}

func contains(haystack, needle string) bool { return strings.Contains(haystack, needle) }

// One reply can legitimately produce two batches of tool calls in quick
// succession — "haan bhej do, mera naam Rakesh hai" is a yes and a name, and the
// model may emit send_demo_link and capture_lead back to back rather than in one
// batch. Refusing the second would mean refusing a link to somebody who really
// did say yes, which is the most expensive mistake this guard could make.
func TestFabricatedToolCallAllowsOneReplyProducingTwoBatches(t *testing.T) {
	s := newSelfTalkSession()
	base := time.Now()

	s.lastSpeechNano.Store(nano(base.Add(-3 * time.Second)))
	s.lastToolBatchNano.Store(nano(base.Add(-200 * time.Millisecond)))
	if s.fabricatedToolCall() {
		t.Fatal("two tool batches 200ms apart are one response to one utterance, not a monologue")
	}
}

// …but the monologue's own spacing must still be caught. Its four calls were
// 1.7s, 1.3s and 1.1s apart, because the model was speaking an invented
// conversation in between.
func TestFabricatedToolCallStillCatchesTheMonologueSpacing(t *testing.T) {
	s := newSelfTalkSession()
	s.lastSpeechNano.Store(nano(time.Now().Add(-4 * time.Second)))
	s.lastToolBatchNano.Store(nano(time.Now().Add(-1100 * time.Millisecond)))
	if !s.fabricatedToolCall() {
		t.Fatal("tool calls 1.1s apart with no speech between them are the model talking to itself")
	}
}

// A dial that never carried a frame of audio is not a conversation, whatever
// the disposition column says — and six of them in a row is not a run of busy
// people, it is the route being down. On the night this guard was written,
// three consecutive calls to a phone that WAS being answered ended at Telnyx's
// 30-second dial timer with no answer signal and no media; without a breaker a
// campaign would have spent every remaining lead discovering that.
func TestDeadDialCountsOnlyCallsThatNeverConnected(t *testing.T) {
	dead := []string{"no_answer", "dial_failed"}
	alive := []string{"link_sent", "not_interested", "hung_up", "voicemail",
		"callback", "interested", "incomplete", "no_response"}

	for _, d := range dead {
		if !deadDial(Result{Disposition: d}) {
			t.Errorf("disposition %q should count towards the route breaker", d)
		}
	}
	for _, d := range alive {
		if deadDial(Result{Disposition: d}) {
			t.Errorf("disposition %q reached a phone — it must reset the route breaker", d)
		}
	}
	// A voicemail is the route WORKING: the call connected and a machine
	// answered. Counting it would stop a campaign that is running perfectly.
	if deadDial(Result{Disposition: "voicemail"}) {
		t.Error("a voicemail proves the route completes calls")
	}
}
