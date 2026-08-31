"""Access to packaged, non-Python engine assets — the versioned system prompts.

Prompts ship as package data under ``pullstar/prompts/``, one file per version
(``brief_v1.txt``, ``brief_v2.txt``, …). New prompt versions are added by
dropping a ``.txt`` file here and opening a pull request — no code change
needed for the CLI to pick it up.

Read prompts through this module (never by ``Path(__file__)`` guessing or the
caller's working directory) so they resolve from an installed wheel.

Import-safe: importing this module reads nothing from disk.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import NamedTuple

#: The prompt used when ``--prompt`` is not given. The version lives in the
#: filename by design (see PRD §8.1).
DEFAULT_BRIEF_PROMPT = "brief_v1"

#: Reported as the prompt identity when a prompt is loaded from a filesystem
#: path rather than a packaged version.
CUSTOM_PROMPT_NAME = "custom"

_PROMPT_ANCHOR = "pullstar.prompts"

# Back-compat: some earlier code referenced this constant directly.
BRIEF_PROMPT_NAME = f"{DEFAULT_BRIEF_PROMPT}.txt"


class BriefPrompt(NamedTuple):
    """A resolved system prompt: its identity for provenance, and its text."""

    name: str  # e.g. "brief_v1", or "custom" for a filesystem path
    text: str


def _packaged_prompt_stems() -> set[str]:
    """Names (without ``.txt``) of every packaged prompt version."""
    return {
        entry.name[:-4]
        for entry in files(_PROMPT_ANCHOR).iterdir()
        if entry.name.endswith(".txt")
    }


def _has_path_separator(spec: str) -> bool:
    return "/" in spec or "\\" in spec


def resolve_brief_prompt(spec: str | None = None) -> BriefPrompt:
    """Resolve a ``--prompt`` value to a :class:`BriefPrompt`.

    * ``None``  → the default packaged prompt (``brief_v1``).
    * a packaged version name (``"brief_v2"``, with or without ``.txt``, and no
      path separator) → that packaged prompt.
    * anything else → a filesystem path to a prompt file; its identity is
      reported as ``"custom"``.

    A bare name always prefers the packaged version; pass an explicit path
    (``./brief_v2.txt``) to force loading a local file of the same name.

    Raises ``FileNotFoundError`` if the value is neither a packaged version nor
    an existing file.
    """
    if spec is None:
        spec = DEFAULT_BRIEF_PROMPT

    if not _has_path_separator(spec):
        stem = spec[:-4] if spec.endswith(".txt") else spec
        if stem in _packaged_prompt_stems():
            text = (files(_PROMPT_ANCHOR) / f"{stem}.txt").read_text(encoding="utf-8")
            return BriefPrompt(stem, text.strip())

    path = Path(spec).expanduser()
    if not path.is_file():
        available = ", ".join(sorted(_packaged_prompt_stems())) or "(none)"
        raise FileNotFoundError(
            f"Prompt {spec!r} is not a packaged prompt version ({available}) "
            f"and not an existing file."
        )
    return BriefPrompt(CUSTOM_PROMPT_NAME, path.read_text(encoding="utf-8").strip())


def brief_prompt_resource():
    """The default packaged prompt as an :mod:`importlib.resources` handle."""
    return files(_PROMPT_ANCHOR) / BRIEF_PROMPT_NAME


def load_brief_prompt(spec: str | None = None) -> str:
    """Return prompt text (stripped). ``spec`` is passed to :func:`resolve_brief_prompt`."""
    return resolve_brief_prompt(spec).text
