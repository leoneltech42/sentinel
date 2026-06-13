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
  > 0.5%; resolved/void/expired signals are immutable. Justification
  preserved across minor upserts; cleared and flagged for regeneration when
  pick changes team or EV delta > 10pp (`justification_regenerated` in features).
- **Today-only ingestion:** events filtered to `commence_time.date() == today
  UTC` at ingestion time — eliminates tomorrow-game churn between runs.
  Logs "Filtered N future-date events (tomorrow or later)".
- **Today-only display:** `valid_for_date == today` filter on all terminal and
  Telegram output — future-dated signals stored but never surfaced early
- **Personal tracking:** `scripts/track.py follow <uuid> <stake>` and
  `scripts/track.py pnl` — reads `UserSignalView` stake/pnl/followed.
  Both `pnl` and `global` default to `poisson_v0.3.0` only; use
  `--version all` or `--version v0.X.X` for historical comparison.
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
- **poisson_v0.3.0** (2026-06-08): three model improvements applied together —
  starting pitcher ERA adjustment (`starter_era / league_avg_era` ratio),
  70/30 recent-form blend (season avg + last-15 games), 50/50 tie
  redistribution (extra innings ≈ coin flip). Model version tracked in
  `model_runs` for A/B comparison against prior versions.
- **LLMJustifier:** generic OpenAI-compatible client for pick justifications
  (`adapters/betting/justification.py`). Default: Groq Llama 3.3 70B free
  tier. Configured via `LLM_JUSTIFIER_API_KEY` / `LLM_JUSTIFIER_BASE_URL` /
  `LLM_JUSTIFIER_MODEL`. `--mock` never calls the LLM; failures degrade
  gracefully (`justification: None` stored, no crash).
- **Refresh display:** matches daily picks format — `📌/⚪` follow status,
  Edge/EV line, `💡` justification, `🔄💡` when justification was cleared and
  will regenerate, delta indicators (`📈/📉`) for odds movement vs morning run.
- **World Cup disabled by default:** `active_sports=['mlb']` in
  `BettingAdapter`. World Cup stays registered in `ALL_SPORT_KEYS` and can
  be re-enabled via `domains.config` jsonb — it never generated valid picks
  because the static ratings map is a placeholder with no real stats feed.

Live paper trading status:
- Started: 2026-05-31
- v0.1.0: 30 picks resolved (13W/17L, 43.3%) — HA=1.10 bias confirmed
- v0.2.0: 28 picks resolved (10W/18L, 35.7%) — HA fixed but still underperforming
- v0.3.0: 48 picks resolved (30W/18L, 62.5%) — gate passed 2026-06-13
  Flat ROI: +23.1% · Kelly ROI: +22.5% · Total staked: 216.3u
  By confidence: 50–60% → 66.7% (n=9) · 60–70% → 53.8% (n=13) · 70%+ → 65.4% (n=26)
- ✅ Phase 0 gate cleared: 48 resolved picks (gate: 30), 62.5% win rate (gate: 53%)

Intentionally deferred — do not implement without discussion:
- **Soccer / World Cup model:** deferred; MLB has 162 games/season, faster
  validation cycle and more reliable Poisson fit
- **Telegram webhook / polling:** deferred to Phase 1 on Railway
- **Alembic migrations:** using `ALTER TABLE` fallback in `core/db.py` for
  Phase 0; Alembic is Phase 1
