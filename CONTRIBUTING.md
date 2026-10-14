# Contributing to Polygraph for MCP

Thanks for helping. This guide covers how the repository is laid out, how to run things locally, and what we look for in a pull request.

## What lives where

| Folder | What it is |
|---|---|
| `packages/pytest-polygraph/` | The pytest plugin, proxy and `polygraph` command (Python) |
| `cloud/api/` | Polygraph Cloud API and upload worker (FastAPI) |
| `cloud/web/` | Landing page and dashboard (Next.js) |
| `action/` | The GitHub Action |
| `benchmark/` | Benchmark tasks and framework adapters |
| `infra/` | Terraform for AWS (ap-south-1) |
| `docs/CONTRACTS.md` | The shared contract every part builds against |

If your change touches an API, a file format, an environment variable or a CLI flag, update `docs/CONTRACTS.md` in the same pull request.

## Getting set up

### The Python package

```bash
cd packages/pytest-polygraph
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev,worlds]"
ruff check . && ruff format --check . && mypy && pytest
```

Try the example suite:

```bash
cd examples/bank
pytest --polygraph-auto --polygraph-report=polygraph.html
```

### The whole cloud stack

You need Docker.

```bash
cp .env.example .env
docker compose up --build
```

The web app is on http://localhost:3000 and the API on http://localhost:8000. In development you can sign in with `POST /api/auth/dev-login`; you don't need a GitHub App.

### Infrastructure

```bash
terraform fmt -recursive infra
cd infra/envs/staging && terraform init -backend=false && terraform validate
```

Never run `apply` from a pull request branch. See `infra/README.md`.

## Pull requests

- Keep each pull request to one change. Small is good.
- Add or update tests. CI runs on Linux and Windows with Python 3.11, 3.12 and 3.13.
- Run the linters before pushing (see above). CI runs the same ones, plus `actionlint`, Trivy and Checkov.
- Write commit messages that say *why*, not only *what*.
- New GitHub Actions must be pinned to a full commit SHA with the version in a comment.

## Data and safety rules

These apply to code, tests, fixtures, docs and screenshots:

- **Only invented data.** Use names like "Customer A" and addresses on `example.test`. No real people, emails, phone numbers or ID numbers.
- **No secrets.** Use placeholders like `YOUR_GITHUB_APP_ID`. Keys belong in environment variables or a secret store, never in the repo.
- **Payload text stays off by default.** Anything that uploads results must strip payload text unless the user opts in.
- **Logs carry ids, never payload text, tokens, cookies or JWTs.**
- **Injected test text must be harmless**, and fake tools must never touch the network.

## Reporting bugs and security problems

Open an issue for normal bugs. For anything security-related, follow [SECURITY.md](SECURITY.md) instead.

## Licence

By contributing you agree that your work is released under the [Apache License 2.0](LICENSE).

## Code of conduct

Everyone taking part follows our [Code of Conduct](CODE_OF_CONDUCT.md).
