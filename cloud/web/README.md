# Polygraph web

The landing page and the dashboard for Polygraph Cloud. Next.js 16 (App Router, Cache Components),
TypeScript, CSS Modules, no UI framework and no chart library: every chart is hand-drawn SVG.

## Run it locally

You need Node 20.9 or newer and the API running on port 8000 (see `cloud/api/README.md`).

```bash
npm install
cp .env.example .env.local
npm run dev
```

Open http://localhost:3000. In development the sign-in page offers **Use a local dev account**, which
calls the API's dev-only login. The API refuses that route outside `ENVIRONMENT=development`.

Requests to `/api/*` are rewritten to `API_ORIGIN`, so the browser always talks to one origin and the
session cookie stays first-party. In production CloudFront sends `/api/*` straight to the API service.

## Checks

```bash
npm run lint
npm run typecheck
npm run build
```

## How it's put together

| Path | What it is |
|---|---|
| `src/app/(marketing)` | Landing page, docs, benchmark, fix recipes, privacy, terms, security, sign-in |
| `src/app/app` | Dashboard: projects, overview, reports, report, run ledger, compare, settings, account, consent |
| `src/components/trace` | The hero chart recording (pure SVG + CSS, no client JS) |
| `src/components/charts` | Interval plots, trend chart, outcome bar, tool × fault heatmap |
| `src/components/ledger` | The Asked / Happened / Told ledger view |
| `src/lib/types.ts` | API and results-file types. Mirrors `docs/CONTRACTS.md` |
| `src/lib/api-server.ts` | Server-side fetches. Forwards only the session cookie, never caches private data |
| `src/lib/api-client.ts` | Browser mutations with the CSRF double-submit header |

Pages that read the session or the URL keep that work inside `<Suspense>`, so every route still has a
static shell (Cache Components). Marketing pages are fully static apart from the live leaderboard.

## Design

Chart paper and pen ink. The page is the paper, colour is ink, and crimson is reserved for the three
outcomes that mean the agent can't be trusted: silent lie, double action, hijacked. Amber marks
"something was done to this call" and honest failures; green means recovered.

Tokens live in `src/app/globals.css`. Type is Schibsted Grotesk throughout, with JetBrains Mono only
for code, tool names and fault names. Light and dark themes both follow `prefers-color-scheme`, and the
only motion that isn't triggered by the user is the hero trace drawing once. It's switched off under
`prefers-reduced-motion`.

## Docker

```bash
docker build -t polygraph-web .
docker run -p 3000:3000 -e API_ORIGIN=http://host.docker.internal:8000 polygraph-web
```
