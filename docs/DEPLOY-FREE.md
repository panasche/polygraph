# Running Polygraph Cloud on one free server

This puts the whole hosted dashboard on a single free Oracle Cloud server: Postgres, file storage, the upload queue, the API, the worker and the web app, with Caddy in front for HTTPS. It's the same `docker-compose.yml` you use locally, with a few changes layered on top in [deploy/free](../deploy/free).

You get a real HTTPS address such as `https://polygraph-yourname.duckdns.org`, real GitHub sign-in, and uploads from GitHub Actions. You don't need a domain name.

For the full AWS setup, see [DEPLOY.md](DEPLOY.md). This page is the small version.

## What's in deploy/free

| File | What it does |
|---|---|
| `compose.free.yml` | Layered on `docker-compose.yml`. Switches the backend to `ENVIRONMENT=staging`, closes every port except Caddy's, and adds Caddy. |
| `Caddyfile` | HTTPS with a free Let's Encrypt certificate. Sends `/api/*` to the API and everything else to the web app. No access log, so visitors' IP addresses aren't recorded. |
| `prepare-server.sh` | One-time server setup: Docker, firewall, automatic security updates, and two daily jobs (retention prune and backup). |
| `init-env.sh` | Asks for your address and GitHub App details, and generates every other secret. Writes `deploy/free/.env` and `deploy/free/s3.json`. Both are gitignored. |
| `polygraph.sh` | Runs `docker compose` with the right files. Use it for everything: `up`, `ps`, `logs`, `down`. |
| `backup.sh` | Dumps the database and copies the stored files, and keeps the last 7 days. Runs daily. |

## Before you start

- **Use your own laptop or a personal device to set this up**, and a personal Oracle and GitHub account. Don't host it on a work machine.
- **Secrets stay on the server.** The GitHub App secret and private key go into `deploy/free/.env` on the server and nowhere else. Don't paste them into chat, issues or commits.
- **The legal pages change with the hosting.** The privacy notice, consent page and footer say where data is stored. `init-env.sh` sets this to Oracle Cloud Mumbai or Hyderabad, so the notice stays true. The notice still needs a review by someone who knows the DPDP Act before real users sign up.
- **The contact email is public.** The DPDP Act needs a grievance contact that works, so `init-env.sh` asks for one email address. It's shown on the privacy and security pages. A separate address just for this is a good idea.

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

## 2. Create the Oracle Cloud server

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com). Oracle asks for a card to check your identity; the Always Free tier isn't charged. Pick **India South (Hyderabad)** or **India West (Mumbai)** as your home region. You can't change it later.
2. **Compute → Instances → Create instance.**
   - **Image:** Canonical Ubuntu 24.04.
   - **Shape:** Ampere, `VM.Standard.A1.Flex`, with 2 OCPUs and 12 GB of memory. The free tier allows up to 4 OCPUs and 24 GB in total.
   - **Networking:** keep "Assign a public IPv4 address" on.
   - **SSH keys:** upload your public key, or let Oracle generate a pair and save the private key.
   - If it says "Out of capacity", try a different availability domain, or try again later.
3. **Open ports 80 and 443 in Oracle's firewall.** Go to Networking → Virtual cloud networks → your network → Security → the default security list → Add ingress rules:
   - Source `0.0.0.0/0`, protocol TCP, destination port `80`.
   - Source `0.0.0.0/0`, protocol TCP, destination port `443`.

   Leave the SSH rule (port 22) as it is, or narrow it to your own IP address.
4. **Optional but recommended:** upgrade the account to Pay As You Go (Billing → Upgrade). Oracle can take back Always Free servers that sit almost idle for a week; upgraded accounts keep them. You still pay nothing while you stay inside the free limits.

Note the server's public IP address.

## 3. Get a free address

