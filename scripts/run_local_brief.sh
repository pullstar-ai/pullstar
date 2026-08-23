#!/usr/bin/env bash
# run_local_brief.sh — Run the full local inference pipeline for one engineer
#
# Usage (named args):
#   ./scripts/run_local_brief.sh --login <github-login> [options]
#
# Usage (legacy positional):
#   ./scripts/run_local_brief.sh <github-login> [days]
#
# Options:
#   --login <login>            GitHub login (required)
#   --days <N>                 Lookback window in days (default: 30)
#   --max-results <N>          Max PRs to fetch (default: 50)
#   --output-dir <path>        Artifact directory (default: .pullstar)
#   --pr_insights              Include PR discussion context
#   --api-mode graphql|rest    API mode (default: graphql)
#   --github-token <TOKEN>     Override GitHub token (prefer .env or credentials file)
#   --provider-config <path>   Path to model_provider.json (default: model_provider.json)
#
# Requires: .venv, .env (GITHUB_TOKEN), model_provider.json, matching API key
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# --- Defaults ---
LOGIN=""
DAYS=30
MAX_RESULTS=""
OUTPUT_DIR=".pullstar"
PR_INSIGHTS=false
API_MODE=""
GITHUB_TOKEN_ARG=""   # never echoed
PROVIDER_CONFIG=""

# --- Parse args (support --named flags or legacy positional) ---
if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
  # Legacy: run_local_brief.sh <login> [days]
  LOGIN="$1"
  DAYS="${2:-30}"
else
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --login)            LOGIN="$2";            shift 2 ;;
      --days)             DAYS="$2";             shift 2 ;;
      --max-results)      MAX_RESULTS="$2";      shift 2 ;;
      --output-dir)       OUTPUT_DIR="$2";       shift 2 ;;
      --pr_insights)      PR_INSIGHTS=true;      shift 1 ;;
      --api-mode)         API_MODE="$2";         shift 2 ;;
      --github-token)     GITHUB_TOKEN_ARG="$2"; shift 2 ;;
      --provider-config)  PROVIDER_CONFIG="$2";  shift 2 ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done
fi

if [ -z "$LOGIN" ]; then
  echo "Usage: $0 --login <github-login> [--days N] [--max-results N] [--output-dir path]" >&2
  echo "       $0 --login <github-login> [--pr_insights] [--api-mode graphql|rest]" >&2
  echo "       $0 --login <github-login> [--provider-config path]" >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Error: .venv not found — run ./scripts/install.sh first" >&2
  exit 1
fi

PYTHON=".venv/bin/python"

echo "==> PullStar local brief: $LOGIN (${DAYS}d lookback)"
echo ""

# --- Step 1: Ingest ---
echo "--> Step 1/3: ingest"
INGEST_CMD=("$PYTHON" scripts/ingest.py --login "$LOGIN" --days "$DAYS" --output-dir "$OUTPUT_DIR")
[ -n "$MAX_RESULTS" ]      && INGEST_CMD+=(--max-results "$MAX_RESULTS")
[ "$PR_INSIGHTS" = true ]  && INGEST_CMD+=(--pr_insights)
[ -n "$API_MODE" ]         && INGEST_CMD+=(--api-mode "$API_MODE")
[ -n "$GITHUB_TOKEN_ARG" ] && INGEST_CMD+=(--github-token "$GITHUB_TOKEN_ARG")
"${INGEST_CMD[@]}"

# --- Step 2: Score ---
echo ""
echo "--> Step 2/3: score"
"$PYTHON" scripts/score.py --login "$LOGIN" --input-dir "$OUTPUT_DIR" --output-dir "$OUTPUT_DIR"

# --- Step 3: Generate brief ---
echo ""
echo "--> Step 3/3: generate brief (local mode)"
BRIEF_CMD=("$PYTHON" scripts/generate_brief.py --login "$LOGIN" --mode local \
           --input-dir "$OUTPUT_DIR" --output-dir "$OUTPUT_DIR")
[ -n "$PROVIDER_CONFIG" ] && BRIEF_CMD+=(--provider-config "$PROVIDER_CONFIG")
"${BRIEF_CMD[@]}"

echo ""
echo "==> Done. Output: ${OUTPUT_DIR}/output_${LOGIN}.json"
echo "    Open the dashboard: ./scripts/run_ui.sh"
echo "    Then visit: http://localhost:5173?login=${LOGIN}"
