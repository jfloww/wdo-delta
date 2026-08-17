"""Result components.

Every calculator emits `CostImpact` values rather than bare numbers. Each one
carries the category it came from, its effect on each of the tracks reported
separately, and the provenance and formula a derivation needs.

This representation is what lets an explanation be assembled from stored facts
instead of asking a language model to reconstruct a calculation it never saw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodKind
from offerdelta.domain.costs.categories import CalculatorName, CostCategory


@dataclass(frozen=True)
class InputRef:
    """One value that fed a calculation, named so a derivation can show it."""

    label: str
    value: str


@dataclass(frozen=True)
class CostImpact:
    """One calculated effect, decomposed across the tracks reported separately.

    Cash, wealth, and time are kept apart deliberately. Collapsing them into a
    single score is the thing this product exists not to do: an offer paying
    more cash while costing 250 unpaid commuting hours is not simply "better".
    """

    code: str
    label: str
    category: CostCategory
    produced_by: CalculatorName
    period: PeriodKind
    effective_date: date
    formula_id: str
    evidence: Evidence

    cash_amount: Money = field(default_factory=Money.zero)
    wealth_amount: Money = field(default_factory=Money.zero)
    time_hours: Decimal = Decimal(0)

    #: When this impact stops applying, exclusive. Set for a cost inherited
    #: from the other side up to a move date; None means it runs to the horizon.
    ends_before: date | None = None

    inputs: tuple[InputRef, ...] = ()
    rounding_policy: str | None = None
    rule_version: str | None = None
    dataset_version: str | None = None
    assumption: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValidationError("a cost impact needs a non-empty code")
        if not self.formula_id.strip():
            raise ValidationError(
                f"cost impact {self.code!r} needs a formula_id; a derivation "
                f"cannot explain a figure with no stated formula"
            )
