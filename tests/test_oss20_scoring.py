"""
OSS-20 scoring tests (requirements 12-16).

Tests score_profile() — the new package-level assembly function.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pullstar.scoring import score_profile


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _pr(repo: str = "o/r", status: str = "merged", lines: int = 100,
        created: str = "2026-06-01T00:00:00+00:00",
        merged:  str = "2026-06-02T00:00:00+00:00",
        desc_len: int = 80) -> dict:
    return {
        "number":                 1,
        "title":                  "test PR",
        "url":                    "https://github.com/o/r/pull/1",
        "repository":             repo,
        "status":                 status,
        "created_at":             created,
        "merged_at":              merged if status == "merged" else None,
        "lines_added":            lines,
        "lines_deleted":          lines // 2,
        "files_changed":          3,
        "commit_count":           2,
        "description_length":     desc_len,
        "label_names":            [],
        "base_branch":            "main",
        "reviews_received_count": 1,
    }


def _review(repo: str = "o/r", submitted: str = "2026-06-10T00:00:00+00:00") -> dict:
    return {
        "repository":   repo,
        "pr_number":    99,
        "pr_title":     "someone else's PR",
        "state":        "approved",
        "submitted_at": submitted,
        "body_length":  120,
    }


def _ingest(prs=None, reviews=None, days: int = 30) -> dict:
    prs     = prs     or []
    reviews = reviews or []
    n       = len(prs)
    ingested_at = datetime.now(timezone.utc).isoformat()
    return {
        "engineer_login": "octouser",
        "engineer_name":  "Octo User",
        "org":            "",
        "lookback_days":  days,
        "ingested_at":    ingested_at,
        "prs_authored":   prs,
        "reviews_given":  reviews,
        "reviews_received": [],
        "summary_stats": {
            "total_prs_authored":    n,
            "prs_merged":            sum(1 for p in prs if p["status"] == "merged"),
            "prs_open":              sum(1 for p in prs if p["status"] == "open"),
            "prs_closed_unmerged":   sum(1 for p in prs if p["status"] == "closed"),
            "total_reviews_given":   len(reviews),
            "total_reviews_received": sum(p["reviews_received_count"] for p in prs),
            "total_lines_added":     sum(p["lines_added"]   for p in prs),
            "total_lines_deleted":   sum(p["lines_deleted"] for p in prs),
            "avg_pr_size_lines":     0.0,
            "active_weeks":          min(n, 4),
            "total_weeks":           max(1, days // 7),
        },
    }


# ---------------------------------------------------------------------------
# Test 12: score_profile returns complete ScoredProfile
# ---------------------------------------------------------------------------

def test_score_profile_returns_complete_profile() -> None:
    """Test 12: score_profile returns all required ScoredProfile keys."""
    prs = [_pr(repo="o/a"), _pr(repo="o/b"), _pr(repo="o/a"), _pr(repo="o/b"),
           _pr(repo="o/a"), _pr(repo="o/b")]
    ingest = _ingest(prs=prs, reviews=[_review()] * 6)

    result = score_profile(ingest)

    required = {
        "engineer_login", "scored_at", "lookback_days",
        "dimensions", "total_score", "confidence", "data_volume_note", "flags",
    }
    assert required.issubset(result.keys())
    assert result["engineer_login"] == "octouser"
    assert isinstance(result["total_score"], int)
    assert result["confidence"] in ("high", "medium", "low")
    assert isinstance(result["flags"], list)


# ---------------------------------------------------------------------------
# Test 13: all five dimension functions contribute to score_profile
# ---------------------------------------------------------------------------

def test_all_five_dimensions_present() -> None:
    """Test 13: score_profile result contains all five dimensions."""
    ingest = _ingest(prs=[_pr()] * 6, reviews=[_review()] * 6)
    result = score_profile(ingest)

    dims = result["dimensions"]
    assert "velocity"             in dims
    assert "pr_quality"           in dims
    assert "review_participation" in dims
    assert "collaboration"        in dims
    assert "consistency"          in dims

    for key, dim in dims.items():
        assert dim["max"] == 20, f"{key} max should be 20"
        assert 0 <= dim["score"] <= 20, f"{key} score out of range"


# ---------------------------------------------------------------------------
# Test 14: confidence assembly
# ---------------------------------------------------------------------------

def test_confidence_high_with_many_prs() -> None:
    """Test 14a: many PRs spread across weeks → overall confidence at least medium."""
    # Build PRs spread across 4 different weeks to satisfy consistency requirements.
    weeks = ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"]
    prs = [
        _pr(created=f"{w}T00:00:00+00:00", merged=f"{w}T12:00:00+00:00")
        for w in weeks
        for _ in range(3)        # 3 PRs per week, 12 total
    ]
    ingest = _ingest(prs=prs, reviews=[_review()] * 12, days=30)
    result = score_profile(ingest)
    # With 12 PRs across 4 weeks all dimensions should be medium or high.
    assert result["confidence"] in ("medium", "high")


def test_confidence_low_with_few_prs() -> None:
    """Test 14b: fewer than 5 PRs → low confidence regardless of dimension scores."""
    ingest = _ingest(prs=[_pr()] * 3)
    result = score_profile(ingest)
    assert result["confidence"] == "low"
    for dim in result["dimensions"].values():
        assert dim["confidence"] == "low"


# ---------------------------------------------------------------------------
# Test 15: flag deduplication and severity ordering
# ---------------------------------------------------------------------------

def test_flag_dedup_and_severity_order() -> None:
    """Test 15: duplicate flag messages are removed; notable > caution > info order."""
    # Construct a profile that will generate stale-PR flags (velocity) and
    # large-PR flags (pr_quality) to exercise the dedup path with real flags.
    stale_pr = _pr(status="open", created="2026-05-01T00:00:00+00:00")
    large_pr = _pr(lines=600)
    prs = [stale_pr] * 5 + [large_pr] * 6
    ingest = _ingest(prs=prs, reviews=[_review()] * 6)
    result = score_profile(ingest)

    messages = [f["message"] for f in result["flags"]]
    # All messages unique (dedup works)
    assert len(messages) == len(set(messages))

    # Severity order: notable ≥ caution ≥ info
    severity_rank = {"notable": 2, "caution": 1, "info": 0}
    for i in range(len(result["flags"]) - 1):
        a = severity_rank[result["flags"][i]["severity"]]
        b = severity_rank[result["flags"][i + 1]["severity"]]
        assert a >= b, f"flags not sorted by severity at index {i}"


# ---------------------------------------------------------------------------
# Test 16: low-data behavior
# ---------------------------------------------------------------------------

def test_low_data_sets_volume_note() -> None:
    """Test 16: <5 PRs sets data_volume_note and overrides all dim confidence to low."""
    ingest = _ingest(prs=[_pr()] * 2)
    result = score_profile(ingest)

    assert result["data_volume_note"] is not None
    assert "2 PRs" in result["data_volume_note"] or "2 PR" in result["data_volume_note"]
    assert result["confidence"] == "low"
    for dim in result["dimensions"].values():
        assert dim["confidence"] == "low"


def test_adequate_data_clears_volume_note() -> None:
    """Test 16b: ≥5 PRs → data_volume_note is None."""
    ingest = _ingest(prs=[_pr()] * 6, reviews=[_review()] * 6)
    result = score_profile(ingest)
    assert result["data_volume_note"] is None


# ---------------------------------------------------------------------------
# score_profile matches legacy CLI output on fixture
# ---------------------------------------------------------------------------

def test_score_profile_matches_fixture(ingest_octodev, expected_score_octodev) -> None:
    """score_profile() produces the same totals as the frozen CLI baseline."""
    result = score_profile(ingest_octodev)
    result.pop("scored_at")

    assert result["total_score"]  == expected_score_octodev["total_score"]
    assert result["confidence"]   == expected_score_octodev["confidence"]
    assert result["dimensions"]["velocity"]["score"] == (
        expected_score_octodev["dimensions"]["velocity"]["score"]
    )
    assert result["dimensions"]["pr_quality"]["score"] == (
        expected_score_octodev["dimensions"]["pr_quality"]["score"]
    )
