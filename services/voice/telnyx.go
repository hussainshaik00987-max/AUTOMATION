package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"sync/atomic"
)

// ---------------------------------------------------------------------------
// Telnyx webhook event types (same shape as inbound)
// ---------------------------------------------------------------------------

type telnyxWebhook struct {
	Data struct {
		EventType string          `json:"event_type"`
		Payload   json.RawMessage `json:"payload"`
	} `json:"data"`
}

type telnyxCallPayload struct {
	CallControlID string `json:"call_control_id"`
	CallLegID     string `json:"call_leg_id"`
	CallSessionID string `json:"call_session_id"`
	From          string `json:"from"`
	To            string `json:"to"`
	Direction     string `json:"direction"`
	ClientState   string `json:"client_state"`

	// Why the call ended, straight from the carrier. Telnyx sends these on
	// call.hangup and we used to drop them — which is why "three of nine dials
	// never connected" was, for a whole campaign, a fact with no explanation
	// attached. A number that rang out, a handset that rejected the call and a
	// carrier that blocked it as spam are three completely different problems
	// and they are indistinguishable without this.
	HangupCause    string `json:"hangup_cause"`
	HangupSource   string `json:"hangup_source"`
	SIPHangupCause string `json:"sip_hangup_cause"`
}

// ---------------------------------------------------------------------------
// Telnyx WebSocket message types (media streaming)
// ---------------------------------------------------------------------------

type telnyxWSMsg struct {
	Event    string         `json:"event"`
	StreamID string         `json:"stream_id,omitempty"`
	Start    *telnyxWSStart `json:"start,omitempty"`
	Media    *telnyxWSMedia `json:"media,omitempty"`
}

type telnyxWSStart struct {
	StreamID      string `json:"stream_id"`
	CallControlID string `json:"call_control_id"`
	ClientState   string `json:"client_state"`
	MediaFormat   struct {
		Encoding   string `json:"encoding"`
		SampleRate int    `json:"sample_rate"`
		Channels   int    `json:"channels"`
	} `json:"media_format"`
}

type telnyxWSMedia struct {
	Track   string `json:"track,omitempty"`
	Chunk   string `json:"chunk,omitempty"`
	Payload string `json:"payload"`
}

// ---------------------------------------------------------------------------
// Webhook handler
// ---------------------------------------------------------------------------

