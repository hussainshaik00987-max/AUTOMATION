package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Call recording.
//
// We ask Telnyx to record each answered call as a two-channel track — the agent
// on one side, the prospect on the other. When Telnyx finishes processing it
// fires `call.recording.saved`; we download the file into RECORDINGS_DIR, named
// by business + phone + time.

// telnyxRecordStart begins recording the call. format=mp3 → small, universally
// playable.
//
// channels=dual, not single. On a mixed-mono track "the agent went silent for 11
// seconds" and "the prospect said nothing for 11 seconds" are indistinguishable,
// and telling them apart after a bad call took an out-of-band Whisper pass plus
// log correlation. Two channels answer it by inspection.
func telnyxRecordStart(cfg Config, callControlID string) error {
	body := map[string]any{
		"format":   "mp3",
		"channels": "dual",
	}
	return telnyxAction(cfg, callControlID, "record_start", body)
}

// telnyxRecordingPayload is the payload of a call.recording.saved webhook.
type telnyxRecordingPayload struct {
	CallControlID string `json:"call_control_id"`
	RecordingID   string `json:"recording_id"`
	Channels      string `json:"channels"`
	RecordingURLs struct {
		MP3 string `json:"mp3"`
		WAV string `json:"wav"`
	} `json:"recording_urls"`
	PublicRecordingURLs struct {
		MP3 string `json:"mp3"`
		WAV string `json:"wav"`
	} `json:"public_recording_urls"`
}

// handleRecordingSaved downloads the finished recording. It runs in its own
// goroutine off the webhook so it never blocks Telnyx's webhook delivery.
func handleRecordingSaved(cfg Config, sessions *SessionRegistry, raw json.RawMessage) {
	var p telnyxRecordingPayload
	if err := json.Unmarshal(raw, &p); err != nil {
		slog.Error("recording.saved: bad payload", "error", err)
		return
	}

	// Prefer the public (presigned, no-auth) URL; fall back to the API URL.
	url := p.PublicRecordingURLs.MP3
	if url == "" {
		url = p.RecordingURLs.MP3
	}
	if url == "" {
		slog.Warn("recording.saved: no mp3 url", "call", p.CallControlID)
		return
	}
	// Only Telnyx API URLs take a Bearer token; the recording_urls are presigned
	// S3 links that reject an Authorization header ("only one auth mechanism").
	authed := strings.Contains(url, "api.telnyx.com")

	dir := cfg.RecordingsDir
	if dir == "" {
		dir = "recordings"
	}
	if err := os.MkdirAll(dir, 0755); err != nil {
		slog.Error("recording: mkdir failed", "dir", dir, "error", err)
		return
	}

	label := sessions.Label(p.CallControlID)
	if label == "" {
		label = p.CallControlID
	}
	name := fmt.Sprintf("%s_%s.mp3", time.Now().Format("2006-01-02_150405"), label)
	path := filepath.Join(dir, name)

	if err := downloadFile(cfg, url, path, authed); err != nil {
		slog.Error("recording: download failed", "url", url, "error", err)
		return
	}
	slog.Info("recording saved", "call", p.CallControlID, "file", path)
}

func downloadFile(cfg Config, url, path string, authed bool) error {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return err
	}
	if authed {
		req.Header.Set("Authorization", "Bearer "+cfg.TelnyxAPIKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("download %d: %s", resp.StatusCode, string(b))
	}

	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

// sanitizeLabel makes a filesystem-safe label from a business name + phone.
func sanitizeLabel(business, phone string) string {
	b := strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			return r
		case r == ' ' || r == '-' || r == '_':
			return '-'
		default:
			return -1
		}
	}, business)
	b = strings.Trim(b, "-")
	if len(b) > 40 {
		b = b[:40]
	}
	last4 := phone
	if len(phone) >= 4 {
		last4 = phone[len(phone)-4:]
	}
	if b == "" {
		return last4
	}
	return b + "_" + last4
}
