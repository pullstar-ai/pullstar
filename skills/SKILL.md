---
name: pullstar-1on1
description: Generate a ready-to-use 1-on-1 brief for any engineer on your team — from their GitHub activity, in seconds. Spots patterns like high output but low review participation, large PR sizes suggesting batching, and cross-repo collaboration signals.
source: https://github.com/pullstar-ai/pullstar
homepage: https://github.com/pullstar-ai/pullstar
---

## Overview

PullStar fetches GitHub activity for one engineer (PRs authored, reviews given), runs a **deterministic local scoring engine** across five dimensions, and prepares an LLM input payload. An **external agent** (your configured AI provider) generates the final structured brief.

**Data Flow Summary:**
| Step | Location | Data Sent |
|------|----------|-----------|
| Ingest | Local | GitHub API only |
| Score | Local | No external calls |
| Prepare | Local | No external calls |
| **Agent inference** | **External** | **LLM input payload sent to your AI provider** |
| Finalize | Local | No external calls |

**⚠️ Important:** The final brief generation step sends data to your configured AI provider. All other steps run locally on your machine.

---

## Requirements

- Python 3.11+
- Install dependencies: `pip install PyGithub python-dotenv`
- A GitHub personal access token (see Security section below)

---

## Security & Privacy

### Token Scope (Important)

This skill requires a GitHub token to read repository activity. You have two options:

**Option A: Fine-grained PAT (Recommended)**
- Create at: https://github.com/settings/personal-access-tokens
- Repository permissions: Read access to code, issues, and pull requests
- Limit to specific repositories or organizations
- **Note:** Fine-grained PATs cannot search across arbitrary users — use only for your own repos

**Option B: Classic PAT (Broader access)**
- Create at: https://github.com/settings/tokens
- Scope: `repo` (full read access to private repos)
- **⚠️ Warning:** This grants broad access. Set `GITHUB_ORG` to limit scope to one organization.

### Token Security Best Practices

| Practice | Why |
|----------|-----|
| Use a dedicated token | Don't reuse personal high-privilege tokens |
| Set `GITHUB_ORG` | Narrows search to one org instead of all accessible repos |
| Store in `.env` or `~/.pullstar/credentials` | Never commit tokens to git |
| Revoke when done | Limit exposure window |
| Use fine-grained PAT when possible | Least-privilege access |

### Data Privacy by Mode

**Default Mode (no `--pr_insights`):**
- ✅ Only aggregated statistics sent to AI provider
- ✅ No raw PR descriptions, comments, or review text included
- ✅ Repository names and PR titles may be included

**PR Insights Mode (`--pr_insights`):**
- ⚠️ Raw PR discussion text (reviews, comments) packaged into LLM prompt
- ⚠️ This text may contain sensitive information or untrusted input from bots/humans
- ✅ Bounded to 5 PRs, 3 reviews/comments each, with character limits

**Recommendation:** Review `.pullstar/llm_input_{login}.json` before running agent inference if you have privacy concerns.

---

## Configuration

### Secrets — `.env`

`.env` contains **secrets only**. Never commit it.

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | GitHub PAT (fine-grained or classic) |
| `GITHUB_ORG` | No | Scope ingestion to one org. Omit to search all accessible repos. |

### Secret Resolution Order

Secrets are resolved using layered lookup:
1. CLI override (highest priority; debug/testing only)
2. Environment variable (includes `.env`)
3. `~/.pullstar/credentials` (central credentials file)
4. `.env` (project-local, final fallback)

---

## Usage Flow

```bash
# 1. Ingest GitHub activity
python scripts/ingest.py --login jsmith

# 2. Score the profile (local, deterministic)
python scripts/score.py --login jsmith

# 3. Prepare the LLM input artifact (local, no AI call)
python scripts/agent_prepare_1on1.py --login jsmith

# 4. External agent reads .pullstar/llm_input_jsmith.json
#    and writes .pullstar/llm_output_jsmith.json with schema:
#    { "version": "1.0", "engineer_login": "jsmith", "brief": "## Quick Summary\n..." }

# 5. Finalize — merge agent output into final artifact
python scripts/agent_finalize_1on1.py --login jsmith
```

### Artifacts

| File | Written by | Contains | Sent to AI? |
|------|------------|----------|-------------|
| `ingest_{login}.json` | `ingest.py` | Raw GitHub activity, PR details | ❌ No |
| `score_{login}.json` | `score.py` | Dimension scores, signals, flags | ❌ No |
| `llm_input_{login}.json` | `agent_prepare_1on1.py` | LLM prompt payload | ✅ Yes |
| `llm_output_{login}.json` | External agent | Generated brief | ❌ No |
| `output_{login}.json` | `agent_finalize_1on1.py` | Final brief + profile | ❌ No |

All artifacts written to `.pullstar/` (gitignored).

---

## PR Insights Mode (Optional)

```bash
python scripts/ingest.py --login jsmith --pr_insights
```

**What it does:**
- Collects review and comment detail per PR
- Packages bounded raw context into LLM prompt
- Enables richer collaboration pattern analysis

**Bounds (safety limits):**
- Max 5 PRs included in context block
- Max 3 reviews per PR (non-empty body only)
- Max 3 comments per PR (non-empty body only)
- Review text truncated to 600 chars
- Comment text truncated to 500 chars

**⚠️ Security Warning:**
- PR comments/reviews may contain untrusted input
- Bot messages are labeled but still included
- Sensitive repository discussion may be sent to your AI provider
- Review `llm_input_{login}.json` before agent inference

**When to use:** Only when you need deeper collaboration insights and have reviewed the privacy implications.

---

## Agent JSON Contract

### Input (from PullStar)

File: `.pullstar/llm_input_{login}.json`

Contains:
- `system`: System prompt with instructions
- `user`: User message with engineer data
- `metadata`: Version, timestamps, scores

### Output (from Agent)

File: `.pullstar/llm_output_{login}.json`

Required schema:
```json
{
  "version": "1.0",
  "engineer_login": "jsmith",
  "brief": "## Quick Summary\n..."
}
```

**Requirements:**
- Valid JSON (no trailing commas)
- `brief` must be non-empty markdown string
- Plain text also accepted (full file content used as brief)

---

## Source & Provenance

- **Repository:** https://github.com/pullstar-ai/pullstar
- **Full Version:** Standalone CLI, UI, and additional features available at the repo above
- **Dependencies:** PyGithub, python-dotenv (install from PyPI)

---

## Troubleshooting

**"GitHub rejected the PR search query (422)"**
- Fine-grained PATs cannot search across arbitrary users
- Use a classic PAT or limit to your own repos

**"GitHub rate limit hit"**
- Default: 5000 req/hr with authenticated token
- 60 req/hr unauthenticated (not recommended)

**Slow ingestion on high-activity users**
- Use `--max-results 20` to cap search results
- Default caps: 20 authored PRs, 20 reviewed PRs

---

## License

MIT — See source repository for full license text.
