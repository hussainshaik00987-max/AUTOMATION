package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// -report — read a run back.
//
// The point of the event file is that nobody should have to read it. This turns
// one back into the four things worth knowing after a campaign: where the dials
// went, whether the agent spoke to any recordings, and how the conversation
// actually sounded in numbers.
//
//	go run . -report campaignlogs/live2.jsonl
//
// It also runs automatically at the end of every campaign, so the answer to "how
// did that go?" is already on the screen.
// ---------------------------------------------------------------------------

type runReport struct {
	manifest RunManifest
	calls    []CallEvent
}

func readRunFile(path string) (*runReport, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	rep := &runReport{}
	sc := bufio.NewScanner(f)
	// Transcripts make some records long; the default 64KB token limit would
	// silently truncate the run at the first big call.
	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	for sc.Scan() {
		line := sc.Bytes()
		if len(strings.TrimSpace(string(line))) == 0 {
			continue
		}
		var probe struct {
			Type string `json:"type"`
		}
		if err := json.Unmarshal(line, &probe); err != nil {
			continue // a half-written last line after a kill -9 is not a reason to fail
		}
		switch probe.Type {
		case "manifest":
			_ = json.Unmarshal(line, &rep.manifest)
		case "call":
			var c CallEvent
			if json.Unmarshal(line, &c) == nil {
				rep.calls = append(rep.calls, c)
			}
		}
	}
	return rep, sc.Err()
}

func runReportMode(path string) {
	rep, err := readRunFile(path)
	if err != nil {
		fmt.Printf("could not read %s: %v\n", path, err)
		os.Exit(1)
	}
	fmt.Println(rep.Render(true))
}

// Render builds the whole report. withCalls adds the per-call table, which is
// worth having on demand and too long to print at the end of every campaign.
func (r *runReport) Render(withCalls bool) string {
	var b strings.Builder
	m := r.manifest

	fmt.Fprintf(&b, "\n══ CAMPAIGN %s — %d calls · %s\n", m.Run, len(r.calls), m.StartedIST)
	fmt.Fprintf(&b, "   leads=%s  from=%s  model=%s  opener=%v\n",
		m.LeadsFile, m.FromNumber, m.BrainModel, m.Tuning["opener_style"])

	r.renderFunnel(&b)
	r.renderAnswerSupervision(&b)
	r.renderOpener(&b)
	r.renderAMD(&b)
	r.renderConversation(&b)
	if withCalls {
		r.renderCalls(&b)
	}
	return b.String()
}

// A call counts as ANSWERED BY A PERSON only if media frames actually arrived.
// call.hangup fires for a rejected or unanswered call too, and reading that as a
// person picking up and putting the phone down is what made three of nine calls
// in the first live run look like human rejections.
func (c CallEvent) connected() bool { return c.AnsweredMs >= 0 && c.FramesFromTelnyx > 0 }
func (c CallEvent) machine() bool   { return c.AMD == "machine" || c.Disposition == "voicemail" }
func (c CallEvent) human() bool     { return c.connected() && !c.machine() }

// pitchedAMachine is the number this whole opener change exists to move: the
// agent spoke, and answering-machine detection then said the thing it spoke to
// was a recording.
func (c CallEvent) pitchedAMachine() bool {
	return c.machine() && c.OpenerAfterMs >= 0
}

