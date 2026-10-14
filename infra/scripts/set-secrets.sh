#!/usr/bin/env bash
# Write application secret values into Secrets Manager, out-of-band from
# Terraform, so they never end up in Terraform state, shell history or CI logs.
#
# Usage:
#   infra/scripts/set-secrets.sh <staging|production> [--only NAME]
#
# - Generates SESSION_SECRET and TOKEN_ENCRYPTION_KEY locally if you press
#   Enter at their prompts.
# - Reads the GitHub App values from your terminal (input is hidden) and the
#   private key from a .pem file path.
# - Needs: aws CLI v2 (logged in to the right account), python3, openssl.
#
# After changing a value, restart the services so tasks pick it up:
#   aws ecs update-service --cluster polygraph-<env> --service polygraph-<env>-api --force-new-deployment
#   (same for -worker)

set -euo pipefail

ENVIRONMENT="${1:-}"
ONLY=""
if [[ "${2:-}" == "--only" ]]; then
  ONLY="${3:-}"
fi

if [[ "$ENVIRONMENT" != "staging" && "$ENVIRONMENT" != "production" ]]; then
  echo "usage: $0 <staging|production> [--only NAME]" >&2
  exit 2
fi

REGION="${AWS_REGION:-ap-south-1}"
PREFIX="polygraph-${ENVIRONMENT}"

for tool in aws python3 openssl; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
echo "Account: ${ACCOUNT}  Region: ${REGION}  Environment: ${ENVIRONMENT}"
read -r -p "Is this the right account? [y/N] " ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || exit 1

put() {
  local key="$1" value="$2"
  if [[ -z "$value" ]]; then
    echo "  skipped ${key} (empty)"
    return
  fi
  # Pass the value on stdin via a temp file readable only by us, never as an argument.
  local tmp
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  printf '%s' "$value" >"$tmp"
  aws secretsmanager put-secret-value \
    --region "$REGION" \
    --secret-id "${PREFIX}/${key}" \
    --secret-string "file://${tmp}" \
    --output text --query VersionId >/dev/null
  rm -f "$tmp"
  echo "  set ${PREFIX}/${key}"
}

want() {
  [[ -z "$ONLY" || "$ONLY" == "$1" ]]
}

ask_hidden() {
  local prompt="$1" value
  read -r -s -p "$prompt" value
  echo >&2
  printf '%s' "$value"
}

if want github_app_id; then
  put github_app_id "$(ask_hidden 'GitHub App id (YOUR_GITHUB_APP_ID): ')"
fi

if want github_app_client_id; then
  put github_app_client_id "$(ask_hidden 'GitHub App client id: ')"
fi

if want github_app_client_secret; then
  put github_app_client_secret "$(ask_hidden 'GitHub App client secret: ')"
fi

if want github_app_private_key; then
  read -r -p "Path to the GitHub App private key (.pem): " pem
  if [[ -n "$pem" ]]; then
    [[ -f "$pem" ]] || { echo "not a file: $pem" >&2; exit 1; }
    put github_app_private_key "$(cat "$pem")"
    echo "  You can now delete the downloaded .pem from this machine."
  fi
fi

if want session_secret; then
  value="$(ask_hidden 'SESSION_SECRET (Enter = generate 48 random bytes): ')"
  if [[ -z "$value" ]]; then
    value="$(openssl rand -base64 48 | tr -d '\n')"
  fi
  put session_secret "$value"
fi

if want token_encryption_key; then
  value="$(ask_hidden 'TOKEN_ENCRYPTION_KEY (Enter = generate a Fernet key): ')"
  if [[ -z "$value" ]]; then
    value="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  fi
  put token_encryption_key "$value"
  echo "  Note: changing TOKEN_ENCRYPTION_KEY makes stored GitHub refresh tokens unreadable;"
  echo "  users simply sign in again."
fi

echo "Done."
