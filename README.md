# Polygraph for MCP

**Polygraph breaks your AI agent's tools inside pytest, and catches it when it lies about what happened.**

> Not the same as [agent-polygraph](https://github.com/najemwehbe/agent-polygraph), which reads a finished
> transcript. Polygraph *causes* the failure and checks the server's real state. It is also unrelated to
> Nx Polygraph.

AI agents get work done by calling tools: pay this invoice, search orders, send this email. In demos the
tools always work. In real life they time out, return errors, send half the data, change their format,
or send back text that tries to trick the agent.

Polygraph sits between your agent and its MCP servers. It breaks tools on purpose, writes every call into
a ledger (what the agent **asked**, what really **happened**, what the agent was **told**), and then checks
the real database. If the agent says "done" and the database says otherwise, that's a **silent lie**.

```bash
pip install pytest-polygraph
pytest --polygraph-auto --polygraph-report=polygraph.html
```

```
Polygraph: silent-lie 0/40 · double-action 1/40 · hijack 0/20 · coverage 18/22
```

## What's in this repo

| Path | What it is |
|---|---|
| [`packages/pytest-polygraph`](packages/pytest-polygraph) | The pytest plugin, the MCP fault-injection proxy (stdio and Streamable HTTP), the planner, the scorer, reports, the CLI, and three fake worlds to test against |
| [`cloud/api`](cloud/api) | Polygraph Cloud's API and upload worker (FastAPI, Postgres, S3, SQS) |
| [`cloud/web`](cloud/web) | The landing page and the dashboard (Next.js) |
| [`action`](action) | The GitHub Action: runs the suite, comments on PRs, uploads without secrets |
| [`infra`](infra) | Terraform for AWS Mumbai (ap-south-1) |
| [`deploy/free`](deploy/free) | Run the whole hosted dashboard on one free server ([guide](docs/DEPLOY-FREE.md)) |
| [`benchmark`](benchmark) | The cross-framework benchmark: tasks, adapters, predictions |
| [`docs/CONTRACTS.md`](docs/CONTRACTS.md) | The one source of truth every part builds against: results file, Python API, CLI, HTTP API |

## The four pieces

1. **The proxy.** A message-level MCP proxy with 13 faults in four families: the tool is down or slow
   (`timeout`, `slow`, `error`, `rate_limit`, `auth_expired`), the tool did something different from what it
   said (`lost_response`, `duplicate_delivery`), the tool's data is wrong (`partial`, `malformed`,
   `schema_drift`, `stale`), and the tool is hostile (`injection`, `description_poison` with a mid-session
   rug pull). It speaks both the older MCP versions and the current stateless 2026-07-28 spec.
2. **The pytest plugin.** Reads each tool's labels (read-only, destructive, safe to repeat, open world),
   plans which faults make sense where, gives you a fault-coverage score, and multiplies your tests across
   the plan. Outputs HTML, JUnit, a PR comment, a badge and OpenTelemetry spans, all from one versioned
   results file.
3. **The benchmark.** Three fake worlds (bank, shop, inbox), four frameworks, the same model and faults,
   with 95% confidence intervals and predictions committed before the paid runs. Plus seven small fix
   recipes, each with before and after numbers from real runs.
4. **Polygraph Cloud.** A free, optional dashboard, like Codecov for agent honesty: trends per branch, a
   report page with a tool × fault heatmap, the ledger view, PR comparisons, a live README badge and the
   public leaderboard. It never runs your agent, never calls a model and never sees your API keys.

## Run everything locally

Without Docker (SQLite, local file storage, inline queue):

```bash
# 1. The plugin and a real example run
cd packages/pytest-polygraph
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -e ".[dev,otel,worlds]"
pytest -q

# 2. The API on :8000 (see cloud/api/README.md)
cd ../../cloud/api && ./scripts/dev.sh

# 3. The web app on :3000
cd ../web && npm install && npm run dev
```

With Docker: `cp .env.example .env && docker compose up --build`.

## Deploy

- **Free, on one server:** [docs/DEPLOY-FREE.md](docs/DEPLOY-FREE.md) puts everything on an Always Free Oracle Cloud
  server with a free HTTPS address. No domain needed.
- **Full AWS setup:** [docs/DEPLOY.md](docs/DEPLOY.md) and [infra/](infra).

## Privacy and safety

- Uploads are off by default. Everything works offline.
- An upload carries numbers, outcome buckets, tool and fault names, timings and **hashes** of payloads, not
  the text. Text upload is a per-project opt-in; your `mask` runs first and fails closed, and the server
  rejects anything that still looks like personal data (emails, phone numbers, PAN, Aadhaar).
- Hosted data stays in AWS Mumbai, encrypted with TLS and KMS. Account data is only your GitHub id,
  username and avatar. Delete or export anything, any time.
- All injected text is harmless and fake, fake addresses use `example.test`, and the fake worlds never
  touch the network.

## Licence

Apache 2.0. See [`SECURITY.md`](SECURITY.md) to report a vulnerability.
