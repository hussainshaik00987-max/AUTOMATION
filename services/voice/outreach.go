package main

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------------------
// Outreach — what happens to a lead AFTER the agent gets a yes on the call.
//
// WhatsApp is handled by the Node/Baileys sidecar (../whatsapp), which owns the
// logged-in WhatsApp session, the typing indicator, the "link bhej doon?" ping,
// the 30–40 minute wait, and reply classification. Go's only job is to hand it a
// fully-formed lead the instant the call hangs up.
//
// Email is deliberately NOT sent from here — it is appended to pending_emails.csv
// for manual sending, because no SMTP identity is configured for this campaign.
// ---------------------------------------------------------------------------

// LinkRequest is the payload handed to the WhatsApp sidecar. It carries
// everything the sidecar needs to compose both messages without re-reading the
// CSV, so the two services share no state beyond this envelope.
type LinkRequest struct {
	Phone     string `json:"phone"`     // E.164, e.g. +919876543210
	Business  string `json:"business"`  // business name, used in the message
	OwnerName string `json:"ownerName"` // first name captured on the call, if any
	DemoURL   string `json:"demoUrl"`   // the demo site to send
	Vertical  string `json:"vertical"`  // wedding_photo | bakery | ...
	City      string `json:"city"`
	Language  string `json:"language"`  // "hi" or "en" — whichever the call ended in
	CallID    string `json:"callId"`    // Telnyx call_control_id, for tracing
	AgreedYes bool   `json:"agreedYes"` // true if they explicitly said yes on the call
}

// DispatchWhatsApp hands the lead to the WhatsApp sidecar. It is called once,
// right after the call hangs up, so the first WhatsApp message lands while the
// conversation is still fresh in the prospect's mind.
//
// It returns whether the sidecar actually took a NEW job. The sidecar answers
// 202 either way — `{"queued": true}` for a fresh lead, `{"queued": false}` when
// it already holds a job for that number — and a caller that only checks the
// status code records a delivery that will never happen. Seven consecutive test
// calls were logged as `queued` against a single job from the first one.
func DispatchWhatsApp(cfg Config, req LinkRequest) (queued bool, err error) {
	if cfg.WhatsAppAPI == "" {
		return false, fmt.Errorf("WHATSAPP_API not configured")
	}
	body, err := json.Marshal(req)
	if err != nil {
		return false, err
	}

	url := strings.TrimSuffix(cfg.WhatsAppAPI, "/") + "/queue"
	httpReq, err := http.NewRequest("POST", url, bytes.NewReader(body))
	if err != nil {
		return false, err
	}
	httpReq.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 8 * time.Second}
	resp, err := client.Do(httpReq)
	if err != nil {
		return false, fmt.Errorf("whatsapp sidecar unreachable: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return false, fmt.Errorf("whatsapp sidecar: %d %s", resp.StatusCode, strings.TrimSpace(string(respBody)))
	}

	// Absent or unparseable `queued` is treated as accepted: an older sidecar that
	// doesn't report it still queues the job, and calling that a failure would
	// send healthy leads to failed_links.csv.
	var ack struct {
		Queued *bool  `json:"queued"`
		Status string `json:"status"`
	}
	if err := json.Unmarshal(respBody, &ack); err != nil || ack.Queued == nil {
		return true, nil
	}
	return *ack.Queued, nil
}

