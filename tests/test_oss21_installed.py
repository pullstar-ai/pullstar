"""
OSS-21 requirement AD — Clean installed-package release test.

Builds the pullstar wheel, installs it into an isolated virtual environment,
then runs a standalone verification script from outside the source checkout.

Verifies all 12 points in requirement AD:
  1.  import pullstar
  2.  import the public engine API
  3.  version == 0.2.0
  4.  resolve brief_v1
  5.  resolve brief_v2
  6.  execute a fully mocked prepare_1on1
  7.  inspect/serialize PreparedInference
  8.  simulate external inference
  9.  finalize to EngineResult
  10. confirm scripts are not needed
  11. confirm .pullstar is not needed
  12. confirm no cwd assumption

Skipped when the `build` package is unavailable (install with
``pip install -e ".[dev]"``).  The test is module-scoped so the wheel is
built once per pytest session.
"""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

import pytest

from conftest import REPO_ROOT

build = pytest.importorskip("build")

# ---------------------------------------------------------------------------
# Standalone release-test script (written to a temp file and executed by the
# installed venv's Python, from a temp directory outside the source tree).
# ---------------------------------------------------------------------------

_RELEASE_TEST_SCRIPT = """\
#!/usr/bin/env python3
\"\"\"PullStar v0.2.0 clean installed-package release test.\"\"\"
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# 1, 2. Import pullstar and the public engine API
import pullstar
from pullstar.engine import (
    PreparedInference, PreparedOneOnOne, EngineResult,
    prepare_1on1, finalize_1on1, FinalizationError,
)
from pullstar.resources import resolve_brief_prompt
import importlib.metadata

# 3. Version
assert importlib.metadata.version("pullstar") == "0.2.0", (
    f"Expected 0.2.0, got {importlib.metadata.version('pullstar')!r}"
)
assert pullstar.__version__ == "0.2.0", (
    f"Expected __version__ == '0.2.0', got {pullstar.__version__!r}"
)

# 4. resolve brief_v1
v1 = resolve_brief_prompt("brief_v1")
assert v1.name == "brief_v1", v1.name
assert v1.text, "brief_v1 text is empty"

# 5. resolve brief_v2
v2 = resolve_brief_prompt("brief_v2")
assert v2.name == "brief_v2", v2.name
assert v2.text, "brief_v2 text is empty"

# Shared fake ingest profile
_NOW = datetime.now(timezone.utc)
FAKE_INGEST = {
    "engineer_login":  "octodev",
    "engineer_name":   "Octo Dev",
    "org":             "",
    "lookback_days":   30,
    "ingested_at":     _NOW.isoformat(),
    "prs_authored": [
        {
            "number":                 i + 1,
            "title":                  f"PR {i + 1}",
            "url":                    f"https://github.com/owner/repo-a/pull/{i + 1}",
            "repository":             "owner/repo-a",
            "status":                 "merged",
            "created_at":             (_NOW - timedelta(days=i + 2)).isoformat(),
            "merged_at":              (_NOW - timedelta(days=i + 1)).isoformat(),
            "lines_added":            100,
            "lines_deleted":          30,
            "files_changed":          2,
            "commit_count":           2,
            "description_length":     80,
            "label_names":            [],
            "base_branch":            "main",
            "reviews_received_count": 1,
        }
        for i in range(6)
    ],
    "reviews_given":   [],
    "reviews_received": [],
    "summary_stats": {
        "total_prs_authored":     6,
        "prs_merged":             6,
        "prs_open":               0,
        "prs_closed_unmerged":    0,
        "total_reviews_given":    0,
        "total_reviews_received": 6,
        "total_lines_added":      600,
        "total_lines_deleted":    180,
        "avg_pr_size_lines":      130.0,
        "active_weeks":           4,
        "total_weeks":            4,
    },
}

TOKEN = "ghp_clean_install_release_test_never_real"
START = datetime(2026, 7, 1, tzinfo=timezone.utc)
END   = datetime(2026, 8, 1, tzinfo=timezone.utc)

# 6. Execute a fully mocked prepare_1on1
with patch("pullstar.engine.ingest_developer_activity", return_value=FAKE_INGEST):
    prepared = prepare_1on1(
        "octodev",
        github_token=TOKEN,
        repositories=["owner/repo-a"],
        start_at=START,
        end_at=END,
    )

assert isinstance(prepared, PreparedOneOnOne), type(prepared)
assert isinstance(prepared.inference, PreparedInference), type(prepared.inference)

# 7. Inspect / serialize PreparedInference
inf = prepared.inference
assert inf.system_prompt, "system_prompt is empty"
assert inf.user_message,  "user_message is empty"
assert inf.prompt_name == "brief_v2", (
    f"Expected default prompt 'brief_v2', got {inf.prompt_name!r}"
)
inf_dict = {
    "system_prompt": inf.system_prompt,
    "user_message":  inf.user_message,
    "prompt_name":   inf.prompt_name,
    "metadata":      inf.metadata,
}
inf_json = json.dumps(inf_dict)  # must not raise
assert TOKEN not in inf_json, "token leaked into serialized inference"
assert "provider" not in inf.metadata, "provider key should be absent"
assert "model"    not in inf.metadata, "model key should be absent"

# 8. Simulate external inference
completion = "## Quick Summary\\n\\nOcto Dev shipped 6 PRs this period."

# 9. Finalize to EngineResult
result = finalize_1on1(prepared, completion)
assert isinstance(result, EngineResult), type(result)
assert result.markdown == completion.strip(), (
    f"Expected {completion.strip()!r}, got {result.markdown!r}"
)
assert result.prompt_name == "brief_v2"
assert result.structured is None
assert TOKEN not in repr(result)
assert TOKEN not in json.dumps(result.metadata)

# 10. Confirm scripts are not needed
try:
    import scripts  # noqa: F401
    raise AssertionError(
        "scripts was importable — sys.path should not include the source tree"
    )
except ImportError:
    pass  # expected

# 11. Confirm .pullstar is not needed
# (the entire test ran without creating or reading any .pullstar directory)
assert not pathlib.Path(".pullstar").exists(), (
    ".pullstar was created during the test — it must not be required"
)

# 12. No cwd assumption — script ran fine from this temp directory
print("OK")
sys.exit(0)
"""


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> Path:
    """Build the pullstar wheel once for this module."""
    out = tmp_path_factory.mktemp("dist_installed")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"Wheel build failed:\n{proc.stdout}\n{proc.stderr}"
    )
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, got: {[w.name for w in wheels]}"
    return wheels[0]


