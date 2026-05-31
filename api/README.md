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
