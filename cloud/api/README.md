# Polygraph Cloud API

This is the backend of **Polygraph Cloud**, the free results dashboard for Polygraph for MCP.
Think of it as Codecov for agent honesty: your CI uploads the Polygraph results file, and the
dashboard turns it into trends, a run page, a compare view, a README badge and a leaderboard.

Two rules shape everything here:

- **The cloud only stores and shows results.** It never runs your agent, never calls a model
  and never sees your API keys.
- **Uploads are optional and private by default.** Payload text is off unless a project turns
  it on, and even then every upload is scanned for personal data.

The HTTP contract lives in [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) (section 4). This
service implements it exactly.

---

## Run it on your laptop

You need Python 3.11+. No Docker, no Postgres, no AWS: locally the API uses SQLite, a folder
for file storage, and an in-process queue.

```bash
cd cloud/api
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ../../packages/pytest-polygraph --no-deps   # the shared results schema
pip install -e ".[test]"

scripts/dev.sh          # or on Windows PowerShell: .\scripts\dev.ps1
```

The API is now on <http://127.0.0.1:8000> (interactive docs at `/api/docs`). The script sets:

| Setting | Value |
|---|---|
| `ENVIRONMENT` | `development` (turns on dev-login) |
| `DATABASE_URL` | `sqlite:///./.data/dev.db` (migrations run at startup) |
| `STORAGE_BACKEND` / `LOCAL_STORAGE_DIR` | `local` / `./.data` |
| `QUEUE_BACKEND` | `inline` (uploads are processed right after the response) |
| `PUBLIC_BASE_URL` | `http://localhost:3000` (the Next.js app; CORS allows it) |

The web app (`cloud/web`) proxies `/api/*` to port 8000, so open <http://localhost:3000>.

### Try it

```bash
# 1. Sign in as a fake user (development only; 404 everywhere else)
curl -c jar.txt -X POST localhost:8000/api/auth/dev-login \
     -H 'Content-Type: application/json' -d '{"login": "dev-user"}'

# 2. Load a results file through the real pipeline (validate, scan, recompute, store)
python -m polygraph_cloud.cli seed --results .polygraph/results.json \
       --project octo-demo/agent --branch main --public

# 3. Make an upload token and upload like the CLI does
TOKEN=$(python -m polygraph_cloud.cli create-token --project octo-demo/agent --name laptop)
gzip -c .polygraph/results.json | curl -X POST localhost:8000/api/upload \
     -H "Authorization: Token $TOKEN" -H 'Content-Encoding: gzip' --data-binary @-

# 4. Look at it
curl localhost:8000/api/projects/octo-demo/agent/trends
curl localhost:8000/api/badge/octo-demo/agent.svg
```

Run `seed` and `create-token` with the same environment as the dev script (from `cloud/api`,
the defaults already match). The dev-login user becomes admin of every local project.

### Run the tests

```bash
pytest -q
```

Everything is mocked: GitHub calls go through `respx`, and OIDC tokens are signed with a key
made on the fly. To run the same suite against PostgreSQL, point it at any empty database:

```bash
POLYGRAPH_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/polygraph_test pytest -q
```

No Postgres around? `pip install pgserver` gives you a throwaway one:
`python -c "import pgserver; print(pgserver.get_server('.data/pg', cleanup_mode=None).get_uri())"`.

---

## Settings

All settings come from environment variables. Outside `development` the API **refuses to
start** with anything that only makes sense on a laptop: SQLite, local storage, the inline
queue, missing or weak secrets, development secrets, or a plain-http public URL. In
production `KMS_KEY_ID` is also required.

