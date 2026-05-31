# `web/` — SaaS product (Layer 3)

The user-facing product. First deployment: the betting SaaS. Built with Next.js
15 (App Router) + TypeScript, styled with Tailwind + shadcn/ui.

**This is a client of the `api/` service.** It never queries Postgres directly —
all data flows through the REST API. Swapping this web app for a different
front-end (or adding a mobile app alongside it) requires zero backend changes.

## Scope (Phase 1)

- Sign up / log in
- Daily picks dashboard with confidence and EV
- Filter by sport/league
- Follow a pick and record stake
- Personal history and cumulative P&L
- Global system stats (social proof: win rate, ROI, CLV)
- Subscription management (Stripe freemium → paid)
- 18+ compliance and responsible-gambling disclaimer

## Note on genericity

The product layer is intentionally thin. The intelligence lives in `core/` and
`adapters/`. This folder is the betting *presentation* of the framework — a B2B
client would get their own product surface (or just consume the API), while the
engine underneath stays identical.
