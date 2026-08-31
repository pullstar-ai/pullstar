"""CLI wrapper — ``python scripts/score.py --login <login>``.

The scoring implementation moved into the importable package at
``pullstar.scoring`` (v0.1.0). This wrapper only preserves the historical
command path. Import the real thing with::

    from pullstar.scoring import score_velocity, score_pr_quality, main
"""

import sys
from pathlib import Path

# Allow ``python scripts/score.py`` from a bare checkout (no install needed).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pullstar.scoring import main  # noqa: E402

if __name__ == "__main__":
    main()
