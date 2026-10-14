# Running Polygraph Cloud on one server

This puts the whole hosted dashboard on a single small server: Postgres, file storage, the upload queue, the API, the worker and the web app, with Caddy in front for HTTPS. It's the same `docker-compose.yml` you use locally, with a few changes layered on top in [deploy/server](../deploy/server).

The steps below use one EC2 instance in your own AWS account, set up to cost as little as possible. You get a real HTTPS address such as `https://polygraph-yourname.duckdns.org`, real GitHub sign-in, and uploads from GitHub Actions. You don't need a domain name. The same files work on an Oracle Cloud server; see [Using Oracle Cloud instead](#using-oracle-cloud-instead).

For the full AWS setup with a load balancer, managed database, KMS keys and a firewall, see [DEPLOY.md](DEPLOY.md). This page is the small version.

## What's in deploy/server

| File | What it does |
|---|---|
| `compose.server.yml` | Layered on `docker-compose.yml`. Switches the backend to `ENVIRONMENT=staging`, closes every port except Caddy's, and adds Caddy. |
| `Caddyfile` | HTTPS with a free Let's Encrypt certificate. Sends `/api/*` to the API and everything else to the web app. No access log, so visitors' IP addresses aren't recorded. |
| `prepare-server.sh` | One-time server setup: Docker, swap on small servers, a cap on log size, automatic security updates, and the scheduled jobs (daily prune and backup, weekly image cleanup). |
| `init-env.sh` | Asks for your address and GitHub App details, and generates every other secret. Writes `deploy/server/.env` and `deploy/server/s3.json`. Both are gitignored. |
| `polygraph.sh` | Runs `docker compose` with the right files. Use it for everything: `up`, `ps`, `logs`, `down`. |
| `backup.sh` | Dumps the database and copies the stored files, and keeps the last 7 days. Runs daily. |

## Before you start

- **Use a personal AWS account and a personal GitHub account**, and set it up from a personal device. Don't use a company AWS account or company credits for this.
- **Secrets stay on the server.** The GitHub App secret and private key go into `deploy/server/.env` on the server and nowhere else. Don't paste them into chat, issues or commits.
- **The legal pages change with the hosting.** The privacy notice, consent page and footer say where data is stored and how. `init-env.sh` sets this to "AWS Mumbai, on a single server, on encrypted disks", so the notice stays true as long as you turn on disk encryption in step 2. The notice still needs a review by someone who knows the DPDP Act before real users sign up.
- **The contact email is public.** The DPDP Act needs a grievance contact that works, so `init-env.sh` asks for one email address. It's shown on the privacy and security pages. A separate address just for this is a good idea.

## Keeping the AWS bill small

This setup only uses things that are cheap or free: one small instance, its disk, and one public IP address. There's no load balancer, NAT gateway, managed database, KMS key or firewall service, which are the parts that make the full setup expensive.

What you pay for while it runs:

- **The instance**, by the hour. `t4g.small` is the smallest size this stack fits in.
- **The disk**, by its size. 20 GB is plenty.
- **The public IP address**, by the hour. AWS charges for every public IPv4 address, including one attached to a running instance.
- **Traffic out to the internet** above the monthly free allowance. A dashboard like this stays far below it.

To keep it that way:

1. **Set a budget alert before anything else.** Billing and Cost Management → Budgets → Create budget → "Monthly cost budget" (or "Zero spend budget" if your account still has free credits). Put in a small monthly amount and your email address. AWS emails you when spending passes it. Budgets themselves are free.
2. **Use the Mumbai region (`ap-south-1`)** for everything. The privacy notice says Mumbai, and mixing regions makes costs harder to follow.
3. **Don't add extras** this guide doesn't ask for: no load balancer, no RDS, no NAT gateway, no extra Elastic IPs, no CloudWatch agent.
4. **When you shut it down, release the Elastic IP.** An Elastic IP that isn't attached to a running instance is still charged. Stopping the instance stops the instance charge, but the disk and the IP address keep costing until you delete them.

If your account was created recently and is on AWS's free plan, these charges come out of the free credits. Check the "Free tier eligible" labels in the launch wizard, and check Billing → Free tier to see what's left.

## 1. Put the code on GitHub

The server downloads the code with `git clone`, so push this repo to GitHub first. Nothing has been committed yet:

```bash
git status                      # check nothing private is listed
git add -A
git commit -m "Polygraph for MCP"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/polygraph.git
git push -u origin main
```

A public repo is simplest, since this is open source. If you keep it private, sign in on the server with `gh auth login` before cloning.

## 2. Create the EC2 instance

Sign in to the AWS console and switch the region (top right) to **Asia Pacific (Mumbai) ap-south-1**.

