# Polygraph for MCP: GitHub Action

Runs your pytest suite with Polygraph, which breaks your agent's MCP tools on purpose and checks what the agent *said* against what *really* happened. Then it posts the numbers on the pull request:

> **Polygraph** vs `main`: silent-lie 4 % → 0 % · double-action 2 % → 2 % · coverage 14/22 → 18/22

Uploading to the free dashboard (Polygraph Cloud) is **off by default**. Everything works without it.

## Quick start

```yaml
# .github/workflows/polygraph.yml
name: Polygraph
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  actions: read          # find the main-branch results to compare against
  pull-requests: write   # the sticky PR comment

jobs:
  polygraph:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - uses: polygraph-dev/polygraph/action@v1
        with:
          install: pytest-polygraph -e .[test]
          runs: 3
          compare-to: main
```

Pushes to `main` save the results as a workflow artifact. Pull requests download the newest one from `main` and compare against it, so keep the `push` trigger.

For production use, pin the action to a full commit SHA instead of `@v1`.

## Inputs

| Input | Default | What it does |
|---|---|---|
| `python-version` | `3.12` | Python for `actions/setup-python`. Empty uses the runner's Python |
| `install` | `pytest-polygraph` | Arguments for `pip install`. Add your project, e.g. `pytest-polygraph -e .[test]` |
| `working-directory` | `.` | Where pytest runs (where your `pyproject.toml` with `[tool.polygraph]` is) |
| `pytest-args` | | Extra pytest arguments: paths, `-k`, `-m` |
| `runs` | `3` | Repeats per (test, fault) pair |
| `auto` | `true` | Plan the tool × fault matrix from your servers' tool labels (`--polygraph-auto`) |
| `compare-to` | `main` | Branch to compare against |
| `upload` | `false` | Send results to Polygraph Cloud. Needs `id-token: write` |
| `api-url` | `https://polygraphmcp.dev/api` | Polygraph Cloud API |
| `audience` | | OIDC audience for uploads (the CLI default is `https://polygraphmcp.dev`) |
| `comment` | `true` | Post or update one sticky PR comment |
| `report-path` | `.polygraph` | Where `results.json`, `report.html`, `junit.xml` and the comment go |
| `fail-on` | `silent_lie,double_action,hijacked` | Outcomes that fail the job |
| `results` | | Use an existing `results.json` and skip pytest (fork-PR pattern below) |
| `pr-number` | | PR to comment on. Default: the `pull_request` event's PR |
| `artifact-name` | `polygraph-results` | Artifact name. Make it unique per matrix leg |
| `upload-artifact` | `true` | Keep the reports as a workflow artifact |
| `github-token` | `${{ github.token }}` | Token for the base artifact and the comment |

### When does the job fail?

- Any run lands in an outcome listed in `fail-on` (choose from `recovered`, `honest_fail`, `silent_lie`, `double_action`, `hijacked`, `loop`, `crash`).
- A test fails for an ordinary reason (a normal `assert`, an exception), not because of a Polygraph outcome.
- pytest can't run at all (no tests collected, bad arguments, internal error).

The reports and the PR comment are written first, so you always see why.

## Outputs

| Output | Example |
|---|---|
| `silent-lie-rate` | `0.025` (empty when there were no faulted runs) |
| `coverage` | `18/22` |
| `report-url` | The dashboard report when uploaded, otherwise this workflow run |

## Uploading to Polygraph Cloud

No secret needed: the Action proves which repository it runs in with GitHub's short-lived OIDC token.

```yaml
permissions:
  contents: read
  actions: read
  pull-requests: write
  id-token: write        # lets the Action prove which repo it runs in

steps:
  - uses: actions/checkout@v7
    with:
      persist-credentials: false
  - uses: polygraph-dev/polygraph/action@v1
    with:
      runs: 3
      compare-to: main
      upload: true
```

What gets uploaded: numbers, outcome buckets, tool and fault names, timings and *hashes* of payloads. Payload text is stripped before upload unless your project has opted in, and even then a `mask` function you write runs first and fails closed.

Public repositories get a project on first upload. Private repositories need the Polygraph GitHub App installed (read-only *metadata* permission, nothing else).

## Badge

With uploads on, add this to your README (public projects only):

```markdown
[![Polygraph](https://polygraphmcp.dev/api/badge/OWNER/REPO.svg?branch=main)](https://polygraphmcp.dev/app/OWNER/REPO)
```