func HandleWebhook(cfg Config, sessions *SessionRegistry) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}

		var evt telnyxWebhook
		if err := json.Unmarshal(body, &evt); err != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}

		var payload telnyxCallPayload
		json.Unmarshal(evt.Data.Payload, &payload)

		log := slog.With("event", evt.Data.EventType, "call_control_id", payload.CallControlID)

		switch evt.Data.EventType {
		case "call.initiated":
			// Outbound: this is just an ack that the dial was placed. Nothing to do.
			log.Info("call initiated", "from", payload.From, "to", payload.To, "direction", payload.Direction)

		case "call.answered":
			log.Info("call answered (prospect picked up)")
			// Everything before this point was ringing. Release the session so the
			// opener can be delivered and inbound audio can reach the model — with
			// dial-time streaming the socket has been open, and carrying ringback
			// tone, for several seconds already.
			if s := sessions.Get(payload.CallControlID); s != nil {
				s.markAnswered()
			}
			// We initiated this call already; the session was created at dial time.
			// Start recording the whole call (one mixed-mono track), then media streaming.
			if cfg.RecordEnabled {
				go func() {
					if err := telnyxRecordStart(cfg, payload.CallControlID); err != nil {
						log.Warn("record_start failed", "error", err)
					}
				}()
			}
			// With StreamBeforeDial the socket is already open; asking again would
			// start a second stream onto the same URL. If Telnyx refused those dial
			// parameters, though, nothing has been started and this path still owns it.
			if !cfg.StreamBeforeDial || streamBeforeDialBroken.Load() {
				go func() {
					if err := telnyxStreamingStart(cfg, payload.CallControlID); err != nil {
						log.Error("streaming_start failed", "error", err)
						if s := sessions.Get(payload.CallControlID); s != nil {
							s.Finish("agent")
						}
					}
				}()
			}

		case "call.hangup":
			s := sessions.Get(payload.CallControlID)
			// A call the far end cleared while we were still being told it was
			// ringing did not ring out — somebody picked it up and heard nothing.
			// Say so on the line an operator is actually watching, because the
			// carrier's own cause for it is "normal_clearing", which reads as a
			// completed conversation.
			explanation := hangupExplanation(payload.HangupCause)
			if s != nil && !s.answered() && payload.HangupSource == "callee" {
				explanation = "the far end HUNG UP on a call the network never told us was answered — they picked up and heard silence"
			}
			log.Info("call hangup", "cause", payload.HangupCause, "source", payload.HangupSource,
				"sip_cause", payload.SIPHangupCause, "explanation", explanation)
			if s != nil {
				s.hangupCause.Store(payload.HangupCause)
				s.hangupSource.Store(payload.HangupSource)
				// If the session hasn't already finished (e.g. because we hung up),
				// mark the prospect as the hangup source.
				s.Finish("prospect")
			}

		case "call.machine.detection.ended", "call.machine.premium.detection.ended":
			var amd struct {
				Result string `json:"result"`
			}
			json.Unmarshal(evt.Data.Payload, &amd)
			result := strings.ToLower(strings.TrimSpace(amd.Result))
			log.Info("answering-machine detection", "result", result)
			s := sessions.Get(payload.CallControlID)
			switch {
			case strings.HasPrefix(result, "human"):
				// Human answered (basic: "human"; premium: "human_residence" /
				// "human_business") → release the listen-first opener.
				if s != nil {
					s.onAMDHuman(result)
				}
			case result == "machine" || strings.HasPrefix(result, "fax"):
				// AMD is a heuristic, and this is the one case it reliably gets
				// wrong: a person saying "हैलो? ... हैलो?" into silence sounds
				// exactly like a recorded greeting. When the opening gate has
				// already identified a human from the SHAPE of what it heard,
				// that outranks AMD — hanging up here drops a live prospect
				// mid-sentence, which is the most expensive mistake on the call.
				if s != nil && (s.looksHuman() || !amdMachineHangup) {
					s.humanEvidence.Store(true)
					log.Warn("AMD says machine — NOT hanging up (human shape, or AMD_MACHINE_HANGUP=false)",
						"hangup_disabled", !amdMachineHangup,
						"looks_human", s.looksHuman(),
						"amd", result, "opener_already_out", s.openerPlayed.Load(),
						"utterances", s.preOpenerUtterances.Load(),
						"spoke_ms", s.speechMillis.Load(),
						"longest_ms", s.longestUtteranceMs.Load())
					// The raw label is what lands in results.csv and the run report,
					// so it has to say which of the two reasons actually applied.
					if s.looksHuman() {
						s.noteAMD(amdHuman, "machine_overridden_by_human_shape")
					} else {
						s.noteAMD(amdHuman, "machine_overridden_by_config")
					}
					break
				}
				if s != nil {
					log.Info("voicemail/machine detected — hanging up and logging voicemail",
						"amd", result,
						"utterances", s.preOpenerUtterances.Load(),
						"spoke_ms", s.speechMillis.Load(),
						"longest_ms", s.longestUtteranceMs.Load(),
						"opener_already_out", s.openerPlayed.Load())
				} else {
					log.Info("voicemail/machine detected — hanging up and logging voicemail", "amd", result)
				}
				if s != nil {
					s.onAMDMachine()
					go telnyxHangup(cfg, payload.CallControlID)
					s.Finish("voicemail")
				} else {
					go telnyxHangup(cfg, payload.CallControlID)
				}
			default:
				// "not_sure" / "silence" — Telnyx ran out of analysis time and has
				// nothing for us. That is NOT a machine: hanging up here would drop
				// real people, and holding the opener any longer would leave them in
				// silence waiting on an answer that is never coming. So it RELEASES
				// the listen-first hold instead of extending it.
				log.Info("AMD inconclusive — releasing the opener hold", "result", result)
				if s != nil {
					s.onAMDInconclusive(result)
				}
			}

		case "call.recording.saved":
			log.Info("recording saved — downloading")
			go handleRecordingSaved(cfg, sessions, evt.Data.Payload)

		case "streaming.started":
			log.Info("streaming started")

		case "streaming.stopped":
			log.Info("streaming stopped")

		default:
			log.Debug("unhandled event")
		}

		w.WriteHeader(http.StatusOK)
	}
}

