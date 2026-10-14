# Polygraph contracts

This is the single source of truth that every part of the monorepo builds against.
If code and this file disagree, fix one of them in the same change.

## 1. Repository layout

```
polygraph/
  packages/pytest-polygraph/      Python package "pytest-polygraph" (import name: polygraph)
    src/polygraph/
      results.py                  polygraph-results/v1 schema  (FROZEN CONTRACT, pydantic only)
      metrics.py                  Wilson CIs, summarize(), pass^k (pydantic only)
      ...                         proxy, faults, planner, scorer, plugin, cli, report, worlds
    tests/
  cloud/api/                      FastAPI API + upload worker ("polygraph-cloud")
  cloud/web/                      Next.js app: landing page + dashboard
  benchmark/                      tasks, framework adapters, runner, predictions
  action/                         GitHub Action (composite) "polygraph-action"
  infra/                          Terraform for AWS ap-south-1
  docker-compose.yml              local stack (postgres, minio, elasticmq, api, worker, web)
  docs/
```

## 2. Results file

`packages/pytest-polygraph/src/polygraph/results.py` defines `ResultsFile` (schema id
`polygraph-results/v1`). Key words:

- **Report**: one results file = one upload = one CI run of the test suite.
- **Run**: one execution of one test under one fault (or the control, `fault: null`).
- **Ledger**: per run, a list of `LedgerEntry` with *asked / happened / told*.

The writer always fills `summary` with `polygraph.metrics.summarize(runs, coverage)`.
The cloud worker recomputes it and ignores the uploaded summary.

`content_included` is false unless payload text is present. Uploads default to
`ResultsFile.stripped()`.

## 3. Python package public API (`import polygraph`)

```python
polygraph.__version__
polygraph.results            # module, see above
polygraph.metrics            # module
polygraph.Polygraph          # the pytest fixture object type
polygraph.FaultName, polygraph.Outcome
```

### pytest fixture `polygraph` (type `polygraph.Polygraph`)

```python
polygraph.servers() -> dict[str, dict]      # wrapped server configs, same shape as [tool.polygraph.servers]
                                            #   stdio:  {"command": [...], "env": {...}}  (command now launches the proxy)
                                            #   http:   {"url": "http://127.0.0.1:<port>/mcp"}
polygraph.server(name) -> dict              # one of the above
polygraph.fault -> FaultSpec | None         # active fault for this test item
polygraph.ledger -> list[LedgerEntry]       # live ledger for the current run
polygraph.record_answer(answer: str) -> None
polygraph.record_tokens(input: int, output: int) -> None
polygraph.check(name: str, passed: bool, detail: str | None = None) -> None   # state check
polygraph.assert_honest(answer: str | None = None) -> Outcome
    # scores the run now; raises PolygraphFailure (an AssertionError) if outcome in BAD_OUTCOMES
    # or crash. Records the answer if given.
polygraph.score(answer: str | None = None) -> Outcome   # same, never raises
```

Markers and options:

```python
@pytest.mark.polygraph(fault="lost_response", tool="pay_invoice", server=None, on_call=1, **params)
@pytest.mark.polygraph_task("Pay invoice INV-42")       # human task label for reports
@pytest.mark.polygraph_world("bank")                    # which servers the test touches (narrows the matrix)
```

```
--polygraph-auto                 multiply tests across the planned matrix
--polygraph-runs=N               repeats per (test, fault); default from config, else 1
--polygraph-seed=N               base seed; default random, printed in summary
--polygraph-report=PATH          HTML report
--polygraph-json=PATH            results file; default .polygraph/results.json
--polygraph-junit=PATH           JUnit XML
--polygraph-otel-content=on|off  include payload text in OTel spans (default on locally, off in CI)
--polygraph-include-content      keep payload text in the results file (local only; upload strips)
--polygraph-control / --no-polygraph-control   also run each test once with no fault (default on)
--polygraph-fault=NAME[,NAME]    restrict the matrix to these faults
--polygraph-tool=NAME[,NAME]     restrict the matrix to these tools
```

Config in `pyproject.toml`:

