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

Built and working:
- **Live pipeline:** The Odds API → Poisson model → signals → Supabase
- **GitHub Actions:** `daily_picks` (09:00 ART / 12:00 UTC) + `daily_resolve`
  (00:00 ART / 03:00 UTC next day), both with pip cache and secrets validation
- **Telegram notifications:** picks message in the morning, results at night,
  via `--notify` flag; `core/output/telegram.py` + `core/output/__init__.py`
- **Resolution loop:** MLB via MLB Stats API — computes `was_correct` and
  stores final score in `outcome_metadata`; `user_signal_views.pnl` updated
  at resolution time
- **Signal upsert:** refreshes an active signal when confidence or EV delta
  > 0.5%; resolved/void signals are immutable
- **Today-only display:** `valid_for_date == today` filter on all terminal and
  Telegram output — future-dated signals stored but never surfaced early
- **Personal tracking:** `scripts/track.py follow <uuid> <stake>` and
  `scripts/track.py pnl` — reads `UserSignalView` stake/pnl/followed

Intentionally deferred — do not implement without discussion:
- **Soccer / World Cup model:** deferred; MLB has 162 games/season, faster
  validation cycle and more reliable Poisson fit
- **Telegram webhook / polling:** deferred to Phase 1 on Railway
- **Alembic migrations:** using `ALTER TABLE` fallback in `core/db.py` for
  Phase 0; Alembic is Phase 1
- **`users` table expansion:** `telegram_chat_id`, `stripe_customer_id`,
  preferences — Phase 1
- **`/refresh` command for premium users:** Phase 1
- **Bankroll / staking suggestions:** deferred

## Recorded design decisions

- `core/` never imports from `adapters/`; genericity is the IP.
- Domain-specific data always in jsonb (`features`, `payload`, etc.), never
  as typed columns.
- `raw_events` is append-only; it is the audit log and retraining corpus.
- Signals are global; `user_signal_views` holds per-user stake and P&L.
- Supabase **Session Mode Pooler** (port 5432) required — the direct
  connection (`db.*.supabase.co`) is IPv6-only and unreachable on this machine.
- `signals.updated_at` was added via `ALTER TABLE` fallback in `init_db()`,
  not Alembic. SQLAlchemy `onupdate=_now` keeps it current on every ORM write.
- Signal upsert fires when confidence **or** EV delta > 0.5%, and only on
  `status == "active"` signals — resolved/void are never overwritten.
- Daily output is filtered to `valid_for_date == today`; signals for future
  dates are stored but not displayed until their date arrives.
- `SENTINEL_USER_ID` in `.env` is a Phase 0 shortcut. Phase 1 replaces
  `_get_or_create_user()` in `scripts/track.py` with a JWT lookup from
  Supabase Auth — nothing else in that file needs to change.
- Back-to-back games for the same team on consecutive dates are valid,
  distinct signals — not duplicates. Dedup key is `(event_key, pick)`.
- Telegram picks message shows full signal UUID in a `<code>` block so it
  can be pasted directly into `scripts/track.py follow`.
- Soccer / World Cup model deferred — static `WORLD_CUP_RATINGS` map in
  `adapters/betting/stats.py` is a placeholder; without a real stats feed it
  just reads the market back and finds no genuine edge.

## Conventions

- Python 3.12. Type hints everywhere. `from __future__ import annotations`.
- Code comments and docstrings in **English** (portfolio is English-based).
- Commits: Conventional Commits (`feat:`, `fix:`, `ci:`, `chore:`, `docs:`).
- DB target via `DATABASE_URL` env var: Supabase/Postgres in prod, SQLite
  locally for fast iteration. See `.env.example`.
- `--mock` never calls external APIs, writes nothing to the DB, and never
  sends notifications. Always safe to run.
- `--notify` must be passed explicitly — notifications are never sent by
  default.
- GitHub Actions secrets: `DATABASE_URL`, `ODDS_API_KEY`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- `SENTINEL_USER_ID` in `.env` identifies the Phase 0 user in all scripts.
- Validate model math in isolation before wiring it to the DB or live APIs.

## Phase 0 remaining work

**Gate to Phase 1:** model shows positive ROI or break-even after 2–3 weeks
of live paper trading on MLB. Track daily with `python -m scripts.track pnl`.

Remaining Phase 0 work:
- Run `daily_picks` → follow signals → `daily_resolve` every day for 2–3 weeks
- Monitor win rate and P&L trend via `scripts/track.py pnl`
- If model validates → start Phase 1 (FastAPI + Next.js + Railway)
- If model needs work → tune `min_ev` / `min_confidence` thresholds or improve
  the MLB stats source before moving to Phase 1

## What to ask before doing

- Anything that adds domain knowledge to `core/`.
- Anything that adds typed domain columns instead of jsonb.
- Per-user pick generation.
- Implementing any item listed under "Intentionally deferred" above.
- Spending real money or placing real bets — this project paper-trades only;
  it never handles user funds or integrates with bookmakers.
