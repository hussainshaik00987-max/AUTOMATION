# ADR 0005 — SQLite with per-row commits as the pipeline's task queue

**Status:** Accepted

## Context

The discovery pipeline is six stages over ~15,000 records, each stage network-bound,
rate-limited by somebody else, and certain to be interrupted — by a block, a rotated
API format, or a laptop lid. Three of the stages hit different hosts and should run
concurrently.

The requirements are unusual for a scraper: **every stage must be resumable at row
granularity**, several must run at once, and the whole thing must be inspectable
mid-run with an ordinary query. A message queue plus a separate store is the
conventional answer and is more infrastructure than this earns.

## Decision

One SQLite database holds both the records and the task state, with per-query offsets
so nothing is re-fetched. Concurrency is threads within a stage and separate processes
across stages.

Three properties had to be true before that was actually safe, and all three were
learned by breaking:

1. **The database must not live on a network-backed filesystem.** WAL mode needs
   shared-memory sidecar files that a 9p/v9fs mount does not properly support. The
   symptom is intermittent `database is locked` the moment two stages overlap.
   Measured after moving it to a local filesystem: **8,798 concurrent writes/sec, 0
   lock errors**, against a hard crash before.

2. **Commit per row, not per batch.** Batching 50 updates behind one commit holds the
   write transaction open for the *whole batch* — at network speed that is **minutes**,
   and every other stage dies. Commits measured at ~8,800/sec, so batching bought
   nothing and cost the run.

3. **A busy timeout is not sufficient.** In WAL mode, a connection that reads and then
   writes after another has committed gets an **immediate** snapshot conflict that the
   timeout does not wait out. A retry wrapper backs off and retries, and re-raises
   non-lock errors untouched.

Network work fans out across threads, but **only the main thread touches SQLite**, and
HTTP sessions are per-thread because the impersonating client is not thread-safe.

## Consequences

**Gained**

- Kill any stage at any time and re-run it. Nothing is re-fetched, nothing is lost.
- The whole corpus is queryable mid-run with `sqlite3`, which is how most of the
  scoring findings were discovered.
- Zero operational surface: no broker, no schema registry, no second process to keep
  alive.
- **Resumability is what makes an aggressive give-up affordable.** The enrichment
  stage aborts after six consecutive rate limits rather than probing every 15 minutes
  and re-arming the block — and that is free, because unenriched rows keep their flag
  unset and a re-run continues exactly where it stopped.

**Paid**

- Concurrency is bounded by one writer. Fine here, because the bottleneck is somebody
  else's rate limit.
- The three rules above are invisible until violated, and each failed in a way that
  looked like a different bug — which is why they are written down.
