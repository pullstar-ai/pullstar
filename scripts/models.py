"""Back-compat shim — type definitions moved to ``pullstar.models`` (v0.1.0).

Kept so ``from models import ...`` keeps working for anything that runs with
``scripts/`` on ``sys.path``. New code should import from ``pullstar.models``.
"""

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pullstar.models import (  # noqa: E402,F401
    DimensionScore,
    Flag,
    IngestedProfile,
    Phase1Dimensions,
    PullRequest,
    ReviewGiven,
    ReviewReceived,
    ScoredProfile,
    SkillOutput,
    SummaryStats,
)

__all__ = [
    "PullRequest",
    "ReviewGiven",
    "ReviewReceived",
    "SummaryStats",
    "IngestedProfile",
    "DimensionScore",
    "Phase1Dimensions",
    "Flag",
    "ScoredProfile",
    "SkillOutput",
]
