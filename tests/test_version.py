"""Requirements 1, 2, 13 + Tests 2, 4 — version metadata is deterministic."""

from __future__ import annotations

import importlib.metadata

EXPECTED = "0.2.0"


def test_dunder_version() -> None:
    import pullstar

    assert pullstar.__version__ == EXPECTED


def test_from_pullstar_import_version() -> None:
    from pullstar import __version__

    assert __version__ == EXPECTED


def test_distribution_metadata_version() -> None:
    assert importlib.metadata.version("pullstar") == EXPECTED


def test_distribution_name_and_import_name() -> None:
    dist = importlib.metadata.distribution("pullstar")
    assert dist.metadata["Name"] == "pullstar"
    # import package resolves and is the same project
    import pullstar

    assert pullstar.__name__ == "pullstar"


def test_version_available_without_ui_github_or_llm(monkeypatch) -> None:
    """`__version__` must resolve without importing UI / GitHub / provider code."""
    import sys

    for mod in ("gradio", "github", "anthropic", "openai"):
        monkeypatch.setitem(sys.modules, mod, None)  # force ImportError if touched
    import importlib

    import pullstar

    importlib.reload(pullstar)
    assert pullstar.__version__ == EXPECTED
