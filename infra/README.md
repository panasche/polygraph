# Polygraph Cloud infrastructure

Terraform for the hosted dashboard. Everything that stores user data runs in **AWS ap-south-1 (Mumbai)**. Only two things live in us-east-1, because CloudFront requires it: the TLS certificate for the public site and the CloudFront web firewall (WAF). Neither stores user data.

Step-by-step instructions for a first launch, rollbacks and incidents are in [docs/DEPLOY.md](../docs/DEPLOY.md). This page explains what is here and why.

## The big picture

```
Browser / CI ──HTTPS──> CloudFront + WAF ──HTTPS + secret header──> ALB ──> ECS Fargate (ARM64)
                         (us-east-1 cert)                         (only CloudFront   ├─ web     :3000
                                                                   can reach it)     ├─ api     :8000 ──> S3 (KMS)
                                                                                     └─ worker  <── SQS ─┘   │
                                                                                          └──────> RDS Postgres 16
```

- `/api/*` goes to the API, everything else to the web app. The web app reaches the API inside the VPC as `http://api:8000` (ECS Service Connect), the same name docker-compose uses.
- The worker runs the API image with a different command.

## Folders

| Folder | What it is |
|---|---|
| `bootstrap/` | Run once per AWS account: the Terraform state bucket, the GitHub Actions OIDC provider and the Route 53 zone |
| `envs/staging/`, `envs/production/` | One root module per environment. `main.tf` is **identical** in both (CI checks it); only the defaults in `variables.tf` and your `terraform.tfvars` differ |
| `modules/` | The building blocks, described below |
| `scripts/` | `set-secrets.sh` (write secret values), `sync-github-env.sh` (copy outputs into GitHub Environments) |
| `local/` | Config for the local docker-compose stack (queue and S3 emulators) |

## What each module does

| Module | In plain words |
|---|---|
| `network` | A private network (VPC) across 2 AZs in staging and 3 in production. Public subnets hold only the load balancer and NAT gateways. App tasks sit in private subnets. The database sits in "data" subnets with no internet route at all. AWS services (S3, ECR, CloudWatch Logs, SQS, Secrets Manager, KMS) are reached through private VPC endpoints. Staging shares one NAT gateway; production has one per AZ |
| `kms` | Customer-managed encryption keys with yearly rotation: one for data (S3, SQS, database, secrets, images), one for logs and alarms, one in us-east-1 for WAF logs |
| `storage` | The results bucket: encrypted with KMS, never public, versioned, TLS-only. Run files under `runs/` expire after 90 days, raw uploads under `raw/` after 2 years. Deleted objects linger as old versions for a short safety window (7 days by default) and are then gone for good. A second bucket keeps access logs |
| `queue` | The upload queue and its dead-letter queue, both encrypted. A job that fails 5 times moves to the dead-letter queue and raises an alarm |
| `database` | PostgreSQL 16 on RDS, encrypted, private, TLS forced (`rds.force_ssl`), automated backups, Performance Insights. Multi-AZ and deletion protection in production. RDS creates and rotates the master password itself in Secrets Manager, so it never touches Terraform state |
| `ecr` | Image repositories with immutable tags, scan on push and clean-up rules |
| `ecs` | The Fargate cluster and the three services, all ARM64 (Graviton). Containers run as non-root with a read-only file system (only `/tmp` and the Next.js cache are writable) and drop all Linux capabilities. Each service has its own IAM role with the least it needs. Failed deployments roll back by themselves (circuit breaker). Autoscaling: API and web on CPU and requests per task, the worker on queue backlog per task. A daily scheduled task runs the retention prune. A small function restarts the API and worker after RDS rotates the database password |
| `alb` | The load balancer. Its firewall only lets in CloudFront's own servers (AWS managed prefix list), and every rule also needs a secret header that only our CloudFront distribution sends. Anything else gets a 403 |
| `edge` | CloudFront. Badges are cached 5 minutes, `/api/*` is never cached and gets all headers, cookies and query strings, Next.js static files are cached for a year, pages get a short cache that respects the app's own `Cache-Control`. Security headers: HSTS, frame deny, nosniff, referrer policy and a fallback CSP (the web app sends the real one) |
| `waf` | AWS managed rules (core, known bad inputs, IP reputation) plus two per-IP rate limits: a strict one on `/api/upload` and a general one. Only blocked requests are logged, with auth headers and cookies removed |
| `dns` | Certificates and DNS records. Everything comes from variables (`zone_name`, `site_domain`), so the domain can change: the product name still has a pending trademark check |
| `secrets` | Empty Secrets Manager entries for the GitHub App values, `SESSION_SECRET` and `TOKEN_ENCRYPTION_KEY`. You fill them in yourself (see below) |
| `observability` | Log groups (30 days in staging, 90 in production, KMS-encrypted), one alarm topic, alarms for 5xx rate, p95 latency, no healthy targets, dead-letter queue not empty, queue age, database CPU, storage and memory, and an optional monthly budget |
| `github_oidc` | The deploy role GitHub Actions uses, without any stored AWS keys. Each environment's role trusts exactly one subject: `repo:polygraph-dev/polygraph:environment:<staging or production>` |

## Before you start

- Terraform 1.9 or newer (CI uses 1.12), AWS CLI v2, `gh`, `python3`, `openssl`.
- An AWS account, signed in with an admin role for the first apply.
- The domain registered (do the trademark check first).

## Bootstrap (once per account)

