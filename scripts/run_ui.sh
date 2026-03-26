#!/usr/bin/env bash
# run_ui.sh — Start the PullStar local UI (Vite dev server)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d "ui" ]; then
  echo "Error: ui/ directory not found" >&2
  exit 1
fi

if [ ! -d "ui/node_modules" ]; then
  echo "Error: ui/node_modules not found — run ./scripts/install.sh first" >&2
  exit 1
fi

echo "==> Starting UI at http://localhost:5173"
echo "    Append ?login=<github-login> to view a brief"
echo ""
cd ui && npm run dev