| Variable | What it does |
|---|---|
| `ENVIRONMENT` | `development`, `staging` or `production` |
| `PUBLIC_BASE_URL` | The site's address, e.g. `https://polygraphmcp.dev`. CORS allows only this origin. The GitHub callback is `{PUBLIC_BASE_URL}/api/auth/github/callback` |
| `DATABASE_URL` | `postgresql+psycopg://...` (or `postgresql://`), or `sqlite:///./dev.db` in dev |
| `STORAGE_BACKEND`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `LOCAL_STORAGE_DIR` | Where raw uploads and run files go. `S3_ENDPOINT_URL` is for MinIO |
| `KMS_KEY_ID` | Every S3 object is written with SSE-KMS using this key |
| `QUEUE_BACKEND`, `SQS_QUEUE_URL`, `SQS_ENDPOINT_URL` | The upload queue (`sqs` or `inline`) |
| `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY` | The GitHub App (from Secrets Manager) |
| `SESSION_SECRET` | 32+ bytes. Signs the OAuth state cookie and derives CSRF tokens and IP hashes |
| `TOKEN_ENCRYPTION_KEY` | A Fernet key. Encrypts GitHub user tokens at rest |
| `OIDC_AUDIENCE` | The audience the Action asks GitHub for (default `https://polygraphmcp.dev`) |
| `LEADERBOARD_PROJECT` | The public project whose reports make the leaderboard |
| `PRIVACY_VERSION` | The current privacy notice version users must accept |
| `PII_SCANNER` | `regex` (always available) or `presidio` (install `.[presidio]` and a spaCy model) |

Optional knobs, with defaults: `AWS_REGION` (ap-south-1), `LOG_LEVEL` (INFO),
`TRUSTED_PROXY_HOPS` (0; set it to the number of proxies in front, e.g. 2 for CloudFront +
ALB, so per-IP limits see the real client), `MAX_UPLOAD_BYTES` (10 MB),
`MAX_UNCOMPRESSED_BYTES` (50 MB), `MAX_REQUEST_BYTES` (1 MB, every other endpoint),
`MAX_RUNS_PER_FILE` (5000), `UPLOADS_PER_PROJECT_PER_HOUR` (60), `UPLOADS_PER_IP_PER_HOUR`
(600), `MAX_TOKENS_PER_PROJECT` (20), `SESSION_DAYS` (30), `MEMBERSHIP_MAX_AGE_HOURS` (24),
`AUTO_SYNC_AFTER_MINUTES` (60), `RUN_RETENTION_DAYS` (90), `REPORT_RETENTION_DAYS` (730),
`SQS_VISIBILITY_TIMEOUT_S` (300), `SQS_WAIT_TIME_S` (20), `WORKER_MAX_ATTEMPTS` (5),
`OIDC_ISSUER`, `OIDC_JWKS_URL`, `OIDC_LEEWAY_S` (60), `JWKS_CACHE_TTL_S` (3600),
`GITHUB_API_URL`, `GITHUB_WEB_URL`, `AUTO_MIGRATE` (dev + SQLite only).

---

## How it works

```
CI / laptop ──HTTPS──> CloudFront + WAF ──> API (FastAPI) ──> S3 raw/…json.gz (KMS)
                                               │  └──────────> SQS {"report_id"}
                                               │                    │
Browser ──> Next.js ──/api──> API <── Postgres <── Worker <─────────┘
                                                    └──> S3 runs/…/{run_id}.json
```

1. **Upload.** `POST /api/upload` checks who is sending: a GitHub Actions OIDC token (signature
   against GitHub's published keys, issuer, audience, expiry) or a `pgp_` upload token (looked
   up by its SHA-256 hash). It reads at most 10 MB, unpacks gzip in a stream that stops at
   50 MB, validates the file with the same Pydantic models the plugin writes with, applies
   rate limits and the text policy, saves the raw file, queues a job and answers `202`.
2. **Process.** The worker (`python -m polygraph_cloud.worker`) long-polls SQS. For each job it
   re-validates the file, re-checks the text policy, PII-scans payload text if there is any,
   **recomputes every number** with `polygraph.metrics.summarize` (the uploaded summary is
   ignored, and coverage is recounted from the runs), writes one JSON file per run, then the
   metrics, heatmap and run index, and marks the report `ready`. Anything wrong marks it
   `rejected` with a plain reason, and the raw upload is deleted.
