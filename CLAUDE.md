# CLAUDE.md

Context for Claude Code. Read this before making changes. The full rationale
lives in [`DESIGN.md`](./DESIGN.md) — this file is the condensed, enforceable
version.

## What this project is

Sentinel is a **domain-agnostic** engine that ingests data from any API, runs
models over it, and produces **signals** — opportunities scored by confidence
and expected value. First deployment: a sports value-betting SaaS.

Two goals at once: (1) a real, monetizable betting product; (2) a portfolio
piece proving the same engine works across unrelated domains (betting, flights,
real estate, crypto).

## Architecture — three layers

```
core/      LAYER 1 — generic framework (the IP). Domain-agnostic.
adapters/  LAYER 2 — one plug-in per domain. Domain knowledge lives here.
api/ + web/ LAYER 3 — REST API + Next.js SaaS product (Phase 1+).
```

Pipeline: `ingest → record model_run → generate signals → resolve outcomes`.

## Non-negotiable design rules

These protect the genericity that is the whole point of the project. Do not
violate them without flagging the tradeoff explicitly.

1. **`core/` never knows the domain.** No `if domain == "betting"`, no imports
   from `adapters/betting/`. The orchestrator talks only to the `Adapter`
   interface (`adapters/base.py`). If you're tempted to add domain logic to
   core, it belongs in an adapter.
2. **Dependencies point one way:** adapters depend on core interfaces, never the
   reverse.
3. **Domain-specific data goes in jsonb**, never as typed columns. The fields
   `payload`, `features`, `auth_config`, `hyperparams` are `JSONType`. Adding a
   betting/crypto/flights column to `signals` or `raw_events` is a design
   violation — it breaks genericity. Use jsonb indexes for query performance
   instead (see DESIGN.md §4).
4. **Adapters return dataclasses, not ORM rows.** They use `RawEventData`,
   `SignalData`, `OutcomeData` from `adapters/base.py`. Only the orchestrator
   translates these into database models.
5. **`raw_events` is append-only.** Never update or delete; it's the basis for
   retraining and audit.
6. **Signals are global, tracking is per-user.** One slate of picks per day for
   everyone; `user_signal_views` holds individual stake/P&L. Do not add per-user
   pick generation without discussing it first.
7. **Resolution is configurable per signal** via `resolution_rule`
   (`binary` / `threshold` / `continuous`) and `valid_until`. Don't hardcode a
   single resolution path in core.

## Current state (Phase 0)

Goal of Phase 0: **validate the model shows positive ROI / break-even on paper
before building any product.** No web app, no billing yet.

What works and is tested:
- `core/` schema (8 tables) and orchestrator
- `adapters/betting/`: The Odds API ingestion, Poisson models (soccer +
  baseball), de-vig, EV, value-bet detection
- `scripts/paper_trade.py --mock` runs the full pipeline end-to-end, no network

What is NOT done / needs verification:
- **Resolution is a stub** (`BettingAdapter.resolve` returns `None`). First job
  to close the paper-trading loop: fetch real final scores and compute
  `was_correct` + CLV.
- **Soccer model uses a static ratings map** in `adapters/betting/stats.py`
  (`WORLD_CUP_RATINGS`), not real stats. It's a v0 placeholder — without an
  independent stats feed the model just reads the market back and finds no real
  value. Replace with a feed (e.g. football-data.org).
- **Live API calls are untested** (this code was written without network access).
  Verify The Odds API and MLB Stats API field names against real responses on
  first run.

## Conventions

- Python 3.12. Type hints everywhere. `from __future__ import annotations`.
- Code comments and docstrings in **English** (portfolio is English-base).
- Commits: Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`).
- DB target via `DATABASE_URL` env var: Supabase/Postgres in prod, SQLite local
  for fast iteration. See `.env.example`.
- Run the pipeline: `python -m scripts.paper_trade --mock` (safe, no quota).
- Validate model math in isolation before wiring it to the DB or live APIs.

## Suggested next steps

1. Implement `resolve()` to close the paper-trading loop and start measuring ROI.
2. Wire a real soccer stats feed to replace `WORLD_CUP_RATINGS`.
3. Verify live API field names; run `--mock` first, then a small live slate.
4. Only after the model validates on paper: start Phase 1 (API + web app).

## What to ask before doing

- Anything that adds domain knowledge to `core/`.
- Anything that adds typed domain columns instead of jsonb.
- Per-user pick generation.
- Spending real money or placing real bets — this project paper-trades only;
  it never handles user funds or integrates with bookmakers.
