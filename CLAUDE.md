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
- **poisson_v0.3.1 — LIVE as of 2026-06-20.** Two changes shipped together:
  1. **Post-hoc isotonic calibration of `model_probability`** — analysis of
     103 resolved poisson_v0.3.0 picks found the raw probability
     overconfident by ~16pp (raw Brier 0.272 vs 0.246 baseline).
     `scripts/calibrate.py` fits `sklearn.isotonic.IsotonicRegression`
     against accumulated resolved picks and serializes it to
     `adapters/betting/calibration_v1.joblib`. `BettingAdapter` loads it at
     init (`adapters/betting/calibration.py`) and passes raw
     `model_probability` through it before deriving edge, EV, Kelly units,
     confidence, and star rating — every downstream *stored/displayed*
     value is calibrated, never raw. Both `raw_model_probability` and the
     calibrated `model_probability` are stored in `features` (jsonb).
     Manual recalibration only — **not** auto-retrained in the daily
     pipeline; **re-run `scripts/calibrate.py` after the next ~50 newly
     resolved picks** and overwrite `calibration_v1.joblib`.
  2. **`HOME_ADVANTAGE` 1.04 → 1.05** in `adapters/betting/stats.py` —
     confirmed via the backtest sweep (`scripts/ha_sweep.py`) and the
     103-pick live re-simulation (`scripts/ha_resim.py`); see below.
  - **RESOLVED 2026-06-20 — gating now uses calibrated probability, not
    raw.** Previously `generate_signals()` called `find_value_bets()` with
    the *raw* `model_probs` list, so a pick could clear
    `min_ev`/`min_confidence`/market-edge on overconfident raw numbers
    while its real (calibrated) EV was negative. Fixed: each selection's
    probability is calibrated *before* `find_value_bets()` runs, so the
    gate and the stored/displayed values are always the same number.
    `raw_model_probability` is still computed and stored separately for
    transparency — it's just no longer the gating criterion. Confirmed via
    mock: the previous mock fixture (Dodgers @ 1.55, raw EV +32.6%,
    calibrated EV +3.3%) now correctly fails the 5% min_ev gate and
    generates **zero** signals instead of one — exactly the failure mode
    this fix closes. A synthetic strong-favorite fixture confirmed the
    positive path still works (signal generated, calibrated prob clears
    both thresholds, raw probability still present in `features`).
- **`era_diff` / `era_advantage_tier` now persisted in `features`:** same
  103-pick analysis found `era_diff` the only feature with consistent
  signal across every test. Defined as `(picked team's own starter ERA) -
  (opponent's starter ERA)` — very negative favors the pick (own starter
  suppresses the opponent's offense *and* the opponent's starter is weak).
  Tiered (`strong` / `moderate` / `weak`) for dashboard visibility; no
  filter or sizing penalty applied yet — observe first, decide on a rule
  once more data confirms the pattern holds.
- **`HOME_ADVANTAGE` 1.04 → 1.05 shipped in poisson_v0.3.1 (2026-06-20).**
  The 103-pick live sample showed home picks winning 66% vs 48% away while
  the model assigned them nearly equal probability — 1.04 was understating
  the real home-field effect. Confirmed via two checks before shipping:
  `scripts/ha_sweep.py` sweeps HA against the 419-game May-2026 backtest
  dataset (1.05 minimizes Brier within a sane home-pick-rate band, well
  short of the v0.1.0 100%-home bias at 1.10); `scripts/ha_resim.py`
  re-simulated all 103 live picks at 1.05 (Brier 0.2722 → 0.2684, **zero
  picks flip sides**, home Brier 0.2239→0.2218, away Brier 0.3127→0.3075).
  Both tools remain available for re-validating future HA candidates — they
  don't hardcode 1.05, they print a recommendation for review. Still
  flagged as needing more live data (n≥200–250) for full confidence —
  re-evaluate alongside the next calibration refresh.
