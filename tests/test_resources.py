"""Requirement 10 — packaged prompt/asset resolves via importlib.resources.

Proven not to depend on the current working directory or the source checkout
being on ``sys.path``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

from pullstar.resources import BRIEF_PROMPT_NAME, brief_prompt_resource, load_brief_prompt


def test_brief_prompt_name_is_versioned() -> None:
    assert BRIEF_PROMPT_NAME == "brief_v1.txt"


def test_load_brief_prompt_content() -> None:
    text = load_brief_prompt()
    assert text.startswith("You are an engineering leadership intelligence tool.")
    assert text == text.strip()
    assert len(text) > 500


def test_brief_prompt_resource_is_readable() -> None:
    assert brief_prompt_resource().read_text(encoding="utf-8").strip() == load_brief_prompt()


def test_prompt_resolves_from_arbitrary_cwd(tmp_path) -> None:
    probe = textwrap.dedent(
        """
        from pullstar.resources import load_brief_prompt
        assert len(load_brief_prompt()) > 500
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
