"""PullStar 1-on-1 — GitHub activity analysis engine.

PullStar turns one engineer's recent GitHub activity (PRs authored, reviews
given) into a deterministic five-dimension score and a manager-ready 1-on-1
preparation brief.

This package is the reusable analysis engine. Importing it is side-effect free:
no network calls, no credential access, no filesystem artifacts, no UI. The
CLI/Gradio application is a separate entrypoint (``app.py`` / ``scripts/``).

    >>> import pullstar
    >>> pullstar.__version__
    '0.1.0'

Engine seams live in submodules and are imported explicitly by the caller:

    from pullstar.scoring import score_velocity, score_pr_quality, ...
    from pullstar.prompting import build_user_message, build_llm_input_payload
    from pullstar.resources import load_brief_prompt
"""

from __future__ import annotations

from importlib import metadata as _metadata

#: Semantic version of record. ``importlib.metadata`` is authoritative when the
#: distribution is installed; this constant is the fallback for running straight
#: from a source checkout (and must match ``[project].version`` in pyproject.toml).
_FALLBACK_VERSION = "0.1.0"

try:
    __version__ = _metadata.version("pullstar")
except _metadata.PackageNotFoundError:  # not installed — running from the source tree
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__"]
