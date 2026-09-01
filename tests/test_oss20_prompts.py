"""
OSS-20 prompt / resources tests (requirements 23-26).

Tests resolve_brief_prompt(), BriefPrompt provenance, and brief_v2 guardrails.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Test 23: brief_v1 is resolvable from the package
# ---------------------------------------------------------------------------

def test_brief_v1_resolvable() -> None:
    """Test 23: resolve_brief_prompt('brief_v1') returns non-empty text."""
    from pullstar.resources import resolve_brief_prompt

    bp = resolve_brief_prompt("brief_v1")
    assert bp.name == "brief_v1"
    assert len(bp.text) > 100
    # Standard brief_v1 content marker
    assert "engineering" in bp.text.lower() or "manager" in bp.text.lower()


# ---------------------------------------------------------------------------
# Test 24: brief_v2 is resolvable from the package
# ---------------------------------------------------------------------------

def test_brief_v2_resolvable() -> None:
    """Test 24: resolve_brief_prompt('brief_v2') returns non-empty text."""
    from pullstar.resources import resolve_brief_prompt

    bp = resolve_brief_prompt("brief_v2")
    assert bp.name == "brief_v2"
    assert len(bp.text) > 100


# ---------------------------------------------------------------------------
# Test 25: brief_v2 contains the six supplemental context guardrail rules
# ---------------------------------------------------------------------------

def test_brief_v2_contains_guardrails() -> None:
    """Test 25: brief_v2 text contains all six SUPPLEMENTAL CONTEXT rules."""
    from pullstar.resources import resolve_brief_prompt

    bp = resolve_brief_prompt("brief_v2")
    text = bp.text.lower()

    # Rule 1: context is not evidence of engineering work
    assert "not evidence" in text or "is not evidence" in text

    # Rule 2: statements grounded in github evidence
    assert "github evidence" in text or "github data" in text

    # Rule 3: absence != failure
    assert "absence" in text

    # Rule 4: use context only to sharpen focus / adjust framing
    assert "sharpen" in text or "framing" in text or "focus" in text

    # Rule 5: source of context is irrelevant
    assert "source" in text and "irrelevant" in text

    # Rule 6: context conflicts with evidence → trust github
    assert "conflict" in text or "conflicts" in text


def test_brief_v2_has_supplemental_context_section_header() -> None:
    """Test 25b: brief_v2 contains the SUPPLEMENTAL CONTEXT section marker."""
    from pullstar.resources import resolve_brief_prompt

    bp = resolve_brief_prompt("brief_v2")
    assert "SUPPLEMENTAL CONTEXT" in bp.text


# ---------------------------------------------------------------------------
# Test 26: prompt provenance — BriefPrompt.name identifies the version
# ---------------------------------------------------------------------------

def test_prompt_provenance_brief_v2() -> None:
    """Test 26: BriefPrompt.name == 'brief_v2' when brief_v2 is resolved."""
    from pullstar.resources import BriefPrompt, resolve_brief_prompt

    bp = resolve_brief_prompt("brief_v2")
    assert isinstance(bp, BriefPrompt)
    assert bp.name == "brief_v2"


def test_prompt_provenance_brief_v1() -> None:
    """Test 26b: BriefPrompt.name == 'brief_v1' for the default prompt."""
    from pullstar.resources import BriefPrompt, resolve_brief_prompt

    bp = resolve_brief_prompt()          # None → default
    assert isinstance(bp, BriefPrompt)
    assert bp.name == "brief_v1"


def test_prompt_provenance_custom(tmp_path) -> None:
    """Test 26c: a filesystem path yields BriefPrompt.name == 'custom'."""
    from pullstar.resources import CUSTOM_PROMPT_NAME, BriefPrompt, resolve_brief_prompt

    custom_file = tmp_path / "my_prompt.txt"
    custom_file.write_text("You are a custom prompt.", encoding="utf-8")

    bp = resolve_brief_prompt(str(custom_file))
    assert isinstance(bp, BriefPrompt)
    assert bp.name == CUSTOM_PROMPT_NAME


def test_unknown_prompt_raises() -> None:
    """Test 26d: an unknown packaged name that is not a file raises FileNotFoundError."""
    from pullstar.resources import resolve_brief_prompt

    with pytest.raises(FileNotFoundError, match="brief_nonexistent"):
        resolve_brief_prompt("brief_nonexistent")


# ---------------------------------------------------------------------------
# build_llm_input_payload passes prompt_name through to metadata
# ---------------------------------------------------------------------------

def test_llm_payload_carries_prompt_name() -> None:
    """build_llm_input_payload propagates prompt_name to metadata.prompt."""
    from pullstar.prompting import build_llm_input_payload

    score = {
        "engineer_login": "testuser",
        "lookback_days":  30,
        "confidence":     "medium",
        "data_volume_note": None,
        "total_score":    60,
        "flags":          [],
        "dimensions": {
            k: {"score": 12, "max": 20, "confidence": "medium", "signals": [], "flags": []}
            for k in ("velocity", "pr_quality", "review_participation", "collaboration", "consistency")
        },
    }

    payload = build_llm_input_payload(
        score, None, "system prompt text",
        model="claude-3-5-haiku-20241022",
        provider="anthropic",
        prompt_name="brief_v2",
    )
    assert payload["metadata"]["prompt"] == "brief_v2"


def test_llm_payload_has_supplemental_context_flag() -> None:
    """build_llm_input_payload sets has_supplemental_context in metadata."""
    from pullstar.models import SupplementalContextBlock
    from pullstar.prompting import build_llm_input_payload

    score = {
        "engineer_login": "testuser",
        "lookback_days":  30,
        "confidence":     "medium",
        "data_volume_note": None,
        "total_score":    60,
        "flags":          [],
        "dimensions": {
            k: {"score": 12, "max": 20, "confidence": "medium", "signals": [], "flags": []}
            for k in ("velocity", "pr_quality", "review_participation", "collaboration", "consistency")
        },
    }
    ctx: list[SupplementalContextBlock] = [{"label": "L", "content": "some text"}]

    payload_with    = build_llm_input_payload(score, None, "sys", model=None, provider=None,
                                              supplemental_context=ctx)
    payload_without = build_llm_input_payload(score, None, "sys", model=None, provider=None)

    assert payload_with["metadata"]["has_supplemental_context"]    is True
    assert payload_without["metadata"]["has_supplemental_context"] is False
