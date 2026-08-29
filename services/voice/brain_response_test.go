package main

import (
	"strings"
	"testing"
)

// The "already has an active response" rejection is benign — something IS about
// to be said — and it must not be mistaken for the running response ending.
// Treating it as an ending is what let a second response.create go out, be
// rejected in turn, and leave the call silent.
func TestActiveResponseRejectionIsRecognised(t *testing.T) {
	cases := []struct {
		name string
		msg  string
		code string
		want bool
	}{
		{
			name: "the message OpenAI actually sent on the live call",
			msg:  "Conversation already has an active response in progress: resp_EG0I5mMe7S0CN66GoXvHc. Wait until the response is finished before creating a new one.",
			want: true,
		},
		{"by error code", "", "conversation_already_has_active_response", true},
		{"lowercase message", "conversation already has an active response", "", true},

		// Real failures must still surface as call-level errors.
		{"a genuine failure", "The server had an error while processing your request", "server_error", false},
		{"rate limited", "Rate limit reached", "rate_limit_exceeded", false},
		{"nothing at all", "", "", false},
	}

	for _, tc := range cases {
		if got := isActiveResponseRejection(tc.msg, tc.code); got != tc.want {
			t.Errorf("%s: isActiveResponseRejection(%q, %q) = %v, want %v",
				tc.name, tc.msg, tc.code, got, tc.want)
		}
	}
}

// requestResponse must CLAIM the slot, not merely check it. Two callers racing
// (a tool result and the resume watchdog) both used to pass the check and both
// send response.create; the second was rejected and its turn never happened.
func TestRequestResponseClaimsTheSlot(t *testing.T) {
	b := &BrainConn{}

	// Nothing in flight: the first caller is clear to send...
	b.stateMu.Lock()
	first := !(b.responseActive || b.responseExpected)
	if first {
		b.responseExpected = true // what requestResponse now does before writing
	}
	b.stateMu.Unlock()
	if !first {
		t.Fatal("an idle connection reported itself busy")
	}

	// ...and a second caller arriving before response.created must NOT.
	b.stateMu.Lock()
	second := !(b.responseActive || b.responseExpected)
	b.stateMu.Unlock()
	if second {
		t.Error("two callers both cleared to send response.create — the second is rejected and its turn is lost")
	}

	// response.done releases the claim so the next turn can be requested.
	b.stateMu.Lock()
	b.responseActive, b.responseExpected = false, false
	third := !(b.responseActive || b.responseExpected)
	b.stateMu.Unlock()
	if !third {
		t.Error("claim never released — the agent would stay silent for the rest of the call")
	}
}

// Tool-result notes are directions to the model, not lines to say. On a live
// call the model translated one into Hindi and read it to the prospect, so every
// note now leaves here inside the [brackets] the system prompt defines as
// "never read aloud".
func TestToolNotesAreSealedAgainstBeingSpoken(t *testing.T) {
	in := map[string]any{
		"sent":  false,
		"error": "no_answer_yet",
		"note":  "Nothing was sent — you asked, but they have not answered you yet. Say NOTHING and WAIT for them to speak.",
	}
	out := sealNote(in)

	note, _ := out["note"].(string)
	if len(note) == 0 || note[0] != '[' || note[len(note)-1] != ']' {
		t.Fatalf("note is not bracketed, so the prompt's never-read-aloud rule does not cover it: %q", note)
	}
	if !strings.Contains(note, "not words to say") && !strings.Contains(note, "NOT words to say") {
		t.Error("the sealed note does not say it must not be spoken")
	}
	// The original direction has to survive — it is what the model must act on.
	if !strings.Contains(note, "they have not answered you yet") {
		t.Error("sealing dropped the actual instruction")
	}
	// Sealing must not mutate the caller's map or lose the other fields.
	if in["note"].(string)[0] == '[' {
		t.Error("sealNote mutated the caller's map")
	}
	if out["error"] != "no_answer_yet" || out["sent"] != false {
		t.Error("sealing dropped sibling fields the model needs")
	}
	// Already-sealed notes must not be double-wrapped.
	if sealNote(out)["note"].(string) != note {
		t.Error("an already-sealed note was wrapped twice")
	}
}