// hangupExplanation turns Telnyx's cause code into the thing you actually want
// to know, which is whose fault it was and whether it is worth re-dialling.
//
// The distinction that matters most on this campaign is between a phone that
// rang unanswered and a call the network refused. From our side both produce an
// identical record — no answer webhook, no media, no conversation — but one is
// a person who was busy and the other is the campaign being blocked.
func hangupExplanation(cause string) string {
	switch cause {
	case "":
		return ""
	case "normal_clearing":
		return "the call ended normally"
	case "originator_cancel":
		return "WE hung up — usually the ring timeout expiring before they answered"
	case "timeout", "no_answer":
		return "it rang out unanswered"
	case "user_busy":
		return "their line was busy"
	case "call_rejected":
		return "the network or the handset REFUSED the call — on an Indian mobile reached from a US number this is what carrier spam filtering looks like"
	case "unallocated_number", "invalid_number_format", "not_found":
		return "the number does not exist"
	case "normal_unspecified", "network_out_of_order", "temporary_failure", "recovery_on_timer_expire":
		return "the carrier failed the call"
	}
	return "unrecognised cause — check Telnyx's hangup cause reference"
}

// ---------------------------------------------------------------------------
// Telnyx Call Control API
// ---------------------------------------------------------------------------

const telnyxAPI = "https://api.telnyx.com/v2"

