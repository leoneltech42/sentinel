# Sentinel — Data-driven signals framework

> A generic engine for ingesting, modeling, and generating signals that works
> over any domain with an available data API. First deployment: a SaaS platform
> for sports value betting.

**Status:** design · in development
**Author:** Leonel Roa
**Last updated:** May 2026

---

## 1. Executive summary

Sentinel is a framework that takes raw data from any API source, normalizes it,
runs models over it, and produces **signals** — detected opportunities with an
associated confidence score and expected value. It then measures whether those
signals were correct and reports results.

The framework is **domain-agnostic** by design. The logic specific to each
business lives in interchangeable *adapters*. The same engine that detects
positive expected-value bets can detect cheap flights, undervalued properties,
or crypto trading signals — without touching the core.

The first deployment is an in-house product: a SaaS where a user signs up and
receives suggested daily sports bets, each with a confidence level, and can
track their performance. This deployment validates the framework with a real
production use case and produces the portfolio's main case study.

### Dual objective

1. **Product:** a functional betting SaaS, monetizable via freemium +
   subscription, with real users.
2. **Portfolio:** demonstrate the ability to build data-driven decision systems
   for any niche with an API.

---

## 2. Business model

### In-house product (betting)

- **Freemium:** 3 picks per day for free.
- **Monthly subscription:** unlimited picks, full history, alerts.
- The user sees the suggested picks and bets on their own with whichever
  bookmaker they choose. No integration with bookmakers, no handling of user
  funds.
- **Global picks** (all users see the same daily picks) with **per-user
  tracking** (each user records what they followed and their P&L).

### Framework as a service (B2B)

The expansion model: B2B clients contract the system and the operator configures
a domain adapter for them. The software is not sold as self-service; what's sold
is the ability to deploy a decision system in the client's niche in days, not
months.

---

## 3. Three-layer architecture

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 1 — Generic framework (the reusable IP)               │
│  Ingestion · Model engine · Signal engine · Resolution ·     │
│  Multi-channel output · Notifications                        │
│  Domain-agnostic. It doesn't know what a bet is.             │
└─────────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────────┐
│  LAYER 2 — Domain adapters (plug-ins)                        │
│  Betting · Flights · Real estate · Crypto · ...              │
│  One adapter per domain or client. The core never changes.   │
└─────────────────────────────────────────────────────────────┘
                            ▲
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3 — SaaS product (first deployment: betting)          │
│  Web app · Auth · Freemium billing · 18+ compliance ·        │
│  Dashboard · Per-user tracking                               │
└─────────────────────────────────────────────────────────────┘
```

### Guiding principle: the framework owns the data

The Python framework is the **sole owner** of the schema and the business logic.
It exposes a REST API. The web app is just a client of that API and never
touches the database directly.

This decouples the framework from the product: the same backend can power the
betting SaaS, a B2B client dashboard, a mobile app, or a webhook, without
changing a single line of the core.

---

## 4. Data model

Two groups of tables: **framework** tables (agnostic) and **product** tables
(the SaaS layer).

### Framework tables

| Table | Purpose |
|-------|---------|
| `domains` | One row per instance/client. Everything hangs off this via `domain_id`, guaranteeing isolation without separate DBs. |
| `data_sources` | Configuration of each consumed API: provider, endpoint, encrypted credentials (`auth_config` jsonb), status. |
| `raw_events` | Append-only log of everything ingested. Never modified or deleted. `payload` in jsonb. Basis for retraining models. |
| `model_runs` | Each engine execution: version, hyperparameters, score. Enables comparing model versions over time. |
| `signals` | The model output. A detected opportunity with type, confidence, EV, and `features` in jsonb. The central table. |
| `signal_outcomes` | Closes the loop: whether the signal was correct and the actual value. Feeds continuous backtesting. |

### Product tables

| Table | Purpose |
|-------|---------|
| `users` | User, their `plan` (free/paid) and `domain_id`. |
| `user_signal_views` | Per-user tracking: whether the user saw the pick, followed it, their stake, their P&L. |

### Central design decision: jsonb for domain-specific data

The fields that vary by domain (`payload`, `features`, `auth_config`,
`hyperparams`) are **jsonb**, not columns. This is what makes the framework
genuinely generic: the schema doesn't need to know what fields The Odds API
brings versus Binance versus Amadeus.

The query performance tradeoff is solved with **indexes over jsonb**, defined by
each adapter according to its query patterns — without touching the core schema:

```sql
-- General index over features
CREATE INDEX idx_signals_features ON signals USING gin (features);