func (r *runReport) renderFunnel(b *strings.Builder) {
	var dialled, noAnswer, machines, humans, quick, refused, promised int
	links := map[string]int{}
	for _, c := range r.calls {
		dialled++
		switch {
		case !c.connected():
			noAnswer++
		case c.machine():
			machines++
		default:
			humans++
			if c.TalkMs >= 0 && c.TalkMs < 10_000 {
				quick++
			}
			if c.Refused || c.Disposition == "not_interested" || c.Disposition == "do_not_call" {
				refused++
			}
			if c.LinkChannel != "" {
				promised++
				links[c.LinkStatus]++
			}
		}
	}
	fmt.Fprintf(b, "\nFUNNEL\n")
	fmt.Fprintf(b, "  dialled                      %3d\n", dialled)
	fmt.Fprintf(b, "  ├─ never connected           %3d   no media frames\n", noAnswer)
	// WHY they never connected is the difference between a list of busy people
	// and a campaign the network is refusing, and those need opposite responses.
	if noAnswer > 0 {
		causes := map[string]int{}
		for _, c := range r.calls {
			if c.connected() {
				continue
			}
			cause := c.HangupCause
			if cause == "" {
				cause = "(not reported)"
			}
			causes[cause]++
		}
		order := make([]string, 0, len(causes))
		for k := range causes {
			order = append(order, k)
		}
		sort.Strings(order)
		for _, k := range order {
			why := hangupExplanation(k)
			if why != "" {
				why = "— " + why
			}
			fmt.Fprintf(b, "  │     %-26s %3d %s\n", k, causes[k], why)
		}
	}
	fmt.Fprintf(b, "  ├─ answering machine         %3d\n", machines)
	fmt.Fprintf(b, "  └─ a person answered         %3d\n", humans)
	fmt.Fprintf(b, "     ├─ gone inside 10s        %3d   they heard the opening and left\n", quick)
	fmt.Fprintf(b, "     ├─ declined               %3d\n", refused)
	fmt.Fprintf(b, "     └─ link promised          %3d   queued %d · duplicate %d · failed %d · suppressed %d\n",
		promised, links["queued"], links["duplicate"], links["failed"], links["suppressed"])
}

// renderAnswerSupervision reports how long the network took to tell us a call
// had been answered, and whether the ring timer is cutting that distribution off.
//
// This section exists because the campaign spent two whole runs unable to see the
// most expensive thing that was happening to it. On the Telnyx→India route the
// answer signal arrives late by a large and variable amount — live2's answered
// calls span 5.5s to 29.2s from the dial, and five of those are answering
// machines, which do not take eleven seconds to pick up. With RingTimeout at 30s
// the top of that distribution sat ONE SECOND inside the cutoff, and the 19 dials
// the same run filed as "rang out" are where everything past it went.
//
// From results.csv all of that looked like an ordinary Indian no-answer rate.
// It was not. Two controlled dials to a phone answered on the first ring came
// back hangup_cause=timeout with no call.answered webhook ever sent. Every one of
// those is a real person holding a silent line, which loses the lead and teaches
// their carrier that this number is worth blocking.
//
// So the number to watch is not the median, it is the WORST sample against the
// ring timer. If they are close, the timer is deciding the campaign's answer rate.
func (r *runReport) renderAnswerSupervision(b *strings.Builder) {
	var ms []int64
	for _, c := range r.calls {
		if c.AnsweredMs >= 0 {
			ms = append(ms, c.AnsweredMs)
		}
	}
	if len(ms) == 0 {
		return
	}
	fmt.Fprintf(b, "\nANSWER SUPERVISION — how long the network took to say \"answered\"\n")
	fmt.Fprintf(b, "  measured from dial:  median %.1fs · p90 %.1fs · worst %.1fs   (n=%d)\n",
		s(percentile(ms, 0.5)), s(percentile(ms, 0.9)), s(percentile(ms, 1)), len(ms))

	ring := ringTimeoutMs(r.manifest)
	if ring <= 0 {
		return
	}
	worst := percentile(ms, 1)
	fmt.Fprintf(b, "  ring timer:          %.1fs\n", s(ring))

	// A distribution whose top sample is within a few seconds of the timer is a
	// distribution being clipped, and the calls it clipped are filed as no-answers.
	if headroom := ring - worst; headroom < 5_000 {
		fmt.Fprintf(b, "  ⚠ only %.1fs of headroom — answers are being CUT OFF by the ring timer.\n", s(headroom))
		fmt.Fprintf(b, "    Calls past it are logged as \"rang out\" and are indistinguishable from\n")
		fmt.Fprintf(b, "    nobody picking up. Raise RING_TIMEOUT; ring time is not billed.\n")
	}

	// answer_lost is the same failure caught red-handed: the far end hung up on a
	// call we were never told had been answered. Only a person can do that.
	var lost int
	for _, c := range r.calls {
		if c.Disposition == "answer_lost" {
			lost++
		}
	}
	if lost > 0 {
		fmt.Fprintf(b, "  ⚠ %d call(s) PICKED UP AND HUNG UP while the network still said ringing.\n", lost)
		fmt.Fprintf(b, "    Those are real people who answered and heard silence. The route, not the list.\n")
	}
}

// ringTimeoutMs reads the ring timer the run actually used out of its own
// manifest, so a report read weeks later compares against the right number
// rather than against whatever the code happens to default to today.
func ringTimeoutMs(m RunManifest) int64 {
	v, ok := m.Tuning["ring_timeout"].(string)
	if !ok {
		return 0
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		return 0
	}
	return d.Milliseconds()
}

