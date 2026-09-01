"""PullStar 1-on-1 — GitHub activity analysis engine.

PullStar turns one engineer's recent GitHub activity (PRs authored, reviews
given) into a deterministic five-dimension score and a manager-ready 1-on-1
preparation brief.

This package is the reusable analysis engine. Importing it is side-effect free:
no network calls, no credential access, no filesystem artifacts, no UI. The
legacy CLI/Gradio application is a separate entrypoint (``app.py`` / ``scripts/``).

    >>> import pullstar
    >>> pullstar.__version__
    '0.2.0'

Supported engine API (v0.2.0):

    from pullstar.engine import prepare_1on1, finalize_1on1

    prepared = prepare_1on1(developer_login, github_token=token,
                            start_at=start, end_at=end)
    completion = external_inference(prepared.inference.system_prompt,
                                    prepared.inference.user_message)
    result = finalize_1on1(prepared, completion)

Lower-level seams (for advanced/direct use):

    from pullstar.ingest import ingest_developer_activity
    from pullstar.scoring import score_profile
    from pullstar.prompting import build_user_message, build_llm_input_payload
    from pullstar.resources import resolve_brief_prompt, load_brief_prompt
"""

from __future__ import annotations

from importlib import metadata as _metadata

#: Semantic version of record. ``importlib.metadata`` is authoritative when the
#: distribution is installed; this constant is the fallback for running straight
#: from a source checkout (and must match ``[project].version`` in pyproject.toml).
_FALLBACK_VERSION = "0.2.0"

try:
    __version__ = _metadata.version("pullstar")
except _metadata.PackageNotFoundError:  # not installed — running from the source tree
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__"]
