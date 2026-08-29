package main

import (
	"log/slog"
	"strings"
	"testing"
)

// The pitch hangs on classifying each lead's web presence correctly. Nearly half
// the list has SOMETHING at a website URL, so a blanket "aapki website nahi hai"
// would be a provable lie on hundreds of calls — the fastest way to lose one.
func TestSiteGap(t *testing.T) {
	cases := []struct {
		name    string
		p       Prospect
		wantHI  string // substring the Hindi line must contain
		notWant string // substring it must NOT contain
	}{
		{
			name:   "no website at all",
			p:      Prospect{PitchAngle: "No website at all - they run entirely on DMs/calls. Lead with: clients Google you before booking."},
			wantHI: "कोई website नहीं",
		},
		{
			name:    "instagram as website",
			p:       Prospect{PitchAngle: "Their 'website' is an Instagram page. Lead with: you're renting your portfolio...", Website: "http://www.instagram.com/kmjproductions"},
			wantHI:  "Instagram page",
			notWant: "कोई website नहीं है — Google",
		},
		{
			name:   "facebook as website",
			p:      Prospect{PitchAngle: "Their 'website' is a Facebook page.", Website: "https://facebook.com/x"},
			wantHI: "Facebook page",
		},
		{
			name:   "broken site",
			p:      Prospect{PitchAngle: "Website is down/broken. Lead with: your site is dead.", Website: "http://brokensite.in"},
			wantHI: "खुल ही नहीं रही",
		},
		{
			name:   "abandoned builder",
			p:      Prospect{PitchAngle: "Site is on an abandoned builder (Blogspot/GoDaddy Sites).", Website: "http://x.blogspot.com"},
			wantHI: "पुरानी",
		},
		{
			name:   "link hub",
			p:      Prospect{PitchAngle: "Enquiries run through a link-hub/Google Form.", Website: "https://linktr.ee/x"},
			wantHI: "link page",
		},
		{
			name:   "http only",
			p:      Prospect{PitchAngle: "HTTP only - browsers flag it 'Not Secure'.", Website: "http://x.in"},
			wantHI: "Not Secure",
		},
		{
			name:   "decent site",
			p:      Prospect{PitchAngle: "Site is decent - pitch WhatsApp automation / booking flow instead of a rebuild.", Website: "https://good.in"},
			wantHI: "booking",
		},
		{
			// The single most important case: when we don't know, we must ASK, never
			// assert a gap we can't back up. 264 leads land here.
			name:   "unknown status asks instead of asserting",
			p:      Prospect{PitchAngle: "Website status unknown (listing source doesn't report it) - check before calling."},
			wantHI: "पूछनी थी",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			hi, en := siteGap(tc.p)
			if !strings.Contains(hi, tc.wantHI) {
				t.Errorf("Hindi gap = %q, want it to contain %q", hi, tc.wantHI)
			}
			if tc.notWant != "" && strings.Contains(hi, tc.notWant) {
				t.Errorf("Hindi gap = %q, must NOT contain %q", hi, tc.notWant)
			}
			if en == "" {
				t.Error("English fallback is empty — a language switch would leave the agent with nothing to say")
			}
		})
	}
}

func TestCleanPersonName(t *testing.T) {
	cases := []struct {
		got, business, want string
	}{
		{"Rakesh", "Meridian Studios", "Rakesh"},
		{"rakesh sharma", "Meridian Studios", "Rakesh"},
		{"  Priya  ", "Nova Interiors", "Priya"},
		// The observed live failure: the business name handed back as the person's.
		{"Meridian Studios", "Meridian Studios", ""},
		{"Meridian", "Meridian Studios", ""},
		{"Productions", "Meridian Studios", ""},
		{"Lumen", "Lumen Picture Works", ""},
		// Honorifics and filler the model sometimes passes instead of a name.
		{"sir", "Meridian Studios", ""},
		{"ji", "Meridian Studios", ""},
		{"owner", "Meridian Studios", ""},
		{"unknown", "Meridian Studios", ""},
		{"", "Meridian Studios", ""},
		{"A", "Meridian Studios", ""},
		{"Bhupinderjeetsinghwala", "Meridian Studios", ""}, // 22 chars — not a first name
	}

	for _, tc := range cases {
		if got := cleanPersonName(tc.got, tc.business); got != tc.want {
			t.Errorf("cleanPersonName(%q, %q) = %q, want %q", tc.got, tc.business, got, tc.want)
		}
	}
}