```bash
cd infra/bootstrap
cp terraform.tfvars.example terraform.tfvars    # fill in your account id
terraform init
terraform apply
```

This keeps its own state on your machine (`terraform.tfstate`, gitignored). Store a copy somewhere safe. If you prefer, move it into the new bucket afterwards with a `backend "s3"` block and `terraform init -migrate-state`.

Write down the outputs: `state_bucket` goes into each environment's `backend.hcl`, and `name_servers` go to your domain registrar.

## Deploy order

1. **Bootstrap** (above). Set the name servers at the registrar.
2. **Create the empty secrets first** and fill them, so tasks have their settings from the start:
   ```bash
   cd infra/envs/staging
   cp backend.hcl.example backend.hcl            # put the state bucket name in it
   cp terraform.tfvars.example terraform.tfvars  # fill in placeholders
   terraform init -backend-config=backend.hcl
   terraform apply -target=module.secrets        # only for this first run
   ../../scripts/set-secrets.sh staging
   ```
3. **Full apply**: `terraform apply`. The services are created with a placeholder image tag (`initial`) that doesn't exist yet, so their first tasks fail to start and the "no healthy targets" alarms fire. That is expected until step 5.
4. **Copy the outputs into the GitHub Environment** `staging`: `../../scripts/sync-github-env.sh staging`.
5. **Run the Deploy workflow** (Actions tab, or push to `main`). It builds the ARM64 images, pushes them, runs migrations and rolls out the real images. From now on every push to `main` deploys.
6. Repeat 2 to 5 for `production`. Then give the production deploy role read access to the staging images (`ecr_pull_principal_arns` in staging, `promotion_source_ecr_repository_arns` in production) so production runs exactly the images staging tested, and set required reviewers on the `production` GitHub Environment.

After the first full apply, Terraform owns the *shape* of each task (sizes, environment, secrets) and CI owns the *image*. CI copies the latest task definition and only swaps the image, so the two never fight.

## How secrets get set

Terraform creates empty secrets only. You write the values with:

```bash
infra/scripts/set-secrets.sh staging          # or production
```

It asks for the GitHub App id, client id and client secret (hidden input), reads the private key from a `.pem` file, and generates `SESSION_SECRET` and `TOKEN_ENCRYPTION_KEY` if you press Enter. Values go straight to Secrets Manager; they are never passed on the command line, written to Terraform state or printed. Restart the API and worker after changing one (the script prints the command).

The database password is created and rotated by RDS. ECS hands it to the containers at start, and a tiny entry point builds `DATABASE_URL` from it, so it is never stored anywhere else. After each rotation the API and worker are restarted automatically, one task at a time.

The only secret Terraform does hold is the CloudFront-to-ALB origin header (it has to appear in both configs). The state bucket is encrypted with KMS and private. Two header values exist so you can rotate one without a gap: change `origin_secret_keepers` for the inactive one, apply, switch `origin_secret_active`, apply.

## Keeping costs in check

This is a free service, so the design keeps running costs predictable without any numbers baked in:

- The cloud never runs agents or calls AI models; it only stores and shows numbers.
- CloudFront caches badges and static files; the WAF rate limits stop floods before they reach the app.
- Staging uses one NAT gateway, two AZs, a single-AZ database and small tasks. Production adds redundancy where it matters.
- Graviton (ARM64) everywhere, storage autoscaling with a ceiling, and lifecycle rules that delete old run files, logs and images.
- Set `monthly_budget_usd` to get an email at 80% and 100% of your limit. AWS Budgets only works in US dollars, so pick the dollar figure that matches the rupee limit you have in mind. The budget filters on the `Environment` tag: activate it as a cost allocation tag in the Billing console.

## Data residency and the DPDP Act 2023

- All user data (database, results bucket, queue, secrets, logs) is in ap-south-1. The results bucket has no cross-region replication on purpose.
- The two us-east-1 pieces are the CloudFront certificate and the WAF. WAF logs (blocked requests only, with auth headers and cookies removed) are kept in us-east-1 for a short time because AWS requires CloudFront WAF logging there. They still contain client IP addresses; switch `logging_enabled` off in the WAF module if you want none.
- CloudFront serves from edge locations worldwide, as every CDN does; it caches only public responses (badges, static files, public pages).
- Encryption: TLS 1.2+ in transit everywhere (viewer, origin, database, S3, SQS), KMS at rest everywhere.
- Logs and access logs hold IP addresses, so they are kept for short periods only.
- Deletion is real: the app deletes database rows and S3 objects; old object versions disappear after the safety window; backups expire with their retention period.

Have the privacy notice, retention periods and the breach process reviewed by someone qualified before launch.

## Hardening you can add later

- DNSSEC on the Route 53 zone (needs a DS record at the registrar).
- CloudFront VPC origins, which let the ALB move into private subnets entirely.
- AWS Config, GuardDuty and Security Hub at the account level.
- A CloudTrail trail with log file validation, if the account doesn't have one.

## Checks

```bash
terraform fmt -recursive infra
for d in infra/bootstrap infra/envs/staging infra/envs/production; do
  terraform -chdir=$d init -backend=false && terraform -chdir=$d validate
done
checkov -d infra --config-file infra/.checkov.yaml
trivy config --ignorefile infra/.trivyignore infra
```

CI runs all of these on every pull request. Every skipped Checkov or Trivy rule has a written reason, either in `infra/.checkov.yaml`, `infra/.trivyignore` or next to the resource.
