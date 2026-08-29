package main

// ---------------------------------------------------------------------------
// Tools the brain can call.
//
// Two rules govern everything in this file, both of them learned on live calls:
//
// 1. **A tool DESCRIPTION is prompt.** The model reads these more literally than
//    the playbook, because they arrive attached to an action it is about to
//    take. Anything vague here outranks anything careful there.
//
// 2. **A tool RESPONSE is prompt too, and it is the more dangerous one.** See
//    session.go's handleToolCalls: a response that lists steps is a list the
//    model will work through in one breath, without ever yielding the floor. On
//    a live call that produced four tool calls in 4.1 seconds with the prospect
//    silent throughout — the agent asked their name, answered itself, asked when
//    to talk again, answered itself, and hung up on a man still listening.
//
// So every response names ONE thing to say and then requires silence. The step
// after it is still named — a note that ends a turn without naming what follows
// is how the agent came to hang up right after asking someone's name — but it is
// explicitly placed in a LATER turn.
// ---------------------------------------------------------------------------

// brainToolDecls returns the function declarations registered on every session,
// live or harness, so the two can never drift apart.
func brainToolDecls() []map[string]any {
	return []map[string]any{
		{
			"type":        "function",
			"name":        "capture_lead",
			"description": "Record the PERSON's first name the instant they tell you it. Only call this once they have actually said their own name out loud — never before, and never with the business name, which you already have. If they haven't given you a personal name yet, do not call this at all.",
			"parameters": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"name": map[string]any{
						"type":        "string",
						"description": "The human being's first name, exactly as they said it (e.g. \"Mike\", \"Danielle\"). NEVER the business or company name.",
					},
				},
				"required": []string{"name"},
			},
		},
		{
			"type": "function",
			"name": "send_demo_link",
			// This is the win, and it has a PRECONDITION the model gets wrong on
			// its own. On a live call the prospect said "yes" to "आपकी कोई website
			// नहीं दिखी, सही है ना?" — agreeing he had no website — and the agent
			// read that yes as consent, announced it was sending the link, and
			// never once offered the site or asked for it. So the precondition is
			// spelled out here, where the model reads it attached to the action,
			// and enforced again in code (session.go's linkOffered latch) because
			// a description on its own has never held on this call.
			"description": "Send this business their demo website link. This is the GOAL of the call.\n\nPRECONDITION — all three, in this order, or the call is rejected:\n1. You have told them OUT LOUD that you have already built the site FOR THEM, naming their business (\"मैंने आपके [business] के लिए एक demo website बना दी है\").\n2. You have ASKED permission to send it (\"WhatsApp पे भेज दूँ?\").\n3. You have actually HEARD them answer yes, AFTER that question.\n\nA \"haan\" to any OTHER question is NOT permission. Agreeing that they have no website, that you reached the right business, or that they can hear you — none of those are consent to be sent anything, and calling this on one of them is refused. Ask, then stop, then wait for their answer, then call this.\n\nCall it the instant they do agree, BEFORE you say it is on its way. Saying you sent it does not send it; only this does.",
			"parameters": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"channel": map[string]any{
						"type":        "string",
						"description": "How to deliver it. Always \"whatsapp\" unless they specifically asked for email instead.",
						"enum":        []string{"whatsapp", "email"},
					},
					"email": map[string]any{
						"type":        "string",
						"description": "Only when channel is \"email\": the address they said out loud, read back and confirmed with them first. Leave empty for WhatsApp.",
					},
				},
				"required": []string{"channel"},
			},
		},
		{
			"type":        "function",
			"name":        "set_followup",
			"description": "Record when they want to be contacted again. Call this once you have asked for a follow-up time. A rough day is enough (\"next week\", \"Tuesday morning\"); you do not need an exact minute. Call it even when they refuse or stay vague, so the outcome is recorded either way. Call this BEFORE end_call.",
			"parameters": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"when": map[string]any{
						"type":        "string",
						"description": "The day/time they gave, in their own words (e.g. \"Tuesday morning\", \"after the holidays\"). Leave empty only if they gave nothing at all.",
					},
					"kind": map[string]any{
						"type":        "string",
						"description": "What kind of follow-up they agreed to.",
						"enum": []string{
							"meeting",  // committed to a specific day/time
							"callback", // wants a call back but gave no firm time
							"declined", // does not want a follow-up call at all
						},
					},
				},
				"required": []string{"kind"},
			},
		},
		{
			"type":        "function",
			"name":        "end_call",
			"description": "End the call. ALWAYS call this to finish — after your closing line. Never let the call drag on.",
			"parameters": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"disposition": map[string]any{
						"type":        "string",
						"description": "The outcome of the call.",
						"enum": []string{
							"meeting_booked", // they agreed a time and you called book_meeting
							"link_sent",      // they took the link but would not book
							"interested",     // keen, but neither happened
							"callback",       // asked to be called back later — put the time in `callback`
							"not_interested", // a clear no
							"do_not_call",    // hostile, or asked to be removed from the list
							"gatekeeper",     // could not reach the owner / decision-maker
							"wrong_number",   // not this business, or wrong contact
							"voicemail",      // reached a recording, not a person
						},
					},
					"callback": map[string]any{
						"type":        "string",
						"description": "If disposition is 'callback', the time they asked you to call back (e.g. \"Monday morning\"). Otherwise leave empty.",
					},
				},
				"required": []string{"disposition"},
			},
		},
	}
}
