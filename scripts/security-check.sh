#!/usr/bin/env bash
#
# security-check.sh — project security and compliance gate.
#
#   1. Genericization: fail if upstream brand/source terms appear in the source.
#   2. Hygiene: ensure runtime, secret, and dependency paths are git-ignored.
#   3. Advisory: dependency vulnerability audits (non-fatal).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
status=0

echo "== genericization scan =="
if command -v rg >/dev/null 2>&1; then
  # Specific upstream identifiers only (the generic word "Threat" used in team
  # names is intentionally allowed). Attribution files are excluded by design.
  matches="$(rg -i -n --no-ignore --hidden \
    -g '!**/.git/**' \
    -g '!**/.venv/**' \
    -g '!**/node_modules/**' \
    -g '!**/dist/**' \
    -g '!**/.coding_agent/**' \
    -g '!**/data/**' \
    -g '!**/logs/**' \
    -g '!**/.pids/**' \
    -g '!NOTICE' \
    -g '!THIRD-PARTY-NOTICES.md' \
    -g '!scripts/security-check.sh' \
    -e 'threat[-_ ]?feeds?' \
    -e 'feeds[-_]lite' \
    -e 'feedslite' \
    -e 'jusafing' \
    "$PROJECT_DIR" || true)"
  if [[ -n "$matches" ]]; then
    echo "FAIL: upstream brand/source terms found in source:" >&2
    echo "$matches" >&2
    status=1
  else
    echo "OK: no upstream brand/source terms in source"
  fi
else
  echo "warning: ripgrep (rg) not available; skipping genericization scan" >&2
fi

echo "== git-ignore hygiene =="
must_ignore=(
  "backend/.venv/x"
  "frontend/node_modules/x"
  "frontend/dist/x"
  "data/app.db"
  "data/first-run-admin-credentials.txt"
  "logs/server.log"
  ".pids/server.pid"
)
for rel in "${must_ignore[@]}"; do
  if git -C "$PROJECT_DIR" check-ignore -q "$PROJECT_DIR/$rel"; then
    echo "OK: ignored -> $rel"
  else
    echo "FAIL: not git-ignored -> $rel" >&2
    status=1
  fi
done

echo "== dependency audit (advisory) =="
if command -v uv >/dev/null 2>&1; then
  ( cd "$PROJECT_DIR/backend" && uv run --with pip-audit pip-audit ) || \
    echo "advisory: pip-audit reported findings"
else
  echo "advisory: uv not installed; skipping Python audit"
fi
if [[ -d "$PROJECT_DIR/frontend/node_modules" ]] && command -v npm >/dev/null 2>&1; then
  ( cd "$PROJECT_DIR/frontend" && npm audit --omit=dev --audit-level=high ) || \
    echo "advisory: npm audit reported findings"
else
  echo "advisory: frontend dependencies not installed; skipping npm audit"
fi

if [[ "$status" -ne 0 ]]; then
  echo "security-check: FAILED" >&2
  exit 1
fi
echo "security-check: passed"
