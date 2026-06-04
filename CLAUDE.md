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
- **Backtest:** `scripts/backtest.py` — MLB historical backtesting (Retrosheet
  2025, MLB Stats API 2026+), point-in-time stats, confidence band output;
  results saved to `scripts/backtest_results*.csv` (gitignored)
- **Flights adapter:** SerpAPI Google Flights, EZE→MAD default route
  - **Flexible mode** (default): auto-generates 5 weekly departure dates
    from today+7, interval 7 days — zero config needed
  - **Range mode:** `--range DATE_FROM DATE_TO` distributes 5 dates uniformly
    across a specific calendar range (e.g. monitoring August for a trip)
  - Both modes can run simultaneously on the same route;
    `get_dates_to_monitor()` returns the union, deduped and sorted
  - **`price_drop` fast-path:** fires immediately when Google's
    `price_insights` rates the price "low" and it is at/below the typical
    range floor — no prior observations needed
  - **`monthly_minimum`:** fires when current price is the cheapest seen
    this month for the route across all departure dates
  - Domain filter: flights signals never bleed into betting output and
    vice-versa; all signal queries join `Signal → Domain` and filter by slug
- **Two domains running in parallel:** betting + flights, isolated by
  `domain_id`; `core/` has zero domain-specific knowledge

Live paper trading status (update as results come in):
- Started: 2026-05-31
- Picks resolved: 29 (12W 17L, 41.4% — model v0.1.0, HA=1.10 bias confirmed)
- Model updated to v0.2.0 on 2026-06-04 (HA=1.10 → 1.04)
- Backtest (May 2026, 419 games): 58.7% accuracy, well-calibrated
- Gate to Phase 1: 30+ resolved picks with v0.2.0, win rate > 53%

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
- **Flights: additional routes beyond EZE→MAD** — configure via
  `domains.config` jsonb; deferred to Phase 1
- **Flights resolution:** re-fetch prices after 7 days to verify `was_correct`;
  stub exists in `adapter.py`, wiring deferred to Phase 1

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
- **SerpAPI Google Flights replaces Tequila/Amadeus** — both shut down their
  free/self-service tiers in early 2026. SerpAPI is the sole flights source.
- **Flights uses EZE (Ezeiza) not BUE** — Google Flights requires specific
  airport IATA codes, not city codes.
- **Range mode distributes dates uniformly** — `dates_for_range()` spaces n
  points evenly so first == `date_from` and last == `date_to`; simpler and
  more predictable than weighted distributions.
- **Both monitoring modes (flexible + range) can be active simultaneously**
  on the same route; `get_dates_to_monitor()` returns the union, deduped and
  sorted — quota cost is always `len(result)` SerpAPI requests.
- **`price_drop` fast-path bypasses `min_observations`** — Google's
  `price_insights` model has far more price history than our own observations,
  so a "low" rating with price ≤ typical floor fires immediately (n=0 ok).
- **Domain filter added to all signal queries** — `_print_by_date`,
  `_render_betting`, `_verify_supabase`, and `_verify_outcomes_supabase` all
  join `Signal → Domain` and filter by `slug`; prevents cross-domain crashes
  (e.g. `KeyError: 'match'` when flights signals reach the betting renderer).
- `run_resolution` in the orchestrator is already domain-filtered by
  `domain_id` via the adapter's `domain_slug` — confirmed, no change needed.
- **Do not tune model hyperparameters until 30+ resolved picks** — 8 picks is
  statistically irrelevant; the backtest (419 games) is the reliable signal for
  calibration direction.
- **`HOME_ADVANTAGE = 1.04` chosen to match empirical MLB home win rate (~53%).**
  At 1.10 the model generates 72.8% home picks and suppresses away value. Backtest
  confirms overall accuracy is insensitive to this parameter (57.8–58.2% across
  HA=1.00–1.10); the fix improves calibration without sacrificing performance.
- **`HOME_ADVANTAGE` changed 1.10 → 1.04 on 2026-06-04 at the 30-pick gate.**
  Live analysis of 10 resolved 70%+ picks (30% win rate, 100% home) confirmed
  the bias was active: WSH, NYY, MIL were reaching 70%+ only because HA inflated
  their true 64–67% probability. At HA=1.04, 4 of the 7 losing 70%+ picks are
  filtered out entirely. `model_version` bumped to `poisson_v0.2.0`; all future
  signals are tagged for A/B comparison against v0.1.0 picks in the DB.

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
- `--range DATE_FROM DATE_TO` overrides route config for flights range mode
  testing without touching Supabase (pairs with `--domain flights`).
- `--domain flights` selects the flights adapter throughout all scripts.
- **SerpAPI quota:** 5 requests/run by default (one per monitored date),
  100/month free tier — quota used is logged on every run and recorded in
  `model_runs.hyperparams.serpapi_quota_used`.
- Backtest results are saved to `scripts/backtest_results*.csv` (gitignored).

## Phase 0 remaining work

**Gate to Phase 1:** 30+ resolved picks with v0.2.0, win rate > 53%.
Currently at 29 resolved picks (v0.1.0). Running daily with v0.2.0 from 2026-06-04.

Decision tree at 30+ v0.2.0 picks:
- Win rate **> 53%** → start Phase 1
- Win rate **45–53%** → consider recency weighting in MLBStatsProvider (last-15-games blend)
- Win rate **< 45%** → investigate systematic model issue first

**Next model improvement (consider at 50+ v0.2.0 picks):**
- Add recency weighting to `MLBStatsProvider.runs_per_game()`: blend season avg (70%)
  with last-15-games avg (30%). Evidence: WSH, NYY, MIL significantly underscored
  their season RPG averages in observed losses (avg 2.3 actual vs 5.8 expected for WSH).
  Defer until HA fix effect is measurable — needs 20+ v0.2.0 picks to isolate.

Phase 1 will include:
- FastAPI service (`api/` scaffold already exists)
- Railway deployment (Python service + Telegram webhook)
- Next.js web app (auth, dashboard, Stripe billing)
- Alembic migrations replacing `ALTER TABLE` fallback
- `users` table expansion (`telegram_chat_id`, `stripe_customer_id`,
  preferences, timezone)
- Multi-user tracking replacing `SENTINEL_USER_ID` shortcut

## What to ask before doing

- Anything that adds domain knowledge to `core/`.
- Anything that adds typed domain columns instead of jsonb.
- Per-user pick generation.
- Implementing any item listed under "Intentionally deferred" above.
- Spending real money or placing real bets — this project paper-trades only;
  it never handles user funds or integrates with bookmakers.
