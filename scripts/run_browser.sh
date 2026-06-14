#!/usr/bin/env bash
# run_browser.sh — Start the PullStar browser UI (app.py)
#
# Launches a local web app for generating 1-on-1 briefs without using the CLI.
# Browser opens automatically. Press Ctrl+C to stop.
#
# Requires: .venv set up by install.sh, model_provider.json configured.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d ".venv" ]; then
  echo "Error: .venv not found — run ./scripts/install.sh first" >&2
  exit 1
fi

if [ ! -f "model_provider.json" ]; then
  echo "Error: model_provider.json not found — run ./scripts/install.sh first" >&2
  exit 1
fi

echo "==> Starting PullStar browser UI"
echo "    Opening http://localhost:7860 ..."
echo "    Press Ctrl+C to stop"
echo ""

source .venv/bin/activate
python app.py
