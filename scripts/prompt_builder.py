"""Back-compat shim — the implementation moved to ``pullstar.prompting`` (v0.1.0).

Kept so ``from prompt_builder import ...`` keeps working for anything that runs
with ``scripts/`` on ``sys.path``. New code should import from
``pullstar.prompting`` directly.
"""

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pullstar.prompting import (  # noqa: E402,F401
    build_llm_input_payload,
    build_user_message,
    write_llm_input,
)

__all__ = ["build_user_message", "build_llm_input_payload", "write_llm_input"]
