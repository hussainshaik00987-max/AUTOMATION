# Architecture decision records

Decisions that could reasonably have gone the other way, with what was actually
tried and what it cost.

| # | Decision | Status |
|---|---|---|
| [0001](0001-text-out-brain-plus-dedicated-tts.md) | Text-out brain + dedicated TTS, not a native-audio model | Accepted · supersedes the original design |
| [0002](0002-listen-first-opener.md) | Release the opener on positive evidence of a person only | Accepted |
| [0003](0003-guardrails-in-code.md) | Safety guarantees live in code, never in the prompt | Accepted |
| [0004](0004-deterministic-renderer.md) | Deterministic frame-by-frame renderer, not a real-time recorder | Accepted |
| [0005](0005-sqlite-as-the-task-queue.md) | SQLite with per-row commits as the pipeline's task queue | Accepted |
| [0006](0006-ask-before-sending.md) | Ask before sending, even though it costs conversions | Accepted |

Format: context → decision → consequences, with the evidence that moved it.
