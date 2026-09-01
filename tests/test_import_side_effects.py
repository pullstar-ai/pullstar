"""Requirement 3 + Tests 5, 6, 7, 8 — `import pullstar` has no side effects.

Run in a fresh subprocess so the check is not polluted by pytest's own imports.
Network access is monkeypatched to raise; the working directory is a scratch
dir so a stray ``.pullstar`` artifact would be visible.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

_PROBE = textwrap.dedent(
    """
    import sys, os

    # Any socket use during import is a failure.
    import socket
    def _blocked(*a, **k):
        raise AssertionError("network access during 'import pullstar'")
    socket.socket = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked

    before = set(sys.modules)
    import pullstar
    new = set(sys.modules) - before

    assert pullstar.__version__ == "0.2.0", pullstar.__version__

    # No UI, no GitHub client, no LLM SDKs, no HTTP stack, no arg parsing.
    forbidden = {"gradio", "github", "anthropic", "openai", "requests",
                 "httpx", "argparse", "dotenv"}
    leaked = forbidden & {m.split(".")[0] for m in new}
    assert not leaked, f"import pullstar pulled in: {sorted(leaked)}"

    # Submodules must not be imported eagerly by the package __init__.
    assert "pullstar.scoring" not in sys.modules
    assert "pullstar.prompting" not in sys.modules

    # No runtime artifacts created.
    assert not os.path.exists(".pullstar"), ".pullstar created on import"
    assert not os.listdir("."), f"import created files: {os.listdir('.')}"

    print("OK")
    """
)


def test_import_pullstar_is_side_effect_free(tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().endswith("OK")


def test_import_resources_is_side_effect_free(tmp_path) -> None:
    probe = textwrap.dedent(
        """
        import os
        from pullstar import resources
        from pullstar import models, scoring, prompting  # engine seams, stdlib-only
        assert not os.listdir("."), os.listdir(".")
        # scoring/prompting import argparse/json internally but never at import touch I/O
        text = resources.load_brief_prompt()
        assert text and not text.startswith(" ") and not text.endswith(" ")
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