@pytest.fixture(scope="module")
def venv_python(tmp_path_factory, built_wheel) -> Path:
    """Create an isolated venv, install the built wheel, verify pip check passes."""
    venv_dir = tmp_path_factory.mktemp("venv_installed")
    venv.create(str(venv_dir), with_pip=True)

    # Cross-platform Python executable path
    if sys.platform == "win32":
        python = venv_dir / "Scripts" / "python.exe"
    else:
        python = venv_dir / "bin" / "python"

    # Install the wheel (pip resolves only declared dependencies from PyPI)
    proc = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(built_wheel)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"pip install failed:\n{proc.stdout}\n{proc.stderr}"
    )

    # 6. pip check — verify no broken dependencies in the clean environment
    check = subprocess.run(
        [str(python), "-m", "pip", "check"],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, (
        f"pip check failed (broken dependencies in clean install):\n"
        f"{check.stdout}\n{check.stderr}"
    )

    # Verify provider SDKs are NOT installed (they're not engine deps)
    show_anthropic = subprocess.run(
        [str(python), "-m", "pip", "show", "anthropic"],
        capture_output=True, text=True,
    )
    assert show_anthropic.returncode != 0, \
        "anthropic must not be installed as an engine dependency"

    show_openai = subprocess.run(
        [str(python), "-m", "pip", "show", "openai"],
        capture_output=True, text=True,
    )
    assert show_openai.returncode != 0, \
        "openai must not be installed as an engine dependency"

    show_dotenv = subprocess.run(
        [str(python), "-m", "pip", "show", "python-dotenv"],
        capture_output=True, text=True,
    )
    assert show_dotenv.returncode != 0, \
        "python-dotenv must not be installed as an engine dependency"

    return python


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory) -> Path:
    """A clean temp directory completely outside the source tree."""
    return tmp_path_factory.mktemp("run_installed")


# ---------------------------------------------------------------------------
# AD — The release test
# ---------------------------------------------------------------------------


def test_clean_installed_package_release(
    venv_python: Path,
    run_dir: Path,
    tmp_path_factory,
) -> None:
    """AD: full clean installed-package release test outside the source checkout."""
    script_dir = tmp_path_factory.mktemp("script_installed")
    script = script_dir / "release_test.py"
    script.write_text(_RELEASE_TEST_SCRIPT, encoding="utf-8")

    proc = subprocess.run(
        [str(venv_python), str(script)],
        capture_output=True,
        text=True,
        cwd=str(run_dir),  # completely outside the source tree
    )
    assert proc.returncode == 0, (
        f"Clean install release test failed:\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert "OK" in proc.stdout
