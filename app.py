"""
app.py — Gradio UI wrapper for PullStar 1-on-1 (Hugging Face Space demo)

Mimics the local CLI pipeline (run_local_brief.sh):
  1. scripts/ingest.py              — fetch GitHub activity
  2. scripts/score.py               — compute dimension scores
  3. scripts/generate_brief.py      — build prompt, call AI, write output

Provider and model are configured via model_provider.json (gitignored).
API keys are read from environment (Space secrets or .env locally).

Secrets (Hugging Face Space):
  GITHUB_TOKEN  — GitHub PAT for ingest (optional — falls back to unauthenticated)
  <provider key> — API key matching the provider in model_provider.json
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Generator

import gradio as gr
from dotenv import load_dotenv

# Load .env when running locally; no-op if file is absent (e.g. on HF Space)
load_dotenv(Path(__file__).parent / ".env", override=False)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PULLSTAR_DIR = Path(".pullstar")
DEFAULT_DAYS = 7  # lookback window passed to ingest.py via --days


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], env: dict) -> tuple[int, str]:
    """Run a subprocess. Returns (returncode, combined stdout+stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    combined = result.stdout
    if result.stderr.strip():
        combined += "\n" + result.stderr
    return result.returncode, combined.strip()


def run_pipeline(
    login: str,
    pr_insights: bool,
    days: int,
) -> Generator[tuple[str, float, str], None, None]:
    """
    Orchestrate the PullStar local pipeline for one engineer.

    Yields (status_text, progress_value, brief_markdown) after each step so
    the Gradio UI can display live updates. brief_markdown is empty until done.
    """
    login = login.strip()
    if not login:
        raise ValueError("GitHub login is required.")

    # Forward all env vars (Space secrets, API keys) into subprocesses.
    # Drop an empty GITHUB_TOKEN so ingest.py falls back to unauthenticated.
    env = {**os.environ}
    if not env.get("GITHUB_TOKEN", "").strip():
        env.pop("GITHUB_TOKEN", None)

    STEPS = [
        ("Fetching GitHub activity...", 0.20),
        ("Scoring dimensions...",       0.50),
        ("Generating brief...",         0.85),
        ("Done",                        1.00),
    ]

    # ----- Step 1: Ingest -----
    yield STEPS[0][0], STEPS[0][1], ""
    ingest_cmd = ["python", "scripts/ingest.py", "--login", login, "--days", str(days)]
    if pr_insights:
        ingest_cmd.append("--pr_insights")
    rc, out = _run(ingest_cmd, env)
    if rc != 0:
        raise RuntimeError(f"ingest.py failed (exit {rc}):\n{out}")

    # ----- Step 2: Score -----
    yield STEPS[1][0], STEPS[1][1], ""
    rc, out = _run(["python", "scripts/score.py", "--login", login], env)
    if rc != 0:
        raise RuntimeError(f"score.py failed (exit {rc}):\n{out}")

    # ----- Step 3: Generate brief -----
    yield STEPS[2][0], STEPS[2][1], ""
    rc, out = _run(
        ["python", "scripts/generate_brief.py", "--login", login, "--mode", "local"],
        env,
    )
    if rc != 0:
        raise RuntimeError(f"generate_brief.py failed (exit {rc}):\n{out}")

    # Read brief from the output artifact written by generate_brief.py
    output_path = PULLSTAR_DIR / f"output_{login}.json"
    if not output_path.exists():
        raise RuntimeError(f"Output artifact not found: {output_path}")
    brief = json.loads(output_path.read_text(encoding="utf-8")).get("brief", "")
    if not brief.strip():
        raise RuntimeError("Brief is empty in output artifact.")

    # ----- Done -----
    yield STEPS[3][0], STEPS[3][1], brief


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def on_submit(
    login: str, pr_insights: bool, days: int
) -> Generator[tuple[str, float, str], None, None]:
    """Gradio submit handler — streams status updates then delivers the brief."""
    try:
        yield from run_pipeline(login, pr_insights, days)
    except Exception as exc:
        yield f"Error: {exc}", 0.0, ""


with gr.Blocks(title="PullStar 1-on-1") as demo:
    gr.Markdown(
        """
# PullStar 1-on-1

Generate a manager-ready 1-on-1 brief from a GitHub engineer's recent activity using the open-source PullStar pipeline.

**Open-source repo:** `github.com/pullstar-ai/pullstar`

**What this demo does**
- Pulls recent GitHub activity for a public engineer login
- Optionally includes PR insight context
- Generates a concise manager-ready 1-on-1 brief

        """
    )

    with gr.Group():
        with gr.Row():
            login_input = gr.Textbox(
                label="GitHub engineer login",
                placeholder="e.g. steipete",
                value="steipete",
                scale=3,
            )
            pr_insights_checkbox = gr.Checkbox(
                label="Include PR insights",
                value=False,
                scale=1,
            )

        days_slider = gr.Slider(
            minimum=3,
            maximum=30,
            value=DEFAULT_DAYS,
            step=1,
            label="Lookback window (days)",
        )

        with gr.Row():
            example_1 = gr.Button("Try steipete", size="sm")
            example_2 = gr.Button("Try torvalds", size="sm")
            example_3 = gr.Button("Try nyldn", size="sm")
            example_4 = gr.Button("Clear", size="sm")

        submit_btn = gr.Button("Generate Brief", variant="primary")

    with gr.Row():
        status_box = gr.Textbox(
            label="Status",
            interactive=False,
            scale=3,
        )
        progress_bar = gr.Slider(
            minimum=0,
            maximum=1,
            value=0,
            step=0.01,
            label="Progress",
            interactive=False,
            scale=2,
        )

    gr.Markdown("### 1-on-1 Brief")
    brief_output = gr.Markdown()

    with gr.Accordion("Why errors may happen", open=False):
        gr.Markdown(
            """
This demo depends on two external services:

1. **GitHub API** for fetching recent activity and PR context
2. **AI provider** for generating the final brief (configured via model_provider.json)

Either service may occasionally rate limit, timeout, or return temporary errors.
If that happens, try again in a minute or disable **PR insights** to reduce request volume.
            """
        )

    gr.Markdown(
        """
---
**Tips**
- PR insights usually improve the brief, but may increase rate-limit risk
- If inference fails, check the model provider configuration and retry

PullStar OSS repo: `github.com/pullstar-ai/pullstar`
        """
    )

    example_1.click(fn=lambda: "steipete", outputs=login_input)
    example_2.click(fn=lambda: "torvalds", outputs=login_input)
    example_3.click(fn=lambda: "nyldn", outputs=login_input)
    example_4.click(fn=lambda: "", outputs=login_input)

    submit_btn.click(
        fn=on_submit,
        inputs=[login_input, pr_insights_checkbox, days_slider],
        outputs=[status_box, progress_bar, brief_output],
    )

if __name__ == "__main__":
    demo.launch(inbrowser=True)
