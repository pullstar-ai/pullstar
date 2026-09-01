"""
OSS-20 supplemental context tests (requirements 17-22).

Tests SupplementalContextBlock TypedDict and build_user_message() context
injection behaviour.
"""

from __future__ import annotations

import pytest

from pullstar.models import SupplementalContextBlock
from pullstar.prompting import build_user_message


# ---------------------------------------------------------------------------
# Minimal score / ingest fixtures
# ---------------------------------------------------------------------------

def _minimal_score(login: str = "testuser") -> dict:
    return {
        "engineer_login": login,
        "lookback_days":  30,
        "confidence":     "medium",
        "data_volume_note": None,
        "total_score":    60,
        "flags":          [],
        "dimensions": {
            "velocity":             {"score": 14, "max": 20, "confidence": "medium", "signals": [], "flags": []},
            "pr_quality":           {"score": 12, "max": 20, "confidence": "medium", "signals": [], "flags": []},
            "review_participation": {"score": 10, "max": 20, "confidence": "medium", "signals": [], "flags": []},
            "collaboration":        {"score": 12, "max": 20, "confidence": "medium", "signals": [], "flags": []},
            "consistency":          {"score": 12, "max": 20, "confidence": "medium", "signals": [], "flags": []},
        },
    }


def _minimal_ingest(login: str = "testuser") -> dict:
    return {
        "engineer_login": login,
        "engineer_name":  "Test User",
        "org":            "myorg",
        "lookback_days":  30,
        "ingested_at":    "2026-08-01T00:00:00+00:00",
        "prs_authored":   [],
        "reviews_given":  [],
        "reviews_received": [],
        "summary_stats": {
            "total_prs_authored":     0,
            "prs_merged":             0,
            "prs_open":               0,
            "prs_closed_unmerged":    0,
            "total_reviews_given":    0,
            "total_reviews_received": 0,
            "total_lines_added":      0,
            "total_lines_deleted":    0,
            "avg_pr_size_lines":      0.0,
            "active_weeks":           0,
            "total_weeks":            4,
        },
    }


# ---------------------------------------------------------------------------
# Test 17: SupplementalContextBlock is a TypedDict with label and content
# ---------------------------------------------------------------------------

def test_supplemental_context_block_shape() -> None:
    """Test 17: SupplementalContextBlock is a plain dict with label/content at runtime."""
    block: SupplementalContextBlock = {"label": "Sprint Goals", "content": "Ship feature X"}
    assert block["label"]   == "Sprint Goals"
    assert block["content"] == "Ship feature X"
    # TypedDicts are plain dicts at runtime
    assert isinstance(block, dict)


def test_supplemental_context_block_keys() -> None:
    """Test 17b: SupplementalContextBlock has exactly label and content fields."""
    hints = SupplementalContextBlock.__annotations__
    assert "label"   in hints
    assert "content" in hints
    assert len(hints) == 2, f"unexpected extra fields: {hints}"


# ---------------------------------------------------------------------------
# Test 18: no Jira-specific or goals-specific models exist
# ---------------------------------------------------------------------------

def test_no_source_specific_models() -> None:
    """Test 18: pullstar.models has no Jira, goals, or sprint-specific TypedDicts."""
    import inspect
    import pullstar.models as models_mod

    src = inspect.getsource(models_mod)

    for banned in ("JiraContext", "SprintGoals", "GoalsContext", "JiraBlock"):
        assert banned not in src, f"Found source-specific model: {banned}"


# ---------------------------------------------------------------------------
# Test 19: no supplemental_context → no SUPPLEMENTAL CONTEXT section
# ---------------------------------------------------------------------------

def test_no_context_produces_no_section() -> None:
    """Test 19: omitting supplemental_context produces no section header."""
    msg = build_user_message(_minimal_score(), _minimal_ingest())
    assert "SUPPLEMENTAL CONTEXT" not in msg


def test_empty_list_produces_no_section() -> None:
    """Test 19b: supplemental_context=[] also produces no section."""
    msg = build_user_message(_minimal_score(), _minimal_ingest(), supplemental_context=[])
    assert "SUPPLEMENTAL CONTEXT" not in msg


# ---------------------------------------------------------------------------
# Test 20: one non-empty block appears in the user message
# ---------------------------------------------------------------------------

def test_single_block_appears_in_message() -> None:
    """Test 20: a single SupplementalContextBlock's label and content appear in output."""
    block: SupplementalContextBlock = {
        "label":   "Sprint Goals",
        "content": "Ship the payments refactor and close the P1 auth bug.",
    }
    msg = build_user_message(
        _minimal_score(), _minimal_ingest(),
        supplemental_context=[block],
    )
    assert "SUPPLEMENTAL CONTEXT" in msg
    assert "[Sprint Goals]" in msg
    assert "Ship the payments refactor" in msg


# ---------------------------------------------------------------------------
# Test 21: multiple blocks preserve insertion order
# ---------------------------------------------------------------------------

def test_multiple_blocks_preserve_order() -> None:
    """Test 21: multiple blocks appear in the message in insertion order."""
    blocks: list[SupplementalContextBlock] = [
        {"label": "Sprint Goals", "content": "Alpha task"},
        {"label": "Eng Context",  "content": "Beta task"},
        {"label": "On-Call",      "content": "Gamma task"},
    ]
    msg = build_user_message(
        _minimal_score(), _minimal_ingest(),
        supplemental_context=blocks,
    )
    pos_alpha = msg.index("Alpha task")
    pos_beta  = msg.index("Beta task")
    pos_gamma = msg.index("Gamma task")
    assert pos_alpha < pos_beta < pos_gamma, "blocks not in insertion order"


# ---------------------------------------------------------------------------
# Test 22: empty / whitespace-only blocks are silently skipped
# ---------------------------------------------------------------------------

def test_empty_block_skipped() -> None:
    """Test 22: a block with empty or whitespace-only content is silently dropped."""
    blocks: list[SupplementalContextBlock] = [
        {"label": "Empty Block",  "content": ""},
        {"label": "Spaces Block", "content": "   \n  \t  "},
        {"label": "Good Block",   "content": "This should appear"},
    ]
    msg = build_user_message(
        _minimal_score(), _minimal_ingest(),
        supplemental_context=blocks,
    )
    assert "SUPPLEMENTAL CONTEXT" in msg
    assert "[Empty Block]"  not in msg
    assert "[Spaces Block]" not in msg
    assert "[Good Block]"   in msg
    assert "This should appear" in msg


def test_all_empty_blocks_no_section() -> None:
    """Test 22b: if all blocks are empty, no SUPPLEMENTAL CONTEXT header appears."""
    blocks: list[SupplementalContextBlock] = [
        {"label": "A", "content": ""},
        {"label": "B", "content": "\n"},
    ]
    msg = build_user_message(
        _minimal_score(), _minimal_ingest(),
        supplemental_context=blocks,
    )
    assert "SUPPLEMENTAL CONTEXT" not in msg
