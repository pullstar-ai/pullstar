"""Versioned prompt selection — `--prompt <name|path>` + provenance metadata."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import FIXTURES, REPO_ROOT

from pullstar.resources import (
    DEFAULT_BRIEF_PROMPT,
    _packaged_prompt_stems,
    load_brief_prompt,
    resolve_brief_prompt,
)

_DEFAULT_HEAD = "You are an engineering leadership intelligence tool."


# --------------------------------------------------------------------------- #
# resolve_brief_prompt
# --------------------------------------------------------------------------- #


def test_default_is_brief_v1() -> None:
    assert DEFAULT_BRIEF_PROMPT == "brief_v1"
    bp = resolve_brief_prompt(None)
    assert bp.name == "brief_v1"
    assert bp.text.startswith(_DEFAULT_HEAD)
    assert bp.text == bp.text.strip()


@pytest.mark.parametrize("spec", ["brief_v1", "brief_v1.txt"])
def test_packaged_name_resolves(spec) -> None:
    assert resolve_brief_prompt(spec) == resolve_brief_prompt(None)


def test_packaged_stems_contains_default() -> None:
    assert "brief_v1" in _packaged_prompt_stems()


def test_unknown_name_raises_with_available_list() -> None:
    with pytest.raises(FileNotFoundError) as exc:
        resolve_brief_prompt("brief_v999")
    assert "brief_v1" in str(exc.value)


def test_filesystem_path_resolves_as_custom(tmp_path) -> None:
    f = tmp_path / "my_prompt.txt"
    f.write_text("  You are a custom reviewer.\n", encoding="utf-8")
    bp = resolve_brief_prompt(str(f))
    assert bp.name == "custom"
    assert bp.text == "You are a custom reviewer."


def test_explicit_relative_path_beats_packaged_name(tmp_path, monkeypatch) -> None:
    (tmp_path / "brief_v1.txt").write_text("local override", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # bare name -> packaged; explicit path -> the local file
    assert resolve_brief_prompt("brief_v1").name == "brief_v1"
    assert resolve_brief_prompt("./brief_v1.txt") == ("custom", "local override")


def test_missing_path_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_brief_prompt(str(tmp_path / "nope" / "x.txt"))


def test_load_brief_prompt_accepts_spec(tmp_path) -> None:
    f = tmp_path / "p.txt"
    f.write_text("hello", encoding="utf-8")
    assert load_brief_prompt(str(f)) == "hello"
    assert load_brief_prompt().startswith(_DEFAULT_HEAD)


# --------------------------------------------------------------------------- #
# CLI wiring + provenance
# --------------------------------------------------------------------------- #


def _run(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True
    )


def _prep(work):
    (work / "ingest_octodev.json").write_bytes(
        (FIXTURES / "ingest_octodev.json").read_bytes()
    )
    p = _run(REPO_ROOT / "scripts" / "score.py", "--login", "octodev",
             "--input-dir", work, "--output-dir", work, cwd=work)
    assert p.returncode == 0, p.stderr


def test_generate_brief_records_default_prompt(tmp_path) -> None:
    work = tmp_path
    _prep(work)
    p = _run(REPO_ROOT / "scripts" / "generate_brief.py", "--login", "octodev",
             "--mode", "stub", "--input-dir", work, "--output-dir", work, cwd=work)
    assert p.returncode == 0, p.stderr
    out = json.loads((work / "output_octodev.json").read_text(encoding="utf-8"))
    assert out["prompt"] == "brief_v1"
    meta = json.loads((work / "llm_input_octodev.json").read_text(encoding="utf-8"))["metadata"]
    assert meta["prompt"] == "brief_v1"


def test_generate_brief_with_custom_prompt_file(tmp_path) -> None:
    work = tmp_path
    _prep(work)
    custom = work / "custom_prompt.txt"
    custom.write_text("You are a terse reviewer.", encoding="utf-8")
    p = _run(REPO_ROOT / "scripts" / "generate_brief.py", "--login", "octodev",
             "--mode", "stub", "--prompt", str(custom),
             "--input-dir", work, "--output-dir", work, cwd=work)
    assert p.returncode == 0, p.stderr
    out = json.loads((work / "output_octodev.json").read_text(encoding="utf-8"))
    assert out["prompt"] == "custom"
    llm_in = json.loads((work / "llm_input_octodev.json").read_text(encoding="utf-8"))
    assert llm_in["system"] == "You are a terse reviewer."
    assert llm_in["metadata"]["prompt"] == "custom"


def test_generate_brief_rejects_unknown_prompt(tmp_path) -> None:
    work = tmp_path
    _prep(work)
    p = _run(REPO_ROOT / "scripts" / "generate_brief.py", "--login", "octodev",
             "--mode", "stub", "--prompt", "brief_v999",
             "--input-dir", work, "--output-dir", work, cwd=work)
    assert p.returncode != 0
    assert "not a packaged prompt" in p.stderr


def test_agent_flow_propagates_prompt(tmp_path) -> None:
    work = tmp_path
    _prep(work)
    assert _run(REPO_ROOT / "scripts" / "agent_prepare_1on1.py", "--login", "octodev",
                "--input-dir", work, "--prompt", "brief_v1", cwd=work).returncode == 0
    (work / "llm_output_octodev.json").write_text(
        json.dumps({"version": "1.0", "engineer_login": "octodev", "brief": "## X\nbody"}),
        encoding="utf-8",
    )
    assert _run(REPO_ROOT / "scripts" / "agent_finalize_1on1.py", "--login", "octodev",
                "--input-dir", work, "--output-dir", work, cwd=work).returncode == 0
    out = json.loads((work / "output_octodev.json").read_text(encoding="utf-8"))
    assert out["prompt"] == "brief_v1"