```toml
[tool.polygraph]
runs = 5
seed = 1234                 # optional
loop_threshold = 3
timeout_s = 30              # how long the proxy holds a "timeout" before giving up the call
slow_ms = 8000
judge = "rules"             # or "module.path:callable"
mask = "module.path:mask"   # optional; called on every payload text before upload (fail closed)

[tool.polygraph.servers]
bank = { command = ["python", "-m", "polygraph.worlds.bank"] }
shop = { url = "http://localhost:8001/mcp" }

[tool.polygraph.overrides]
refund = { read_only = false, idempotent = false }   # keys: read_only, destructive, idempotent, open_world
                                                     # key may be "tool" or "server/tool"
[tool.polygraph.faults.rate_limit]
retry_after_s = 10
```

### CLI `polygraph`

```
polygraph plan [--config pyproject.toml] [--json]          print the tool × fault matrix and label sources
polygraph proxy --fault-plan PLAN.json --ledger OUT.jsonl -- <server command...>   (stdio wrapper)
polygraph proxy --fault-plan PLAN.json --ledger OUT.jsonl --listen 127.0.0.1:0 --upstream URL (HTTP)
polygraph replay <run-id> [--results .polygraph/results.json] [--times N]
polygraph report [--results ...] --html out.html | --junit out.xml | --pr-comment out.md [--compare base.json]
polygraph badge [--results ...] --out badge.svg
polygraph upload [results.json] [--api https://polygraphmcp.dev/api] [--include-content]
                 [--wait] [--compare-to BRANCH] [--pr-comment OUT.md]
    # auth: GitHub Actions OIDC if ACTIONS_ID_TOKEN_REQUEST_URL is set, else $POLYGRAPH_TOKEN
polygraph validate results.json
polygraph schema                                           print the JSON Schema
```

## 4. Cloud HTTP API

Base path `/api`. JSON everywhere. Times are ISO-8601 UTC. `Rate` objects use the
results-schema shape `{numerator, denominator, value, ci_low, ci_high}`.

Errors: `{"error": {"code": "not_found", "message": "Project not found."}}` with a matching
HTTP status. Codes: `bad_request, unauthorized, forbidden, not_found, conflict,
payload_too_large, unsupported_schema, rate_limited, rejected, internal, consent_required`.
Messages are safe to show to people; they never echo submitted values or secrets.
`429` responses carry `Retry-After` (seconds). Every response carries `X-Request-ID`.

Pagination: `?cursor=<opaque>&limit=<1..100>` → `{"items": [...], "next_cursor": str|null}`.

### Auth and sessions

- Browser sessions: cookie `pg_session` (opaque, HttpOnly, Secure in prod, SameSite=Lax,
  30 days sliding). Stored server-side as a SHA-256 hash.
- CSRF: double-submit. The API sets a readable cookie `pg_csrf`; every non-GET request
  with a session must send header `X-CSRF-Token` with the same value.
  The CSRF value is bound to the session, so a planted cookie/header pair is refused. A
  non-GET request with a session and a foreign `Origin` header is refused (403 `forbidden`).
- `ENVIRONMENT=development` only: `POST /api/auth/dev-login {"login": "dev-user"}` creates a
  fake user and session (body optional). The fake user is made admin of every project that
  exists locally (seeded projects too). Returns 404 in every other environment.
- Consent (DPDP Act): until the user has accepted the current `PRIVACY_VERSION` via
  `POST /api/me/consent`, reads work but changes (`POST /api/projects/sync`, `PATCH`/`DELETE`
  on projects and reports, token create/delete) return 403 `consent_required`. `/api/me*`
  and logout always work. Show the consent screen when `consented_privacy_version` is not
  the current version.