1. Sign in at [duckdns.org](https://www.duckdns.org).
2. Add a subdomain, such as `polygraph-yourname`.
3. Set its IP to your server's public IP and press **update ip**.

Your address is now `polygraph-yourname.duckdns.org`. Keep your DuckDNS token private. Oracle keeps the same public IP while the server exists; if it ever changes, update it on DuckDNS.

## 4. Create the GitHub App

Follow [DEPLOY.md, section 3](DEPLOY.md#3-create-the-github-app), with your address in place of `polygraphmcp.dev`. Create it under your personal account.

- **Homepage URL:** `https://polygraph-yourname.duckdns.org`
- **Callback URL:** `https://polygraph-yourname.duckdns.org/api/auth/github/callback`
- **Permissions:** only Repository → Metadata: read-only. No email.
- **Webhook:** off.

Note the App ID, Client ID and slug, generate a client secret, and download a private key (`.pem`).

Copy the key to the server. Use the IP address and SSH key from step 2:

```bash
scp -i PATH_TO_YOUR_SSH_KEY PATH_TO_THE_DOWNLOADED.pem ubuntu@YOUR_SERVER_IP:~/github-app.pem
```

Then delete the downloaded `.pem` from your laptop.

## 5. Set up the server

SSH in:

```bash
ssh -i PATH_TO_YOUR_SSH_KEY ubuntu@YOUR_SERVER_IP
```

Then on the server:

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/polygraph.git
cd polygraph
sudo bash deploy/free/prepare-server.sh
exit
```

Log back in, so your user can run Docker. Then create the settings:

```bash
cd polygraph
./deploy/free/init-env.sh
```

It asks for:

- your address;
- the region;
- the contact email;
- your repo address;
- the GitHub App details.

The client secret is hidden as you type. For the private key, give the path `~/github-app.pem`. It offers to delete the `.pem` once it has copied it.

## 6. Start it

```bash
./deploy/free/polygraph.sh up -d --build
```

The first run builds both images on the server, which takes a while. Later runs reuse most of the work. When it finishes:

```bash
./deploy/free/polygraph.sh ps
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
| Deploy new code | `git pull && ./deploy/free/polygraph.sh up -d --build` (database migrations run on their own) |
| See what's running | `./deploy/free/polygraph.sh ps` |
| Follow the logs | `./deploy/free/polygraph.sh logs -f api worker web` |
| Restart one service | `./deploy/free/polygraph.sh restart api` |
| Back up now | `./deploy/free/backup.sh` |
| Prune and backup history | `journalctl -t polygraph-prune` and `journalctl -t polygraph-backup` |
| Free disk space after many deploys | `docker image prune -f` |
| Stop the site (data is kept) | `./deploy/free/polygraph.sh down` |

The daily jobs run at 03:00 IST (prune) and 03:30 IST (backup).

Backups land in `polygraph/backups/` on the same server. They protect against mistakes, not against losing the server, so copy them somewhere else now and then. They hold the same personal data as the live site, so keep the copies encrypted. `backup.sh` has the restore steps at the top.

### Changing a secret

Edit `deploy/free/.env` and run `./deploy/free/polygraph.sh up -d`.

- Changing `SESSION_SECRET` signs everyone out.
- Changing `TOKEN_ENCRYPTION_KEY` makes stored GitHub tokens unreadable, so everyone has to sign in again.
- Don't change `POSTGRES_PASSWORD` this way. Postgres keeps the password it was first started with.

## What this setup doesn't have

Compared with the AWS design in [infra/](../infra):

- **One server, no spare.** If it goes down, the site is down until it comes back. There's no uptime promise, and the site says so.
- **No web application firewall.** The API's own rate limits still apply, and the security page says this instead of claiming a firewall.
- **No managed encryption keys.** Data is encrypted in transit with TLS and at rest by Oracle's default disk encryption, not with KMS keys you control.
- **Backups are on the same disk** unless you copy them off.
- **`ENVIRONMENT=staging`.** Production mode needs AWS KMS. Staging has the same strict startup checks; the only visible difference is that the API docs at `/api/docs` stay public.

## Shutting it down

1. Tell your users, and let them export their data from the account page.
2. Run `./deploy/free/polygraph.sh down -v`. The `-v` **permanently deletes** the database and every stored file.
3. Delete the backups in `polygraph/backups/` and any copies you made.
4. Delete the GitHub App, then terminate the server in the Oracle console.