// Indian numbers arrive in several shapes and a wrong country code means every
// call in the campaign dials a US number that doesn't exist.
func TestNormalizePhoneIndia(t *testing.T) {
	defaultRegion = "IN"
	cases := []struct {
		in      string
		want    string
		wantErr bool
	}{
		{"+91 90000 00002", "+919000000002", false},
		{"+919000000003", "+919000000003", false},
		{"9876543210", "+919876543210", false},
		{"09876543210", "+919876543210", false},
		{"919876543210", "+919876543210", false},
		{"98765 43210", "+919876543210", false},
		{"+1 910 317 2041", "+19103172041", false}, // explicit country code is trusted
		{"1234567890", "", true},                   // 10 digits starting 1 — not an Indian mobile
		{"12345", "", true},
		{"", "", true},
	}
	for _, tc := range cases {
		got, err := normalizePhone(tc.in)
		if tc.wantErr {
			if err == nil {
				t.Errorf("normalizePhone(%q) = %q, want an error", tc.in, got)
			}
			continue
		}
		if err != nil {
			t.Errorf("normalizePhone(%q) errored: %v", tc.in, err)
			continue
		}
		if got != tc.want {
			t.Errorf("normalizePhone(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// The scraper writes integers as floats. Left alone, "474.0" is read aloud as
// "four hundred seventy four point zero" — an instant robocall tell.
func TestTrimFloat(t *testing.T) {
	cases := map[string]string{
		"474.0": "474", "15312.0": "15312", "4.8": "4.8", "": "", " 340.0 ": "340",
	}
	for in, want := range cases {
		if got := trimFloat(in); got != want {
			t.Errorf("trimFloat(%q) = %q, want %q", in, got, want)
		}
	}
}

// The research block is the model's only source of facts. If a field is missing
// it must be absent entirely — a blank or zero value invites the model to
// improvise, and an invented review count is the fastest way to get caught.
func TestProspectResearchOmitsMissingFacts(t *testing.T) {
	sparse := Prospect{Name: "Test Studio", PitchAngle: "No website at all"}
	out := prospectResearchHI(sparse, false)

	for _, banned := range []string{"Google:", "Instagram followers", "Location:", "price band"} {
		if strings.Contains(out, banned) {
			t.Errorf("research names %q for a lead with no such data:\n%s", banned, out)
		}
	}
	if !strings.Contains(out, "Test Studio") {
		t.Error("research must always carry the business name — it's the first thing said on the call")
	}

	full := Prospect{
		Name: "Meridian Studios", City: "Guwahati", Area: "Santipur",
		Rating: "4.8", TotalReviews: "474", IGFollowers: "15312",
		Vertical: "wedding_photo", PitchAngle: "Their 'website' is an Instagram page.",
		DemoURL: "https://example.test/x",
	}
	out = prospectResearchHI(full, false)
	for _, want := range []string{"Meridian Studios", "Guwahati", "Instagram page"} {
		if !strings.Contains(out, want) {
			t.Errorf("research is missing %q:\n%s", want, out)
		}
	}
	// The figures must NOT be there. A live prospect heard this agent open with
	// "4.8 rating है, आप Instagram पर भी 15312 followers रखते हैं" and said it
	// sounded like it had his file open. The model cannot recite what it was
	// never given, which is why this is a test and not a line in the prompt.
	for _, forbidden := range []string{"4.8", "474", "15312"} {
		if strings.Contains(out, forbidden) {
			t.Errorf("research leaks the figure %q — it must never reach the model:\n%s", forbidden, out)
		}
	}
}

// The opener is pre-synthesised and played before the model says anything, so
// the model must be told Stage 1 is already done — otherwise it introduces
// itself a second time, which sounds exactly like a broken robocall.
func TestPersonalOpenerSuppressesRepeatIntro(t *testing.T) {
	cfg := Config{SystemPrompt: "PLAYBOOK"}
	p := Prospect{Name: "Meridian Studios", PitchAngle: "No website at all"}

	personal := buildCallInstructionHI(cfg, p, true, true)
	// The opener asks about their business and nothing else, so the model still
	// owes the introduction (Stage 1b). Telling it the intro was already given —
	// as an earlier version did — leaves the prospect never learning who called.
	if !strings.Contains(personal, "the introduction is still owed") {
		t.Error("personalised opener must tell the model it still owes the introduction")
	}
	if !strings.Contains(personal, "Do NOT repeat that question") {
		t.Error("personalised opener must stop the model re-asking the business question")
	}
	if !strings.Contains(personal, openerLineHI(p)) {
		t.Error("personalised opener must quote the exact line that was already spoken")
	}

	generic := buildCallInstructionHI(cfg, p, true, false)
	if strings.Contains(generic, "the introduction is still owed") {
		t.Error("generic filler owes the WHOLE of Stage 1, not just the introduction")
	}
	if !strings.Contains(generic, fillerLineHI()) {
		t.Error("generic opener must quote the filler line that was spoken")
	}
}

// Stage 7 is the campaign's callback list. The model does not always pass a
// clean `kind`, and a follow-up we can't classify is still the only next step
// the call produced — it must never be dropped on the floor.
func TestSetFollowupNormalisesKind(t *testing.T) {
	cases := []struct {
		gotKind, gotWhen   string
		wantKind, wantWhen string
	}{
		{"meeting", "kal shaam 6 baje", "meeting", "kal shaam 6 baje"},
		{"callback", "baad me", "callback", "baad me"},
		{"declined", "", "declined", ""},
		{"MEETING", "kal", "callback", "kal"},               // wrong case → not a valid enum value
		{"appointment", "tomorrow", "callback", "tomorrow"}, // invented kind
		{"", "Sunday ko", "callback", "Sunday ko"},          // omitted entirely
		{"meeting", "  kal subah  ", "meeting", "kal subah"},
	}

	for _, tc := range cases {
		s := &Session{log: slog.Default()}
		args := map[string]any{"kind": tc.gotKind, "when": tc.gotWhen}
		var endPending bool
		s.applyFollowup(args)

		if s.followupKind != tc.wantKind {
			t.Errorf("kind %q → %q, want %q", tc.gotKind, s.followupKind, tc.wantKind)
		}
		if s.followupWhen != tc.wantWhen {
			t.Errorf("when %q → %q, want %q", tc.gotWhen, s.followupWhen, tc.wantWhen)
		}
		_ = endPending
	}
}

// The Hindi build used to hand the model every figure it had — rating, review
// count, follower count — and take exactly ONE of them back if the opener had
// already said it out loud. That was not enough. On a live validation call the
// agent still opened with "Google पर आपकी 4.8 rating बहुत अच्छी है, आप Instagram
// पर भी 15312 followers रखते हैं, सही ना?", and the person on the other end said
// it sounded like a stranger reading his file back at him.
//
// So the rule is no longer "don't repeat the spent one". It is: NO NUMBER ABOUT
// THE BUSINESS EVER REACHES THE MODEL. What it cannot see it cannot recite —
// the same reasoning that moved the refusal guard out of the prompt and into
// code. This test is the guard.
func TestResearchCarriesNoNumbersAboutTheBusiness(t *testing.T) {
	p := Prospect{
		Name: "Fairlight Studio", City: "Delhi", Rating: "4.8", TotalReviews: "474",
		IGFollowers: "15312", Price: "25000", Instagram: "https://instagram.com/sharmastudio",
		PitchAngle: "No website at all",
	}

	for _, spent := range []bool{true, false} {
		out := prospectResearchHI(p, spent)
		for _, forbidden := range []string{"4.8", "474", "15312", "25000"} {
			if strings.Contains(out, forbidden) {
				t.Errorf("openerSpentPraise=%v: research leaks %q:\n%s", spent, forbidden, out)
			}
		}
		// What it SHOULD carry instead: which page they are on, not how big it is.
		if !strings.Contains(out, "INSTAGRAM") {
			t.Errorf("openerSpentPraise=%v: research must name the platform they run on:\n%s", spent, out)
		}
	}

	// A lead with only a Facebook page is told Facebook, not Instagram.
	fb := Prospect{Name: "Cake Corner", Facebook: "https://facebook.com/cakecorner", PitchAngle: "No website at all"}
	out := prospectResearchHI(fb, false)
	if !strings.Contains(out, "FACEBOOK") || strings.Contains(out, "INSTAGRAM") {
		t.Errorf("a Facebook-only lead must be described as Facebook:\n%s", out)
	}
}
