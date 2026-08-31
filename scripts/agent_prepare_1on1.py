"""
agent_prepare_1on1.py — Prepare the LLM input artifact for agent-managed inference.

Usage:
    python scripts/agent_prepare_1on1.py --login jsmith
    python scripts/agent_prepare_1on1.py --login jsmith --input-dir .pullstar --mode pr_insights

Reads:
    .pullstar/score_{login}.json   (required)
    .pullstar/ingest_{login}.json  (optional — for engineer name, org, stats, PR insights)
    pullstar/prompts/brief_v1.txt  (system prompt — packaged with the pullstar distribution)

Writes:
    .pullstar/llm_input_{login}.json

Does NOT call any AI provider.
Does NOT write output_{login}.json.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

# Allow ``python scripts/agent_prepare_1on1.py`` from a bare checkout (no install needed).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv

from pullstar.prompting import build_llm_input_payload, write_llm_input
from pullstar.resources import resolve_brief_prompt

load_dotenv(Path(__file__).parent.parent / ".env")


def fail(msg: str) -> NoReturn:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare a canonical LLM input artifact for agent-managed inference."
    )
    parser.add_argument("--login",     required=True,    help="Engineer GitHub login")
    parser.add_argument("--input-dir", default=".pullstar", help="Directory containing score and ingest files")
    parser.add_argument("--mode",      default="default",   help="Preparation mode label stored in payload metadata (e.g. default, pr_insights)")
    parser.add_argument("--prompt",    default=None, metavar="NAME_OR_PATH",
                        help="Packaged prompt version (e.g. brief_v1, brief_v2) or a path to "
                             "a prompt file. Default: brief_v1.")
    args = parser.parse_args()

    input_dir  = Path(args.input_dir)
    output_dir = input_dir   # llm_input written to same directory as inputs

    # -- Load score (required) ------------------------------------------------
    score_path = input_dir / f"score_{args.login}.json"
    if not score_path.exists():
        fail(f"Score file not found: {score_path}\n  Run: python scripts/score.py --login {args.login}")
    score = json.loads(score_path.read_text(encoding="utf-8"))
    print(f"> Score loaded — {score['engineer_login']} "
          f"{score['total_score']}/100 ({score['confidence']} confidence)")

    # -- Load ingest (optional) -----------------------------------------------
    ingest_path = input_dir / f"ingest_{args.login}.json"
    ingest: dict | None = None
    if ingest_path.exists():
        ingest = json.loads(ingest_path.read_text(encoding="utf-8"))
        assert ingest is not None
        n_insights = sum(
            1 for p in ingest.get("prs_authored", [])
            if "discussion_summary_stats" in p
        )
        print(f"> Ingest loaded — {len(ingest.get('prs_authored', []))} PRs"
              + (f", {n_insights} with PR insights" if n_insights else ""))
    else:
        print("> No ingest file found — score data only")

    # -- Resolve the system prompt (packaged version or a file) -------------
    try:
        brief_prompt = resolve_brief_prompt(args.prompt)
    except FileNotFoundError as exc:
        fail(str(exc))
    print(f"> Prompt: {brief_prompt.name}")

    # -- Build and write LLM input payload ------------------------------------
    payload = build_llm_input_payload(
        score, ingest, brief_prompt.text,
        model=None, provider=None, mode=args.mode,
        prompt_name=brief_prompt.name,
    )
    out_path = write_llm_input(payload, output_dir)
    print(f"> LLM input written to {out_path}")
    print(f"  mode={args.mode}  |  prompt={brief_prompt.name}  |  "
          f"has_insights={payload['metadata']['has_insights']}  |  "
          f"user message: {len(payload['user'])} chars")


if __name__ == "__main__":
    main()
