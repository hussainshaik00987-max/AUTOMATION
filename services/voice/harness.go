package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// ---------------------------------------------------------------------------
// Shared harness helpers — used by -probe, -local and -bench.
//
// These deliberately pull a REAL row out of the campaign CSV rather than
// accepting a hand-written stub. The whole pitch hangs on per-lead fields
// (Pitch Angle, Vertical, Demo Site, review counts) and a stub would silently
// rehearse a different script than the one that actually gets dialled.
// ---------------------------------------------------------------------------

// formatToolCall renders a tool call for the harness transcripts.
func formatToolCall(c BrainToolCall) string {
	if len(c.Args) == 0 {
		return c.Name + "()"
	}
	keys := make([]string, 0, len(c.Args))
	for k := range c.Args {
		keys = append(keys, k)
	}
	sortStrings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s=%v", k, c.Args[k]))
	}
	return fmt.Sprintf("%s(%s)", c.Name, strings.Join(parts, ", "))
}

// sortStrings keeps harness output stable between runs, so two probe
// transcripts can actually be diffed against each other.
func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

// parseSimProspect builds the prospect the harnesses rehearse against.
//
// The most useful form is a real lead pulled straight out of the campaign CSV —
// "#12" or a substring of the business name — because the whole pitch hangs on
// per-lead fields (Pitch Angle, Vertical, Demo Site, IG followers) that a
// hand-written stub would not carry, and a stub would silently rehearse a
// different script than the one that actually gets dialled.
//
// Accepted:
//
//	""              → the first lead in PROSPECTS_FILE
//	"#12"           → the 12th lead
//	"kmj"           → first lead whose business name contains "kmj"
//	"Name|City|Rating|Reviews" → a manual stub (legacy form)
func parseSimProspect(arg string) Prospect {
	arg = strings.TrimSpace(arg)

	// The manual stub form is the only one containing a pipe.
	if strings.Contains(arg, "|") {
		p := Prospect{Name: "ABC Studio", Phone: "+910000000000", Rating: "4.8", TotalReviews: "342", City: "Delhi"}
		f := strings.Split(arg, "|")
		set := func(i int, dst *string) {
			if i < len(f) && strings.TrimSpace(f[i]) != "" {
				*dst = strings.TrimSpace(f[i])
			}
		}
		set(0, &p.Name)
		set(1, &p.City)
		set(2, &p.Rating)
		set(3, &p.TotalReviews)
		return p
	}

	file := os.Getenv("PROSPECTS_FILE")
	if file == "" {
		file = "top_leads.csv"
	}
	leads, err := ReadProspects(file)
	if err != nil || len(leads) == 0 {
		fmt.Printf("could not read %s (%v) — falling back to a stub prospect\n", file, err)
		return Prospect{Name: "ABC Studio", Phone: "+910000000000", Rating: "4.8", TotalReviews: "342", City: "Delhi", Vertical: "wedding_photo"}
	}

	if arg == "" {
		return leads[0]
	}
	if n, convErr := strconv.Atoi(strings.TrimPrefix(arg, "#")); convErr == nil && n >= 1 && n <= len(leads) {
		return leads[n-1]
	}
	needle := strings.ToLower(arg)
	for _, l := range leads {
		if strings.Contains(strings.ToLower(l.Name), needle) {
			return l
		}
	}
	fmt.Printf("no lead in %s matches %q — using the first one\n", file, arg)
	return leads[0]
}

func orDash(s string) string {
	if s == "" {
		return "—"
	}
	return s
}

func yesNo(b bool) string {
	if b {
		return "yes"
	}
	return "no"
}
