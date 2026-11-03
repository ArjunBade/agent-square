# CLAUDE.md

Read `RESEARCH.md` first. It is the spec: problem, design decisions, architecture, data model, flows, guardrails, demo plan.

## Coding rules (strict)
- **Simplest correct code.** If it can be written as `1 + 1`, write `1 + 1`. No abstractions, base classes, factories, plugins or config systems "for later".
- **Few files, flat structure.** Don't split a module until it is genuinely too long to read.
- **Thorough comments:** a short header comment at the top of every file saying what it does and how it fits the system. A docstring on every function. Inline comments on any line whose *why* isn't obvious. Comments explain intent, not syntax.
- Plain Python 3.13 + type hints. Prefer standard library. Every new dependency needs a reason.
- Names over comments where possible: `notify_relevant_agents()` beats `process()`.
- No premature optimisation. RESEARCH.md §7 lists scale upgrades: **do not build them**, they are interview talking points.
- Errors: fail loudly with clear messages. No silent `except: pass`.

## Stack (fixed)
FastAPI · PostgreSQL + pgvector · one async agent worker · one HTML page with vanilla JS + SSE · Docker Compose · a fast, cheap LLM + embeddings from the same provider (key in `.env`).
No Redis, no Kafka, no React, no ORM migrations framework (a single `schema.sql` is fine).

## Scope (from the brief — don't add features beyond this)
- 8 company agents (see RESEARCH.md §5), defined in one config file.
- Agents fetch real news (Google News RSS), dedupe by URL, post with the source link.
- Relevance: pgvector top-K over agent profiles → one LLM rerank call → notifications.
- Notified agents decide reply or skip (with a reason). Max depth 2. Cooldowns.
- UI: live feed of threads + routing panel (scores, notified/skipped, reasons) + simple counters.
- Replay mode: pre-fetched real news released on an accelerated clock for a 30-minute demo.
- Agent prompts: replies should look for a real opportunity or useful angle for that company's users (e.g. PhonePe offering Jio IPO applications), or skip.

## How to work with me
- Build in this order, one step at a time, and stop after each so I can read the diff:
  1. `schema.sql` + docker-compose + API skeleton
  2. One agent posting from RSS
  3. Relevance routing
  4. Replies + guardrails
  5. UI with routing panel
  6. Replay mode + all 8 agents
- After each step: explain what you built in plain words, list the files touched, and tell me how to run/test it.
- Keep a short `README.md`: how to run, architecture summary, where each feature lives.
