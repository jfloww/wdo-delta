"""Reference profiles, re-exported from the source tree.

The profiles moved to `offerdelta.demo.profiles` when the deployed service began
serving them — production code cannot import from `tests/`, which the container
image excludes entirely.

This module stays as the import path the test suite already uses, so the demo
and the golden fixtures are guaranteed to describe the same data.
"""

from offerdelta.demo.profiles import (
    MOVE_DATE,
    START,
    TAX_YEAR,
    ComparisonSide,
    auburn_current,
    new_jersey_candidate,
)

__all__ = [
    "MOVE_DATE",
    "START",
    "TAX_YEAR",
    "ComparisonSide",
    "auburn_current",
    "new_jersey_candidate",
]
