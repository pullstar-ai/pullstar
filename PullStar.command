#!/usr/bin/env bash
# PullStar.command — macOS double-click launcher for the PullStar browser UI
#
# Double-click this file in Finder to start PullStar.
# Your browser will open automatically.
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

if [ ! -d ".venv" ]; then
  echo "Error: .venv not found — run ./scripts/install.sh first"
  read -p "Press Enter to close..."
  exit 1
fi

if [ ! -f "model_provider.json" ]; then
  echo "Error: model_provider.json not found — run ./scripts/install.sh first"
  read -p "Press Enter to close..."
  exit 1
fi

echo "Starting PullStar..."
echo "Your browser will open shortly at http://localhost:7860"
echo "Close this window to stop PullStar."
echo ""

source .venv/bin/activate
python app.py
