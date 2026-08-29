package main

import (
	"log/slog"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// The consent guard.
//
// These all come from one live call. The agent asked "आपकी अपनी कोई website
// नहीं दिखी, सही है ना सर?", the prospect said "yes" — agreeing he had no
// website — and the agent replied "ठीक है सर, मैं अभी WhatsApp पर लिंक भेज रही
// हूँ." He was never told a site had been built for him, and he was never asked.
// The link went out and the run was recorded as link_sent.
//
// The fix is not a better sentence in the playbook; it is that the offer has to
// have physically happened before anything can be sent. That is what is tested
// here.
// ---------------------------------------------------------------------------

func offerSession() *Session { return &Session{log: slog.Default()} }

// The exact utterance from the failed call must not unlock sending.
func TestGapConfirmationIsNotAnOffer(t *testing.T) {
	notOffers := []string{
		"आपकी अपनी कोई website नहीं दिखी, सही है ना सर?",
		"जी नमस्ते सर, मैं मीरा बोल रही हूँ Sharma IT Solution से — एक मिनट लूँगी बस।",
		"Fairlight Studio से बात हो रही है?",
		"सर आपका काम बहुत अच्छा है, कितने साल से कर रहे हैं ये?",
		"आजकल client पहले Google करता है सर।",
		"हैलो सर, सुन रहे हैं आप?",
		// The gift without the ask. Beat 1 alone is still not consent.
		"मैंने आपके लिए एक demo website बना दी है, बिलकुल free।",
	}
	for _, line := range notOffers {
		s := offerSession()
		s.noteLinkOffer(line)
		if s.linkOffered.Load() {
			t.Errorf("this is not an offer to send, but it unlocked sending: %q", line)
		}
	}
}

// The real ask, in every form the call actually produces, must unlock it.
func TestRealOfferUnlocksSending(t *testing.T) {
	offers := []string{
		"WhatsApp पे लिंक भेज दूँ? एक मिनट लगेगा देखने में।",
		"तो मैंने आपके लिए बना दी है — भेज दूँ सर?",
		"देख लीजिए एक बार सर, free है — भेज दूँ?",
		"link भेज देती हूँ, free है, टाइम मिले तो देख लीजिएगा। ठीक है?",
		"सर email ID दे दीजिए, वहाँ भेज देती हूँ?",
		"bhej doon sir WhatsApp pe?",
		"Shall I send it to you on WhatsApp?",
		"Can I send you the link, sir?",
		"Would you like me to send it over?",
		"क्या मैं आपको screenshot या demo link WhatsApp पे भेज दूँ?",
		"देख लीजिए एक बार सर, free है — screenshot भेज दूँ?",
	}
	for _, line := range offers {
		s := offerSession()
		s.noteLinkOffer(line)
		if !s.linkOffered.Load() {
			t.Errorf("this IS an offer to send and must unlock sending: %q", line)
		}
	}
}

// A refusal in Hindi has to latch. Every cue in the original list was English,
// on a campaign that dials India and speaks Hindi — so the guard that stops a
// refused number being messaged was, in practice, switched off.
func TestHindiRefusalLatches(t *testing.T) {
	refusals := []string{
		"नहीं सर, मुझे नहीं चाहिए।",
		"कोई ज़रूरत नहीं है भाई।",
		"रहने दो, मत भेजो।",
		"अभी नहीं, दोबारा मत call करना।",
		"mujhe nahi chahiye",
		"rehne do sir",
		"interested nahi hoon",
	}
	for _, line := range refusals {
		s := offerSession()
		s.noteRefusal(line)
		if !s.refused.Load() {
			t.Errorf("Hindi refusal did not latch, so the link would still be sent: %q", line)
		}
	}
}

// An ordinary warm reply must NOT read as a refusal — a false latch silently
// kills a good lead, which is the more expensive direction to be wrong in.
func TestOrdinaryRepliesDoNotLatchRefusal(t *testing.T) {
	fine := []string{
		"हाँ जी बोलिए",
		"हाँ भेज दीजिए",
		"अच्छा, क्या है ये?",
		"कितने का पड़ेगा सर?",
		"अभी थोड़ा busy हूँ, बाद में बात करते हैं",
		"yes please send it",
	}
	for _, line := range fine {
		s := offerSession()
		s.noteRefusal(line)
		if s.refused.Load() {
			t.Errorf("ordinary reply wrongly latched as a refusal, killing the lead: %q", line)
		}
	}
}

// The playbook must carry the two beats and the rule that separates the two
// kinds of yes. If this drifts, the model has nothing to fall back on when the
// code guard bounces it.
func TestPlaybookRequiresGiftThenAsk(t *testing.T) {
	must := []string{
		"screenshot या demo link WhatsApp पे भेज दूँ?", // the ask, verbatim
		"a screenshot really is sent, as a picture",    // the promise the sidecar must keep
		"बना दी है",                                    // the gift, past tense
		`A "haan" is only permission if the question it answered was`,
		"Beat 1",
		"Beat 2",
	}
	for _, want := range must {
		if !strings.Contains(hindiSystemPrompt, want) {
			t.Errorf("playbook no longer contains %q — the close has drifted", want)
		}
	}
}

// The second greeting. The opener says नमस्ते and is answered; the model then
// opened Stage 1b with "जी नमस्ते सर" because that is what its own template
// showed it. Both the template and the rule must stay fixed.
func TestNoSecondGreeting(t *testing.T) {
	cfg := Config{SystemPrompt: hindiSystemPrompt}
	p := Prospect{Name: "Fairlight Studio", PitchAngle: "No website at all", Instagram: "x"}

	instr := buildCallInstructionHI(cfg, p, true, true)
	if strings.Contains(instr, `\"जी नमस्ते सर, मैं मीरा`) ||
		strings.Contains(instr, "जी नमस्ते सर, मैं मीरा") {
		t.Error("Stage 1b template still greets a second time — this is the double-नमस्ते")
	}
	if !strings.Contains(instr, "DO NOT GREET THEM AGAIN") {
		t.Error("the instruction must forbid the second greeting outright")
	}
	if !strings.Contains(instr, "DO NOT put the website question in this turn") {
		t.Error("Stage 1b must not carry the gap question — that fusion is what made the yes ambiguous")
	}
}

// send_demo_link's own description is the thing the model reads closest to the
// moment it acts. The precondition has to live there too.
func TestSendLinkToolStatesThePrecondition(t *testing.T) {
	var sendDecl map[string]any
	for _, d := range brainToolDecls() {
		if d["name"] == "send_demo_link" {
			sendDecl = d
		}
		if d["name"] == "book_meeting" {
			t.Error("book_meeting is still declared: it is unhandled in session.go, and its description calls itself 'the win', pulling the model away from the WhatsApp close")
		}
	}
	if sendDecl == nil {
		t.Fatal("send_demo_link is no longer declared")
	}
	desc, _ := sendDecl["description"].(string)
	for _, want := range []string{"PRECONDITION", "is NOT permission", "WhatsApp पे भेज दूँ?"} {
		if !strings.Contains(desc, want) {
			t.Errorf("send_demo_link description is missing %q", want)
		}
	}
	params, _ := sendDecl["parameters"].(map[string]any)
	req, _ := params["required"].([]string)
	if len(req) != 1 || req[0] != "channel" {
		t.Errorf("send_demo_link should require only channel (WhatsApp campaign), got %v", req)
	}
}

// The note handed back after the link is queued, on both channels.
//
// Two separate live defects meet here. The probe harness answers this tool too,
// so when its acknowledgement drifted from the live one the rehearsal quietly
// stopped being a rehearsal — hence one function, both callers. And the note
// itself used to quote the exact sentence to say, which the model recited on top
// of the version it had already spoken, telling one prospect twice in a single
// breath that his link was on the way.
func TestQueuedNoteStopsTheRepeat(t *testing.T) {
	for _, ch := range []string{"whatsapp", "email"} {
		note := linkQueuedNote(ch)

		// No sentence to recite. This is the fix that mattered: a conditional
		// above a quoted line does not stop the line being said.
		for _, recitable := range []string{
			"अभी भेज रही हूँ सर", "एक-दो मिनट में आ जाएगा", "on its way, NOT already arrived",
		} {
			if strings.Contains(note, recitable) {
				t.Errorf("%s note hands the model a sentence to recite: %q", ch, recitable)
			}
		}

		// Silence is the default, and the turn ends.
		if !strings.Contains(note, "ALREADY told them") {
			t.Errorf("%s note does not assume it has already announced the send", ch)
		}
		if !strings.Contains(strings.ToUpper(note), "SAY NOTHING MORE") {
			t.Errorf("%s note must make staying quiet the default", ch)
		}
		if !strings.Contains(note, "STOP TALKING and WAIT") {
			t.Errorf("%s note must end the turn", ch)
		}
		if !strings.Contains(note, "NEVER the past") && !strings.Contains(note, "never the past") {
			t.Errorf("%s note must still forbid the past tense", ch)
		}
	}
}
