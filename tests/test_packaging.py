"""Requirement 1, 8, 10 + Tests 1, 14 — the distribution builds cleanly.

Builds an sdist + wheel into a temp dir and inspects them. Skipped if the
``build`` package is unavailable (install with ``pip install -e ".[dev]"``).
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import REPO_ROOT

build = pytest.importorskip("build")


@pytest.fixture(scope="module")
def dists(tmp_path_factory) -> list[Path]:
    out = tmp_path_factory.mktemp("dist")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(out), str(REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return sorted(out.iterdir())


def test_builds_sdist_and_wheel(dists) -> None:
    names = [p.name for p in dists]
    assert any(n.endswith(".tar.gz") for n in names), names
    assert any(n == "pullstar-0.2.0-py3-none-any.whl" for n in names), names


def test_wheel_contains_package_and_prompt_data(dists) -> None:
    wheel = next(p for p in dists if p.name.endswith(".whl"))
    with zipfile.ZipFile(wheel) as zf:
        members = zf.namelist()
    assert "pullstar/__init__.py" in members
    assert "pullstar/scoring.py" in members
    assert "pullstar/prompting.py" in members
    assert "pullstar/models.py" in members
    assert "pullstar/prompts/brief_v1.txt" in members
    assert "pullstar/prompts/brief_v2.txt" in members
    assert "pullstar/ingest.py" in members
    assert "pullstar/engine.py" in members
    # UI shell / scripts / skills must NOT be shipped in the wheel.
    assert not any(m.startswith("scripts/") for m in members)
    assert not any(m.startswith("skills/") for m in members)
    assert not any(m == "app.py" for m in members)


def test_wheel_metadata(dists) -> None:
    wheel = next(p for p in dists if p.name.endswith(".whl"))
    with zipfile.ZipFile(wheel) as zf:
        meta = zf.read("pullstar-0.2.0.dist-info/METADATA").decode()
    assert "Name: pullstar" in meta
    assert "Version: 0.2.0" in meta
    assert "Requires-Python: >=3.11" in meta
    assert "Requires-Dist: gradio" not in meta.split("Provides-Extra")[0]


def test_wheel_dependency_manifest(dists) -> None:
    """Verify the v0.2.0 engine dependency manifest is accurate.

    The packaged engine only imports requests and PyGithub.
    Provider SDKs (anthropic, openai) and python-dotenv are used
    only by scripts/ which is not distributed — they must not appear
    as engine dependencies.
    """
    wheel = next(p for p in dists if p.name.endswith(".whl"))
    with zipfile.ZipFile(wheel) as zf:
        meta = zf.read("pullstar-0.2.0.dist-info/METADATA").decode()

    # Parse only the non-extras Requires-Dist lines
    requires_lines = [
        line for line in meta.splitlines()
        if line.startswith("Requires-Dist:")
        and "; extra ==" not in line   # exclude extras
    ]
    declared = " ".join(requires_lines).lower()

    # Core engine deps must be declared
    assert any("requests" in ln for ln in requires_lines), \
        "requests must be a declared engine dependency"
    assert any("pygithub" in ln.lower() for ln in requires_lines), \
        "PyGithub must be a declared engine dependency"

    # Provider SDKs and dotenv must NOT be core engine deps
    assert "anthropic" not in declared, \
        "anthropic must not be an engine dependency (scripts/ only)"
    assert "openai" not in declared, \
        "openai must not be an engine dependency (scripts/ only)"
    assert "python-dotenv" not in declared and "dotenv" not in declared, \
        "python-dotenv must not be an engine dependency (scripts/ only)"