The badge shows the silent-lie rate next to coverage, so "0 % lies" with almost nothing tested can't mislead anyone. Without uploads, `polygraph badge --out badge.svg` makes a static one in CI.

## Pull requests from forks

GitHub gives pull requests from forks a **read-only token and no OIDC token**, so the job can neither comment nor upload. The safe pattern has two workflows:

1. The `pull_request` workflow runs the tests (untrusted code, no secrets, read-only token) and saves the results and the PR number as artifacts.
2. A `workflow_run` workflow starts when the first one finishes. It runs in *your* repository with a write token. It **never checks out or runs the fork's code**. It only downloads the artifacts, treats them as data, checks them, and comments.

Never use `pull_request_target` together with a checkout of the PR's code: that runs untrusted code with write access to your repository.

### Workflow 1: run the tests

```yaml
# .github/workflows/polygraph.yml
name: Polygraph
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  polygraph:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read
      pull-requests: write   # used only for same-repo PRs
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false

      - uses: polygraph-dev/polygraph/action@v1
        with:
          install: pytest-polygraph -e .[test]
          runs: 3
          # Same-repo PRs comment here; fork PRs are handled by workflow 2.
          comment: ${{ github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository }}

      - name: Save the PR number
        if: always() && github.event_name == 'pull_request'
        env:
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          mkdir -p "$RUNNER_TEMP/pr"
          printf '%s\n' "$PR_NUMBER" > "$RUNNER_TEMP/pr/pr-number.txt"

      - uses: actions/upload-artifact@v7
        if: always() && github.event_name == 'pull_request'
        with:
          name: polygraph-pr
          path: ${{ runner.temp }}/pr/pr-number.txt
          retention-days: 5
```

### Workflow 2: comment (and upload) from the base repository

```yaml
# .github/workflows/polygraph-fork.yml
name: Polygraph (fork PR comment)
on:
  workflow_run:
    workflows: [Polygraph]
    types: [completed]

permissions:
  contents: read

jobs:
  comment:
    if: >-
      github.event.workflow_run.event == 'pull_request' &&
      github.event.workflow_run.head_repository.full_name != github.repository
    runs-on: ubuntu-latest
    permissions:
      actions: read          # download the other run's artifacts
      pull-requests: write   # comment
    steps:
      # Data only: the fork's results and PR number. No fork code is checked out.
      - uses: actions/download-artifact@v8
        with:
          run-id: ${{ github.event.workflow_run.id }}
          github-token: ${{ github.token }}
          path: ${{ runner.temp }}/fork

      # The PR number came from untrusted code. Keep digits only, then make sure
      # that PR's head really is the commit that produced these results.
      - name: Verify the PR number
        id: pr
        env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          HEAD_SHA: ${{ github.event.workflow_run.head_sha }}
          FILE: ${{ runner.temp }}/fork/polygraph-pr/pr-number.txt
        run: |
          pr="$(head -c 16 "$FILE" | tr -dc '0-9')"
          [ -n "$pr" ] || exit 1
          [ "$(gh api "repos/$REPO/pulls/$pr" --jq .head.sha)" = "$HEAD_SHA" ] || exit 1
          echo "number=$pr" >> "$GITHUB_OUTPUT"

      - uses: polygraph-dev/polygraph/action@v1
        with:
          results: ${{ runner.temp }}/fork/polygraph-results/results.json
          pr-number: ${{ steps.pr.outputs.number }}
          compare-to: main
          comment: true
          upload: false
```

`workflow_run` always uses the workflow file from your default branch, so a fork can't change what this second workflow does. The results file is validated against the results schema before anything reads it.

**Comment only, don't upload, from workflow 2.** The OIDC token in a `workflow_run` job belongs to your default branch, not to the fork's PR, so an upload from there would be counted as a `main` result. Fork PRs still get the full comparison in their comment (against the `main` artifact); results reach the dashboard once the change is merged and `main` uploads.

## Permissions summary

| Feature | Permission |
|---|---|
| Run tests, keep artifacts | `contents: read` |
| Compare with `main` (download its artifact) | `actions: read` |
| PR comment | `pull-requests: write` |
| Upload to Polygraph Cloud | `id-token: write` |

The comment is posted with the workflow's own `GITHUB_TOKEN`. Polygraph Cloud never needs write access to your repository.

## Privacy

- Payload text is never uploaded unless your project opts in, and OpenTelemetry spans from CI never carry payload text.
- The HTML and JSON reports stay in your workflow artifacts (kept for 30 days).
- Test with invented data. Use `example.test` addresses for anything that looks like an email.
