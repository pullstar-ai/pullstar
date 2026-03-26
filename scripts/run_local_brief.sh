#!/usr/bin/env bash
# run_local_brief.sh — Run the full local inference pipeline for one engineer
#
# Usage:
#   ./scripts/run_local_brief.sh <github-login> [days]
#
# Arguments:
#   github-login   Engineer's GitHub login (required)
#   days           Lookback window in days (optional, default: 30)
#
# Requires: .venv, .env (GITHUB_TOKEN), model_provider.json, matching API key
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

LOGIN="${1:-}"
DAYS="${2:-30}"

if [ -z "$LOGIN" ]; then
  echo "Usage: $0 <github-login> [days]" >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Error: .venv not found — run ./scripts/install.sh first" >&2
  exit 1
fi

PYTHON=".venv/bin/python"

echo "==> PullStar local brief: $LOGIN (${DAYS}d lookback)"
echo ""

echo "--> Step 1/3: ingest"
"$PYTHON" scripts/ingest.py --login "$LOGIN" --days "$DAYS"

echo ""
echo "--> Step 2/3: score"
"$PYTHON" scripts/score.py --login "$LOGIN"

echo ""
echo "--> Step 3/3: generate brief (local mode)"
"$PYTHON" scripts/generate_brief.py --login "$LOGIN" --mode local

echo ""
echo "==> Done. Output: .pullstar/output_${LOGIN}.json"
echo "    Open the dashboard: ./scripts/run_ui.sh"
echo "    Then visit: http://localhost:5173?login=${LOGIN}"
