package main

// The call-flow code (main.go, opener.go, session.go, the offline harnesses) is
// shared verbatim with the US build and asks for the playbook by neutral names.
// The Hindi playbook lives in pitch_hi.go under -HI names, and keeps them: its
// tests address it that way, and the suffix is what makes a Hindi string in an
// English file obvious on sight.
//
// So the two meet here rather than in either file. This is the whole language
// binding for the build — swap these four lines and the same machine runs a
// different playbook, which is exactly how the US port started.

func buildCallInstruction(cfg Config, p Prospect, fillerSpoken, personalOpener bool) string {
	return buildCallInstructionHI(cfg, p, fillerSpoken, personalOpener)
}

func fillerLine() string { return fillerLineHI() }

func openerLine(p Prospect) string { return openerLineHI(p) }
