# `api/` — REST API

FastAPI service that exposes the framework to the outside world. This is the
**only** way clients reach the data and logic — the web app, a future mobile
app, a B2B dashboard, or a webhook all go through here.

## Why an API boundary

The core owns the schema and the business logic. Rather than letting multiple
languages (Python core, TypeScript web) write to Postgres directly — which
drifts into two competing schema definitions — everything funnels through this
API. One source of truth, one set of migrations.

This is also what makes the framework genuinely product-independent: swap the
web app for any other client and the backend doesn't change.

## Surface (planned)

| Area | Endpoints |
|------|-----------|
| Signals | List today's signals, filter, signal detail with features |
| Tracking | Mark a signal as followed, record stake, fetch personal P&L |
| Stats | Aggregate system performance (win rate, ROI, CLV) |
| Admin | Domains, data sources, model runs, hyperparameters |
| Billing | Stripe webhooks, plan state, free-tier enforcement |

Contracts are defined with Pydantic models, shared with the validation layer so
the API shape and the data shape can't drift apart.

## Deployment

Hosted on Railway. **Deploys are manual** — pushing to GitHub does not
trigger a deploy by itself.

- **Production URL:** https://sentinel-api-production-746a.up.railway.app
- **Deploy:** `railway up --service "Sentinel API"` from the repo root
  (requires `railway login` once; CLI install: `npm i -g @railway/cli`)
- **Check status:** `railway status`
- Env vars (`SENTINEL_API_KEY`, `DATABASE_URL`, `ODDS_API_KEY`,
  `PRODUCTION_MODEL_BASELINE`, etc.) are set directly in the Railway
  dashboard — see `.env.example` at the repo root for the full list and
  what each one does.