// WhatsAppStatus reads the sidecar's own counters.
//
// This account is the fragile part of the whole system — an unofficial client
// messaging strangers from a real personal number, one ban away from costing
// every remaining lead on the list — so it is not enough to know it was linked
// when the campaign started. Every field the sidecar tracks is pulled and
// recorded: sends against the daily cap, how many people declined on WhatsApp,
// and how many numbers came back unreachable. A rise in that last one is a fact
// about the LEAD LIST (landlines, numbers not on WhatsApp), not noise.
func WhatsAppStatus(cfg Config) WhatsAppSample {
	s := WhatsAppSample{}
	if cfg.WhatsAppAPI == "" {
		s.Detail = "WHATSAPP_API not set"
		return s
	}
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(strings.TrimSuffix(cfg.WhatsAppAPI, "/") + "/status")
	if err != nil {
		s.Detail = "sidecar unreachable: " + err.Error()
		return s
	}
	defer resp.Body.Close()

	var status struct {
		Connected   bool   `json:"connected"`
		State       string `json:"state"`
		Pending     int    `json:"pending"`
		Total       int    `json:"total"`
		Queued      int    `json:"queued"`
		Pinged      int    `json:"pinged"`
		Sent        int    `json:"sent"`
		Declined    int    `json:"declined"`
		Unreachable int    `json:"unreachable"`
		Failed      int    `json:"failed"`
		SentToday   int    `json:"sentToday"`
		DailyCap    int    `json:"dailyCap"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
		s.Detail = "bad status response: " + err.Error()
		return s
	}
	s.Reachable = true
	s.Connected = status.Connected
	s.State = status.State
	s.Total, s.Queued, s.Pinged = status.Total, status.Queued, status.Pinged
	s.Sent, s.Declined, s.Unreachable, s.Failed = status.Sent, status.Declined, status.Unreachable, status.Failed
	s.SentToday, s.DailyCap = status.SentToday, status.DailyCap
	if !status.Connected {
		s.Detail = "sidecar running but WhatsApp not linked (state: " + status.State + ")"
	}
	return s
}

// WhatsAppReady reports whether the sidecar is up AND actually logged in to
// WhatsApp. Checked at campaign start, and again periodically, so we don't run
// 2,000 calls promising a link that can never be delivered.
func WhatsAppReady(cfg Config) (bool, string) {
	s := WhatsAppStatus(cfg)
	if !s.Reachable || !s.Connected {
		return false, s.Detail
	}
	return true, fmt.Sprintf("connected, %d in flight, %d sent today", s.Queued+s.Pinged, s.SentToday)
}

// whatsAppWatch records one health sample and shouts about anything on it that
// threatens the account or the lead list. Called at the start of a run, every
// whatsAppRecheckEvery leads, and at the end.
//
// It returns the sample so the caller can compare it with the previous one:
// what matters is not the absolute numbers, it is which of them MOVED while the
// campaign was dialling.
func whatsAppWatch(cfg Config, when string, prev *WhatsAppSample) WhatsAppSample {
	s := WhatsAppStatus(cfg)
	s.When = when
	runLog.WhatsApp(s)

	log := slog.With("component", "whatsapp-watch", "when", when)
	if !s.Reachable {
		log.Error("WHATSAPP SIDECAR UNREACHABLE — every link promised from here on would silently never arrive", "reason", s.Detail)
		return s
	}
	if !s.Connected {
		log.Error("WHATSAPP NOT LINKED", "state", s.State, "reason", s.Detail)
		return s
	}
	log.Info("whatsapp health",
		"sent", s.Sent, "declined", s.Declined, "unreachable", s.Unreachable,
		"failed", s.Failed, "in_flight", s.Queued+s.Pinged,
		"sent_today", s.SentToday, "daily_cap", s.DailyCap)

	if s.DailyCap > 0 && s.SentToday*10 >= s.DailyCap*8 {
		log.Warn("approaching today's WhatsApp send cap — the cap is what keeps this account alive",
			"sent_today", s.SentToday, "cap", s.DailyCap)
	}
	if prev != nil && prev.Reachable {
		if d := s.Unreachable - prev.Unreachable; d > 0 {
			log.Warn("numbers came back NOT ON WHATSAPP during this run — a lead-list quality signal, not noise",
				"new_unreachable", d, "total", s.Unreachable)
		}
		if d := s.Failed - prev.Failed; d > 0 {
			log.Warn("WhatsApp sends failed during this run", "new_failures", d, "total", s.Failed)
		}
		if d := s.Declined - prev.Declined; d > 0 {
			log.Info("leads declined on WhatsApp and were not sent a link — the consent guard working",
				"declined", d, "total", s.Declined)
		}
	}
	return s
}

// ---------------------------------------------------------------------------
// Email queue
// ---------------------------------------------------------------------------

var emailMu sync.Mutex

// QueueEmail appends a lead who asked for the demo by email to pending_emails.csv.
// Nothing is actually sent — this campaign has no verified sending identity, and
// blasting demo links from an unverified address would land them in spam and burn
// the domain. The agent still accepts the email on the call so the lead isn't lost.
func QueueEmail(path string, r Result, p Prospect) error {
	emailMu.Lock()
	defer emailMu.Unlock()

	newFile := false
	if _, err := os.Stat(path); os.IsNotExist(err) {
		newFile = true
	}

	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()

	w := csv.NewWriter(f)
	defer w.Flush()

	if newFile {
		if err := w.Write([]string{
			"timestamp", "business", "owner_name", "email", "phone",
			"city", "vertical", "demo_url", "language",
		}); err != nil {
			return err
		}
	}
	return w.Write([]string{
		time.Now().Format(time.RFC3339),
		r.Business,
		r.OwnerName,
		r.AttendeeEmail,
		r.Phone,
		p.City,
		p.Vertical,
		p.DemoURL,
		r.Language,
	})
}

// ---------------------------------------------------------------------------
// Dispatch — called once per call, from Session.Finish, after hangup
// ---------------------------------------------------------------------------

// dispatchLink delivers whatever the agent promised on the call. It runs after
// the line is down, so a slow sidecar can never add latency to a live call, and
// it mutates the Result in place so results.csv records what actually happened
// rather than what the agent said it would do.
func dispatchLink(cfg Config, p Prospect, r *Result) {
	if r.LinkChannel == "" {
		return // the agent never got a yes — nothing promised, nothing to send
	}
	// A refusal recorded anywhere on the call outranks whatever the model asked
	// for. This is the last gate before a real message reaches a real person who
	// said no, so it is checked here as well as at the tool call — the refusal can
	// land after send_demo_link, and on a live call it did.
	if r.Disposition == "not_interested" || r.Disposition == "do_not_call" {
		slog.Warn("not dispatching — the prospect declined on the call",
			"business", r.Business, "phone", r.Phone, "disposition", r.Disposition)
		r.LinkStatus = "suppressed"
		return
	}

	log := slog.With("business", r.Business, "phone", r.Phone, "channel", r.LinkChannel)

	if p.DemoURL == "" {
		log.Warn("agent promised a demo link but this lead has no Demo Site in the CSV")
		r.LinkStatus = "failed"
		return
	}
	r.DemoURL = p.DemoURL

	switch r.LinkChannel {
	case "email":
		if r.AttendeeEmail == "" {
			log.Warn("email channel chosen but no address captured")
			r.LinkStatus = "failed"
			return
		}
		if err := QueueEmail(cfg.PendingEmailsFile, *r, p); err != nil {
			log.Error("queue email failed", "error", err)
			r.LinkStatus = "failed"
			return
		}
		log.Info("email queued for manual send", "email", r.AttendeeEmail, "file", cfg.PendingEmailsFile)
		r.LinkStatus = "queued"

	default: // whatsapp
		queued, err := DispatchWhatsApp(cfg, LinkRequest{
			Phone:     r.Phone,
			Business:  r.Business,
			OwnerName: r.OwnerName,
			DemoURL:   p.DemoURL,
			Vertical:  p.Vertical,
			City:      p.City,
			Language:  r.Language,
			CallID:    r.CallID,
			AgreedYes: true,
		})
		if err != nil {
			log.Error("whatsapp dispatch failed — lead saved to failed_links.csv for retry", "error", err)
			r.LinkStatus = "failed"
			_ = QueueEmail(cfg.FailedLinksFile, *r, p)
			return
		}
		if !queued {
			// The sidecar already holds a job for this number and will not create a
			// second one. Something WILL be sent — but not necessarily this call's
			// demo URL, name or language — so this is neither "queued" nor "failed".
			// Recording it as its own status keeps results.csv honest about which
			// promises are actually backed by a job of their own.
			log.Warn("whatsapp sidecar already holds a job for this number — not re-queued",
				"demo", p.DemoURL)
			r.LinkStatus = "duplicate"
			return
		}
		log.Info("whatsapp follow-up queued", "demo", p.DemoURL)
		r.LinkStatus = "queued"
	}
}
