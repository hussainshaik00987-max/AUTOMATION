package main

import (
	"bufio"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------------------
// Suppression list
// ---------------------------------------------------------------------------
//
// The Hindi build recorded "take me off your list" as a disposition in
// results.csv and nothing more, so the request survived exactly as long as
// anybody remembered to read that file before the next run. This is the US
// build's answer to the same problem, carried back: a persistent list, read
// before every dial and appended the moment somebody refuses.
//
// "call मत करो" / "DND" is an instruction, not an outcome.

// Suppression is the campaign's own do-not-call list: one E.164 number per line,
// appended the moment a prospect refuses and read before every dial.
//
// It is append-only and re-read into memory at startup. The file is the record
// of every "take me off your list" this system has ever been told, and losing it
// means calling those people again.
type Suppression struct {
	path string
	mu   sync.Mutex
	set  map[string]bool
}

func LoadSuppression(path string) *Suppression {
	s := &Suppression{path: path, set: map[string]bool{}}
	f, err := os.Open(path)
	if err != nil {
		if !os.IsNotExist(err) {
			slog.Warn("could not read the suppression list — numbers that asked to be removed may be dialled again",
				"path", path, "error", err)
		}
		return s
	}
	defer f.Close()

	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		// Strip the trailing "# reason timestamp" BEFORE taking digits. Without
		// this the timestamp's own digits end up in the key, every entry this file
		// wrote is unmatchable on reload, and a number that asked to be removed is
		// dialled again on the next campaign — while the file sitting there says
		// it was recorded.
		if i := strings.IndexByte(line, '#'); i >= 0 {
			line = strings.TrimSpace(line[:i])
		}
		if line == "" {
			continue
		}
		s.set[suppressionKey(line)] = true
	}
	if len(s.set) > 0 {
		slog.Info("suppression list loaded", "path", path, "numbers", len(s.set))
	}
	return s
}

// suppressionKey reduces a number to something two different spellings of it
// agree on: digits only, with the Indian country code dropped.
//
// The list is hand-editable and hand-edited — somebody pastes in a complaint
// from a WhatsApp reply — so "+91 90000 00001", "090000-00001" and "9000000001"
// all have to mean the same number. They otherwise do not, and the entry
// silently protects nobody while looking exactly like it works.
//
// The leading 0 is the India-specific case the US version had no equivalent of:
// a ten-digit mobile is routinely written with a trunk 0 in front of it.
func suppressionKey(number string) string {
	d := digitsOnly(number)
	if len(d) == 12 && strings.HasPrefix(d, "91") {
		return d[2:]
	}
	if len(d) == 11 && d[0] == '0' {
		return d[1:]
	}
	return d
}

func (s *Suppression) Contains(number string) bool {
	if s == nil {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.set[suppressionKey(number)]
}

// Add records a refusal. It writes through to disk immediately rather than at
// shutdown: a campaign killed mid-run must not lose the fact that someone asked
// not to be called.
func (s *Suppression) Add(number, reason string) {
	if s == nil || number == "" {
		return
	}
	d := suppressionKey(number)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.set[d] {
		return
	}
	s.set[d] = true

	f, err := os.OpenFile(s.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		slog.Error("COULD NOT RECORD A DO-NOT-CALL REQUEST — this number may be dialled again",
			"number", number, "path", s.path, "error", err)
		return
	}
	defer f.Close()
	fmt.Fprintf(f, "%s  # %s  %s\n", number, reason, time.Now().Format(time.RFC3339))
	slog.Info("added to the suppression list", "number", number, "reason", reason)
}
