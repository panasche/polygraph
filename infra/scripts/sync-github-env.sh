#!/usr/bin/env bash
# Copy Terraform outputs into the GitHub Environment of the same name, as
# Environment *variables* (none of them are secrets).
#
# Usage (from the repo root, after terraform apply):
#   infra/scripts/sync-github-env.sh <staging|production> [owner/repo]
#
# Needs: terraform, gh (authenticated with admin on the repo), python3.

set -euo pipefail

ENVIRONMENT="${1:-}"
REPO="${2:-polygraph-dev/polygraph}"

if [[ "$ENVIRONMENT" != "staging" && "$ENVIRONMENT" != "production" ]]; then
  echo "usage: $0 <staging|production> [owner/repo]" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="${HERE}/../envs/${ENVIRONMENT}"

json="$(terraform -chdir="$ENV_DIR" output -json github_environment_variables)"

python3 - "$json" <<'PY' | while IFS=$'\t' read -r name value; do
import json, sys
for k, v in sorted(json.loads(sys.argv[1]).items()):
    print(f"{k}\t{v}")
PY
  gh variable set "$name" --env "$ENVIRONMENT" --repo "$REPO" --body "$value"
  echo "set ${ENVIRONMENT}/${name}"
done