- **`scripts/bin_analysis.py` (Investigation 1): the 60–70% confidence
  bin's 37.5% win rate is real, not noise.** A binomial test against the
  bin's declared ~65% confidence gives p=0.0023 (n=32) — statistically
  significant overconfidence, not sample variance. No single feature
  (era_diff, home/away split, odds, market probability, edge) distinguishes
  this bin from its 50–60%/70–80% neighbors; the only significant
  era_diff difference is between 60–70% and 70–80% (p<0.001), which is
  just neighbouring bins differing from each other, not a property unique
  to the 60–70% bin itself. Conclusion: this is exactly the kind of
  non-monotonic miscalibration isotonic regression is built to fix — no
  new feature or filter is indicated; the existing calibration work
  already addresses it.
- **`scripts/era_weight_sensitivity.py` (Investigation 2): the formula
  lives in `adapters/betting/stats.py::mlb_lambdas()`**, not
  `adapters/betting/mlb/model.py` — that path assumes the Phase 1
  per-league restructure, which hasn't happened (see Phase 1 scope below).
  Sweeping the ERA-ratio exponent (`(starter_era/league_avg_era) **
  exponent`) from 0.5→1→2 while holding HOME_ADVANTAGE at the production
  value (1.04) and RPG blend constant: Brier **improves** at exponent=0.5
  (0.2469) vs the current exponent=1 (0.2689), and **gets worse** at
  exponent=2 (0.3185) — the opposite direction from what era_diff's
  strong univariate signal might suggest. By era tier, "weak" (era_diff >
  -1.05) wins only 41.9% (n=31) while "strong" (era_diff < -1.99) wins
  71.9% (n=32) — the tiering itself holds up, but *amplifying* the ERA
  term in the lambda formula doesn't capture that any better; it overshoots
  and makes the model more confident in the wrong direction. Sensitivity
  direction only — not a formula change. era_diff's predictive power may
  be better captured by a sizing/filter rule on top of the existing
  formula (the original era_advantage_tier scaffold) rather than reshaping
  the lambda math itself. Needs more data and/or a proper grid search
  before any change ships.

Live paper trading status:
- Started: 2026-05-31
- v0.1.0: 30 picks resolved (13W/17L, 43.3%) — HA=1.10 bias confirmed
- v0.2.0: 28 picks resolved (10W/18L, 35.7%) — HA fixed but still underperforming
- v0.3.0: 48 picks resolved (30W/18L, 62.5%) — gate passed 2026-06-13
  Flat ROI: +23.1% · Kelly ROI: +22.5% · Total staked: 216.3u
  By confidence: 50–60% → 66.7% (n=9) · 60–70% → 53.8% (n=13) · 70%+ → 65.4% (n=26)
  Final tally at retirement (2026-06-20, n=103): 56.3% win rate. Raw Brier
  0.272, worse than the 0.246 constant-mean baseline — the overconfidence
  finding that triggered the v0.3.1 calibration work.
- ✅ Phase 0 gate cleared: 48 resolved picks (gate: 30), 62.5% win rate (gate: 53%)
- **poisson_v0.3.1 shipped 2026-06-20** — isotonic calibration +
  `HOME_ADVANTAGE` 1.04→1.05 (see decisions above). 0 picks resolved yet;
  first live `daily_picks` run pending confirmation.

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
- **Investigated 2026-06-22: no missing "6th pick" on 2026-06-21 — the
  reported count was off, not the system.** All 5 signals for that date
  are accounted for: 4 inserted by the 12:27 UTC `daily_picks` run
  (Reds, Giants, Angels, Phillies), 1 (Padres) newly qualified and
  inserted by the 16:58 UTC `--refresh` run when the Rangers-Padres
  game's odds moved — its `RawEvent` existed from the morning ingest but
  didn't clear the calibrated EV/confidence gate until refresh. That's
  4+1=5, matching both the resolution output and the web dashboard
  exactly — there's no invisible 6th signal. All three hypothesized
  causes (model_version mismatch, status filter, UTC date-shift) were
  individually ruled out — all 5 signals are `poisson_v0.3.1`,
  `status='resolved'`, `valid_for_date=2026-06-21`. Replaying that
  morning's stored odds through `generate_signals()` *today* surfaces a
  6th candidate (Dodgers vs Orioles) — but this is reconstruction drift,
  not a missed signal: the replay uses today's live pitcher-ERA/recent-RPG
  data, which has shifted since 6/21 (same caveat documented for
  `scripts/ha_resim.py`). Noted here so a future session doesn't re-chase
  this red herring.
