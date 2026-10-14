# Security policy

Polygraph for MCP has two parts with different risks:

- **The open-source tools** (`pytest-polygraph`, the proxy, the GitHub Action). These run on your machine or in your CI.
- **Polygraph Cloud**, the free hosted dashboard at `polygraphmcp.dev`. It stores test results that people upload.

We take reports about both seriously.

## Reporting a problem

Please **do not** open a public issue for a security problem.

- Email **security@polygraphmcp.dev**, or
- use GitHub's **private vulnerability reporting** on this repository (Security tab, "Report a vulnerability").

Tell us:

- what is affected (package version, Action version, or the cloud URL),
- how to reproduce it, step by step,
- what an attacker could do with it,
- whether you have shared it with anyone else.

Please use made-up data in your report. Don't send us real personal data, API keys or tokens. If you found someone else's data by accident, stop, don't keep a copy, and tell us.

## What happens next

1. We confirm we got your report, normally within 3 working days.
2. We check it, agree on how serious it is, and keep you updated.
3. We fix it, release the fix, and then publish an advisory. We credit you unless you'd rather we didn't.

If a report involves personal data held by Polygraph Cloud, we follow the breach process in [docs/DEPLOY.md](docs/DEPLOY.md#incidents-and-data-breaches), which includes telling affected users and the regulator where the law requires it.

## Good-faith research

We won't take action against you for research that:

- only uses accounts and repositories you own or have permission to test,
- doesn't read, change or delete other people's data,
- doesn't degrade the service for others (no load tests, no automated scanning at volume, no spam),
- gives us a fair chance to fix the problem before you talk about it publicly.

Polygraph Cloud is a free service with rate limits. Hitting those limits on purpose is not a finding.

## Out of scope

- Findings that need a rooted or already-compromised device.
- Missing security headers on pages with no sensitive content, unless you can show a real impact.
- Reports from automated scanners with no proof that the issue can be exploited.
- Fault-injection behaviour that is the product working as designed (for example, the proxy sending fake "injected" text to *your own* agent under test).

## Supported versions

We fix security problems in the latest released version of `pytest-polygraph` and the latest major version of the Action. Polygraph Cloud always runs the latest version.

A machine-readable contact is published at `https://polygraphmcp.dev/.well-known/security.txt`.