3. **Show.** The dashboard reads numbers from Postgres. Rates come back with 95 % Wilson
   confidence intervals. Run pages read the per-run file from storage.

Jobs are safe to repeat. A report is claimed with a conditional update, derived rows are
replaced in one transaction, and run files have fixed names. If processing throws, the
report goes back to `queued` and SQS redelivers the message after the visibility timeout. On
the `WORKER_MAX_ATTEMPTS`-th delivery the report is rejected and the message deleted.
Malformed messages are dropped. Give the queue a dead-letter queue with a higher
`maxReceiveCount` to catch worker crashes too.

### Who can see what

Visibility follows GitHub.

- **Public repo:** results are public by default. An admin can switch that off.
- **Private repo:** only people GitHub says can see the repo. We learn this through the GitHub
  App (read-only *metadata* permission): at sign-in, on `POST /api/projects/sync`, and
  automatically when the project list is opened after an hour. GitHub `admin` or `maintain`
  means **admin** here; any other access means **member**. Cached permissions older than 24
  hours are ignored.
- **Anything you can't see looks exactly like something that doesn't exist:** the same 404,
  and the same grey "unknown" badge. If a repo turns private, its results are hidden on the
  next upload or sync.

### Sign-in

GitHub App user authorization with PKCE and a signed, single-use state. No email scope: we
keep the GitHub user id, login and avatar URL, nothing else. GitHub tokens are encrypted with
Fernet and used only to read repo permissions. The session cookie `pg_session` is HttpOnly,
SameSite=Lax, Secure on https, and lasts 30 days (sliding). It's stored as a SHA-256 hash.
Every change made with a session needs the `X-CSRF-Token` header matching the `pg_csrf`
cookie, which is itself tied to the session.

---

## Privacy and security

### What we store

| Data | Why | How long |
|---|---|---|
| GitHub user id, login, avatar URL | To show who you are and check repo access | Until you delete your account |
| Encrypted GitHub tokens | To re-read repo permissions | Until you delete your account (and we revoke the grant at GitHub) |
| Session hashes | To keep you signed in | 30 days after last use |
| Results numbers, heatmap, run index | The dashboard | 2 years |
| Raw upload and per-run files | Run and ledger pages | 90 days |
| Audit log (ids only) | Security | Kept; your user id is removed when you delete your account |
| Rate-limit counters (keyed IP hashes) | Abuse control | 2 days |

Payload text is not stored unless the project opted in, and then only after it passed the PII
scan. IP addresses are never stored or logged in the clear.

### The PII scanner

`polygraph_cloud/pii.py` scans **payload text only**. The `regex` backend finds email
addresses (reserved test domains from RFC 2606 and RFC 6761 such as `example.test`,
`example.com`, `.invalid` and `.localhost` are allowed), Indian mobile numbers, E.164 phone
numbers, PAN numbers, Aadhaar numbers (12 digits that pass the Verhoeff checksum) and public
IP addresses (private, loopback, link-local, CGNAT and documentation ranges are allowed). The
`presidio` backend adds Microsoft Presidio's recognisers on top. A rejection names the kind
of data and the run, never the value.

### Logs

One JSON line per request: method, path (never the query string), status, time and a request
id. A redaction step drops any field that looks like a token, cookie, secret, JWT or payload,
and masks credential-shaped strings anywhere. Uvicorn's own access log is off.

### Hardening

Security headers on every response (`nosniff`, `DENY` framing, `no-referrer`, a strict CSP,
HSTS on https), `Cache-Control: no-store` except on the badge and leaderboard, a 1 MB body
limit on everything but uploads, CORS locked to `PUBLIC_BASE_URL`, `/api/docs` off in
production, and one error shape everywhere: `{"error": {"code": ..., "message": ...}}`.

### DPDP Act 2023