- **`Signal.model_run_id` always reflects the most recent run that
  touched it, not the run that created it.** `run_pipeline()`'s upsert
  path (`core/orchestrator.py`) sets `existing.model_run_id = run.id` on
  every update, even a same-pick odds-noise refresh. Confirmed while
  investigating the above: the morning run's `model_runs` row showed
  `n_signals=0` because all 4 signals it created were later touched by
  the afternoon refresh and reassigned to *that* run's id. Cosmetic
  surprise, not a bug — `Signal.created_at` is still the reliable field
  for "when was this signal first generated."
- **Telegram results message hides which date a backfilled pick belongs
  to.** Confirmed 2026-06-22: a same-matchup back-to-back series (e.g.
  Phillies vs Mets on consecutive days) renders identically in the
  "Backfilled from previous days" section except for score — looks like
  a duplicate/re-resolved pick, but `signal_outcomes.resolved_at` confirms
  each was resolved on its own day's run, never re-resolved (the
  `existing_outcome` guard in `run_resolution()` is working correctly —
  zero signals in the 7-day backfill window were found in a pending
  `active`/`expired` state, all are already `resolved` or `void`).
  Fixed: `core/output/telegram.py::_format_results()`'s backfill section
  now appends a `[YYYY-MM-DD]` date tag per row (`_render_section(...,
  show_date=True)`) and the label changed from "Backfilled from previous
  days" to "Previously resolved (last 7 days)" to stop implying these
  were just resolved by the current run. The terminal output
  (`scripts/paper_trade.py::_verify_outcomes_supabase`) already had a
  per-row date tag; only its section header was relabeled to match
  ("Previously resolved (last 7 days)", was "Historical backfill (7-day
  window)") — no behavior change there, display-only fix in both places.
- **Telegram no longer carries follow commands or a backfill section
  (2026-06-22) — following and history live on the web dashboard now.**
  `_format_picks()`/`_format_refresh()` no longer append
  `python -m scripts.track follow <id>` lines; `_format_results()` no
  longer renders the "Previously resolved" section (today's resolved
  picks only). `_send_results_notification()` in `paper_trade.py` now
  queries only `for_date` instead of a 1-day backfill window, since the
  data it fetched is no longer rendered. Terminal output
  (`_verify_outcomes_supabase()`) is unchanged — still shows the 7-day
  backfill window for debugging.
- **`POST /refresh` (web dashboard on-demand refresh) reuses the exact
  same pipeline path as `daily_refresh.yml`** — `adapters/betting/
  refresh.py::build_betting_adapter()` was extracted from
  `scripts/paper_trade.py`'s CLI-coupled `_build_betting_adapter(args)`
  so both the CLI and the API call the identical adapter-construction
  logic (env vars / mock fixtures), not a duplicate. `run_refresh()` in
  the same module wraps `run_pipeline()` and diffs a pre-run
  `snapshot_signals()` snapshot to report `odds_updated`/
  `justifications_updated` counts. The endpoint
  (`api/routers/refresh.py`) runs this in a FastAPI `BackgroundTask` and
  returns `{"status": "started"}` immediately (202) — idempotent, since
  it relies on `run_pipeline()`'s existing upsert-or-insert semantics.
  ⚠️ **`TestClient` runs `BackgroundTasks` synchronously before
  returning** (unlike a real deployed server) — verifying this endpoint
  with `TestClient` triggers a real live-API refresh against
  `DATABASE_URL`/`ODDS_API_KEY`. Harmless (same idempotent operation
  `daily_refresh` already runs), but worth knowing before writing
  automated tests against this route.
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
- **Isotonic regression chosen over Platt scaling for probability
  calibration** — the miscalibration pattern across confidence bins is
  non-monotonic (the 60–70% bin actually wins *less* than the 50–60% bin
  in the 103-pick sample), which a single-sigmoid Platt fit can't represent
  but isotonic's free-form monotonic step function can.
  `out_of_bounds='clip'` so probabilities outside the training range don't
  extrapolate wildly. See `scripts/calibrate.py`.