func (r *runReport) renderOpener(b *strings.Builder) {
	reasons := map[string]int{}
	var afterMs, audioMs, overlapMs []int64
	var held, overlapped int
	for _, c := range r.calls {
		if !c.connected() {
			continue
		}
		if c.OpenerAfterMs < 0 {
			held++
			reasons["held — never spoke"]++
			continue
		}
		reasons[c.OpenerReason]++
		afterMs = append(afterMs, c.OpenerAfterMs)
		if c.OpenerAudioMs > 0 {
			audioMs = append(audioMs, c.OpenerAudioMs)
		}
		if c.SpokeDuringOpenerMs > 0 {
			overlapped++
			overlapMs = append(overlapMs, c.SpokeDuringOpenerMs)
		}
	}
	order := []string{"speech_settled", "fragment_settled", "timeout", "amd_human",
		"amd_inconclusive", "amd_hold_expired", "held — never spoke"}
	fmt.Fprintf(b, "\nOPENER — which rule decided someone was there\n")
	b.WriteString(histogram(reasons, order, 24))
	if len(afterMs) > 0 {
		fmt.Fprintf(b, "  spoke after pickup:  median %.1fs · p90 %.1fs · worst %.1fs\n",
			s(percentile(afterMs, 0.5)), s(percentile(afterMs, 0.9)), s(percentile(afterMs, 1)))
	}
	if len(audioMs) > 0 {
		fmt.Fprintf(b, "  opening line ran:    median %.1fs · worst %.1fs\n",
			s(percentile(audioMs, 0.5)), s(percentile(audioMs, 1)))
	}
	// The opener plays to completion — barge-in is off for it — so this is the
	// measure of how often, and for how long, it talks over the person it just
	// reached. It is what decides whether a longer opening line is affordable.
	if overlapped > 0 {
		fmt.Fprintf(b, "  talked over them on  %d call(s): median %.1fs · worst %.1fs of their speech during the opener\n",
			overlapped, s(percentile(overlapMs, 0.5)), s(percentile(overlapMs, 1)))
	} else {
		fmt.Fprintf(b, "  talked over them on  0 calls\n")
	}
}

func (r *runReport) renderAMD(b *strings.Builder) {
	verdicts := map[string]int{}
	var lat []int64
	pitched, pitchedMs := 0, []int64{}
	for _, c := range r.calls {
		if !c.connected() {
			continue
		}
		v := c.AMD
		if v == "" {
			v = "no verdict"
		}
		verdicts[v]++
		if c.AMDAfterMs >= 0 {
			lat = append(lat, c.AMDAfterMs)
		}
		if c.pitchedAMachine() {
			pitched++
			if c.AMDAfterMs > c.OpenerAfterMs {
				pitchedMs = append(pitchedMs, c.AMDAfterMs-c.OpenerAfterMs)
			}
		}
	}
	order := make([]string, 0, len(verdicts))
	for k := range verdicts {
		order = append(order, k)
	}
	sort.Strings(order)

	fmt.Fprintf(b, "\nANSWERING-MACHINE DETECTION\n")
	b.WriteString(histogram(verdicts, order, 24))
	if len(lat) > 0 {
		// The opener's hold deadlines are set from this. It is the one number that
		// must be re-read every run rather than trusted from a constant.
		fmt.Fprintf(b, "  verdict arrived:     median %.1fs · p90 %.1fs · worst %.1fs after pickup\n",
			s(percentile(lat, 0.5)), s(percentile(lat, 0.9)), s(percentile(lat, 1)))
	}
	if pitched == 0 {
		fmt.Fprintf(b, "  ✓ spoke to a recording on 0 calls\n")
	} else {
		total := int64(0)
		for _, v := range pitchedMs {
			total += v
		}
		fmt.Fprintf(b, "  ⚠ SPOKE TO A RECORDING on %d call(s) — %.1fs of pitch delivered to voicemail\n",
			pitched, s(total))
	}
}

