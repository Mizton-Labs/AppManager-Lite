#!/usr/bin/env bash
#
# test.sh — run the backend (pytest) and frontend (vitest) test suites.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "== backend: pytest =="
if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv not found; install uv (https://docs.astral.sh/uv/)" >&2
  exit 1
fi
( cd "$PROJECT_DIR/backend" && uv run pytest )

echo "== frontend: vitest =="
if [[ -d "$PROJECT_DIR/frontend/node_modules" ]]; then
  ( cd "$PROJECT_DIR/frontend" && npm test )
else
  echo "error: frontend dependencies not installed; run 'npm install' in frontend/" >&2
  exit 1
fi

echo "OK: all tests passed"
