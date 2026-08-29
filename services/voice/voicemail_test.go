package main

import (
	"strings"
	"testing"
)

// The two failure modes here are not symmetric. Missing a machine costs forty
// seconds of pitching a recording. Matching a LIVE prospect hangs up on a real
// lead mid-sentence — and the ASR feeding these cues is not perfect. So the list
// must not contain anything an ordinary busy shop owner might say.
//
// Every line below is a plausible prospect turn. None may match.
func TestVoicemailCuesDoNotMatchLiveProspects(t *testing.T) {
	prospectLines := []string{
		// The single most valuable non-yes outcome on the whole campaign: a callback.
		"I'm not available right now, can you try me later",
		"yeah I'm not able to take your call, I'm with a customer",
		"my phone was off, what were you saying",
		"just leave me a voicemail and I'll get back to you",
		"my mailbox is full so don't bother emailing",
		"that number's been disconnected, use this one",
		"you'll have to press one for the office, hang on",
		"please try again later, I've got someone here",
		"yeah speaking, this is Miller Roofing",
		"send it to my email and I'll take a look",
		"who is this, what company are you with",
		"I don't need a website, thanks though",
		"we're out of service area for that",
	}

	for _, line := range prospectLines {
		lower := strings.ToLower(line)
		for _, cue := range voicemailCues {
			if strings.Contains(lower, cue) {
				t.Errorf("cue %q matches a live prospect saying %q — this hangs up on a real lead", cue, line)
			}
		}
	}
}

// The recordings are the whole point. Telnyx premium AMD is the real detector
// and these cues are only the backstop for when AMD runs out of analysis time,
// but the backstop still has to catch the unambiguous ones.
func TestVoicemailCuesMatchCarrierAnnouncements(t *testing.T) {
	machineLines := []string{
		"please leave a message after the tone",
		"record your message after the beep",
		"at the sound of the tone please record your message",
		"you have reached the voicemail of the person you are calling",
		"the number you have dialed has been changed",
		"the subscriber you have dialed is not accepting calls",
		"the person you are trying to reach has a voice mailbox that has not been set up yet",
		"this number is no longer in service",
	}

	for _, line := range machineLines {
		lower := strings.ToLower(line)
		matched := false
		for _, cue := range voicemailCues {
			if strings.Contains(lower, cue) {
				matched = true
				break
			}
		}
		if !matched {
			t.Errorf("no cue matches carrier announcement %q — the agent will pitch a recording", line)
		}
	}
}

// A cue short enough to appear inside ordinary speech will eventually match one
// by accident. Length is the cheapest guard against that.
func TestVoicemailCuesAreLongEnoughToBeUnambiguous(t *testing.T) {
	const minCueLen = 12
	for _, cue := range voicemailCues {
		if len([]rune(cue)) < minCueLen {
			t.Errorf("cue %q is only %d characters — too short to be safely unambiguous", cue, len([]rune(cue)))
		}
	}
}

// The refusal list is the other half of the same asymmetry, in the opposite
// direction. A bare "no" must NOT latch it: the agent asks "do you have a
// website?" and "no" there is the best-qualified lead on the list.
func TestRefusalCuesDoNotMatchAQualifyingNo(t *testing.T) {
	notRefusals := []string{
		"no, we don't have one",
		"no I don't think we do actually",
		"no website, no. been meaning to sort that",
		"nope, nothing like that",
	}
	for _, line := range notRefusals {
		lower := strings.ToLower(line)
		for _, cue := range refusalCues {
			if strings.Contains(lower, cue) {
				t.Errorf("cue %q latches a refusal on %q — that is the best-qualified lead on the list", cue, line)
			}
		}
	}
}

// And a real refusal must still latch, because the guard is the only thing
// standing between a "no" and a marketing email.
func TestRefusalCuesMatchRealRefusals(t *testing.T) {
	refusals := []string{
		"yeah no thanks, not interested",
		"we don't need anything like that",
		"take me off your list please",
		"stop calling this number",
		"we're all set, thanks",
	}
	for _, line := range refusals {
		lower := strings.ToLower(line)
		matched := false
		for _, cue := range refusalCues {
			if strings.Contains(lower, cue) {
				matched = true
				break
			}
		}
		if !matched {
			t.Errorf("no cue matches the refusal %q — the agent may pitch on and send anyway", line)
		}
	}
}

// "Take me off your list" is not merely a no, it is an instruction, and it has
// to reach the permanent suppression list rather than just ending this call.
func TestDoNotCallCuesAreASubsetOfRefusals(t *testing.T) {
	for _, dnc := range doNotCallCues {
		found := false
		for _, r := range refusalCues {
			if r == dnc {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("%q suppresses the number but does not latch a refusal — the call would carry on pitching", dnc)
		}
	}
}

// The model fills required arguments in rather than waiting for them. Observed
// in the probe: send_demo_link called with email="(need user-provided email)".
// A placeholder is worse than a blank, because it passes an emptiness check and
// records the lead as delivered.
func TestLooksLikeEmailRejectsWhatTheModelInventsForARequiredField(t *testing.T) {
	bad := []string{
		"", " ", "(need user-provided email)", "their email address",
		"the email they gave", "unknown", "n/a", "mike at millerroofing dot example",
		"mike@", "@example.com", "mike@example", "mike example.com",
		"Mike <mike@example.com>", "mike@example.com, bob@example.com",
	}
	for _, v := range bad {
		if looksLikeEmail(v) {
			t.Errorf("looksLikeEmail(%q) = true — this would be sent marketing, or booked against", v)
		}
	}
	good := []string{
		"mike@millerroofing.example", "MIKE@Miller-Roofing.com",
		"m.sullivan+leads@sub.domain.co.uk", "a@b.co",
	}
	for _, v := range good {
		if !looksLikeEmail(v) {
			t.Errorf("looksLikeEmail(%q) = false — that is a real address the prospect said out loud", v)
		}
	}
}