// telnyxDial places an outbound call. Returns call_control_id.
func telnyxDial(cfg Config, to string) (string, error) {
	url := telnyxAPI + "/calls"
	body := map[string]any{
		"connection_id": cfg.TelnyxConnectionID,
		"to":            to,
		"from":          cfg.FromNumber,
		// Answering-machine detection: Telnyx classifies human vs voicemail and
		// fires call.machine.premium.detection.ended with the result. On "machine"
		// we hang up and log a voicemail (see HandleWebhook).
		"answering_machine_detection": "premium",
		// How long it may ring. Stated rather than defaulted — see Config.RingTimeout.
		"timeout_secs": int(cfg.RingTimeout.Seconds()),
	}

	// Where Telnyx anchors the MEDIA for this call.
	//
	// This is the single biggest latency lever that is not in our code, because
	// every turn of audio traverses it twice. The default, "Latency", picks by RTT
	// from Telnyx to our connection — which is the right heuristic when the server
	// and the callee are on the same side of the world, and the wrong one when
	// they are not. The India build measured the cost of a badly-placed anchor
	// directly: reply latency median 2.6s on the wire against 1.0s on the same box
	// offline, roughly 1.6s per turn spent on a San Jose detour between an Indian
	// handset and a Mumbai server.
	//
	// Left empty, Telnyx chooses. Set it when you know better than the heuristic —
	// e.g. "Amsterdam, Netherlands" for a European server, which puts the anchor
	// one short hop from the box instead of across an ocean.
	if cfg.AnchorSite != "" {
		body["anchorsite_override"] = cfg.AnchorSite
	}
	if cfg.WebhookURL != "" {
		body["webhook_url"] = cfg.WebhookURL
	}

	// Ask for the media stream at DIAL time rather than on answer.
	//
	// The old sequence cost a full second of the prospect's attention at the worst
	// possible moment: they pick up, Telnyx posts call.answered to us in Mumbai, we
	// POST streaming_start back to Telnyx in the US, and only then does the media
	// socket open. Measured on a live call that was 1.0s of pure silence after
	// "hello" before we could physically send anything.
	//
	// stream_establish_before_call_originate opens the socket while the phone is
	// still ringing, so the instant they answer we are already able to speak. The
	// cost is that inbound frames now start arriving during ringing — ringback
	// tone, not a human — which is why Session gates everything on call.answered.
	if cfg.StreamBeforeDial {
		for k, v := range telnyxStreamParams(cfg) {
			body[k] = v
		}
		body["stream_establish_before_call_originate"] = true
	}

	id, status, respBody, err := telnyxPostDial(cfg, url, body)
	if err == nil {
		return id, nil
	}

	// Dial-time streaming is an optimisation, never a reason a campaign cannot
	// call anyone. If Telnyx rejects the request as malformed — an account without
	// the feature, a parameter renamed under us — drop the streaming fields and
	// dial the old way, which still works because call.answered starts the stream.
	// Without this fallback one unsupported parameter would fail all 2,000 leads
	// identically and look like an outage.
	// ...but ONLY when the rejection could plausibly BE the parameters. A D-series
	// telnyx_error code is a verdict about the CALL — no outbound profile, channel
	// limit, daily/hourly cap, forbidden prefix — and no combination of optional
	// fields will change it. Retrying one anyway is not merely futile: each POST
	// is counted as an outbound call attempt, so the ladder spends THREE of the
	// account's allowance per lead. On an L1 account, capped at 10 outbound calls
	// per hour, that is three leads' worth of budget burned on one refusal.
	//
	// It also lies twice on the way down. A D7 refusal was logged as "telnyx
	// rejected dial-time streaming", then as "telnyx rejected the anchorsite",
	// before the real reason appeared — two plausible, specific, wrong diagnoses
	// ahead of the right one.
	if code := telnyxErrorCode(respBody); code != "" {
		return "", fmt.Errorf("telnyx refused the call: %s — %s (not retried: this is a verdict about the call, "+
			"not the dial parameters; see https://developers.telnyx.com/docs/voice/sip-trunking/troubleshooting/response-codes)",
			code, telnyxErrorMeaning(code))
	}

	if cfg.StreamBeforeDial && status >= 400 && status < 500 {
		slog.Warn("telnyx rejected dial-time streaming — falling back to streaming on answer (costs ~1s of silence per call)",
			"status", status, "response", strings.TrimSpace(string(respBody)),
			"fix", "set STREAM_BEFORE_DIAL=false to silence this, or check the account's streaming features")
		streamBeforeDialBroken.Store(true)
		for k := range telnyxStreamParams(cfg) {
			delete(body, k)
		}
		delete(body, "stream_establish_before_call_originate")
		id, status, respBody, err = telnyxPostDial(cfg, url, body)
		if err == nil {
			return id, nil
		}
		if code := telnyxErrorCode(respBody); code != "" {
			return "", fmt.Errorf("telnyx refused the call: %s — %s (not retried)", code, telnyxErrorMeaning(code))
		}
	}

	// Same reasoning for the anchorsite. It is a latency optimisation and Telnyx
	// gates some sites to particular connection types — "Chennai, IN" answers
	// "not allowed for call control applications" rather than "unknown value". A
	// site that turns out to be unavailable on this account must cost a slightly
	// longer media path, not every call on the list.
	if cfg.AnchorSite != "" && status >= 400 && status < 500 {
		slog.Warn("telnyx rejected the anchorsite — dialling without it, media may take a longer path",
			"anchorsite", cfg.AnchorSite, "status", status,
			"response", strings.TrimSpace(string(respBody)))
		delete(body, "anchorsite_override")
		id, _, _, err = telnyxPostDial(cfg, url, body)
		if err == nil {
			return id, nil
		}
	}
	return "", err
}

// streamBeforeDialBroken latches once Telnyx has refused dial-time streaming, so
// the on-answer path knows it still owns starting the stream.
var streamBeforeDialBroken atomic.Bool

// telnyxErrorCode pulls the D-series code out of a rejected dial.
//
// Telnyx answers a refused call with an HTTP 403 whose generic code is always
// 10010 and whose "detail" is a fixed, frequently WRONG sentence — a D7 arrives
// described as "Daily OB profile limit exceeded ... Daily outbound concurrent
// call limit has been reached", naming two limits, neither of which was the
// cause. The machine-readable telnyx_error.error_code is the part that is true.
// Read that, and nothing else.
func telnyxErrorCode(respBody []byte) string {
	var r struct {
		TelnyxError struct {
			ErrorCode string `json:"error_code"`
		} `json:"telnyx_error"`
	}
	if json.Unmarshal(respBody, &r) != nil {
		return ""
	}
	c := r.TelnyxError.ErrorCode
	if len(c) < 2 || c[0] != 'D' {
		return ""
	}
	for _, ch := range c[1:] {
		if ch < '0' || ch > '9' {
			return ""
		}
	}
	return c
}

