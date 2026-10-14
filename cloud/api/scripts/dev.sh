#!/usr/bin/env bash
# Start the Polygraph Cloud API on http://127.0.0.1:8000 with development settings:
# SQLite at cloud/api/.data/dev.db, local file storage, inline queue, dev-login on,
# CORS for the Next.js app on http://localhost:3000.
#
#   scripts/dev.sh            # start
#   scripts/dev.sh --reload   # restart on code changes
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  echo "No virtualenv found. Create one first:" >&2
  echo "  python -m venv .venv && .venv/bin/pip install -e ../../packages/pytest-polygraph --no-deps && .venv/bin/pip install -e '.[test]'" >&2
  exit 1
fi

mkdir -p .data

export ENVIRONMENT=development
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://localhost:3000}"
export DATABASE_URL="sqlite:///./.data/dev.db"
export STORAGE_BACKEND=local
export LOCAL_STORAGE_DIR=./.data
export QUEUE_BACKEND=inline
export PII_SCANNER="${PII_SCANNER:-regex}"
export LEADERBOARD_PROJECT="${LEADERBOARD_PROJECT:-polygraph-dev/benchmark}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "Polygraph Cloud API (development) on http://127.0.0.1:8000  docs: http://127.0.0.1:8000/api/docs"
exec "$PY" -m uvicorn polygraph_cloud.app:create_app --factory \
  --host 127.0.0.1 --port 8000 --no-access-log "$@"