1. **EC2 → Instances → Launch instances.**
   - **Name:** `polygraph`.
   - **Image:** Ubuntu Server 24.04 LTS, architecture **64-bit (Arm)**.
   - **Instance type:** `t4g.small` (2 vCPUs, 2 GB of memory). Graviton (Arm) instances cost less than the same size on Intel or AMD, and every image in this stack has an Arm build. Smaller types with 1 GB of memory are too small for this stack.
   - **Key pair:** Create new key pair → type ED25519, format `.pem`. Your browser downloads it. Keep it safe; it's the only way in.
   - **Network settings → Edit:**
     - Keep "Auto-assign public IP" on.
     - Create a security group named `polygraph`.
     - Rule 1: SSH, port 22, source **My IP**.
     - Add rule: HTTP, port 80, source Anywhere (`0.0.0.0/0`).
     - Add rule: HTTPS, port 443, source Anywhere (`0.0.0.0/0`).
     - Optional: Custom UDP, port 443, source Anywhere. This lets browsers use HTTP/3; without it they use HTTP/2, which is fine.
   - **Configure storage:** 20 GiB, `gp3`. Open **Advanced**, and set **Encrypted** to Yes with the default `aws/ebs` key. The default key is free, and the privacy notice says the disk is encrypted, so don't skip this.
   - **Advanced details:**
     - **Metadata version:** V2 only (token required).
     - **Credit specification:** Standard. With "Unlimited", a busy instance can run past its CPU credits and you're charged for the extra. With "Standard" it just slows down.
2. **Launch instance.**
3. **Give it a fixed address.** EC2 → Elastic IPs → Allocate Elastic IP address → Allocate. Then select it → Actions → Associate Elastic IP address → choose the `polygraph` instance → Associate. Without this, the address changes every time the instance stops and starts.

Note the Elastic IP address.

Your home IP address can change, which locks you out of SSH. If `ssh` stops connecting, edit the security group's SSH rule and choose **My IP** again.

## 3. Get a free address