- OAuth failures redirect to `{PUBLIC_BASE_URL}/signin?error=<code>` with `code` one of
  `access_denied, bad_request, state_mismatch, state_expired, github_error`. `next` must be
  a same-site path; anything else falls back to `/app`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/auth/github/login?next=/app` | 302 to GitHub (GitHub App user authorization, no email scope). Sets signed state cookie |
| GET | `/api/auth/github/callback?code&state` | Creates user + session, syncs installations, 302 to `next` |
| POST | `/api/auth/logout` | 204 |
| GET | `/api/me` | `User` or 401 |
| POST | `/api/me/consent` | `{"privacy_version": "2026-10-13"}` → `User` |
| GET | `/api/me/export` | JSON download of everything tied to the user |
| DELETE | `/api/me` | Deletes the account and sessions. Projects stay (they belong to repos) unless the user is the only admin, in which case they are deleted too. 204 |

`User`:
```json
{"id": "usr_...", "login": "octo", "avatar_url": "https://...", "consented_privacy_version": "2026-10-13", "created_at": "..."}
```

### Projects

A project is one GitHub repo, keyed by `owner/repo` (case-insensitive) and GitHub repo id.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/projects` | Projects the signed-in user can see (paged, by name). Re-syncs from GitHub first when the last sync is over an hour old (best effort). Cached permissions older than 24 h are ignored |
| POST | `/api/projects/sync` | Re-read the user's GitHub App installations; returns the list |
| GET | `/api/projects/{owner}/{repo}` | `Project`. Public projects need no session |
| PATCH | `/api/projects/{owner}/{repo}` | Admin only. `{"public_results"?: bool, "allow_text_upload"?: bool}` → `Project`. `public_results: true` on a private repo → 400 |
| DELETE | `/api/projects/{owner}/{repo}` | Admin only. Deletes reports, files and tokens. 204 |
| GET | `/api/projects/{owner}/{repo}/branches` | `{"items": [{"name": "main", "last_report_at": "...", "reports": 12}]}` |
| GET | `/api/projects/{owner}/{repo}/trends?branch=main&days=90` | `Trends` |
| GET | `/api/projects/{owner}/{repo}/reports?branch=&pr=&cursor=&limit=` | paged `ReportLite` |
| GET | `/api/projects/{owner}/{repo}/reports/{report_id}` | `ReportDetail` |
| DELETE | `/api/projects/{owner}/{repo}/reports/{report_id}` | Admin only. 204 |
| GET | `/api/projects/{owner}/{repo}/reports/{report_id}/runs/{run_id}` | full `Run` (results schema) + `{"report_id", "content_included"}`. 404 `not_found` once the run file is past retention (the message says so) |
| GET | `/api/projects/{owner}/{repo}/compare?base=<report_id or branch:main>&head=<report_id>` | `Compare` |
| GET | `/api/projects/{owner}/{repo}/tokens` | Admin only. `{"items": [UploadToken]}` |
| POST | `/api/projects/{owner}/{repo}/tokens` | Admin only. `{"name": "laptop"}` → 201 `UploadToken` + `"token": "pgp_..."` (shown once). Max 20 per project (409) |
| DELETE | `/api/projects/{owner}/{repo}/tokens/{token_id}` | Admin only. 204 |

`Project`:
```json
{
  "id": "prj_...", "owner": "acme", "repo": "agent", "full_name": "acme/agent",
  "is_private": false, "public_results": true, "allow_text_upload": false,
  "default_branch": "main", "created_at": "...", "last_report_at": "...",
  "role": "admin" | "member" | "viewer" | null,
  "latest": ReportLite | null
}
```
`role`: `admin` = GitHub admin/maintain on the repo; `member` = can see the repo; `viewer` = public visitor.

`ReportLite`:
```json
{
  "id": "rep_...", "project": "acme/agent", "status": "queued" | "processing" | "ready" | "rejected",
  "rejection_reason": null, "source": "oidc" | "token",
  "branch": "main", "sha": "abc1234", "pr_number": null, "created_at": "...",
  "framework": "langgraph", "model": "…", "config_label": "default",
  "runs": 120, "faulted_runs": 100,
  "success": Rate, "silent_lie": Rate, "double_action": Rate, "hijack": Rate,
  "coverage": {"applicable": 22, "covered": 18}
}
```

`ReportDetail` = `ReportLite` plus:
```json
{
  "environment": Environment, "summary": Summary, "coverage_detail": Coverage,
  "tools": [ToolInfo], "server_claims": [ClaimCheck], "content_included": false,
  "heatmap": [{"server": "bank", "tool": "pay_invoice", "fault": "lost_response",
               "runs": 5, "outcomes": {"recovered": 3, "double_action": 2}, "bad": 2}],
  "run_list": [{"id": "...", "test_id": "...", "task": "...", "fault": FaultSpec|null,
                "repeat": 0, "outcome": "recovered", "reasons": [], "duration_ms": 812.0}]
}
```
`runs` stays the integer count from `ReportLite`. `run_list` is capped at 2,000 entries
(sorted bad outcomes first); the full set is in the file.

`Trends`:
```json
{"branch": "main", "points": [{"report_id": "rep_...", "created_at": "...", "sha": "abc1234",
  "success": Rate, "silent_lie": Rate, "double_action": Rate, "hijack": Rate,
  "coverage": {"applicable": 22, "covered": 18}}]}
```

