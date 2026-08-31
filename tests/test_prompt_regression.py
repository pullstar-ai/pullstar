"""Requirement 5 + Test 12 — prompt construction behavior is unchanged.

``expected_user_message_octodev.txt`` was generated from the pre-packaging
``scripts/prompt_builder.py`` (commit 61ed0865).
"""

from __future__ import annotations

import json

from conftest import FIXTURES

from pullstar import prompting


def _score() -> dict:
    data = json.loads(
        (FIXTURES / "expected_score_octodev.json").read_text(encoding="utf-8")
    )
    data["scored_at"] = "2026-06-30T12:00:00+00:00"
    return data


def test_build_user_message_matches_frozen_baseline(ingest_octodev) -> None:
    expected = (FIXTURES / "expected_user_message_octodev.txt").read_text(encoding="utf-8")
    assert prompting.build_user_message(_score(), ingest_octodev) == expected


def test_build_llm_input_payload_shape(ingest_octodev) -> None:
    payload = prompting.build_llm_input_payload(
        _score(), ingest_octodev, "SYSTEM", model="m1", provider="p1", mode="ai"
    )
    assert payload["version"] == "1.0"
    assert payload["engineer_login"] == "octodev"
    assert payload["mode"] == "ai"
    assert payload["system"] == "SYSTEM"
    md = payload["metadata"]
    assert md["provider"] == "p1" and md["model"] == "m1"
    assert md["total_score"] == 76
    assert md["has_insights"] is True
    assert "generated_at" in md


def test_write_llm_input_atomic(tmp_path, ingest_octodev) -> None:
    payload = prompting.build_llm_input_payload(
        _score(), ingest_octodev, "S", model=None, provider=None, mode="stub"
    )
    out = prompting.write_llm_input(payload, tmp_path)
    assert out == tmp_path / "llm_input_octodev.json"
    assert json.loads(out.read_text(encoding="utf-8"))["mode"] == "stub"
    assert not list(tmp_path.glob("*.tmp"))
