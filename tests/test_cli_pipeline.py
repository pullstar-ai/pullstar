"""Requirement 9 + Test 10 — the score -> brief (stub) -> agent flow still runs.

Exercises the wrappers end-to-end on the fixture with no network and no AI
provider, and proves output is deterministic.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import FIXTURES, REPO_ROOT


def _py(*args, cwd) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def _pipeline(work) -> dict:
    (work / "ingest_octodev.json").write_bytes(
        (FIXTURES / "ingest_octodev.json").read_bytes()
    )
    s = REPO_ROOT / "scripts"
    _py(s / "score.py", "--login", "octodev", "--input-dir", work, "--output-dir", work, cwd=work)
    _py(s / "generate_brief.py", "--login", "octodev", "--mode", "stub",
        "--input-dir", work, "--output-dir", work, cwd=work)
    _py(s / "agent_prepare_1on1.py", "--login", "octodev", "--input-dir", work, cwd=work)
    (work / "llm_output_octodev.json").write_text(
        json.dumps({"version": "1.0", "engineer_login": "octodev",
                    "brief": "## Quick Summary\nreviewed"}),
        encoding="utf-8",
    )
    _py(s / "agent_finalize_1on1.py", "--login", "octodev",
        "--input-dir", work, "--output-dir", work, cwd=work)
    return json.loads((work / "output_octodev.json").read_text(encoding="utf-8"))


def test_stub_pipeline_produces_brief(tmp_path) -> None:
    work = tmp_path / "run"
    work.mkdir()
    out = _pipeline(work)
    assert out["mode"] == "agent"
    assert out["brief"].strip()
    assert out["prompt"] == "brief_v1"
    assert out["scored_profile"]["total_score"] == 76


def test_stub_brief_is_deterministic(tmp_path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    def stub_brief(work):
        (work / "ingest_octodev.json").write_bytes(
            (FIXTURES / "ingest_octodev.json").read_bytes()
        )
        s = REPO_ROOT / "scripts"
        _py(s / "score.py", "--login", "octodev", "--input-dir", work,
            "--output-dir", work, cwd=work)
        _py(s / "generate_brief.py", "--login", "octodev", "--mode", "stub",
            "--input-dir", work, "--output-dir", work, cwd=work)
        return json.loads(
            (work / "output_octodev.json").read_text(encoding="utf-8")
        )["brief"]

    assert stub_brief(a) == stub_brief(b)


@pytest.mark.parametrize("script", ["score.py", "generate_brief.py"])
def test_scripts_do_not_need_source_dir_on_syspath(script, tmp_path) -> None:
    """Wrappers import ``pullstar`` from the installed package, not a sibling dir."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "usage:" in proc.stdout.lower()