-- Partial index specific to the betting adapter
CREATE INDEX idx_signals_sport
  ON signals ((features->>'sport'))
  WHERE domain_id = '<uuid-betting>';
```

### Generic fields for flexible resolution

The cold run with crypto revealed that different domains resolve their signals
differently. Two fields were added to `signals`, both agnostic:

- `valid_until` (timestamp, nullable) — validity window. `NULL` = no expiry.
- `resolution_rule` (string) — `binary` | `threshold` | `continuous`.

Betting uses `binary` + `valid_until: NULL`. Crypto uses `threshold` with a 24h
window. The framework only applies the rule; it doesn't know which domain it is.

---

## 5. Use cases

### End user (the bettor)
- Sign up and log in
- See daily picks with confidence and EV
- Filter picks by sport/league
- Mark a followed pick and record their stake
- See personal history and cumulative P&L
- See global system stats (win rate, ROI, CLV)
- Receive picks via email and/or Telegram
- Manage subscription (upgrade, cancel)
- Configure preferences (sports, channel, bankroll)

### System / framework (automated processes)
- Ingest data from configured APIs on schedule
- Detect and avoid duplicate events
- Run the model and generate signals with their score
- Persist each run for traceability
- Resolve signals at close and compute outcome + CLV
- Update P&L for users who followed each signal
- Fire notifications through configured channels
- Retrain/evaluate the model with historical data (continuous backtesting)
- Handle API failures (retries, alerts if a source goes down)

### Operator / owner
- Onboard a new domain
- Configure a new data source (API + credentials)
- Enable/disable sources
- See system health metrics
- Compare performance across model versions
- Tune hyperparameters (EV threshold, minimum confidence)
- See the business dashboard (users, conversion, MRR)

### Monetization
- Process payments and subscriptions (Stripe)
- Enforce the free-tier limit (3 picks/day)
- Manage the subscription lifecycle

### Compliance (from the MVP)
- Responsible-gambling disclaimer
- Age verification (18+)

---

## 6. Flow walkthrough (validated cold run)

The four core flows were walked through cold with sample data and the schema
supported them with no structural changes.

1. **Ingestion** — raw payload from The Odds API → `raw_events` untransformed.
   The `event_key` prevents duplicates.
2. **Modeling** — daily cron reads `raw_events`, records a `model_run`, and for
   each opportunity generates a `signal` with its confidence, EV, and features.
3. **Resolution** — at match close, `signal_outcomes` records whether it hit,
   the actual value, the closing line, and the CLV.
4. **User tracking** — `user_signal_views` records what the user followed, their
   stake, and their P&L, updated when the outcome resolves.

The chain `raw_events → model_runs → signals → signal_outcomes` is complete and
traceable: you can reconstruct exactly what data the model used for any
historical pick.

---

## 7. Tech stack

### Framework + API (Python)
- Python 3.12 + FastAPI
- SQLAlchemy 2.0 + Alembic (the framework owns the schema)
- Pydantic (validation and API contracts)
- Pandas, NumPy, scikit-learn; statsmodels (Poisson for betting)

### Web app (TypeScript)
- Next.js 15 (App Router) + TypeScript
- Tailwind + shadcn/ui
- Client of the FastAPI API; never touches Postgres

### Data & infrastructure
- Managed PostgreSQL on Supabase (DB + optional auth)
- Stripe (freemium billing)
- Scheduling: Railway cron / APScheduler in the MVP →
  Celery + Redis once streaming enters (Phase 2)

### Hosting
- Railway (Python service: API + worker)
- Vercel (Next.js)

---

## 8. Repository structure

Monorepo, because the structure itself tells the three-layer story.

```
sentinel/
├── core/                      # LAYER 1 — domain-agnostic framework (the IP)
│   ├── ingestion/             # multi-cadence sources → raw_events
│   ├── models/                # model engine, run versioning
│   ├── signals/               # signal engine: EV, confidence, ranking
│   ├── resolution/            # resolves outcomes per resolution_rule
│   └── output/                # channels: email, telegram, webhook
│
├── adapters/                  # LAYER 2 — one plug-in per domain
│   ├── base.py                # interface every adapter implements
│   ├── betting/               # odds → poisson → value_bet (binary)
│   ├── flights/               # amadeus → time series → threshold
│   ├── realestate/            # comparables → continuous
│   └── crypto/                # binance → TA → threshold (stream)
│
├── api/                       # FastAPI: exposes the framework as REST
│   ├── routers/
│   └── main.py
│
├── web/                       # LAYER 3 — SaaS product (Next.js)
│   ├── app/
│   └── components/
│
├── migrations/                # Alembic — the schema lives here
├── tests/
└── docker-compose.yml         # spins up the whole local stack
```

`adapters/base.py` defines the contract any new domain must fulfill (how it
ingests, models, resolves). Adding a B2B client = creating a folder in
`adapters/` that implements that interface. The `core/` is never touched.

---

## 9. Build phases

| Phase | Objective | Scope | Approx. duration |
|-------|-----------|-------|------------------|
| **Phase 0** | Validate the model | 1 sport (MLB + World Cup), 1 model, output to Notion + email/Telegram. No product. | 4–6 weeks |
| **Phase 1** | Public MVP | Web app, auth, dashboard, freemium billing, Telegram bot, 18+ compliance, first paying users. | 8–12 weeks |
| **Phase 2** | Scale & monetize | More sports/leagues, advanced models, admin + metrics, streaming (crypto), ad campaign. | 8–12 weeks |

The Phase 0 validation is the gate to Phase 1: if after 3–4 weeks the model
shows positive ROI or break-even on paper, the product gets built.

---

## 10. Data sources by domain

| Domain | API | Cost | Cadence | Resolution rule |
|--------|-----|------|---------|-----------------|
| Betting | The Odds API | Free (500 req/mo) → $50/mo | Daily batch | binary |
| Betting (stats) | MLB Stats API, NBA Stats API | Free | Daily batch | — |
| Flights | Amadeus, Kiwi/Tequila | Free tier | Polling | threshold |
| Real estate | ATTOM, Zillow (or scraping) | Variable | Batch | continuous |
| Crypto | Binance, CoinGecko | Free | Streaming | threshold |

---

## 11. Recorded design decisions

1. **Global picks + per-user tracking**, not per-user picks. Keeps infra simple
   and cheap, and enables aggregate metrics as social proof.
2. **Store everything** (odds, stats, features, outcome), not just the outcome.
   Enables retraining with proprietary data and builds a defensible dataset.
3. **Domain-specific data in jsonb**, never as columns. Preserves schema
   genericity. Performance via per-adapter jsonb indexes.
4. **The framework owns the data**; the web app is an API client. Decouples core
   from product.

---

## 12. Risks and notes

- **Real edge in betting:** generating sustained positive expected value is hard.
  CLV (closing line value) is the long-term validation metric, more reliable
  than short-term win rate.
- **Crypto as a business:** excellent for validating the framework (free APIs,
  fast iteration), dubious as a profitable business (efficient, saturated market).
- **Real estate:** high ticket but slow validation cycle (months).
- **Free API limits:** The Odds API's free tier (500 req/mo) is enough for the
  MVP but requires aggressive caching in `raw_events`.
