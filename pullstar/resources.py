"""Access to packaged, non-Python engine assets.

The system prompt ships as package data (``pullstar/prompts/brief_v1.txt``).
Read it through this module so it resolves whether PullStar is run from a
source checkout or an installed wheel — never by ``Path(__file__)`` guessing
or the caller's current working directory.

Import-safe: importing this module reads nothing from disk.
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable

#: Filename of the current system-prompt version. The version lives in the
#: filename by design (see PRD §8.1) — bump the file, not this string in place.
BRIEF_PROMPT_NAME = "brief_v1.txt"

_PROMPT_ANCHOR = "pullstar.prompts"


def brief_prompt_resource() -> Traversable:
    """Return the packaged system prompt as an :class:`importlib.resources` handle."""
    return files(_PROMPT_ANCHOR) / BRIEF_PROMPT_NAME


def load_brief_prompt() -> str:
    """Return the system-prompt text, stripped — matching the historical CLI behavior."""
    return brief_prompt_resource().read_text(encoding="utf-8").strip()