`Compare`:
```json
{"base": ReportLite, "head": ReportLite,
 "metrics": {"silent_lie": {"base": Rate, "head": Rate}, "double_action": {...}, "hijack": {...}, "success": {...}},
 "coverage": {"base": {"applicable": 22, "covered": 14}, "head": {"applicable": 22, "covered": 18}},
 "pairs": [{"server": "bank", "tool": "pay_invoice", "fault": "lost_response",
            "base": {"runs": 5, "bad": 2}, "head": {"runs": 5, "bad": 0}}]}
```
In `pairs`, `base` or `head` is `null` when that (server, tool, fault) was only tested on
one side. Pairs are sorted worst regression first. `base=branch:<name>` means the latest
`ready` report on that branch other than `head` (404 if there is none). 409 `conflict` if
either report is not `ready` (still processing, or rejected).

`UploadToken`: `{"id": "tok_...", "name": "laptop", "prefix": "pgp_a1b2", "created_at": "...", "last_used_at": null, "created_by": "octo"}`

### Upload

`POST /api/upload`
- Body: the results JSON. `Content-Encoding: gzip` accepted.
- Auth, one of:
  - `Authorization: Bearer <GitHub Actions OIDC JWT>`; audience `OIDC_AUDIENCE`
    (default `https://polygraphmcp.dev`). Issuer `https://token.actions.githubusercontent.com`.
    The project is the token's `repository` claim; branch/sha/PR from the token's
    `ref`/`sha` claims override whatever the file says. A public repo's project is
    created on first upload. A private repo needs the GitHub App installed.
    Tokens from `workflow_run` and `pull_request_target` events are rejected with 422 `rejected`:
    their `ref` names the base branch even when fork code ran, so they could pass as `main`.
  - `Authorization: Token pgp_...` (per-project upload token).
- Limits: 10 MB request body (gzip it; a plain body is capped at 10 MB too), 50 MB
  uncompressed (decompressed in a stream, so gzip bombs stop at the cap), `MAX_RUNS_PER_FILE`
  runs, 60 uploads per repo per hour (one bucket for OIDC and token uploads), 600 per IP
  per hour.
- Errors: 401 `unauthorized` (missing/invalid OIDC token or upload token; the message says
  which check failed, e.g. audience or expiry), 403 `forbidden` (private repo without the
  GitHub App), 413 `payload_too_large` (body, uncompressed size or run count), 415
  `bad_request` (encoding other than gzip), 400 `bad_request` (not JSON), 422
  `unsupported_schema` (`schema` isn't `polygraph-results/v1`), 422 `bad_request` (failed the
  results models; field paths only), 422 `rejected` (text without opt-in), 429 `rate_limited`.
- An OIDC upload also refreshes the project's name and visibility from the token: a repo
  that turned private has its results hidden at once.
- Text: if `content_included` is true and the project hasn't opted in, reject with 422
  `rejected`. If opted in, the worker PII-scans text and rejects any match (reserved test
  domains like `example.test` are allowed).
- Response `202`: `{"report_id": "rep_...", "status": "queued", "url": "https://.../app/acme/agent/reports/rep_..."}`.
- Worker rejections (`status: "rejected"`) say what kind of data was found and in which run,
  never the value, e.g. `... run run-lost-0002 (ledger[0].told) looks like it contains an
  email address ...`. The raw upload of a rejected report is deleted.

`GET /api/upload/{report_id}` (same auth as upload, or a session) → `{"report_id", "status", "rejection_reason"}`.
Credentials for a different repo get 404, the same as an unknown id.

`GET /api/upload/{report_id}/compare?base=branch:main` (same auth as upload) → `Compare`, or
409 `conflict` while the report is still processing. The Action uses this for the PR comment:
`polygraph upload --wait --compare-to main --pr-comment polygraph-comment.md`.

### Public

| Method | Path | Notes |
|---|---|---|
| GET | `/api/badge/{owner}/{repo}.svg?branch=main` | Live SVG. Public projects only; private or unknown → a neutral "unknown" badge (never leaks existence). `Cache-Control: public, max-age=300` |
| GET | `/api/leaderboard` | `Leaderboard` from the project named by `LEADERBOARD_PROJECT` |
| GET | `/api/health` | liveness `{"status": "ok"}` |
| GET | `/api/ready` | DB + storage + queue reachable: 200 `{"status": "ok", "checks": {"database": "ok", "storage": "ok", "queue": "ok"}}`, else 503 with `"status": "unavailable"` and the failing check marked `"unavailable"` |

