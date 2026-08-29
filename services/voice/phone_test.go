package main

import "testing"

// In the US build this file asserted the opposite rule: that the digits must
// NEVER be consulted, because number portability makes them meaningless and a
// wrong guess is a $500–1,500 TCPA exposure. Neither half of that applies here.
// India has no landline/mobile portability to defeat the prefix, there is no
// carrier lookup on this build to fill LineType, and the decision this drives is
// not a compliance gate — it is whether WhatsApp can physically reach the number.
//
// The cost of getting it wrong is still real, just different: the agent asks
// "WhatsApp पे लिंक भेज दूँ?", the prospect says yes, and the sidecar comes back
// `unreachable`. A promise that cannot be kept is worse than no offer.
func TestCanWhatsAppAcceptsIndianMobiles(t *testing.T) {
	// Ten digits starting 6-9, in every spelling the CSV actually contains.
	for _, n := range []string{
		"+919000000001",
		"919000000001",
		"9000000001",
		"09000000001",     // trunk 0, as routinely written
		"+91 90000 00001", // spaced, as pasted from a listing
		"+919000000002",
		"+918123456789",
	} {
		if !(Prospect{Phone: n}).CanWhatsApp() {
			t.Errorf("%q is an Indian mobile and must be reachable on WhatsApp", n)
		}
	}
}

// Landlines are the case this exists for. 19 of the 2,000 leads are landlines
// and one turned up inside a nine-call test run.
func TestCanWhatsAppRejectsLandlinesAndJunk(t *testing.T) {
	for _, n := range []string{
		"+911126543210", // Delhi 011
		"+914428520000", // Chennai 044
		"+912026543210", // Pune 020
		"+919876543",    // too short
		"+9190000000010",
		"",
	} {
		if (Prospect{Phone: n}).CanWhatsApp() {
			t.Errorf("%q must not be treated as WhatsApp-reachable", n)
		}
	}
}

// IsMobile and CanWhatsApp are the same question on this route, and the call
// flow asks it both ways. They must not drift apart.
func TestIsMobileTracksCanWhatsApp(t *testing.T) {
	for _, n := range []string{"+919000000001", "+911126543210", "9000000001", ""} {
		p := Prospect{Phone: n}
		if p.IsMobile() != p.CanWhatsApp() {
			t.Errorf("%q: IsMobile()=%v but CanWhatsApp()=%v — these must agree",
				n, p.IsMobile(), p.CanWhatsApp())
		}
	}
}
