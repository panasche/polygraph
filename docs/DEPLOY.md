# Deploying Polygraph Cloud

This is the runbook for the hosted dashboard: first launch, everyday deploys, rollbacks and incidents. For what each piece of infrastructure does, see [infra/README.md](../infra/README.md).

Placeholders look like `YOUR_AWS_ACCOUNT_ID`. Replace them with your own values, and never commit real ones.

## 0. Before anything else

- **Trademark check.** The name "Polygraph" is already used by other AI tools. Do a proper trademark search before buying the domain or launching. Everything below is driven by variables (`zone_name`, `site_domain`), so a different domain only means changing those.
- **Legal review.** Have the privacy notice, terms, retention periods and the breach process in section 8 reviewed by someone qualified in Indian data protection law.
- **Tools:** Terraform 1.9+, AWS CLI v2, GitHub CLI (`gh`), Python 3, OpenSSL.

## 1. Bootstrap the AWS account

Once per account. This creates the Terraform state bucket (encrypted, versioned, with S3 lock files), the GitHub Actions OIDC provider and the Route 53 zone.

```bash
cd infra/bootstrap
cp terraform.tfvars.example terraform.tfvars   # allowed_account_ids, zone_name
terraform init
terraform apply
terraform output
```

- Put `state_bucket` into `infra/envs/*/backend.hcl` (copy from `backend.hcl.example`).
- Keep `infra/bootstrap/terraform.tfstate` safe (it is gitignored).

## 2. DNS

Set the domain's name servers at your registrar to the `name_servers` output. Check it worked:

```bash
dig NS polygraphmcp.dev +short
```

Certificates are validated through DNS, so the first full `terraform apply` waits until this works.

## 3. Create the GitHub App

Sign-in uses a GitHub App, not an OAuth App. Create one per environment (staging and production), under the `polygraph-dev` organisation:

1. **Settings → Developer settings → GitHub Apps → New GitHub App.**
2. **Name:** e.g. "Polygraph for MCP" (production) and "Polygraph for MCP (staging)".
3. **Homepage URL:** `https://polygraphmcp.dev` (staging: `https://staging.polygraphmcp.dev`).
4. **Callback URL:** `https://polygraphmcp.dev/api/auth/github/callback`.
5. Tick **Request user authorization (OAuth) during installation**. Leave **Enable Device Flow** off and **Expire user authorization tokens** on.
6. **Webhook:** off (untick Active). The service doesn't need webhooks.
7. **Permissions:** only **Repository → Metadata: Read-only**. No other repository, organisation or account permissions. In particular **no email address permission**: the dashboard only keeps the GitHub user id, login and avatar.
8. **Where can this GitHub App be installed?** Any account.
9. Create it, then:
   - note the **App ID** and **Client ID**,
   - **Generate a new client secret**,
   - **Generate a private key** (downloads a `.pem` file).

Write them into Secrets Manager in step 4, then delete the downloaded `.pem` from your machine. Don't paste any of these into chat, tickets or files in the repo.

## 4. Set the secrets

```bash
cd infra/envs/staging
cp terraform.tfvars.example terraform.tfvars    # fill in placeholders
terraform init -backend-config=backend.hcl
terraform apply -target=module.secrets           # empty secrets only, first run only
../../scripts/set-secrets.sh staging
```

The script asks for the GitHub App values (input hidden), reads the `.pem` path, and generates `SESSION_SECRET` (48 random bytes) and `TOKEN_ENCRYPTION_KEY` (a Fernet key) if you press Enter. Nothing goes on the command line or into Terraform state.

The database password needs no action: RDS creates it, keeps it in Secrets Manager and rotates it.

## 5. First deploy

```bash
terraform apply                                  # everything
../../scripts/sync-github-env.sh staging         # copy outputs into the GitHub Environment
```

In GitHub, **Settings → Environments**:

