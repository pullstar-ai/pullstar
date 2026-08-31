"""Requirement 5, 11 + Tests 11, 13 — scoring behavior is unchanged.

``expected_score_octodev.json`` was generated from the pre-packaging
``scripts/score.py`` (commit 61ed0865) and is byte-compatible with the moved
``pullstar.scoring`` implementation.
"""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import FIXTURES, REPO_ROOT

from pullstar import scoring


def _run_cli(out_dir) -> dict:
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "score.py"),
            "--login",
            "octodev",
            "--input-dir",
            str(FIXTURES),
            "--output-dir",
            str(out_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads((out_dir / "score_octodev.json").read_text(encoding="utf-8"))
    data.pop("scored_at")
    return data


def test_cli_score_matches_frozen_baseline(tmp_path, expected_score_octodev) -> None:
    assert _run_cli(tmp_path) == expected_score_octodev


def test_cli_score_is_deterministic(tmp_path) -> None:
    a = _run_cli(tmp_path / "a")
    b = _run_cli(tmp_path / "b")
    assert a == b


def test_expected_totals(expected_score_octodev) -> None:
    exp = expected_score_octodev
    assert exp["total_score"] == 76
    assert exp["confidence"] == "medium"
    assert exp["dimensions"]["velocity"]["score"] == 20
    assert exp["dimensions"]["pr_quality"]["score"] == 16
    assert [f["message"] for f in exp["flags"]] == [
        "1 PR exceeded 1000 lines — worth discussing scope or decomposition"
    ]


def test_dimension_functions_are_pure_and_hand_computable() -> None:
    # 4 merged PRs (base 12) + all merged same-day (<3d, +4) + 2 repos (+4) = 20
    prs = [
        {
            "status": "merged",
            "repository": f"o/{'a' if i < 2 else 'b'}",
            "created_at": "2026-06-01T00:00:00+00:00",
            "merged_at": "2026-06-01T06:00:00+00:00",
        }
        for i in range(4)
    ]
    ingested_at = scoring.parse_dt("2026-06-30T00:00:00+00:00")
    result, top_flags = scoring.score_velocity(prs, 30, ingested_at)
    assert result["score"] == 20
    assert result["max"] == 20
    assert top_flags == []