As the operator, Polygraph Cloud is the **Data Fiduciary** for account data. What that means
in practice here:

- **Notice and consent.** Users accept the privacy notice (`PRIVACY_VERSION`) right after
  first sign-in. Until they do, they can read but can't change anything (403
  `consent_required`). When the notice changes, bump `PRIVACY_VERSION` and users are asked
  again.
- **Data minimisation.** Only GitHub id, login and avatar. No email. No payload text by
  default.
- **Rights.** `GET /api/me/export` returns everything tied to the account as JSON.
  `DELETE /api/me`, `DELETE /api/projects/...` and `DELETE .../reports/...` really delete:
  database rows and storage objects.
- **Storage location and security.** AWS ap-south-1 (Mumbai), TLS in transit, KMS at rest.
- **Grievance contact:** `[EMAIL]` (replace before launch, and publish it in the privacy
  notice together with a breach-reporting process).

Get the privacy notice and these flows reviewed before launch.

---

## Operations

| Task | Command |
|---|---|
| API | `uvicorn polygraph_cloud.app:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log` |
| Worker | `python -m polygraph_cloud.worker` (`--once` drains and exits) |
| Migrations (release step) | `python -m polygraph_cloud.cli migrate` |
| Retention (daily) | `python -m polygraph_cloud.jobs.prune` (`--dry-run` counts only) |

The prune job deletes run files and raw uploads after 90 days, report rows after 2 years,
expired sessions and OAuth states, old rate-limit windows, and rejects reports stuck in the
queue for a day.

### Docker

One image runs the API (default command), the worker, migrations and the prune job. It is
multi-stage, runs as a non-root user (uid 10001) and has a health check on `/api/health`.
The build context is `cloud/api`; the shared schema package comes in as a named context:

```bash
docker build -t polygraph-cloud \
  --build-context polygraph_pkg=packages/pytest-polygraph \
  cloud/api
```

In docker compose: `build: {context: cloud/api, additional_contexts: {polygraph_pkg: packages/pytest-polygraph}}`.

### Migrations

`src/polygraph_cloud/migrations` holds the Alembic history. The first revision was generated
against PostgreSQL 16 and checked to match the models exactly (no drift), and to upgrade and
downgrade cleanly. The same files run on SQLite in development.

---

## Code map

```
src/polygraph_cloud/
  app.py            FastAPI factory, routers, middleware order, lifespan
  config.py         settings + the "refuse to start" checks
  context.py        shared services (db, storage, queue, http, OIDC, GitHub, crypto)
  models.py         tables and indexes
  db.py             async engine (psycopg 3 / aiosqlite)
  api/
    middleware.py   request id, security headers, body limit, CSRF, cookies, access log
    deps.py         db session, signed-in user, sliding sessions, consent check
    auth.py         GitHub sign-in (PKCE), logout, dev-login
    me.py           profile, consent, export, account deletion
    projects.py     projects, reports, runs, trends, branches, compare, tokens
    upload.py       POST /api/upload, upload status and compare
    public.py       badge, leaderboard, health, ready
  services/
    access.py       visibility rules
    ingest.py       safe gzip, schema validation, git metadata, report creation
    processing.py   the worker pipeline
    queries.py      latest reports, compare
    sync.py         GitHub installations -> projects and roles
    deletion.py     real deletes (storage first, then rows)
    serialize.py    rows -> API shapes, cursors
    audit.py        audit log
  oidc.py           GitHub Actions OIDC verification with a rotating JWKS cache
  github.py         GitHub App calls
  pii.py            PII scanners (regex, presidio) + Verhoeff
  badge.py          the SVG badge
  storage.py        S3 (SSE-KMS) and local storage
  queue.py          SQS and inline queue
  ratelimit.py      fixed-window counters
  security.py       hashing, signing, CSRF, Fernet
  logs.py           structured logs with redaction
  worker.py         queue consumer
  jobs/prune.py     retention job
  cli.py            migrate, seed, create-token
  migrations/       Alembic
```
