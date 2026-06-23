This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deployment

Hosted on Vercel. **Deploys are manual** — pushing to GitHub does not
trigger a deploy by itself.

- **Production URL:** https://sentinel-dashboard-fawn.vercel.app
- **Deploy:** `vercel --prod` from this directory (`web/`)
  (requires `vercel login` once; CLI install: `npm i -g vercel`)

⚠️ Every `vercel --prod` run creates a **new** deployment with its own
permanent, unique URL (e.g. `sentinel-dashboard-<hash>-leonel-delta-7.vercel.app`).
That per-deployment URL never changes content, even after the next deploy —
it's a frozen snapshot. **Always use the production URL above** (or run
`vercel alias ls` to see all aliases currently pointing at the live
deployment) — don't bookmark a `vercel --prod` output URL and expect it to
stay current.

Basic Auth (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`) and `NEXT_PUBLIC_API_URL`
/ `NEXT_PUBLIC_API_KEY` are set as Vercel project env vars — see
`.env.local.example` for the full list.
