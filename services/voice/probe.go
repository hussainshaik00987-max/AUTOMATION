package main

import (
	"bufio"
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

// runProbe rehearses a whole call with no phone, no mic and no stranger on the
// far end. You type the prospect's lines; it prints, verbatim, what the agent
// would say and every tool it would call.
//
// This got strictly better with the move to a text-out brain. Against the old
// audio-only model the agent's words had to be recovered from output
// transcription — an ASR pass over synthetic speech, which is exactly as lossy
// as it sounds, and which rendered one live call's "भेज दो" as "déjà". Now the
// text IS what the agent says. What you read here is what the synthesiser will
// be handed, character for character.
//
// The session is the real one: same instruction, same tools, same model, same
// prospect row out of the campaign CSV.
func runProbe(cfg Config, prospectArg string) {
	p := parseSimProspect(prospectArg)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	fmt.Println("──────────────────────────────────────────────────────────────")
	fmt.Println(" Outbound agent — SCRIPT PROBE (no phone, no mic)")
	fmt.Println(" What the agent says is printed verbatim; tool calls are shown.")
	fmt.Printf(" Prospect : %s — %s\n", orDash(p.Name), orDash(p.City))
	gapHI, gapEN := siteGap(p)
	fmt.Printf(" Gap line : %s\n", orDash(gapHI))
	fmt.Printf("            %s\n", orDash(gapEN))
	fmt.Printf(" Landline : %s  (WhatsApp %s)\n", yesNo(!p.IsMobile()), yesNo(p.CanWhatsApp()))
	fmt.Println("──────────────────────────────────────────────────────────────")
	fmt.Printf(" Opener already spoken (pre-synthesised, plays on pickup):\n   \"%s\"\n", openerLine(p))
	fmt.Println("──────────────────────────────────────────────────────────────")

	// The instruction a live call gets, in the configuration a live call gets it:
	// the personalised opener HAS been played. Rehearsing with personalOpener=false
	// would hand the model this lead's review count and then judge it for using
	// the number the real call deliberately withholds — a harness that rehearses a
	// different agent than the one that gets dialled is worse than no harness.
	// A real Session, purely for its consent state. This harness earns its keep
	// only by rehearsing the agent that actually gets dialled — one that let the
	// model send a link it had never offered would hide the exact defect the
	// guard was written for.
	sess := &Session{log: slog.Default(), prospect: p, startNano: time.Now().UnixNano()}

	b, err := ConnectBrain(ctx, cfg, buildCallInstruction(cfg, p, true, true))
	if err != nil {
		fmt.Printf("could not connect the brain: %v\n", err)
		return
	}
	defer b.Close()

	evCh := make(chan BrainEvent, 32)
	errCh := make(chan error, 1)
	go func() {
		for {
			ev, err := b.ReadEvent()
			if err != nil {
				errCh <- err
				return
			}
			evCh <- ev
		}
	}()

	// No kickoff turn: with the opener already spoken, the live agent is WAITING
	// for the prospect's answer, and so is this. The first thing you type is that
	// answer.
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for {
		fmt.Print("\nPROSPECT > ")
		if !scanner.Scan() {
			break
		}
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if line == "/quit" || line == "/exit" {
			break
		}
		// The prospect has spoken: latch a refusal if that is what it was, and
		// record that they have now had a turn in which to answer any offer.
		sess.noteRefusal(line)
		if sess.linkOffered.Load() {
			sess.offerAnswered.Store(true)
		}
		if err := b.SendText(line); err != nil {
			fmt.Printf("send failed: %v\n", err)
			return
		}
		if ended := drainProbeTurn(sess, b, evCh, errCh); ended {
			fmt.Println("\n[agent called end_call]")
			return
		}
	}
}

// drainProbeTurn consumes one agent turn: prints what it says, prints and
// acknowledges any tool calls so the session keeps going, and returns true on
// end_call.
//
// The tool responses here mirror session.go's exactly. They are prompt, and a
// probe that acknowledges tools more loosely than the live path rehearses a
// different agent than the one that gets dialled — which is the whole failure
// this harness exists to catch.
func drainProbeTurn(sess *Session, b *BrainConn, evCh <-chan BrainEvent, errCh <-chan error) (ended bool) {
	var said strings.Builder
	gotAny := false

	flush := func() {
		if text := strings.TrimSpace(said.String()); text != "" {
			fmt.Printf("\nAGENT: %s\n", text)
			// Exactly what the live path does off the spoken transcript: the offer
			// counts as made once the agent has actually said it.
			sess.noteLinkOffer(text)
			said.Reset()
		}
	}

	for {
		timeout := 30 * time.Second
		if gotAny {
			timeout = 5 * time.Second
		}
		select {
		case err := <-errCh:
			flush()
			fmt.Printf("\n[brain closed: %v]\n", err)
			return true

		case <-time.After(timeout):
			flush()
			if !gotAny {
				fmt.Print("   (no response)\n")
			}
			return ended

		case ev := <-evCh:
			gotAny = true
			switch ev.Kind {
			case BrainTextDelta:
				said.WriteString(ev.Text)

			case BrainToolCalls:
				flush()
				var responses []BrainToolResult
				for _, c := range ev.Calls {
					fmt.Printf("   ⚙  TOOL CALL: %s\n", formatToolCall(c))
					out := map[string]any{"output": "ok"}
					switch c.Name {
					case "send_demo_link":
						// The same guard the live call runs, called from the same
						// function, so this harness can never be the more forgiving of
						// the two.
						if refusal, ok := sess.checkLinkConsent(); !ok {
							fmt.Printf("   ⛔ REFUSED BY GUARD: %v\n", refusal["error"])
							out = refusal
							break
						}
						// Same note the live call gets, from the same function.
						out = map[string]any{"queued": true, "channel": "whatsapp",
							"note": linkQueuedNote("whatsapp")}
					case "capture_lead":
						out = map[string]any{"recorded": true,
							"note": "Name recorded. Say ONE short warm line using it, then STOP TALKING and WAIT for them to speak."}
					case "set_followup":
						out = map[string]any{"recorded": true,
							"note": "Noted. Confirm the day/time back to them warmly in ONE short line, thank them, then call end_call."}
					case "end_call":
						ended = true
					}
					responses = append(responses, BrainToolResult{ID: c.ID, Name: c.Name, Response: out})
				}
				if ended {
					return true
				}
				if err := b.SendToolResponse(responses); err != nil {
					fmt.Printf("\n[tool response failed: %v]\n", err)
				}

			case BrainTurnComplete:
				flush()
				return ended

			case BrainError:
				flush()
				fmt.Printf("   ⚠  brain error: %s\n", ev.Text)
			}
		}
	}
}
