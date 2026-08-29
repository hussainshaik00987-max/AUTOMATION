package main

import (
	"context"
	"log/slog"
	"sync"
	"time"
)

// ---------------------------------------------------------------------------
// Pre-synthesised Hindi openers
//
// This is the single biggest latency lever on the call. The naive flow is:
//
//     prospect says "hello" → open Gemini WS → setup → first token → first audio
//
// which is 1.5–3.5s of dead air at exactly the moment a cold call is won or
// lost. An Indian SMB owner hearing two seconds of silence hangs up, because
// silence is the signature of an autodialer.
//
// So the FULL personalised first line — including their business name — is
// synthesised ahead of time, while earlier calls in the campaign are still
// running, and streamed into the call the instant a human is detected. Time to
// first audio becomes ~0ms, and the live Gemini session gets those same seconds
// to warm up (and, per maybePlayOpener, to actually generate its first reply)
// before it has to say anything.
//
// Synthesis goes through ElevenLabs — the SAME voice id, model and settings as
// the live call. That is the load-bearing part. An earlier version of this
// system synthesised openers on a different engine from the one that spoke the
// rest of the call, because a plain HTTPS POST is cheaper and faster than
// holding a session open per lead. On a live call the voice change at the
// handoff was glaringly obvious: it sounded like two different people sharing
// one call. Voice continuity beats synthesis cost, and the cost is off the
// critical path anyway because openers are prefetched during the previous call.
//
// It is also why the whole brain is a text-out model. If the conversation were
// generated as audio by some other engine, no amount of care here could make the
// opener match it.

// ---------------------------------------------------------------------------
// Opener prefetch pool
// ---------------------------------------------------------------------------

// OpenerCache synthesises each lead's personalised opening line ahead of its
// dial and hands it over instantly when the call connects.
//
// Calls run strictly sequentially, so there is a long idle stretch — the whole
// duration of call N — in which to prepare call N+1..N+lookahead for free. By
// the time the campaign loop reaches a lead its audio is almost always already
// in memory; if it isn't, Get falls back to the generic filler rather than
// blocking the dial.
type OpenerCache struct {
	cfg     Config
	mu      sync.Mutex
	entries map[string]*openerEntry // keyed by dial number
	generic []byte                  // business-name-free fallback, synthesised once
	log     *slog.Logger
}

// openerSynthTakes is how many attempts an opener gets to come back under the
// ceiling. Three, because the measured distribution puts most takes under it and
// the work happens entirely inside the previous call — there is nothing to save
// by trying less, and nothing to gain by trying more.
const openerSynthTakes = 3

type openerEntry struct {
	once  sync.Once
	ready chan struct{}
	audio []byte
	err   error
}

func NewOpenerCache(cfg Config) *OpenerCache {
	return &OpenerCache{
		cfg:     cfg,
		entries: make(map[string]*openerEntry),
		log:     slog.With("component", "opener-cache"),
	}
}

// Generic returns the neutral, business-name-free greeting, synthesising it on
// first use. It is the fallback whenever a personalised opener isn't ready, and
// is safe to play to anyone — owner, staff, or a recording.
func (c *OpenerCache) Generic(ctx context.Context) []byte {
	c.mu.Lock()
	if c.generic != nil {
		defer c.mu.Unlock()
		return c.generic
	}
	c.mu.Unlock()

	audio, err := elevenSynthBounded(ctx, c.cfg, fillerLine(), openerMaxSeconds, openerSynthTakes)
	if err != nil {
		c.log.Warn("generic filler synthesis failed; calls will open cold", "error", err)
		return nil
	}

	c.mu.Lock()
	c.generic = audio
	c.mu.Unlock()
	c.log.Info("generic filler ready", "seconds", MulawSeconds(audio))
	return audio
}

// Prefetch starts synthesising a lead's opener in the background. Safe to call
// repeatedly for the same lead — only the first call does any work.
func (c *OpenerCache) Prefetch(ctx context.Context, p Prospect) {
	if c.cfg.ElevenAPIKey == "" || p.Name == "" {
		return
	}
	key := p.DialNumber()

	c.mu.Lock()
	e, ok := c.entries[key]
	if !ok {
		e = &openerEntry{ready: make(chan struct{})}
		c.entries[key] = e
	}
	c.mu.Unlock()

	e.once.Do(func() {
		go func() {
			defer close(e.ready)
			genCtx, cancel := context.WithTimeout(ctx, 45*time.Second) // room for openerSynthTakes attempts
			defer cancel()
			audio, err := elevenSynthBounded(genCtx, c.cfg, openerLine(p), openerMaxSeconds, openerSynthTakes)
			if err != nil {
				e.err = err
				c.log.Warn("opener synthesis failed — falling back to generic filler",
					"business", p.Name, "error", err)
				return
			}
			e.audio = audio
			c.log.Debug("opener ready", "business", p.Name, "seconds", MulawSeconds(audio))
		}()
	})
}

// Get returns the personalised opener for a lead, waiting at most `wait` for an
// in-flight synthesis to land. It NEVER blocks a dial for long: on timeout or
// failure it returns the generic filler, and the live model still delivers the
// real opening itself.
//
// The bool reports whether the returned audio is the personalised line (which
// already names the business, so the model must not repeat Stage 1's question)
// or the generic greeting (where the model still owes the full Stage 1 open).
func (c *OpenerCache) Get(ctx context.Context, p Prospect, wait time.Duration) ([]byte, bool) {
	key := p.DialNumber()

	c.mu.Lock()
	e := c.entries[key]
	c.mu.Unlock()

	if e != nil {
		select {
		case <-e.ready:
			if e.err == nil && len(e.audio) > 0 {
				return e.audio, true
			}
		case <-time.After(wait):
			c.log.Warn("opener not ready in time — using generic filler", "business", p.Name)
		case <-ctx.Done():
			return nil, false
		}
	}
	return c.Generic(ctx), false
}

// Forget drops a lead's cached audio once its call is done, so a 2,000-lead
// campaign doesn't accumulate every opener it ever generated in memory.
func (c *OpenerCache) Forget(p Prospect) {
	c.mu.Lock()
	delete(c.entries, p.DialNumber())
	c.mu.Unlock()
}