- **`users` table expansion:** `telegram_chat_id`, preferences — Phase 1
  (stripe_customer_id removed — billing handled by external platform)
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
  `status == "active"` signals — resolved/void/expired are never overwritten.
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
- **poisson_v0.3.0 (2026-06-08): three fixes applied together** — pitcher ERA
  adjustment using `starter_era / league_avg_era` ratio (corrected direction:
  a good opposing starter suppresses your team's expected runs), 70/30
  recent-form blend (season avg + last-15 games via MLB Stats API), 50/50 tie
  redistribution (extra innings ≈ coin flip; prior proportional split was
  wrong in theory). `model_version` bumped; all signals tagged for comparison.
- **LLMJustifier is generic** — `base_url` and `model` are configurable. Switching
  from Groq to Claude or OpenAI requires only env var changes, zero code changes.
  Failures degrade gracefully: `justification: None` stored, pipeline continues.
- **Today-only ingestion filter uses UTC date** for consistency with the rest of
  the system. Games that commence after midnight UTC (e.g. 00:30 UTC) appear
  as "today" UTC even if they feel like "last night" in ART — acceptable
  tradeoff; the alternative (ART-aware filtering) would add timezone complexity
  to a layer that is otherwise UTC-only.
- **Justification regeneration threshold: pick team change OR EV delta > 10pp.**
  Below that threshold, the existing justification text is preserved across
  minor odds-noise upserts to avoid unnecessary LLM quota burn. When cleared,
  `justification_regenerated = True` is written to `features` (jsonb — no
  schema change) and the `🔄` indicator appears in CLI and Telegram refresh.
- **P&L commands default to the current model version (v0.3.0)** — never
  silently mix model versions in analysis. `--version all` shows all versions
  with a breakdown table; `--version v0.X.X` selects a specific version.
  Older models had known calibration issues; mixing them would distort the
  current model's read.
- **`active_sports=['mlb']` default** — World Cup excluded at the adapter
  level (not by removing it from `ALL_SPORT_KEYS`). It never generated valid
  picks because `WORLD_CUP_RATINGS` is a placeholder. Can be re-enabled via
  `domains.config` jsonb when a real ratings feed is available.
- **Void signals have no `signal_outcomes` row by design** — `run_resolution()`
  skips void signals without inserting an outcome. `_verify_outcomes_supabase()`
  uses a separate `_fetch_void()` query to surface them as `[void]` in terminal
  output. Do not add outcome rows for void signals.
- **Telegram message hard limit is 4096 chars (HTML mode)** — 
  `_send_results_notification()` uses a 1-day backfill window (yesterday only)
  to stay well under the limit. `TelegramChannel._broadcast()` auto-splits any
  message exceeding 3800 chars on newline boundaries as a safety net.

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
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `LLM_JUSTIFIER_API_KEY`,
  `LLM_JUSTIFIER_BASE_URL`, `LLM_JUSTIFIER_MODEL`.
- `LLM_JUSTIFIER_API_KEY` / `LLM_JUSTIFIER_BASE_URL` / `LLM_JUSTIFIER_MODEL`:
  any OpenAI-compatible chat API for pick justifications (defaults to Groq's
  free tier — groq.com, no credit card). Optional; never called in `--mock`
  mode, and failures degrade gracefully (signal stores `justification: None`).
- `SENTINEL_USER_ID` in `.env` identifies the Phase 0 user in all scripts.
- Validate model math in isolation before wiring it to the DB or live APIs.
- `--range DATE_FROM DATE_TO` overrides route config for flights range mode
  testing without touching Supabase (pairs with `--domain flights`).
- `--route ORIG DEST` overrides the default EZE→MAD route with any IATA pair.
  Combine with `--range` for date-range mode on a custom route.
  Examples: `--domain flights --route JFK LHR`
            `--domain flights --route EZE MIA --range 2026-08-01 2026-08-31`
- `--domain flights` selects the flights adapter throughout all scripts.
- **SerpAPI quota:** 5 requests/run by default (one per monitored date),
  100/month free tier — quota used is logged on every run and recorded in
  `model_runs.hyperparams.serpapi_quota_used`.
- Backtest results are saved to `scripts/backtest_results*.csv` (gitignored).
- `python -m scripts.track global --version all` — show all model versions
  with a per-version breakdown table (picks, W/L, win rate).
- `python -m scripts.track pnl --version v0.X.X` — personal P&L filtered to
  a specific model version. Default for both commands is `poisson_v0.3.0`.

## Phase 1 scope

Goal: personal dashboard to track daily picks, follow/unfollow signals, and
view P&L — global and per followed picks. Signal distribution handled
externally (Dubclub or equivalent). No multi-user, no billing layer.

**FastAPI (api/)**
- Authentication: X-API-Key header validated against SENTINEL_API_KEY env var.
  Return 401 on missing or invalid key.
- Endpoints:
  - GET  /picks?date=YYYY-MM-DD&sport=baseball&league=mlb
  - GET  /outcomes?date=YYYY-MM-DD&sport=baseball&league=mlb
  - POST /signals/{id}/follow    — body: {"stake": float | null}
                                   if null, defaults to features['stake_units']
  - DELETE /signals/{id}/follow
  - GET  /pnl/global             — all resolved picks for SENTINEL_USER_ID
  - GET  /pnl/personal           — followed picks only
- sport and league are independent optional filters. ?sport=baseball returns
  all baseball leagues. ?league=mlb returns MLB only. Both together are
  equivalent to ?league=mlb.
- sport/league are derived at read time by splitting features['sport'] on
  the first underscore: "baseball_mlb" → sport="baseball", league="mlb".
  No schema change — existing picks are untouched.
- SENTINEL_USER_ID env var identifies the single user (Phase 0 shortcut
  retained for Phase 1).
- Alembic migrations replace ALTER TABLE fallback in core/db.py.

**Betting adapter restructure (adapters/betting/)**
- Current monolithic BettingAdapter splits into per-league sub-adapters.
- Shared logic (de-vig, EV, Kelly sizing, signal schema) moves to
  base_betting.py. Per-league logic (ingesta, model, resolution, stats
  API client) lives in its own subdirectory.
- A registry maps league slug → adapter class so new leagues are added
  by creating a folder and registering, without touching existing code.
- domains.config jsonb stores active leagues and per-league config params
  (API keys, thresholds). The registry reads this at runtime.
- This restructure is scaffolded in Phase 1 but MLB logic is not moved
  yet — migration happens as a separate task after scaffold is confirmed.

Target structure:
  adapters/betting/
  ├── base_betting.py      — shared: de-vig, EV, Kelly, SignalData schema
  ├── registry.py          — maps league slug → adapter class; reads
                             domains.config to determine active leagues
  ├── mlb/
  │   ├── adapter.py       — ingesta + model + resolution (current MLB logic)
  │   ├── stats.py         — MLB Stats API client
  │   └── model.py         — Poisson v0.3.0
  └── __init__.py          — exports BettingAdapter (facade over registry)

**Next.js 15 (web/)**
- HTTP Basic Auth via middleware on all routes. Credentials from
  DASHBOARD_USER and DASHBOARD_PASSWORD env vars.
- Views:
  - /             — today's picks: EV, confidence stars, sizing, follow toggle
  - /date/[date]  — same for a past date
  - /pnl          — global P&L and personal P&L side by side
- Hosted on Vercel. Never touches Postgres directly — all data via FastAPI.

**Infra**
- FastAPI on Railway. Next.js on Vercel.
- New env vars: SENTINEL_API_KEY, DASHBOARD_USER, DASHBOARD_PASSWORD.
- Add all three to .env.example at repo root.
- Existing GitHub Actions workflows unchanged.

## What to ask before doing

- Anything that adds domain knowledge to `core/`.
- Anything that adds typed domain columns instead of jsonb.
- Per-user pick generation.
- Implementing any item listed under "Intentionally deferred" above.
- Spending real money or placing real bets — this project paper-trades only;
  it never handles user funds or integrates with bookmakers.