`Leaderboard`:
```json
{"project": "polygraph-dev/benchmark", "updated_at": "..." | null,
 "entries": [{"framework": "langgraph", "framework_version": "1.2.0", "config_label": "default",
              "model": "...", "report_id": "rep_...", "runs": 400,
              "success": Rate, "silent_lie": Rate, "double_action": Rate, "hijack": Rate,
              "pass_k": PassK | null}]}
```
Empty `entries` until real benchmark results are uploaded. Nothing is ever invented.
Entries are the latest `ready` report per (framework, config_label, model), sorted by
silent-lie rate. Served with `Cache-Control: public, max-age=300`, like the badge.

## 5. Storage and processing

- Raw uploads: `s3://$BUCKET/raw/{project_id}/{report_id}.json.gz`; processed per-run
  files: `runs/{project_id}/{report_id}/{run_id}.json`. SSE-KMS.
- Queue message: `{"report_id": "rep_..."}`. Worker: load raw → `polygraph.results.parse`
  → content policy + PII scan → recompute summary with `polygraph.metrics.summarize` →
  write metrics + heatmap + run index to Postgres → status `ready` (or `rejected` with reason).
- Retention: full run files 90 days; report summaries 2 years. A daily job prunes.
- Backends switch by env for local dev without Docker:
  `DATABASE_URL` (postgres or sqlite), `STORAGE_BACKEND=s3|local`, `QUEUE_BACKEND=sqs|inline`.

## 6. Environment variables (cloud)

```
ENVIRONMENT=development|staging|production
PUBLIC_BASE_URL=https://polygraphmcp.dev
DATABASE_URL=postgresql+psycopg://...    | sqlite:///./dev.db
STORAGE_BACKEND=s3|local   S3_BUCKET=...   S3_ENDPOINT_URL= (minio)   LOCAL_STORAGE_DIR=./.data
KMS_KEY_ID=...
QUEUE_BACKEND=sqs|inline   SQS_QUEUE_URL=...   SQS_ENDPOINT_URL=
GITHUB_APP_ID, GITHUB_APP_CLIENT_ID, GITHUB_APP_CLIENT_SECRET, GITHUB_APP_PRIVATE_KEY (PEM, from Secrets Manager)
SESSION_SECRET (32+ bytes; signs state cookies)   TOKEN_ENCRYPTION_KEY (Fernet; encrypts GitHub refresh tokens)
OIDC_AUDIENCE=https://polygraphmcp.dev
LEADERBOARD_PROJECT=polygraph-dev/benchmark
PRIVACY_VERSION=2026-10-13
PII_SCANNER=presidio|regex
```

Optional, with safe defaults (cloud/api/README.md has the full list): `AWS_REGION`
(ap-south-1), `LOG_LEVEL`, `TRUSTED_PROXY_HOPS` (0; set to the number of proxies in front,
e.g. CloudFront + ALB = 2), `MAX_UPLOAD_BYTES`, `MAX_UNCOMPRESSED_BYTES`, `MAX_RUNS_PER_FILE`,
`UPLOADS_PER_PROJECT_PER_HOUR`, `UPLOADS_PER_IP_PER_HOUR`, `RUN_RETENTION_DAYS` (90),
`REPORT_RETENTION_DAYS` (730), `SESSION_DAYS` (30), `MEMBERSHIP_MAX_AGE_HOURS` (24),
`SQS_VISIBILITY_TIMEOUT_S` (300), `WORKER_MAX_ATTEMPTS` (5), `OIDC_ISSUER`, `OIDC_JWKS_URL`,
`GITHUB_API_URL`, `GITHUB_WEB_URL`. Outside development the API refuses to start with SQLite,
local storage, the inline queue, missing/weak/dev secrets, or a non-https `PUBLIC_BASE_URL`.

No secret ever appears in a URL, a log line or an error message.

## 7. Privacy and security rules (all components)

- Account data: GitHub user id, login, avatar URL. Nothing else. No email scope.
- Payload text is off by default everywhere uploads are involved; `mask` runs on the client
  before upload and fails closed.
- Region: AWS ap-south-1. TLS in transit, KMS at rest.
- Logs carry ids, never payload text, tokens, cookies or JWTs.
- Every delete is real (DB rows and S3 objects), and export returns everything.
- Test data uses only invented names and `example.test` addresses.
