#!/usr/bin/env bash
#
# check.sh — static checks: backend byte-compile and frontend type-check.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "== backend: byte-compile =="
if command -v uv >/dev/null 2>&1; then
  ( cd "$PROJECT_DIR/backend" && uv run python -m compileall -q app )
else
  python3 -m compileall -q "$PROJECT_DIR/backend/app"
fi

echo "== frontend: type-check =="
if [[ -d "$PROJECT_DIR/frontend/node_modules" ]]; then
  ( cd "$PROJECT_DIR/frontend" && npm run typecheck )
else
  echo "frontend dependencies not installed; skipping (run 'npm install' in frontend/)"
fi

echo "OK: static checks passed"
