"""What a calculator is given, and what every calculator looks like.

External data is resolved before the engine runs. Calculators are pure
functions of this context — they never query a database, call a service, or
read the clock — which is what makes a comparison run reproducible from its
stored inputs alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from offerdelta.domain.common.dates import DateRange
from offerdelta.domain.comparisons.impacts import CostImpact
from offerdelta.domain.costs.categories import CalculatorName, CostCategory
from offerdelta.domain.costs.household import HouseholdProfile
from offerdelta.domain.costs.items import CostProfile
from offerdelta.domain.employment.profile import EmploymentProfile
from offerdelta.domain.taxes.ports import TaxModel


@dataclass(frozen=True)
class CalculationContext:
    """Everything one side of a comparison needs, resolved up front."""

    employment: EmploymentProfile
    costs: CostProfile
    household: HouseholdProfile
    tax_model: TaxModel
    horizon: DateRange

    #: Zero by default, and stated explicitly rather than left implicit. Summing
    #: multi-year cash flows at a hidden zero rate reads as an oversight; an
    #: explicit zero reads as a decision, and it appears in the derivation.
    discount_rate_annual: Decimal = Decimal(0)


class ComponentCalculator(Protocol):
    """One contributor to a comparison result."""

    @property
    def name(self) -> CalculatorName: ...

    def owned_categories(self) -> frozenset[CostCategory]:
        """The cost categories this calculator consumes, possibly none.

        Income and benefit calculators own no categories: they derive their
        figures from the employment profile rather than from cost items. The
        partition check only concerns calculators that consume costs.
        """
        ...

    def calculate(self, context: CalculationContext) -> tuple[CostImpact, ...]: ...
