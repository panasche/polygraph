#!/usr/bin/env bash
# Run docker compose with the free-server files and settings.
#
#   ./deploy/server/polygraph.sh up -d --build     start or update everything
#   ./deploy/server/polygraph.sh ps                what's running
#   ./deploy/server/polygraph.sh logs -f api       follow the API log
#   ./deploy/server/polygraph.sh down              stop (data is kept)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"

if [[ ! -f "$here/.env" || ! -f "$here/s3.json" ]]; then
  echo "Missing deploy/server/.env or s3.json. Run ./deploy/server/init-env.sh first." >&2
  exit 1
fi

exec docker compose \
  --project-directory "$root" \
  --env-file "$here/.env" \
  -f "$root/docker-compose.yml" \
  -f "$here/compose.server.yml" \
  "$@"
