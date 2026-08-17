"""Derivation trees.

Every figure the product shows can be expanded to reveal the inputs, formula,
and provenance behind it. This is the strongest demo feature in the project and
the reason a viewer can trust the numbers, so it is a first-class domain type
rather than a presentation concern.

A node with children must equal the sum of those children. Child amounts are
signed — income positive, costs negative — so the whole tree is one addition.
That invariant is the seed of the monthly cash-flow reconciliation check the
engine enforces in milestone 3.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodKind


@dataclass(frozen=True)
class DerivationNode:
    """One step in the explanation of a calculated figure."""

    code: str
    label: str
    amount: Money
    period: PeriodKind
    formula: str
    evidence: Evidence
    children: tuple[DerivationNode, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.children:
            return

        for child in self.children:
            if child.period is not self.period:
                raise ValidationError(
                    f"derivation node {self.code!r} has period {self.period} but "
                    f"child {child.code!r} has period {child.period}"
                )

        total = Money.zero(self.amount.currency)
        for child in self.children:
            total = total + child.amount
        if total != self.amount:
            raise ValidationError(
                f"derivation node {self.code!r} does not equal the sum of its "
                f"children: stated {self.amount}, children sum to {total}"
            )

    def walk(self) -> Iterator[DerivationNode]:
        """Yield this node then every descendant, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()