1. Sign in at [duckdns.org](https://www.duckdns.org).
2. Add a subdomain, such as `polygraph-yourname`.
3. Set its IP to your Elastic IP and press **update ip**.

Your address is now `polygraph-yourname.duckdns.org`. Keep your DuckDNS token private. The Elastic IP stays the same as long as you keep it, so you only do this once.

## 4. Create the GitHub App

Follow [DEPLOY.md, section 3](DEPLOY.md#3-create-the-github-app), with your address in place of `polygraphmcp.dev`. Create it under your personal account.

- **Homepage URL:** `https://polygraph-yourname.duckdns.org`
- **Callback URL:** `https://polygraph-yourname.duckdns.org/api/auth/github/callback`
- **Permissions:** only Repository → Metadata: read-only. No email.
- **Webhook:** off.

Note the App ID, Client ID and slug, generate a client secret, and download a private key (`.pem`).

Copy the key to the server. Use the Elastic IP and the key pair from step 2:

```bash
scp -i PATH_TO_YOUR_KEY_PAIR.pem PATH_TO_THE_GITHUB_APP_KEY.pem ubuntu@YOUR_ELASTIC_IP:~/github-app.pem
```

Then delete the downloaded GitHub App `.pem` from your laptop.

On Windows, `ssh` and `scp` come with Windows. If `ssh` complains that the key pair's permissions are too open, run this once in PowerShell:

```powershell
icacls PATH_TO_YOUR_KEY_PAIR.pem /inheritance:r /grant:r "$($env:USERNAME):R"
```

## 5. Set up the server

SSH in:

```bash
ssh -i PATH_TO_YOUR_KEY_PAIR.pem ubuntu@YOUR_ELASTIC_IP
```

Then on the server:

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/polygraph.git
cd polygraph
sudo bash deploy/server/prepare-server.sh
exit
```

Log back in, so your user can run Docker. Then create the settings:

```bash
cd polygraph
./deploy/server/init-env.sh
```

It asks for:

- your address;
- where the server runs (choose **1, AWS Mumbai**);
- the contact email;
- your repo address;
- the GitHub App details.

The client secret is hidden as you type. For the private key, give the path `~/github-app.pem`. It offers to delete the `.pem` once it has copied it.

## 6. Start it

```bash
./deploy/server/polygraph.sh up -d --build
```

The first run builds both images on the server. On a `t4g.small` that's slow, and it leans on the swap that `prepare-server.sh` added. Later runs reuse most of the work. When it finishes:

```bash
./deploy/server/polygraph.sh ps
curl -fsS https://polygraph-yourname.duckdns.org/api/health
```

Every service should be running or healthy (the `migrate` and `s3-init` helpers run once and exit), and the health check should answer `{"status":"ok"}`. Caddy gets the certificate on the first request, so give that first request a moment.

Open your address in a browser, choose **Sign in with GitHub**, accept the notice, and install the GitHub App on the repo you want to track.

## 7. Send results to it

**From this repo.** The dogfood workflow `.github/workflows/polygraph.yml` uploads on pushes to `main` once you set three repository variables (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|---|---|
| `POLYGRAPH_UPLOAD` | `true` |
| `POLYGRAPH_API_URL` | `https://polygraph-yourname.duckdns.org/api` |
| `POLYGRAPH_AUDIENCE` | `https://polygraph-yourname.duckdns.org` |

No secret is needed; GitHub's short-lived OIDC token proves which repo is uploading. Push to `main`, or run the workflow by hand, and the report appears on your dashboard.

**From other repos.** Add `api-url` and `audience` with the same values to the Action's `with:` block. The snippets on your site already include them. Other repos can only use the Action once `pytest-polygraph` is published to PyPI and the Action is published to the Marketplace; until then, this repo's own workflow is the working example.

## Everyday use

| Task | Command (from the `polygraph` folder on the server) |
|---|---|
| Deploy new code | `git pull && ./deploy/server/polygraph.sh up -d --build` (database migrations run on their own) |
| See what's running | `./deploy/server/polygraph.sh ps` |
| Follow the logs | `./deploy/server/polygraph.sh logs -f api worker web` |
| Restart one service | `./deploy/server/polygraph.sh restart api` |
| Back up now | `./deploy/server/backup.sh` |
| Job history | `journalctl -t polygraph-prune`, `journalctl -t polygraph-backup`, `journalctl -t polygraph-docker` |
| Check disk space | `df -h /` |
| Stop the site (data is kept) | `./deploy/server/polygraph.sh down` |

The daily jobs run at 03:00 IST (prune) and 03:30 IST (backup). Old images and build cache are cleaned up every Sunday at 04:00 IST.

Backups land in `polygraph/backups/` on the same disk. They protect against mistakes, not against losing the server, so copy them somewhere else now and then. They hold the same personal data as the live site, so keep the copies encrypted. `backup.sh` has the restore steps at the top.

If you'd rather have AWS keep copies of the whole disk, EC2 → Lifecycle Manager can take a daily snapshot and keep the last few. Snapshots are charged by the space they use, so keep the count low.

### Changing a secret

Edit `deploy/server/.env` and run `./deploy/server/polygraph.sh up -d`.

- Changing `SESSION_SECRET` signs everyone out.
- Changing `TOKEN_ENCRYPTION_KEY` makes stored GitHub tokens unreadable, so everyone has to sign in again.
- Don't change `POSTGRES_PASSWORD` this way. Postgres keeps the password it was first started with.

## What this setup doesn't have

Compared with the full AWS design in [infra/](../infra):

- **One server, no spare.** If it goes down, the site is down until it comes back. There's no uptime promise, and the site says so.
- **No web application firewall.** The API's own rate limits still apply, and the security page says this instead of claiming a firewall.
- **No customer-managed encryption keys.** Data is encrypted in transit with TLS and at rest with EBS encryption under AWS's default key, not with KMS keys you control.
- **Backups are on the same disk** unless you copy them off or add snapshots.
- **`ENVIRONMENT=staging`.** Production mode needs AWS KMS keys. Staging has the same strict startup checks; the only visible difference is that the API docs at `/api/docs` stay public.

## Shutting it down

1. Tell your users, and let them export their data from the account page.
2. Run `./deploy/server/polygraph.sh down -v`. The `-v` **permanently deletes** the database and every stored file.
3. Delete the backups in `polygraph/backups/` and any copies you made.
4. Delete the GitHub App.
5. In the AWS console, in this order:
   - EC2 → Instances → terminate `polygraph`. This also deletes its disk.
   - EC2 → Elastic IPs → **release** the address. Otherwise it keeps being charged.
   - Delete any snapshots you made, and the `polygraph` security group.
   - Check Billing → Bills a few days later to make sure nothing is left running.

## Using Oracle Cloud instead

The same files work on an Oracle Cloud Always Free server, if you have a card Oracle accepts. Only step 2 changes:

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) with **India South (Hyderabad)** or **India West (Mumbai)** as your home region. You can't change it later.
2. Compute → Instances → Create instance: Canonical Ubuntu 24.04, shape `VM.Standard.A1.Flex` with 2 OCPUs and 12 GB of memory, a public IPv4 address, and your SSH key. If it says "Out of capacity", try another availability domain or try later.
3. Networking → Virtual cloud networks → your network → Security → the default security list → add ingress rules for TCP 80 and TCP 443 from `0.0.0.0/0`. `prepare-server.sh` opens the same ports in the server's own firewall, which Oracle's images lock down.
4. Optional: upgrade to Pay As You Go. Oracle can take back Always Free servers that sit almost idle; upgraded accounts keep them.

In `init-env.sh`, choose Oracle Mumbai or Hyderabad, so the privacy notice names the right provider. Everything else is the same.
