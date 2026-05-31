# Sentinel

> A domain-agnostic engine that ingests data from any API, runs models over it,
> and produces **signals** — opportunities scored by confidence and expected
> value. First deployment: a SaaS platform for sports value betting.

**Status:** 🟡 In development · Phase 0 (model validation)

---

## What it is

Most data products are built for one problem. Sentinel is built so the same
core works across completely different problems. The engine doesn't know what a
bet, a flight price, or a crypto candle is — that knowledge lives in swappable
**adapters**. Add a new domain by writing an adapter, not by rewriting the core.

The first deployment is a real product: a betting SaaS where users get daily
suggested bets with a confidence score and track their own results. It validates
the framework with a live use case in production.

```
┌──────────────────────────────────────────────────────────────┐
│  LAYER 1 — Generic framework (the reusable IP)                 │
│  Ingestion · Model engine · Signal engine · Resolution ·       │
│  Multi-channel output · Notifications                          │
│  Domain-agnostic. It doesn't know what a bet is.               │
└──────────────────────────────────────────────────────────────┘
                              ▲
┌──────────────────────────────────────────────────────────────┐
│  LAYER 2 — Domain adapters (plug-ins)                          │
│  Betting · Flights · Real estate · Crypto · ...                │
│  One adapter per domain or client. The core never changes.     │
└──────────────────────────────────────────────────────────────┘
                              ▲
┌──────────────────────────────────────────────────────────────┐
│  LAYER 3 — SaaS product (first deployment: betting)            │
│  Web app · Auth · Freemium billing · 18+ compliance ·          │
│  Dashboard · Per-user tracking                                 │
└──────────────────────────────────────────────────────────────┘
```

## Why this design

The same engine running four unrelated businesses is the proof of genericity.
Each adapter has a completely different signature — different data source,
cadence, model, and resolution rule — yet all speak the same language to the
core:

| Domain | Data source | Cadence | Resolution |
|--------|-------------|---------|------------|
| Betting | Odds APIs | Daily batch | binary |
| Flights | Amadeus | Polling | threshold |
| Real estate | Comparables | Batch | continuous |
| Crypto | Binance | Streaming | threshold |

## Repository structure

```
sentinel/
├── core/          # LAYER 1 — domain-agnostic framework (the IP)
│   ├── ingestion/ # multi-cadence sources → raw_events
│   ├── models/    # model engine, run versioning
│   ├── signals/   # signal engine: EV, confidence, ranking
│   ├── resolution/# resolves outcomes per resolution_rule
│   └── output/    # channels: email, telegram, webhook
│
├── adapters/      # LAYER 2 — one plug-in per domain
│   ├── base.py    # interface every adapter implements
│   ├── betting/   # odds → poisson → value_bet (binary)
│   ├── flights/   # amadeus → time series → threshold
│   ├── realestate/# comparables → continuous
│   └── crypto/    # binance → TA → threshold (stream)
│
├── api/           # FastAPI: exposes the framework as REST
├── web/           # LAYER 3 — SaaS product (Next.js)
├── migrations/    # Alembic — the schema lives here
└── tests/
```

The core owns the data and the logic, and exposes a REST API. The web app is
just a client of that API — it never touches Postgres directly. This decouples
the framework from the product: the same backend can power the betting SaaS, a
B2B client dashboard, a mobile app, or a webhook, with no changes to the core.

## Tech stack

- **Core + API:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic
- **Models:** Pandas, NumPy, scikit-learn, statsmodels
- **Web:** Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui
- **Data + infra:** PostgreSQL (Supabase), Stripe, Railway + Vercel

## Roadmap

- [ ] **Phase 0 — Model validation** (4–6 weeks)
      One sport (MLB + World Cup), one model, paper trading, output to Notion +
      email/Telegram. No product yet. Gate: positive ROI or break-even on paper.
- [ ] **Phase 1 — Public MVP** (8–12 weeks)
      Web app, auth, dashboard, freemium billing, Telegram bot, 18+ compliance,
      first paying users.
- [ ] **Phase 2 — Scale & monetize** (8–12 weeks)
      More sports/leagues, advanced models, admin + metrics, streaming (crypto),
      ad campaign.

## Design document

The full design rationale — data model, design decisions, cold-run validation,
risks — lives in [`DESIGN.md`](./DESIGN.md).

## Getting started

> _Coming in Phase 0._ Setup instructions will land here as the core takes shape.

---

_Built by Leonel Roa. A working data-decision system, validated on a real
product in production._
