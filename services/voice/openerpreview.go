package main

import (
	"context"
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// -opener-preview — hear and time the opening line before strangers do.
//
// The opener is the only part of the call that is spoken from a recording, and
// it is the part with the least room for error: on the first live campaign two
// of three people who answered were gone inside nine seconds. Changing its
// wording without knowing how long it takes to say is guesswork — a line that
// reads as tight on the page can run seven seconds through TTS, and seven
// seconds of pre-recorded audio at a stranger who has just said "हैलो?" is a
// monologue, not an opening.
//
// So this synthesises the real line, for real leads out of the real CSV, through
// the same Gemini voice a live call uses, and reports how long each one actually
// takes. It writes the audio out as .wav too, because the other half of "is this
// a good opening?" is a question only ears can answer.
// ---------------------------------------------------------------------------

// runSayPreview times one arbitrary spoken line through the live voice. Wording
// is tuned against this, not against how long it looks on the page: syllable
// counting is not a substitute for hearing the synthesiser say it, and the first
// draft of the praise opener read as tight and measured 7.4 seconds.
func runSayPreview(cfg Config, lines []string, outDir string) {
	if outDir != "" {
		_ = os.MkdirAll(outDir, 0o755)
	}
	for i, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
		audio, err := elevenSynth(ctx, cfg, line)
		cancel()
		if err != nil {
			fmt.Printf("  ??  SYNTHESIS FAILED: %v\n", err)
			continue
		}
		sec := float64(len(audio)) / 8000.0
		flag := "  "
		if sec > openerTooLongSec {
			flag = "!!"
		}
		fmt.Printf("%s %5.2fs  %s\n", flag, sec, line)
		if outDir != "" {
			_ = writeMulawWAV(filepath.Join(outDir, fmt.Sprintf("say_%02d.wav", i+1)), audio)
		}
	}
}

func runOpenerPreview(cfg Config, count int, outDir string) {
	prospects, err := ReadProspects(cfg.ProspectsFile)
	if err != nil {
		fmt.Printf("read prospects: %v\n", err)
		os.Exit(1)
	}
	if len(prospects) == 0 {
		fmt.Println("no leads in", cfg.ProspectsFile)
		os.Exit(1)
	}
	if count > len(prospects) {
		count = len(prospects)
	}
	if outDir != "" {
		if err := os.MkdirAll(outDir, 0o755); err != nil {
			fmt.Printf("mkdir %s: %v\n", outDir, err)
			os.Exit(1)
		}
	}

	fmt.Printf("Opener preview — style=%q, %d leads from %s\n", openerStyle, count, cfg.ProspectsFile)
	fmt.Println(strings.Repeat("─", 100))

	var secs []float64
	for i := 0; i < count; i++ {
		p := prospects[i]
		line := openerLine(p)

		ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
		audio, err := elevenSynth(ctx, cfg, line)
		cancel()
		if err != nil {
			fmt.Printf("%2d. %-34s  SYNTHESIS FAILED: %v\n", i+1, trunc(p.Name, 34), err)
			continue
		}
		// μ-law at 8kHz: one byte is one sample is 125µs.
		sec := float64(len(audio)) / 8000.0
		secs = append(secs, sec)

		flag := "  "
		if sec > openerTooLongSec {
			flag = "!!" // long enough that the prospect is being talked at, not to
		}
		fmt.Printf("%2d.%s %-34s  %5.2fs  %s\n", i+1, flag, trunc(p.Name, 34), sec, line)

		if outDir != "" {
			name := fmt.Sprintf("%02d_%s.wav", i+1, sanitizeLabel(p.Name, p.Phone))
			if err := writeMulawWAV(filepath.Join(outDir, name), audio); err != nil {
				fmt.Printf("    (could not write wav: %v)\n", err)
			}
		}
	}

	if len(secs) == 0 {
		return
	}
	sort.Float64s(secs)
	var sum float64
	long := 0
	for _, s := range secs {
		sum += s
		if s > openerTooLongSec {
			long++
		}
	}
	fmt.Println(strings.Repeat("─", 100))
	fmt.Printf("spoken length: min %.2fs · median %.2fs · max %.2fs · mean %.2fs\n",
		secs[0], secs[len(secs)/2], secs[len(secs)-1], sum/float64(len(secs)))
	fmt.Printf("%d of %d run past %.1fs — that is the point where a pre-recorded opener stops being an\n"+
		"opening and starts being a monologue, because it is exempt from barge-in and plays to the end.\n",
		long, len(secs), openerTooLongSec)
	if outDir != "" {
		fmt.Printf("wavs written to %s/ — listen before shipping the wording.\n", outDir)
	}
}

// openerTooLongSec is where a pre-recorded opener stops helping. It plays to
// completion whatever the prospect does (barge-in is deliberately disabled for
// it, because what they say over it is their ANSWER, not an interruption), so
// every second past this is a second of being talked at by a recording. The
// line that worked on the live run measured 3.7s.
const openerTooLongSec = 5.5

// writeMulawWAV decodes μ-law to 16-bit PCM and writes a plain WAV, so the
// opener can be played by anything without a codec argument.
func writeMulawWAV(path string, mulaw []byte) error {
	pcm := make([]byte, len(mulaw)*2)
	for i, b := range mulaw {
		binary.LittleEndian.PutUint16(pcm[i*2:], uint16(mulawDecode[b]))
	}
	var hdr []byte
	put32 := func(v uint32) { b := make([]byte, 4); binary.LittleEndian.PutUint32(b, v); hdr = append(hdr, b...) }
	put16 := func(v uint16) { b := make([]byte, 2); binary.LittleEndian.PutUint16(b, v); hdr = append(hdr, b...) }

	hdr = append(hdr, "RIFF"...)
	put32(uint32(36 + len(pcm)))
	hdr = append(hdr, "WAVEfmt "...)
	put32(16)
	put16(1) // PCM
	put16(1) // mono
	put32(8000)
	put32(16000) // byte rate = 8000 * 1 * 2
	put16(2)     // block align
	put16(16)    // bits
	hdr = append(hdr, "data"...)
	put32(uint32(len(pcm)))

	return os.WriteFile(path, append(hdr, pcm...), 0o644)
}

func trunc(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n-1] + "…"
}
