"""
generate_brief.py — Local inference brief generator for PullStar 1-on-1

Usage:
    python scripts/generate_brief.py --login jsmith --mode local
    python scripts/generate_brief.py --login jsmith --mode stub   # dev/demo only, no AI

Reads  .pullstar/score_{login}.json   (required)
Reads  .pullstar/ingest_{login}.json  (optional)
Reads  model_provider.json            (required for --mode local)
Writes .pullstar/llm_input_{login}.json  (always — for debugging and agent workflow parity)
Writes .pullstar/output_{login}.json     (always on success)

Modes:
  local  — loads model_provider.json, validates API key in .env, calls AI, writes output
  stub   — deterministic brief from scored data only, no AI call (dev/demo use only)
  agent  — not supported here; fails with instructions to use the agent scripts

Configuration:
  model_provider.json  — provider/model/temperature/max_tokens (NOT .env)
  .env                 — API keys only (GITHUB_TOKEN, ANTHROPIC_API_KEY, etc.)

For agent mode:
  python scripts/agent_prepare_1on1.py --login jsmith
  # (external agent writes .pullstar/llm_output_{login}.json)
  python scripts/agent_finalize_1on1.py --login jsmith
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

from dotenv import load_dotenv
from prompt_builder import build_llm_input_payload, write_llm_input

load_dotenv(Path(__file__).parent.parent / ".env")

PROMPT_PATH             = Path(__file__).parent.parent / "prompts" / "brief_v1.txt"
DEFAULT_PROVIDER_CONFIG = Path(__file__).parent.parent / "model_provider.json"

# ---------------------------------------------------------------------------
# Provider registry
# Maps provider name → API key env var + base URL.
# Model name comes from model_provider.json — no defaults here.
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, dict] = {
    "anthropic":   {"key_env": "ANTHROPIC_API_KEY",   "base_url": None},
    "openai":      {"key_env": "OPENAI_API_KEY",       "base_url": None},
    "openrouter":  {"key_env": "OPENROUTER_API_KEY",   "base_url": "https://openrouter.ai/api/v1"},
    "together":    {"key_env": "TOGETHER_API_KEY",     "base_url": "https://api.together.xyz/v1"},
    "huggingface": {"key_env": "HUGGINGFACE_API_KEY",  "base_url": "https://api-inference.huggingface.co/v1"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fail(msg: str) -> NoReturn:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def load_prompt() -> str:
    if not PROMPT_PATH.exists():
        fail(f"System prompt not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def load_provider_config(config_path: Path) -> dict:
    """
    Load and validate model_provider.json.

    Required fields: provider, model
    Optional fields: temperature (float), max_tokens (int)

    Fails clearly if the file is missing, malformed, or references an unknown provider.
    """
    if not config_path.exists():
        fail(
            f"Provider config not found: {config_path}\n"
            f"  Create model_provider.json at the repo root (or pass --provider-config).\n"
            f"  Example:\n"
            f"    {{\n"
            f'      "provider": "anthropic",\n'
            f'      "model": "claude-sonnet-4-6",\n'
            f'      "temperature": 0.2,\n'
            f'      "max_tokens": 1200\n'
            f"    }}\n"
            f"  Copy model_provider.json.example to get started."
        )
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"model_provider.json is not valid JSON: {exc}")

    provider = cfg.get("provider", "").strip()
    model    = cfg.get("model", "").strip()
    if not provider:
        fail("model_provider.json is missing required field 'provider'")
    if provider not in PROVIDERS:
        fail(
            f"Unknown provider '{provider}' in model_provider.json.\n"
            f"  Valid providers: {', '.join(PROVIDERS)}"
        )
    if not model:
        fail("model_provider.json is missing required field 'model'")
    return cfg


# ---------------------------------------------------------------------------
# PR insights stats — used only by _insights_stats_for_stub / generate_stub_brief.
# Prompt construction (user message, PR context) lives in prompt_builder.py.
# ---------------------------------------------------------------------------

_STUB_INSIGHTS_PR_CAP = 10   # how many insight PRs the stub brief samples


def _insights_stats_for_stub(ingest: dict | None) -> dict:
    """
    Extract the four key PR insight numbers used to enrich the stub brief.
    Returns an empty dict when no insight detail is present.
    """
    if not ingest:
        return {}
    sample = [
        p for p in ingest.get("prs_authored", [])
        if "discussion_summary_stats" in p
    ][:_STUB_INSIGHTS_PR_CAP]
    if not sample:
        return {}

    n           = len(sample)
    prs_with_cr = sum(1 for p in sample
                      if p["discussion_summary_stats"]["changes_requested_count"] > 0)
    prs_with_disc = sum(
        1 for p in sample
        if any(r.get("body_length", 0) > 50 for r in p.get("reviews_received_detail", []))
        or any(c.get("body_length", 0) > 50 for c in p.get("comments_detail", []))
    )
    revised_count = 0
    for p in sample:
        pending_cr = False
        for r in p.get("reviews_received_detail", []):
            if r["state"] == "changes_requested":
                pending_cr = True
            elif r["state"] == "approved" and pending_cr:
                revised_count += 1
                break

    return {
        "n":             n,
        "prs_with_cr":   prs_with_cr,
        "revised_count": revised_count,
        "prs_with_disc": prs_with_disc,
    }


# ---------------------------------------------------------------------------
# AI call — Anthropic
# ---------------------------------------------------------------------------

def call_anthropic(api_key: str, model: str, system_prompt: str, user_message: str,
                   *, temperature: float = 0.7, max_tokens: int = 2048) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        block = next((b for b in response.content if b.type == "text"), None)
        if block is None:
            fail("Anthropic returned no text content in the response.")
        return block.text  # type: ignore[attr-defined]
    except Exception as exc:
        _raise_ai_error("Anthropic", exc)


# ---------------------------------------------------------------------------
# AI call — OpenAI-compatible (openai, openrouter, together, huggingface)
# ---------------------------------------------------------------------------

def call_openai_compatible(
    api_key: str, model: str, base_url: str | None,
    system_prompt: str, user_message: str,
    provider_name: str,
    *, temperature: float = 0.7, max_tokens: int = 2048,
) -> str:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        # debug log the full response for troubleshooting
        return response.choices[0].message.content or ""
    except Exception as exc:
        _raise_ai_error(provider_name.title(), exc)


def _raise_ai_error(provider: str, exc: Exception) -> NoReturn:
    msg = str(exc)
    name = type(exc).__name__.lower()
    if "auth" in name or "401" in msg:
        fail(f"AI key invalid. Check your {provider} API key.")
    if "rate" in name or "429" in msg or "quota" in msg.lower():
        fail(f"AI quota exceeded. Check your {provider} account billing.")
    if "unavailable" in msg.lower() or "529" in msg or "503" in msg:
        fail(f"Model unavailable ({provider}). Try again shortly.")
    fail(f"{provider} API error: {exc}")


# ---------------------------------------------------------------------------
# Stub fallback — deterministic brief, no AI required
# ---------------------------------------------------------------------------

def _cross_dimension_patterns(dims: dict, stats: dict) -> list[str]:
    """
    Detect non-obvious patterns that only appear when comparing dimensions.
    Returns up to 2 observations, each a complete sentence.
    """
    vel   = dims.get("velocity", {})
    qual  = dims.get("pr_quality", {})
    rev   = dims.get("review_participation", {})
    cons  = dims.get("consistency", {})

    vel_score  = vel.get("score", 0)
    rev_score  = rev.get("score", 0)
    qual_score = qual.get("score", 0)
    cons_score = cons.get("score", 0)

    n_merged  = stats.get("prs_merged", 0)
    n_reviews = stats.get("total_reviews_given", 0)
    n_total   = stats.get("total_prs_authored", 0)

    patterns: list[str] = []

    # High velocity, low review engagement — output without reciprocity
    if vel_score >= 16 and rev_score <= 8 and n_total >= 5:
        patterns.append(
            f"High output ({n_merged} PRs merged) but limited review activity "
            f"— may be heads-down on delivery rather than engaged in team code flow."
        )

    # Reviewing more than shipping
    elif rev_score >= 12 and vel_score <= 8 and n_reviews >= 5:
        patterns.append(
            f"More active as a reviewer ({n_reviews} reviews) than a shipper "
            f"({n_merged} PRs merged) — worth understanding the workload split."
        )

    # Balanced contributor — both dimensions strong
    if vel_score >= 12 and rev_score >= 12 and n_total >= 5:
        ratio = n_reviews / n_merged if n_merged > 0 else 0
        patterns.append(
            f"Active in both directions: {n_merged} PRs authored, {n_reviews} reviews given "
            f"({ratio:.1f} reviews per PR)."
        )

    # Quality high but consistency low — good work when present, irregular presence
    if qual_score >= 14 and cons_score <= 8 and n_total >= 3:
        patterns.append(
            "When active, PRs are well-scoped — the consistency signal is about availability, not quality."
        )

    # High velocity + high quality (rare combination worth calling out)
    if vel_score >= 16 and qual_score >= 14 and n_total >= 5:
        patterns.append(
            "Both speed and quality signals are strong — a combination that tends to be load-sensitive."
        )

    return patterns[:2]


def _explores_from_flags(flags: list, dims: dict) -> list[str]:
    """
    Derive Areas to Explore from flags and low-scoring dimensions.
    Returns open-ended questions — not accusatory, genuinely curious.
    """
    questions: list[str] = []
    used_dims: set[str] = set()

    for flag in flags:
        msg = flag["message"].lower()
        dim = flag["dimension"]
        if dim in used_dims:
            continue
        used_dims.add(dim)

        if "stale" in msg or "open for over" in msg:
            questions.append(
                "A few PRs have been sitting open for a while — what's the status on those, "
                "and is anything blocking them from moving forward?"
            )
        elif "gap" in msg or "inactive" in msg:
            questions.append(
                "There was a quiet stretch in this window — anything notable happening during that time, "
                "on-call, a project shift, or something else?"
            )
        elif "1000 lines" in msg or "exceeded" in msg:
            questions.append(
                "A few PRs were quite large — what drove the scope on those, "
                "and is that typical for this type of work?"
            )
        elif "no written feedback" in msg or "approving" in msg:
            questions.append(
                "Most reviews appear to be approvals without written comments — "
                "is that a style preference, or has capacity been tight?"
            )
        elif "no reviews given" in msg:
            questions.append(
                "This period was mostly shipping rather than reviewing — "
                "how engaged does the team feel with each other's work right now?"
            )
        elif "feature branch" in msg:
            questions.append(
                "Most PRs targeted feature branches rather than main — "
                "how does the team's branching workflow feel from your end?"
            )

    # Fill remaining slots from low-confidence or low-scoring dimensions
    dim_priority = ["review_participation", "collaboration", "consistency", "pr_quality", "velocity"]
    for dim_name in dim_priority:
        if len(questions) >= 3:
            break
        if dim_name in used_dims:
            continue
        dim = dims.get(dim_name, {})
        if dim.get("score", 20) <= 8 and dim.get("confidence") in ("medium", "high"):
            used_dims.add(dim_name)
            label = dim_name.replace("_", " ")
            questions.append(
                f"The {label} signals are on the lower end — what's been shaping that this period?"
            )

    # Always surface at least 2 questions — add generic ones if needed
    generic_fallbacks = [
        "What felt most useful or impactful about the work in this window?",
        "Anything that slowed things down or felt harder than it should have?",
        "What's coming up next, and how are you feeling about it?",
    ]
    for fb in generic_fallbacks:
        if len(questions) >= 3:
            break
        if fb not in questions:
            questions.append(fb)

    return [f"- {q}" for q in questions[:3]]


_DIM_LABELS: dict[str, str] = {
    "velocity":             "Velocity",
    "pr_quality":           "PR Quality",
    "review_participation": "Review Participation",
    "collaboration":        "Collaboration",
    "consistency":          "Consistency",
}


def generate_stub_brief(score: dict, ingest: dict | None) -> str:
    login  = score["engineer_login"]
    name   = (ingest or {}).get("engineer_name") or login
    days   = score["lookback_days"]
    dims   = score["dimensions"]
    total  = score["total_score"]
    flags  = score.get("flags", [])
    stats  = (ingest or {}).get("summary_stats", {})

    n_merged  = stats.get("prs_merged", 0)
    n_reviews = stats.get("total_reviews_given", 0)
    active_w  = stats.get("active_weeks")
    total_w   = stats.get("total_weeks")
    ins       = _insights_stats_for_stub(ingest)   # empty dict when no insights

    # --- Quick Summary ---
    # Lead with the most concrete numbers, add activity context
    parts = [f"{n_merged} PR{'s' if n_merged != 1 else ''} merged"]
    if n_reviews > 0:
        parts.append(f"{n_reviews} code reviews given")
    summary_lead = " and ".join(parts) if parts else "No PR activity"
    activity = (
        f", active in {active_w} of {total_w} weeks"
        if active_w is not None and total_w
        else ""
    )
    summary = f"{name} — {summary_lead} in the last {days} days{activity}."

    # Add a cross-dimension observation to Quick Summary if one is obvious
    cross = _cross_dimension_patterns(dims, stats)
    if cross:
        summary += f" {cross[0]}"

    # Append a one-sentence PR insights note when the data supports it
    if ins:
        if ins["revised_count"] > 0:
            summary += (
                f" {ins['revised_count']} of {ins['n']} reviewed PRs iterated "
                f"through at least one feedback cycle."
            )
        elif ins["prs_with_cr"] > 0:
            summary += " Some PRs received change requests during the review process."

    # --- Highlights ---
    # Pull the top signal from each dimension that has useful data;
    # avoid repeating what went into the summary
    highlight_bullets: list[str] = []
    for dim_name, dim in dims.items():
        signals = dim.get("signals", [])
        if not signals or signals[0] in ("No PRs in window", "No reviews given in this window"):
            continue
        highlight_bullets.append(f"- {signals[0]}")
        if len(highlight_bullets) >= 4:
            break
    if not highlight_bullets:
        highlight_bullets = ["- Insufficient data for specific highlights in this window."]

    # Append one insights-derived bullet when there's room and data is meaningful
    if ins and len(highlight_bullets) < 4:
        if ins["revised_count"] > 0:
            highlight_bullets.append(
                f"- {ins['revised_count']} of {ins['n']} PRs revised after change requests "
                f"— responsive to review feedback"
            )
        elif ins["prs_with_disc"] > 0:
            highlight_bullets.append(
                f"- Substantive reviewer discussion on {ins['prs_with_disc']} of "
                f"{ins['n']} sampled PRs"
            )

    # --- Areas to Explore ---
    explores = _explores_from_flags(flags, dims)

    # Prepend one insights-specific question when it adds something the flags didn't cover
    if ins and len(explores) < 3:
        if ins["prs_with_cr"] >= 2 and ins["revised_count"] == 0:
            explores = [
                "- Review history shows change requests without a visible revision cycle "
                "— what's the usual workflow for addressing that feedback?"
            ] + explores
        elif ins["revised_count"] > 0:
            explores = [
                f"- {ins['revised_count']} PRs went through revision cycles — how has "
                f"the review feedback process been feeling lately, useful friction or just overhead?"
            ] + explores
        explores = explores[:3]

    # --- Patterns Worth Noting ---
    # Use cross-dimension patterns (skip any already in the summary),
    # then fall back to per-dimension signals not already in Highlights
    patterns_used = cross[1:] if len(cross) > 1 else []

    if not patterns_used:
        # Pull second signals from dimensions not already represented
        for dim_name, dim in dims.items():
            if len(patterns_used) >= 3:
                break
            signals = dim.get("signals", [])
            if len(signals) >= 2:
                candidate = f"{_DIM_LABELS.get(dim_name, dim_name)}: {signals[1]}"
                if candidate not in highlight_bullets:
                    patterns_used.append(candidate)

    # Append an insights-derived pattern when there's room
    if ins and len(patterns_used) < 3:
        if ins["revised_count"] > 0:
            patterns_used.append(
                f"Revision responsiveness: {ins['revised_count']} of {ins['n']} authored PRs "
                f"went through a review-revision cycle before approval."
            )
        elif ins["prs_with_disc"] > 0 and ins["prs_with_disc"] >= ins["n"] // 2:
            patterns_used.append(
                f"Substantive reviewer engagement on {ins['prs_with_disc']} of "
                f"{ins['n']} sampled authored PRs."
            )

    if not patterns_used:
        patterns_used = ["Not enough data to identify clear behavioral patterns in this window."]

    pattern_bullets = [f"- {p}" for p in patterns_used[:3]]

    # --- Score Summary ---
    score_rows = "\n".join(
        f"| {_DIM_LABELS.get(k, k)} | {v['score']}/{v['max']} "
        f"| {v['confidence']} | {v['signals'][0] if v['signals'] else '-'} |"
        for k, v in dims.items()
    )
    score_table = (
        "| Dimension | Score | Confidence | Top Signal |\n"
        "| --- | --- | --- | --- |\n"
        + score_rows
        + f"\n\n**Total: {total}/{len(dims) * 20}**"
    )

    # --- Suggested Focus ---
    vel_score  = dims.get("velocity", {}).get("score", 0)
    rev_score  = dims.get("review_participation", {}).get("score", 0)
    cons_score = dims.get("consistency", {}).get("score", 0)

    if flags and flags[0]["severity"] in ("notable", "caution"):
        flag_dim_key = flags[0]["dimension"]
        focus_label  = _DIM_LABELS.get(flag_dim_key, flag_dim_key.replace("_", " "))
        flag_dim_score = dims.get(flag_dim_key, {}).get("score", 0)
        if flag_dim_score >= 14:
            # Dimension scores well overall — the flag is a specific nuance, not a weak area
            focus = (
                f"{focus_label} scores well overall, but the flag is worth a brief check-in. "
                f"Use the Areas to Explore questions as an entry point — {name} likely has context "
                f"behind it. Keep the tone curious, not corrective."
            )
        else:
            focus = (
                f"The most direct use of this time is to understand what's behind the {focus_label} signal. "
                f"Use the Areas to Explore questions as entry points, not conclusions — {name} may have "
                f"a straightforward explanation. Leave room for them to surface what's been on their mind."
            )
    elif total >= 75:
        focus = (
            f"Across the board, the signals are strong. Rather than reviewing what happened, "
            f"use this 1-on-1 to look forward — what {name} wants to work on next, "
            f"where they feel stretched or constrained, and what would make the next period even more effective."
        )
    elif vel_score >= 12 and rev_score <= 8:
        focus = (
            f"{name} has been heads-down shipping. A useful thread: how connected they feel to the "
            f"team's broader work right now, and whether the current focus is sustainable or starting to feel isolating."
        )
    elif cons_score <= 8:
        focus = (
            f"Consistency is the clearest signal here. Avoid treating it as a performance topic — "
            f"center the conversation on understanding what's been shaping availability, "
            f"and what would help {name} get into a more regular rhythm if that's what they want."
        )
    else:
        focus = (
            f"No single flag dominates the data. Use this 1-on-1 to check in on how {name} is feeling "
            f"about the work itself — what's energizing, what feels like friction, and what's coming up next."
        )

    note = score.get("data_volume_note")
    confidence_block = f"\n> **Note:** {note}\n" if note else ""

    return (
        "> _Stub brief — no AI provider configured. "
        "Set AI_PROVIDER and the matching key in .env for a richer brief._\n"
        + confidence_block
        + f"\n## Quick Summary\n\n{summary}\n"
        f"\n## Highlights\n\n{chr(10).join(highlight_bullets)}\n"
        f"\n## Areas to Explore\n\n{chr(10).join(explores)}\n"
        f"\n## Patterns Worth Noting\n\n{chr(10).join(pattern_bullets)}\n"
        f"\n## Score Summary\n\n{score_table}\n"
        f"\n## Suggested Focus for This 1-on-1\n\n{focus}\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a 1-on-1 brief from a PullStar scored profile."
    )
    parser.add_argument("--login",           required=True)
    parser.add_argument("--mode",            required=True, choices=["local", "stub", "agent"],
                        help="local: call AI via model_provider.json | stub: deterministic, no AI | agent: use separate agent scripts")
    parser.add_argument("--input-dir",       default=".pullstar")
    parser.add_argument("--output-dir",      default=".pullstar")
    parser.add_argument("--provider-config", default=None, metavar="PATH",
                        help="Path to model_provider.json (default: model_provider.json at repo root)")
    args = parser.parse_args()

    # -- Agent mode: do not perform inference here ----------------------------
    if args.mode == "agent":
        print("Error: generate_brief.py does not perform inference in agent mode.",
              file=sys.stderr)
        print("  Use the agent workflow instead:", file=sys.stderr)
        print(f"    python scripts/agent_prepare_1on1.py --login {args.login}", file=sys.stderr)
        print(f"    # (external agent writes .pullstar/llm_output_{args.login}.json)",
              file=sys.stderr)
        print(f"    python scripts/agent_finalize_1on1.py --login {args.login}", file=sys.stderr)
        sys.exit(1)

    input_dir  = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    # -- Load score (required) ------------------------------------------------
    score_path = input_dir / f"score_{args.login}.json"
    if not score_path.exists():
        fail(f"Score file not found: {score_path}\n  Run: python scripts/score.py --login {args.login}")
    print(f"> Score loaded ({score_path})")
    score = json.loads(score_path.read_text(encoding="utf-8"))
    print(f"  {score['engineer_login']} — {score['total_score']}/100 ({score['confidence']} confidence)")

    # -- Load ingest (optional) -----------------------------------------------
    ingest_path = input_dir / f"ingest_{args.login}.json"
    ingest: dict | None = None
    if ingest_path.exists():
        ingest = json.loads(ingest_path.read_text(encoding="utf-8"))
        assert ingest is not None
        n_insights = sum(1 for p in ingest.get("prs_authored", [])
                         if "discussion_summary_stats" in p)
        print(f"  Ingest loaded — {len(ingest.get('prs_authored', []))} PRs"
              + (f", {n_insights} with PR insights" if n_insights else ""))
    else:
        print("  No ingest file — score data only")

    # -- Load provider config (local mode only) --------------------------------
    provider: str | None = None
    model:    str | None = None
    api_key:  str | None = None
    temperature = 0.7
    max_tokens  = 2048

    if args.mode == "local":
        config_path  = Path(args.provider_config) if args.provider_config else DEFAULT_PROVIDER_CONFIG
        provider_cfg = load_provider_config(config_path)
        provider     = provider_cfg["provider"]
        model        = provider_cfg["model"]
        temperature  = provider_cfg.get("temperature", 0.7)
        max_tokens   = int(provider_cfg.get("max_tokens", 2048))

        key_env = PROVIDERS[provider]["key_env"]
        api_key = os.getenv(key_env, "").strip()
        if not api_key:
            fail(
                f"API key not found for provider '{provider}'.\n"
                f"  Set {key_env} in your .env file.\n"
                f"  .env must contain secrets only — do not add provider/model settings there."
            )
        print(f"> Provider: {provider} / {model}  (temp={temperature}, max_tokens={max_tokens})")

    # -- Build canonical LLM input payload ------------------------------------
    # Always written for debugging and agent workflow parity.
    llm_input = build_llm_input_payload(
        score, ingest, load_prompt(),
        model=model, provider=provider, mode=args.mode,
    )
    llm_input_path = write_llm_input(llm_input, output_dir)
    print(f"> LLM input written to {llm_input_path}")

    # -- Run inference or generate stub ---------------------------------------
    if args.mode == "local":
        assert provider is not None and model is not None and api_key is not None
        system_msg = llm_input["system"]
        user_msg   = llm_input["user"]
        print(f"  System: {len(system_msg)} chars  |  User: {len(user_msg)} chars")
        print(f"> Calling {provider}...")

        cfg = PROVIDERS[provider]
        if provider == "anthropic":
            brief = call_anthropic(api_key, model, system_msg, user_msg,
                                   temperature=temperature, max_tokens=max_tokens)
        else:
            brief = call_openai_compatible(
                api_key, model, cfg["base_url"],
                system_msg, user_msg, provider,
                temperature=temperature, max_tokens=max_tokens,
            )

        if not brief.strip():
            fail(
                f"{provider} returned an empty response.\n"
                f"  Check your API key ({PROVIDERS[provider]['key_env']}) and model name."
            )
        print(f"  Response: {len(brief)} chars")

    else:  # stub mode
        print("> Stub mode — generating deterministic brief (no AI call)")
        brief = generate_stub_brief(score, ingest)
        if not brief.strip():
            fail("Stub brief generation returned an empty result.")

    # -- Write final output ---------------------------------------------------
    output = {
        "version":        "1.0",
        "mode":           args.mode,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "engineer_login": score["engineer_login"],
        "engineer_name":  (ingest or {}).get("engineer_name"),
        "org":            (ingest or {}).get("org", ""),
        "lookback_days":  score["lookback_days"],
        "scored_profile": score,
        "brief":          brief,
    }

    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"output_{args.login}.json"
    tmp_path    = output_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    tmp_path.replace(output_path)
    print(f"> Output written to {output_path}")
    print("""
✓ PullStar brief generated successfully.

If this was useful, you can join the early access list for PullStar Pro:
https://savory-step-9d7.notion.site/32fc2b5d4feb8054b937f54c753ff73b?pvs=105

PullStar Pro will explore:
- longitudinal trends across time
- team-level patterns
- coaching and pairing signals
- workflow integrations

Thanks for trying PullStar.""")


if __name__ == "__main__":
    main()
