package main

import (
	"encoding/csv"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

// The Go agent and the Node sidecar are separate processes with no shared types,
// so the JSON field names are the entire contract between them. If they drift,
// every lead silently gets a message addressed to nobody about no business —
// which looks fine in the Go logs and is only visible on the recipient's phone.
func TestWhatsAppDispatchContract(t *testing.T) {
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/queue" {
			t.Errorf("posted to %q, sidecar only serves /queue", r.URL.Path)
		}
		if ct := r.Header.Get("Content-Type"); ct != "application/json" {
			t.Errorf("Content-Type = %q, want application/json", ct)
		}
		body, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(body, &got); err != nil {
			t.Fatalf("sidecar could not parse the body: %v", err)
		}
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"queued":true}`))
	}))
	defer srv.Close()

	cfg := Config{WhatsAppAPI: srv.URL}
	p := Prospect{
		Name:     "Meridian Studios",
		Phone:    "+919000000002",
		DemoURL:  "https://demo.example.com/sites/meridian-studios",
		Vertical: "wedding_photo",
		City:     "Guwahati",
	}
	r := &Result{
		Phone: p.Phone, Business: p.Name, OwnerName: "Rakesh",
		LinkChannel: "whatsapp", Language: "hi",
	}

	dispatchLink(cfg, p, r)

	if r.LinkStatus != "queued" {
		t.Fatalf("LinkStatus = %q, want queued", r.LinkStatus)
	}
	if r.DemoURL != p.DemoURL {
		t.Errorf("DemoURL = %q, want it recorded on the result", r.DemoURL)
	}

	// These keys are read by name in whatsapp/store.js and whatsapp/messages.js.
	want := map[string]string{
		"phone":     "+919000000002",
		"business":  "Meridian Studios",
		"ownerName": "Rakesh",
		"demoUrl":   "https://demo.example.com/sites/meridian-studios",
		"vertical":  "wedding_photo",
		"city":      "Guwahati",
		"language":  "hi",
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("payload[%q] = %v, want %q (sidecar reads this key by name)", k, got[k], v)
		}
	}
}

// A lead with no Demo Site must never be reported as delivered — the sidecar
// rejects the payload anyway, and a false "queued" in results.csv would hide it.
func TestDispatchRefusesLeadWithoutDemoURL(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("sidecar must not be called for a lead with no demo URL")
	}))
	defer srv.Close()

	r := &Result{Phone: "+919000000002", Business: "No Demo Co", LinkChannel: "whatsapp"}
	dispatchLink(Config{WhatsAppAPI: srv.URL}, Prospect{Name: "No Demo Co"}, r)

	if r.LinkStatus != "failed" {
		t.Errorf("LinkStatus = %q, want failed", r.LinkStatus)
	}
}

// Nothing is promised, nothing is sent. The agent only sets LinkChannel when it
// actually got a yes, so an untouched channel must leave the lead alone.
func TestDispatchNoopWhenNothingPromised(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("sidecar must not be called when the agent never got a yes")
	}))
	defer srv.Close()

	r := &Result{Phone: "+919000000002", Business: "Not Interested Co", Disposition: "not_interested"}
	dispatchLink(Config{WhatsAppAPI: srv.URL}, Prospect{DemoURL: "https://x.test/y"}, r)

	if r.LinkStatus != "" {
		t.Errorf("LinkStatus = %q, want empty", r.LinkStatus)
	}
}

// When a lead asks for email instead, the address has to land somewhere the user
// can actually act on — this campaign has no verified sending identity, so the
// row in pending_emails.csv IS the deliverable.
func TestEmailIsQueuedToCSV(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pending_emails.csv")

	cfg := Config{PendingEmailsFile: path, WhatsAppAPI: "http://127.0.0.1:1"}
	p := Prospect{
		Name: "Cake Corner", City: "Pune", Vertical: "bakery",
		DemoURL: "https://demo.example.com/sites/cake-corner",
	}
	r := &Result{
		Phone: "+919876543210", Business: p.Name, OwnerName: "Imran",
		LinkChannel: "email", AttendeeEmail: "cake@example.com", Language: "hi",
	}

	dispatchLink(cfg, p, r)

	if r.LinkStatus != "queued" {
		t.Fatalf("LinkStatus = %q, want queued", r.LinkStatus)
	}

	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("pending_emails.csv was not written: %v", err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatalf("pending_emails.csv is not valid CSV: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("got %d rows (header + data expected)", len(rows))
	}
	if rows[0][0] != "timestamp" || rows[0][3] != "email" {
		t.Errorf("unexpected header: %v", rows[0])
	}
	if rows[1][1] != "Cake Corner" || rows[1][3] != "cake@example.com" || rows[1][7] != p.DemoURL {
		t.Errorf("row does not carry the lead through: %v", rows[1])
	}
}

// An unreachable sidecar must be loud and recoverable: the result says failed and
// the lead is parked in failed_links.csv so the promise can still be honoured.
func TestFailedWhatsAppDispatchIsRecorded(t *testing.T) {
	dir := t.TempDir()
	cfg := Config{
		WhatsAppAPI:     "http://127.0.0.1:1", // nothing listens here
		FailedLinksFile: filepath.Join(dir, "failed_links.csv"),
	}
	p := Prospect{Name: "Fairlight Studio", DemoURL: "https://x.test/sharma"}
	r := &Result{Phone: "+919876543210", Business: p.Name, LinkChannel: "whatsapp"}

	dispatchLink(cfg, p, r)

	if r.LinkStatus != "failed" {
		t.Errorf("LinkStatus = %q, want failed", r.LinkStatus)
	}
	if _, err := os.Stat(cfg.FailedLinksFile); err != nil {
		t.Errorf("failed lead was not parked for retry: %v", err)
	}
}

// The sidecar answers 202 whether or not it took the job: `{"queued":false}`
// means it already holds one for that number and will not create a second. A
// dialer that only checks the status code records a delivery that never happens
// — seven live calls were logged as `queued` against a single job from the first
// of them, and results.csv is supposed to say what ACTUALLY happened.
func TestDuplicateJobIsNotReportedAsQueued(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"queued":false,"refreshed":true,"status":"queued"}`))
	}))
	defer srv.Close()

	r := &Result{
		Phone: "+919000000001", Business: "Fairlight Studio", OwnerName: "Krishna",
		LinkChannel: "whatsapp", Language: "hi",
	}
	dispatchLink(Config{WhatsAppAPI: srv.URL}, Prospect{DemoURL: "https://x.test/y"}, r)

	if r.LinkStatus != "duplicate" {
		t.Fatalf("LinkStatus = %q, want duplicate", r.LinkStatus)
	}
}

// A sidecar that doesn't report `queued` at all still queues the job. Treating a
// missing field as failure would divert healthy leads into failed_links.csv.
func TestMissingQueuedFieldCountsAsQueued(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"status":"queued","key":"919000000001"}`))
	}))
	defer srv.Close()

	r := &Result{Phone: "+919000000001", Business: "Fairlight Studio", LinkChannel: "whatsapp"}
	dispatchLink(Config{WhatsAppAPI: srv.URL}, Prospect{DemoURL: "https://x.test/y"}, r)

	if r.LinkStatus != "queued" {
		t.Fatalf("LinkStatus = %q, want queued", r.LinkStatus)
	}
}