func (r *runReport) renderConversation(b *strings.Builder) {
	var lat []int64
	var barge, back, fills, resumes, turns int64
	humans := 0
	worstFills, worstFillsOn := int64(0), ""
	for _, c := range r.calls {
		if !c.human() {
			continue
		}
		humans++
		lat = append(lat, c.ReplyLatenciesMs...)
		barge += c.BargeIns
		back += c.BackChannels
		fills += c.ThinkingFills
		resumes += c.ResumePrompts
		turns += int64(c.Turns)
		if c.ThinkingFills > worstFills {
			worstFills, worstFillsOn = c.ThinkingFills, c.Business
		}
	}
	fmt.Fprintf(b, "\nCONVERSATION  (%d calls with a person)\n", humans)
	if len(lat) > 0 {
		fmt.Fprintf(b, "  reply latency:       median %.1fs · p90 %.1fs   (n=%d turns)\n",
			s(percentile(lat, 0.5)), s(percentile(lat, 0.9)), len(lat))
	}
	fmt.Fprintf(b, "  barge-in gate:       %d interruptions allowed · %d back-channels withheld\n", barge, back)
	// All back-channels and no interruptions means the prospect cannot break in
	// at all — which is exactly what a six-second monologue over someone trying
	// eleven times to speak looks like from the outside.
	if barge == 0 && back > 3 {
		fmt.Fprintf(b, "     ⚠ nothing got through the gate — bargeInMinFrames may be too high again\n")
	}
	if humans > 0 {
		fmt.Fprintf(b, "  thinking fillers:    %d over %d calls (%.1f per call, worst %d on %q, %d resume prompts)\n",
			fills, humans, float64(fills)/float64(humans), worstFills, trunc(worstFillsOn, 28), resumes)
		// The average hides this. What made the agent sound like a tic was NINE
		// acknowledgements inside one 87-second call — an audible "जी..." in front
		// of very nearly every sentence — while the two short calls either side of
		// it pulled the mean down to something unremarkable.
		if worstFills > fillerDenseCall {
			fmt.Fprintf(b, "     ⚠ %d on a single call is dense enough to sound like a tic — check thinkingFillerAfterMs\n", worstFills)
		}
		fmt.Fprintf(b, "  transcript turns:    %d total, %.1f per call\n", turns, float64(turns)/float64(humans))
	}
}

func (r *runReport) renderCalls(b *strings.Builder) {
	fmt.Fprintf(b, "\nCALLS\n")
	fmt.Fprintf(b, "  %-3s %-30s %-13s %6s %-17s %-21s %s\n",
		"#", "business", "disposition", "talk", "opener", "amd", "link")
	for _, c := range r.calls {
		talk := "     -"
		if c.TalkMs >= 0 {
			talk = fmt.Sprintf("%5.1fs", s(c.TalkMs))
		}
		opener := "-"
		if c.OpenerAfterMs >= 0 {
			opener = fmt.Sprintf("%s@%.1fs", shortReason(c.OpenerReason), s(c.OpenerAfterMs))
		} else if c.connected() {
			opener = "held (silent)"
		}
		amd := c.AMD
		if amd == "" {
			amd = c.HangupCause // a call that never connected has no AMD, but it has a cause
			if amd == "" {
				amd = "-"
			}
		} else if c.AMDAfterMs >= 0 {
			amd = fmt.Sprintf("%s@%.1fs", amd, s(c.AMDAfterMs))
		}
		link := c.LinkChannel
		if link != "" {
			link += "/" + c.LinkStatus
		}
		flag := " "
		if c.pitchedAMachine() {
			flag = "!"
		}
		fmt.Fprintf(b, " %s%-3d %-30s %-13s %6s %-17s %-21s %s\n",
			flag, c.Seq, trunc(c.Business, 30), c.Disposition, talk, opener, amd, link)
	}
	fmt.Fprintf(b, "\n  ! = the agent spoke and AMD then said it was a recording\n")
	fmt.Fprintf(b, "  transcripts are in the event file itself — one JSON object per call\n")
}

func shortReason(r string) string {
	switch r {
	case "speech_settled":
		return "settled"
	case "fragment_settled":
		return "fragment"
	case "amd_hold_expired":
		return "hold-out"
	case "amd_inconclusive":
		return "amd-?"
	case "amd_human":
		return "amd-ok"
	}
	return r
}

// fillerDenseCall is how many acknowledgements inside ONE call stop reading as
// attentive and start reading as a recording. Nine, measured on a live 87-second
// call, was well past it.
const fillerDenseCall = 6

// s converts milliseconds to seconds for display.
func s(ms int64) float64 { return float64(ms) / 1000 }