- **Calibration gating uses the raw probability, not the calibrated one** —
  `find_value_bets()` (which decides whether a selection clears
  `min_ev`/`min_confidence`/market-edge) runs on raw `model_probability`,
  matching the thresholds as originally tuned. Only the *stored* downstream
  values (edge, EV, Kelly units, confidence, stars) are recomputed from the
  calibrated probability. A bet that qualified on raw confidence can still
  show negative calibrated EV — that's intentional signal, not a bug.
- **`raw_model_probability` only exists in `features` from poisson_v0.3.1
  onward** — `scripts/calibrate.py` falls back to reading `model_probability`
  for any pre-v0.3.1 row that lacks the `raw_` field, since at that point
  the stored value *was* the raw one.
- **API default model_version is a semantic-version floor, not a hardcoded
  exact string.** `GET /outcomes`, `GET /pnl/global`, `GET /pnl/personal`
  used to default `model_version` to the literal `"poisson_v0.3.0"` —
  every time a new version shipped, default views would silently exclude
  it until someone updated the hardcoded string. Fixed via
  `api/lib/versioning.py`: when `model_version` is *omitted*, the query
  filters to all versions where `meets_baseline(version,
  PRODUCTION_MODEL_BASELINE)` is true (parsed as `poisson_vMAJOR.MINOR.PATCH`
  tuples, compared as tuples). `PRODUCTION_MODEL_BASELINE` defaults to
  `poisson_v0.3.0` — v0.1.0/v0.2.0 stay permanently excluded from default
  views (discarded for cause — HOME_ADVANTAGE bugs — not just superseded).
  An *explicit* `model_version=<exact>` or `model_version=all` param
  bypasses the floor entirely and behaves exactly as before — the floor
  only kicks in when the param is absent. The web dropdown's default
  option now reads "Production (vX.Y.Z+)" and omits the `model_version`
  query param when selected, rather than sending a hardcoded version
  string — see `web/components/pnl/FilterBar.tsx`.

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
- `python -m scripts.calibrate` — re-fit the probability calibrator against
  accumulated resolved `poisson_v0.3.0`+ picks; re-run every ~50 newly
  resolved picks and overwrite `adapters/betting/calibration_v1.joblib`.
  Manual step, never run automatically by the daily pipeline. `--mock` uses
  an in-memory DB to prove it never touches Supabase.
- `python -m scripts.ha_sweep` — sweep `HOME_ADVANTAGE` against the backtest
  dataset; prints a table only, never modifies `adapters/betting/stats.py`.

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
- New env vars: SENTINEL_API_KEY, DASHBOARD_USER, DASHBOARD_PASSWORD,
  PRODUCTION_MODEL_BASELINE.
- Add all four to .env.example at repo root.
- ⚠️ **PRODUCTION_MODEL_BASELINE must be added to Railway's environment
  variables manually** (dashboard or `railway variables set`) — not set via
  this session. Defaults to `poisson_v0.3.0` in code if unset, so omitting
  it on Railway is non-breaking, just loses the ability to advance the
  floor without redeploying.
- Existing GitHub Actions workflows unchanged.

## What to ask before doing

- Anything that adds domain knowledge to `core/`.
- Anything that adds typed domain columns instead of jsonb.
- Per-user pick generation.
- Implementing any item listed under "Intentionally deferred" above.
- Spending real money or placing real bets — this project paper-trades only;
  it never handles user funds or integrates with bookmakers.