// telnyxErrorMeaning translates the codes worth recognising on sight, from
// Telnyx's own catalogue. The point is to put the real cause in the ERROR line
// where the operator reads it, instead of leaving a bare "403" that reads like
// an outage and costs an hour to trace back to a setting.
func telnyxErrorMeaning(code string) string {
	switch code {
	case "D1":
		return "concurrent outbound calls exceed the account's global limit"
	case "D2":
		return "outbound voice profile concurrent call limit exceeded"
	case "D3":
		return "connection concurrent call limit exceeded"
	case "D7", "D38":
		return "no outbound voice profile resolved for this call — check that FROM_NUMBER is assigned to the connection, " +
			"and that the account has not hit its daily/hourly outbound call allowance (L1 accounts: 100/day, 10/hour)"
	case "D9":
		return "international destination rate limit reached"
	case "D19":
		return "account blocked for billing — check balance and payment method"
	case "D22":
		return "channel limit exceeded (DID, connection, or profile)"
	case "D24":
		return "rate per minute exceeds the profile maximum"
	case "D35":
		return "caller ID is not valid E.164"
	case "D36":
		return "caller ID is a DID belonging to another account"
	case "D41":
		return "dialled prefix forbidden by fraud prevention — contact Telnyx to enable the destination"
	case "D51":
		return "non-Telnyx caller ID is not verified"
	}
	return "see Telnyx SIP response codes for " + code
}

// telnyxPostDial performs one dial attempt and reports the status code so the
// caller can tell "Telnyx said no" from "the network said no".
func telnyxPostDial(cfg Config, url string, body map[string]any) (callControlID string, status int, respBody []byte, err error) {
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", url, bytes.NewReader(b))
	req.Header.Set("Authorization", "Bearer "+cfg.TelnyxAPIKey)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", 0, nil, fmt.Errorf("telnyx dial: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ = io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return "", resp.StatusCode, respBody, fmt.Errorf("telnyx dial: %d %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		Data struct {
			CallControlID string `json:"call_control_id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return "", resp.StatusCode, respBody, fmt.Errorf("telnyx dial: parse response: %w", err)
	}
	return result.Data.CallControlID, resp.StatusCode, respBody, nil
}

// telnyxStreamParams is the one description of how this campaign streams audio,
// shared by the dial-time and on-answer paths so they can never drift apart —
// a mismatch between them is a call that connects but cannot hear or speak.
func telnyxStreamParams(cfg Config) map[string]any {
	streamURL := cfg.StreamURL
	if streamURL == "" {
		streamURL = fmt.Sprintf("wss://%s/media-stream", cfg.PublicHost)
	}
	return map[string]any{
		"stream_url":                         streamURL,
		"stream_track":                       "inbound_track",
		"stream_codec":                       "PCMU",
		"stream_bidirectional_mode":          "rtp",
		"stream_bidirectional_codec":         "PCMU",
		"stream_bidirectional_sampling_rate": 8000,
		"stream_bidirectional_target_legs":   "self",
	}
}

func telnyxStreamingStart(cfg Config, callControlID string) error {
	return telnyxAction(cfg, callControlID, "streaming_start", telnyxStreamParams(cfg))
}

func telnyxHangup(cfg Config, callControlID string) error {
	return telnyxAction(cfg, callControlID, "hangup", nil)
}

func telnyxAction(cfg Config, callControlID, action string, body any) error {
	url := fmt.Sprintf("%s/calls/%s/actions/%s", telnyxAPI, callControlID, action)

	var payload io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		payload = bytes.NewReader(b)
	} else {
		payload = bytes.NewReader([]byte("{}"))
	}

	req, err := http.NewRequest("POST", url, payload)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+cfg.TelnyxAPIKey)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("telnyx %s: %w", action, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("telnyx %s: %d %s", action, resp.StatusCode, string(respBody))
	}
	return nil
}
