#!/usr/bin/env bash
# install.sh — Bootstrap PullStar 1-on-1 local environment (macOS / Linux)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> PullStar 1-on-1 install"

# --- Python virtual environment ---
if [ ! -d ".venv" ]; then
  echo "--> Creating .venv"
  python3 -m venv .venv
else
  echo "--> .venv already exists, skipping"
fi

echo "--> Installing Python dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r scripts/requirements.txt

# --- UI dependencies ---
if [ -d "ui" ]; then
  echo "--> Installing UI dependencies (npm)"
  cd ui && npm install --silent && cd ..
else
  echo "--> ui/ not found, skipping npm install"
fi

# --- Runtime artifact directory ---
mkdir -p .pullstar

# --- Secrets file ---
if [ ! -f ".env" ]; then
  echo "--> Copying .env.example → .env (fill in your tokens)"
  cp .env.example .env
else
  echo "--> .env already exists, skipping"
fi

# --- Provider config ---
if [ ! -f "model_provider.json" ]; then
  echo "--> Copying model_provider.json.example → model_provider.json"
  cp model_provider.json.example model_provider.json
else
  echo "--> model_provider.json already exists, skipping"
fi

echo ""
echo "==> Done. Next steps:"
echo "    1. Edit .env            — add GITHUB_TOKEN and your AI provider key"
echo "    2. Edit model_provider.json — set provider, model, temperature, max_tokens"
echo "    3. Run a brief:         ./scripts/run_local_brief.sh <github-login>"
echo "    4. Open the dashboard:  ./scripts/run_ui.sh"
