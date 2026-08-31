"""Shared test helpers for the PullStar packaging test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def ingest_octodev() -> dict:
    return json.loads((FIXTURES / "ingest_octodev.json").read_text(encoding="utf-8"))


@pytest.fixture
def expected_score_octodev() -> dict:
    return json.loads((FIXTURES / "expected_score_octodev.json").read_text(encoding="utf-8"))
