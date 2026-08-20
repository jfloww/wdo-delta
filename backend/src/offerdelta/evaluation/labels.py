"""The label space a categoriser predicts into.

Derived from `CostCategory` rather than restated, so the taxonomy has exactly
one definition. A label list that drifts from the categories the engine
consumes would let a model score well on categories nothing downstream can use.

Three labels sit outside the cost taxonomy because they are not costs:

- `INCOME` and `TRANSFER` and `REFUND` describe what a row *is*, and a model
  that cannot say `TRANSFER` will call a savings transfer spending — the exact
  double count the taxonomy exists to prevent.
- `UNKNOWN` is abstention. A model that declines to guess is more useful than
  one that guesses confidently, so abstention must be expressible in order to
  be measurable, and it is reported as coverage rather than hidden in the
  error rate.
"""

from __future__ import annotations

from typing import Final

from offerdelta.domain.costs.categories import CostCategory

#: What a row is, when it is not a cost.
NON_SPENDING_LABELS: Final[frozenset[str]] = frozenset({"INCOME", "TRANSFER", "REFUND"})

#: Explicit abstention. Counted as coverage, never as a wrong answer.
ABSTAIN: Final = "UNKNOWN"

SPENDING_LABELS: Final[frozenset[str]] = frozenset(c.value for c in CostCategory)

LABEL_SPACE: Final[frozenset[str]] = SPENDING_LABELS | NON_SPENDING_LABELS | frozenset({ABSTAIN})


def is_valid_label(label: str) -> bool:
    return label in LABEL_SPACE


def is_abstention(label: str) -> bool:
    return label == ABSTAIN
