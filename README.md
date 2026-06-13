# Sentinel

> A domain-agnostic engine that ingests data from any API, runs statistical
> models over it, and produces **signals** — opportunities scored by confidence
> and expected value. Two adapters running in production: MLB value betting
> and flight price monitoring.

**Status:** 🟢 Live · Phase 1 (dashboard) · v0.3.0

---

## What it is

Most data products are built for one problem. Sentinel is built so the same
core works across completely different problems. The engine doesn't know what a
bet or a flight price is — that knowledge lives in swappable **adapters**. Add
a new domain by writing an adapter, not by rewriting the core.

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Generic framework (the reusable IP)                   │
│  Ingestion · Model engine · Signal engine · Resolution ·         │
│  Multi-channel output · Notifications                            │
│  Domain-agnostic. It doesn't know what a bet is.                 │
└─────────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2 — Domain adapters (plug-ins)                            │
│  Betting · Flights · Crypto · ...                                │
│  One adapter per domain or client. The core never changes.       │
└─────────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3 — SaaS product (Phase 1)                                │
│  Web app · Auth · Freemium billing · Dashboard · Per-user        │
│  tracking                                                        │
└─────────────────────────────────────────────────────────────────┘
```

## Two adapters, one engine

The same unchanged core runs two completely different businesses:

| Domain        | Data source                  | Model          | Resolution | Cadence       |
|---------------|------------------------------|----------------|------------|---------------|
| MLB betting   | The Odds API + MLB Stats API | Poisson v0.3.0 | binary     | Daily batch   |
| Flight prices | SerpAPI Google Flights       | Price series   | threshold  | Daily polling |

Adding a new domain = writing one adapter file. The core never changes.

## What's running in production

**MLB betting adapter (poisson_v0.3.0)**

Generates daily value bets on MLB games. The model:
- Fetches pre-match odds from The Odds API and team run-scoring stats
  from the official MLB Stats API (free)
- Pulls probable starting pitcher ERA for both teams and adjusts expected
  runs accordingly — a pitcher with ERA 3.20 facing a 5.0 RPG lineup
  suppresses scoring more than a pitcher with ERA 5.50
- Blends season-average RPG (70%) with last-15-games RPG (30%) for
  recency-adjusted lambda inputs
- Runs a Poisson score matrix with 50/50 extra-inning redistribution
  (extra innings are ~50/50 regardless of regular-season run rates)
- De-vigs the market, computes EV per pick, and filters by confidence
  and expected value thresholds
- Sizes each pick via 1/10 Kelly Criterion (1u = 1% of bankroll)
- Delivers picks via Telegram with AI-generated analyst justifications
  (Groq Llama 3.3 70B, any OpenAI-compatible provider via env vars)

The model went through three calibration iterations:
- v0.1.0: baseline Poisson, HOME_ADVANTAGE=1.10 (100% home picks — broken)
- v0.2.0: HOME_ADVANTAGE=1.04, first away picks appeared
- v0.3.0: pitcher ERA + recent form + 50/50 tie redistribution (current)

**Flights adapter**

Monitors EZE→MAD (and any custom route via `--route`) for price drops
using SerpAPI Google Flights. Two monitoring modes: flexible (5 weekly
dates auto-generated) and range (5 dates distributed across a custom
date range). Signals fire immediately when Google's `price_insights`
says "low", or after 3 price observations via rolling average.

## Repository structure

```
sentinel/
├── core/                  # LAYER 1 — domain-agnostic framework
│   ├── db.py              # SQLAlchemy engine + session factory
│   ├── models.py          # 8-table schema (domains, signals, outcomes...)
│   ├── orchestrator.py    # pipeline: ingest → model → signals → resolve
│   └── output/            # channels: Telegram, (email Phase 1)
│
├── adapters/              # LAYER 2 — one plug-in per domain
│   ├── base.py            # Adapter contract every domain implements
│   ├── betting/           # Poisson model, pitcher ERA, MLB Stats API
│   └── flights/           # SerpAPI Google Flights, price series model
│
├── scripts/               # CLI tools (paper trading, tracking, backtest)
│   ├── paper_trade.py     # daily picks, refresh, resolve
│   ├── track.py           # follow picks, view P&L
│   └── backtest.py        # historical validation (419-game May 2026)
│
├── .github/workflows/     # GitHub Actions (triggered by cron-job.org)
│   ├── daily_picks.yml    # 09:00 ART — generate picks
│   ├── daily_refresh.yml  # 17:00 ART — refresh odds + Telegram update
│   └── daily_resolve.yml  # 03:00 ART — resolve yesterday's picks
│
└── api/ web/              # Phase 1 (FastAPI + Next.js, scaffolded)
```

## Key design decisions

**jsonb for domain-specific data.** Fields that vary by domain (`payload`,
`features`, `auth_config`) are stored as jsonb, never as typed columns.
The schema doesn't need to know what fields The Odds API returns vs SerpAPI.
Performance via per-adapter jsonb indexes.

**The framework owns the data.** A REST API (FastAPI, Phase 1) will be the
only way clients reach the data. The web app will never touch Postgres
directly — decoupling product from framework.

**Model versioning.** Every signal is tagged with `model_version` in
`model_runs`. P&L analysis defaults to the current version (`poisson_v0.3.0`)
and supports `--version all` for cross-version comparison.

**Generic LLM justifications.** Pick explanations use any OpenAI-compatible
provider. Switching from Groq to Claude or GPT-4 requires only env var
changes — zero code changes.

## Tech stack

- **Core:** Python 3.12, SQLAlchemy 2.0, Pydantic
- **Models:** statsmodels (Poisson), Pandas, NumPy
- **Data:** The Odds API, MLB Stats API (free), SerpAPI
- **Infra:** PostgreSQL on Supabase, GitHub Actions, cron-job.org
- **Notifications:** Telegram Bot API
- **AI justifications:** Groq (free tier) — any OpenAI-compatible endpoint
- **Phase 1:** FastAPI, Next.js 15, Stripe, Railway + Vercel

## Getting started

```bash
git clone https://github.com/YOUR_USERNAME/sentinel
cd sentinel
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Fill in ODDS_API_KEY, DATABASE_URL (or leave blank for SQLite)
python -m scripts.paper_trade --mock    # run pipeline with sample data
```

See `.env.example` for all configuration options including Telegram and
Groq API keys.

## Roadmap

- [x] **Phase 0 — Model validation** ✅ 2026-06-13
      Poisson model with pitcher ERA + recent form + tie redistribution.
      Daily picks via Telegram with Kelly sizing and AI justifications.
      Personal P&L tracking. Backtested on 419 MLB games (58.7% accuracy).
      Live paper trading result: 48 resolved picks, 62.5% win rate, +22.5% Kelly ROI.
- [ ] **Phase 1 — Dashboard**
      FastAPI REST layer, personal Next.js dashboard (picks by date, follow/unfollow,
      P&L global and per followed picks). Basic auth (HTTP Basic on Next.js,
      API Key on FastAPI). Hosted on Vercel + Railway. Signal distribution
      via external platform (Dubclub or equivalent).
- [ ] **Phase 2 — Scale**
      More sports, additional adapter domains (crypto signals, real estate),
      admin dashboard, ad campaign.

## Design document

Full rationale — data model, cold-run validation, all recorded design
decisions — in [`DESIGN.md`](./DESIGN.md).

---

*Built by Yondri Leonel Roa. A domain-agnostic data-decision framework validated
on two live use cases: MLB value betting and flight price monitoring.*
