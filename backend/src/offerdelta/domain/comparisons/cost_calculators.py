"""Calculators that turn cost items into result components.

Every recurring and one-time cost follows the same shape — take the items this
calculator owns, apply the household split where the arrangement says to, and
emit a signed impact — so one implementation parameterised by calculator name
covers housing, health, commute, living, and relocation alike.

Sign is applied here, once. Cost items hold positive magnitudes precisely so
that a single place decides whether a category reduces or increases cash; an
item carrying its own sign would be negated twice by any calculator that also
negates.
"""

from __future__ import annotations

from dataclasses import dataclass

from offerdelta.domain.common.money import Money
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.comparisons.impacts import CostImpact, InputRef
from offerdelta.domain.costs.categories import (
    CalculatorName,
    CostCategory,
    categories_owned_by,
)
from offerdelta.domain.costs.household import HouseholdProfile
from offerdelta.domain.costs.items import CostItem, CostProfile


@dataclass(frozen=True)
class CostItemCalculator:
    """Emits one impact per owned cost item."""

    name: CalculatorName

    def owned_categories(self) -> frozenset[CostCategory]:
        return categories_owned_by(self.name)

    def calculate(self, context: CalculationContext) -> tuple[CostImpact, ...]:
        """The `ComponentCalculator` entry point."""
        return self.calculate_from(context.costs, context.household)

    def calculate_from(
        self, costs: CostProfile, household: HouseholdProfile
    ) -> tuple[CostImpact, ...]:
        """Cost routing needs only these two, so its own tests need only these two.

        Kept separate from `calculate` so a unit test does not have to construct
        an employment profile and a tax model to exercise a rent split.
        """
        items = costs.items_for(self.name)
        return tuple(self._impact(item, household, index) for index, item in enumerate(items))

    def _impact(self, item: CostItem, household: HouseholdProfile, index: int) -> CostImpact:
        full = item.amount.money
        share = household.share_of(full) if item.is_shared else full
        was_split = share != full

        inputs = [InputRef(label="amount", value=str(full))]
        if item.is_shared:
            inputs.append(InputRef(label="household_size", value=str(household.size)))

        return CostImpact(
            # Several items can share a category — two subscriptions, say — so
            # the index keeps codes unique and the derivation shows them apart.
            # Inherited stand-ins get their own code so they never collide with
            # the candidate's own cost in the same category.
            code=(
                f"{item.category.value.lower()}_inherited_{index}"
                if item.is_inherited
                else f"{item.category.value.lower()}_{index}"
            ),
            ends_before=item.ends_before,
            label=_humanise(item.category),
            category=item.category,
            produced_by=self.name,
            period=item.amount.period,
            effective_date=item.effective_date,
            formula_id=(
                f"{item.category.value.lower()}_household_split"
                if was_split
                else f"{item.category.value.lower()}_as_entered"
            ),
            evidence=item.evidence,
            cash_amount=-share,
            wealth_amount=Money.zero(full.currency),
            inputs=tuple(inputs),
            assumption=(f"{household.method} split across {household.size}" if was_split else None),
        )


def _humanise(category: CostCategory) -> str:
    """A readable label, since the derivation tree is read by people."""
    _, _, remainder = category.value.partition("_")
    return remainder.replace("_", " ").capitalize()


def default_cost_calculators() -> tuple[CostItemCalculator, ...]:
    """One calculator per name, together covering every category exactly once."""
    return tuple(CostItemCalculator(name) for name in CalculatorName)
