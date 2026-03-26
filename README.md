# PullStar 1-on-1

> Generate a ready-to-use 1-on-1 brief for any engineer on your team — from their GitHub activity, in seconds.

PullStar fetches GitHub activity for one engineer (PRs authored, reviews given), runs a deterministic scoring engine across five dimensions, and calls your configured AI provider to generate a structured 1-on-1 preparation brief. All data stays on your machine.

---

## Requirements

- Python 3.11+
- Node.js 18+ (UI only)
- A GitHub **classic** personal access token with `repo` scope
  - Create one at: <https://github.com/settings/tokens>
  - Fine-grained PATs do not support cross-user search — use a classic PAT
- An AI provider key (required for `--mode local`; not needed for `--mode stub`)

---

## Installation

```bash
# 1. Clone
git clone https://github.com/your-org/pullstar-1on1
cd pullstar-1on1

# 2. Bootstrap (creates .venv, installs deps, copies config stubs)
./scripts/install.sh

# 3. Edit .env — add GITHUB_TOKEN and your AI provider key
# 4. Edit model_provider.json — set provider, model, temperature, max_tokens
```

Or manually:

```bash
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt
cd ui && npm install && cd ..
cp .env.example .env
cp model_provider.json.example model_provider.json
```

---

## Configuration

### Secrets — `.env`

`.env` contains **secrets only**. Never commit it.

| Variable | Required | Description |
| --- | --- | --- |
| `GITHUB_TOKEN` | Yes | Classic PAT with `repo` scope |
| `GITHUB_ORG` | No | Scope ingestion to one org. Omit to search all accessible repos. |
| `ANTHROPIC_API_KEY` | If using anthropic | Anthropic API key |
| `OPENAI_API_KEY` | If using openai | OpenAI API key |
| `OPENROUTER_API_KEY` | If using openrouter | OpenRouter API key |
| `TOGETHER_API_KEY` | If using together | Together AI API key |
| `HUGGINGFACE_API_KEY` | If using huggingface | HuggingFace API key |

Provider/model settings do **not** belong in `.env`. Use `model_provider.json` for those.

### Provider/model config — `model_provider.json`

`model_provider.json` is gitignored and machine-local. It controls inference behavior for local mode.

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "temperature": 0.2,
  "max_tokens": 1200
}
```

Supported providers: `anthropic`, `openai`, `openrouter`, `together`, `huggingface`

Copy `model_provider.json.example` to get started:

```bash
cp model_provider.json.example model_provider.json
```

---

## Local Mode

Run inference directly on your machine using your configured AI provider.

```bash
# One-liner (runs ingest → score → generate_brief automatically)
./scripts/run_local_brief.sh jsmith

# Then open the dashboard
./scripts/run_ui.sh
# open http://localhost:5173?login=jsmith
```

Or step by step:

```bash
python scripts/ingest.py --login jsmith
python scripts/score.py --login jsmith
python scripts/generate_brief.py --login jsmith --mode local
```

Requires: `model_provider.json` + matching API key in `.env`

Writes:

- `.pullstar/ingest_jsmith.json`
- `.pullstar/score_jsmith.json`
- `.pullstar/llm_input_jsmith.json` (prompt payload, for debugging)
- `.pullstar/output_jsmith.json` (final — what the UI reads)

---

## Agent Mode

Use this mode when inference is performed by an external agent (e.g. OpenClaw or any custom workflow).

Flow

```bash
# 1. Ingest GitHub activity
python scripts/ingest.py --login jsmith

# 2. Score the profile
python scripts/score.py --login jsmith

# 3. Prepare the LLM input artifact (no AI call)
python scripts/agent_prepare_1on1.py --login jsmith

# 4. External agent reads .pullstar/llm_input_jsmith.json
#    and writes .pullstar/llm_output_jsmith.json with schema:
#    { "version": "1.0", "engineer_login": "jsmith", "brief": "## Quick Summary\n..." }

# 5. Finalize — merge agent output into final artifact
python scripts/agent_finalize_1on1.py --login jsmith

# 6. Open the dashboard
cd ui && npm run dev
# open http://localhost:5173?login=jsmith
```

Requires: no API keys for steps 3 and 5.

Writes:

- `.pullstar/ingest_jsmith.json`
- `.pullstar/score_jsmith.json`
- `.pullstar/llm_input_jsmith.json` (prompt payload the agent reads)
- `.pullstar/llm_output_jsmith.json` (agent must write this)
- `.pullstar/output_jsmith.json` (final — what the UI reads)

🔴 REQUIRED: JSON Contract

The external agent must strictly follow this contract.

Input (from PullStar)

.pullstar/llm_input_{login}.json

This file contains:
#### Input (from PullStar)
.pullstar/llm_input_{login}.json
This file contains:

system prompt
user prompt
metadata
Treat this as the canonical prompt payload
Do not modify its structure
#### Output (from agent)

.pullstar/llm_output_{login}.json

Must be valid JSON with the following shape:

{
  "version": "1.0",
  "engineer_login": "steipete",
  "brief": "## Quick Summary\n..."
}

#### ⚠️ Requirements

- Output must be valid JSON (no trailing commas, no markdown wrapping)
- brief must be a non-empty markdown string
- Do NOT return plain text, markdown files, or chat logs
- Do NOT change field names

## Stub Mode (dev/demo only)

Generate a deterministic brief from scored data without calling any AI. Useful for UI development and testing the pipeline.

```bash
python scripts/generate_brief.py --login jsmith --mode stub
```

No API key or `model_provider.json` required.

---

## Expected Artifacts
Must be valid JSON. The brief field must contain a non-empty markdown string. This file is the source of truth for the final manager brief in agent mode.
| File | Written by | Contains |
| --- | --- | --- |
| `ingest_{login}.json` | `ingest.py` | Raw GitHub activity, PR details, summary stats |
| `score_{login}.json` | `score.py` | Dimension scores (0–20 each), signals, flags |
| `llm_input_{login}.json` | `generate_brief.py` / `agent_prepare_1on1.py` | Canonical LLM prompt payload (system + user messages) |
| `llm_output_{login}.json` | External agent | Agent-produced brief (agent mode only) |
| `output_{login}.json` | `generate_brief.py` / `agent_finalize_1on1.py` | Final brief + scored profile (what the UI reads) |

All artifacts are written to `.pullstar/` — gitignored, never committed.

---

## PR Insights (optional enrichment)

Run `ingest.py` with `--pr_insights` to collect review and comment detail per PR. When present, this raw context is packaged into the LLM prompt so the model can reason about collaboration patterns.

```bash
python scripts/ingest.py --login jsmith --pr_insights
```

Adds ~3 API calls per PR (capped at 20 PRs). Safe to omit for faster ingestion.

---

## 🔒 Privacy
PullStar is designed to be local-first and explicit about data usage.

#### Defualt Mode
- Only metadata and aggregated signals are used
- No raw PR descriptions, comments, or review text are sent to any LLM
  
#### --pr_insights mode (opt-in)

- Bounded raw PR context may be included:
- PR descriptions
- review text
- comment text (including bot messages)
- This data may be sent to the configured LLM provider or external agent

- This mode is intended for richer insight and is explicitly opt-in.