- `staging`: optional protection; restrict deployments to the `main` branch.
- `production`: **required reviewers** (at least one person who isn't the author), restrict to `main`, and turn on "Prevent self-review".
- `testpypi` and `pypi`: for the release workflow (trusted publishing). Add required reviewers on `pypi`.

Optionally add an Environment variable `MIGRATION_COMMAND` if the migration command ever changes (default `["python","-m","polygraph_cloud.cli","migrate"]`).

Then run **Actions → Deploy → Run workflow**. It:

1. builds the API and web images for ARM64 and pushes them to ECR, tagged with the git SHA, with provenance and an SBOM,
2. checks the images run as a non-root user,
3. registers new task definitions (copying everything but the image from what Terraform set),
4. runs database migrations as a one-off ECS task and stops if they fail,
5. rolls out api, worker and web and waits until each deployment has really completed,
6. smoke-tests `/api/health` and `/` through CloudFront,
7. after a reviewer approves, does the same for production with the *same* image digests copied from staging.

Repeat steps 4 and 5 for `production` (with `envs/production`), then set `ecr_pull_principal_arns` in staging and `promotion_source_ecr_repository_arns` in production and apply both, so production can copy images from staging.

After that, every push to `main` that touches `cloud/` deploys to staging, then waits for approval for production.

### Migrations must be backward compatible

Migrations run *before* new code starts, while old tasks still serve traffic. So every migration must work with both the old and the new code: add columns as nullable or with defaults, backfill in a later release, and drop old columns only once no running code uses them.

## 6. Rolling back

**A bad deploy that fails health checks rolls itself back.** ECS's circuit breaker returns the service to the last working task definition, and the Deploy workflow fails loudly.

**A bad deploy that is healthy but wrong:**

1. Find the last good commit SHA (the Deploy run history shows which SHA each run deployed).
2. **Actions → Deploy → Run workflow**, set **image-tag** to that SHA. This skips the build and redeploys images that are already in ECR (tags are immutable, so it is exactly what ran before).
3. Approve the production step.

If a migration has to be undone, write a new forward migration that reverses it, rather than downgrading in production.

**Infrastructure changes:** revert the Terraform change in git and apply again. State is versioned in S3, so an older state can be recovered if something goes badly wrong (ask for a second pair of eyes before doing that).

**Database:** RDS keeps automated backups (7 days in staging, 30 in production) and can restore to any point in that window into a *new* instance. Restore, check the data, then point the app at it with Terraform.

## 7. Everyday operations

| Task | How |
|---|---|
| See logs | CloudWatch log groups `/polygraph/<env>/{api,worker,web}` |
| Alarms | SNS topic `polygraph-<env>-alarms`; subscribe a team mailbox via `alarm_emails` |
| Upload stuck in the dead-letter queue | Look at the worker logs for the report id, fix the cause, then **SQS → polygraph-<env>-uploads-dlq → Start DLQ redrive** |
| Rotate the GitHub client secret | Generate a new one in the GitHub App, `set-secrets.sh <env> --only github_app_client_secret`, restart api and worker, then delete the old secret in GitHub |
| Rotate `SESSION_SECRET` | `set-secrets.sh <env> --only session_secret`, restart. Everyone gets signed out |
| Rotate the origin header | See "How secrets get set" in infra/README.md |
| Shell into a task | Staging only: `aws ecs execute-command ...`. ECS Exec is off in production |
| Retention prune | Runs daily at 03:00 IST as a scheduled task; check the worker log group |

## 8. Incidents and data breaches

Polygraph Cloud holds a small amount of personal data: GitHub user id, login and avatar URL, encrypted GitHub tokens, session hashes, and (only for projects that opt in) payload text that passed a PII scan. Under the **Digital Personal Data Protection Act, 2023** the operator is the *Data Fiduciary* for this data. The steps below are a practical starting point, not legal advice: have them reviewed, and check the rules in force at the time (including the DPDP Rules) for exact contents and deadlines.

### What counts as a personal data breach

Any unauthorised processing, or accidental disclosure, access, change, loss or destruction of personal data, that affects its confidentiality, integrity or availability. Examples: a leaked database snapshot, a bug that shows one user's private project to another, a stolen GitHub App private key, logs that captured tokens.

### Roles

Name these people before launch and keep the list somewhere the whole team can find it:

- **Incident lead:** runs the response and makes decisions.
- **Technical lead:** investigates and contains.
- **Communications and legal contact:** handles notices to the Board and to users.
- **Grievance officer:** the published contact for user complaints (shown in the privacy notice).

### Steps

1. **Detect and record.** Open a private incident record with the time you found out. Write down facts as you learn them: what, when, who, how many users, which data. Keep records, don't delete evidence.
2. **Contain.** Stop the leak first. Typical actions:
   - revoke and rotate the affected secret (GitHub App private key and client secret, `SESSION_SECRET`, `TOKEN_ENCRYPTION_KEY`, the origin header, the database password via RDS),
   - sign everyone out (rotating `SESSION_SECRET` does this),
   - block abusive IPs in the WAF, or scale the affected service to zero,
   - make a leaked bucket object private, or take the site down to a static notice if needed.
3. **Assess.** Work out which people are affected and what the realistic harm is. Because the service stores so little, many incidents will involve only GitHub logins and avatars, which are public anyway, but treat every case on its facts.
4. **Notify the Data Protection Board of India** without delay. Plan for an initial intimation as soon as you know a breach happened, and a detailed report within **72 hours** of becoming aware of it (or the period the Rules set at the time). The detailed report covers: what happened, when and how, the likely impact, what you have done to contain it, what you are doing to stop it happening again, and the notices sent to users.
5. **Tell affected users** without delay, in plain words: what happened, which of their data, what it could mean for them, what you have done, what they can do (e.g. revoke the app in GitHub settings, re-authorise), and how to reach the grievance officer. Email isn't collected, so use the dashboard (a banner on sign-in), a GitHub issue or discussion for public context, and the GitHub App's own channels.
6. **Recover.** Fix the root cause, restore from backup if data was lost or changed, and confirm the fix with a test.
7. **Review.** Write a short blameless review: timeline, cause, what went well, what to change. Track the follow-up actions to completion.

### Security reports from outside

Reports to `security@polygraphmcp.dev` follow [SECURITY.md](../SECURITY.md). If a report turns out to involve personal data, it becomes an incident and follows the steps above.

## 9. Shutting an environment down

1. Export anything you need to keep.
2. Turn off `db_deletion_protection` and `alb_deletion_protection`, apply.
3. `terraform destroy`. The results bucket refuses to delete while it holds objects; empty it first if you really mean it. A final database snapshot is kept in production.
4. Delete the GitHub App for that environment.
