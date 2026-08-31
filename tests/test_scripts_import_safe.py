"""Requirement 7 + Test 9 — the CLI analysis scripts are import-safe.

Importing any ``scripts/*.py`` module must not parse arguments, hit the
network, or create artifacts. Each is loaded by file path in a fresh
subprocess (``scripts/`` is intentionally not a package).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from conftest import REPO_ROOT

SCRIPTS = [
    "ingest.py",
    "score.py",
    "generate_brief.py",
    "agent_prepare_1on1.py",
    "agent_finalize_1on1.py",
    "prompt_builder.py",
    "models.py",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_import_is_safe(script, tmp_path) -> None:
    probe = textwrap.dedent(
        f"""
        import os, sys, importlib.util
        import socket
        def _blocked(*a, **k):
            raise AssertionError("network during import of {script}")
        # Block outbound connections without breaking the socket class (ssl needs it).
        socket.socket.connect = _blocked
        socket.socket.connect_ex = _blocked
        socket.create_connection = _blocked
        socket.getaddrinfo = _blocked

        path = os.path.join(r"{REPO_ROOT}", "scripts", "{script}")
        spec = importlib.util.spec_from_file_location("_probe_mod", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_probe_mod"] = mod
        spec.loader.exec_module(mod)

        assert not os.path.exists(".pullstar"), ".pullstar created on import"
        assert not os.listdir("."), os.listdir(".")
        assert hasattr(mod, "main") or "{script}" in ("prompt_builder.py", "models.py")
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("script", ["score.py", "generate_brief.py", "agent_finalize_1on1.py"])
def test_script_shows_usage_without_args(script) -> None:
    """No login -> argparse exits 2 with a usage message (CLI still wired)."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "usage:" in proc.stderr.lower()
