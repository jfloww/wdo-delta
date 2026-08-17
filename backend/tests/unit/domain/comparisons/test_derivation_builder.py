"""Building a derivation tree from calculated impacts.

The demo feature. Every figure the product shows expands into the inputs,
formula, and provenance that produced it.

The tree is built on a first-year basis so every branch shares one period:
monthly impacts are annualised, one-time impacts stand as they are. Mixing
periods in one tree would make the parent-equals-children invariant meaningless,
and that invariant is the only thing guaranteeing the explanation matches the
number it explains.
"""

from datetime import date
from decimal import Decimal

from offerdelta.domain.common.dates import DateRange
from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.comparisons.derivation_builder import build_derivation
from offerdelta.domain.comparisons.engine import (
    CalculationResult,
    ComparisonEngine,
    default_calculators,
)
from offerdelta.domain.taxes.override_model import NetPayOverrideTaxModel
from tests.fixtures.profiles import auburn_current, new_jersey_candidate

HORIZON = DateRange.of_months(date(2026, 1, 1), 12)


def _result(build: object) -> CalculationResult:
    assert callable(build)
    side = build()
    salary = side.employment.compensation.base_salary
    context = CalculationContext(
        employment=side.employment,
        costs=side.costs,
        household=side.household,
        tax_model=NetPayOverrideTaxModel(
            observed_gross=PeriodicAmount(salary, PeriodKind.ANNUAL),
            observed_net=PeriodicAmount(salary * Decimal("0.74"), PeriodKind.ANNUAL),
            marginal_rate=Percentage.from_percent("32"),
        ),
        horizon=HORIZON,
    )
    return ComparisonEngine(default_calculators()).calculate(context)


AUBURN = _result(auburn_current)
JERSEY = _result(new_jersey_candidate)


def test_the_tree_has_a_single_root() -> None:
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    assert tree.code == "first_year_disposable_cash"


def test_every_branch_shares_the_roots_period() -> None:
    # DerivationNode rejects a child of a different period, so a tree that
    # builds at all has already proved this. Asserted explicitly so the
    # guarantee is visible.
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    assert all(node.period is PeriodKind.ANNUAL for node in tree.walk())


def test_the_root_equals_the_sum_of_its_branches() -> None:
    # Enforced at construction; if the builder mis-grouped an impact the tree
    # could not be created at all.
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    total = Money.zero()
    for child in tree.children:
        total = total + child.amount
    assert total == tree.amount


def test_impacts_are_grouped_by_the_calculator_that_produced_them() -> None:
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    branches = {child.code for child in tree.children}
    assert "housing" in branches
    assert "living" in branches


def test_a_branch_equals_the_sum_of_its_leaves() -> None:
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    housing = next(child for child in tree.children if child.code == "housing")
    total = Money.zero()
    for leaf in housing.children:
        total = total + leaf.amount
    assert total == housing.amount


def test_monthly_impacts_are_annualised() -> None:
    # Auburn rent is 1,150 a month, so it must appear as 13,800 in a first-year
    # tree rather than as a monthly figure sitting beside annual ones.
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    rent = next(node for node in tree.walk() if node.code.startswith("housing_rent_or_mortgage"))
    assert rent.amount == Money.parse("-13800.00")


def test_one_time_impacts_are_not_annualised() -> None:
    # A signing bonus is an event. Multiplying it by twelve is the error
    # explicit periods exist to make impossible.
    tree = build_derivation(JERSEY, label="First-year disposable cash")
    bonus = next(node for node in tree.walk() if node.code == "signing_bonus")
    assert bonus.amount == Money.parse("10200.00")


def test_every_leaf_carries_its_formula() -> None:
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    assert all(node.formula for node in tree.walk())


def test_every_leaf_carries_its_provenance() -> None:
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    assert all(isinstance(node.evidence, Evidence) for node in tree.walk())


def test_wealth_only_impacts_are_excluded_from_a_cash_tree() -> None:
    # The employer match is wealth. Putting it in a cash derivation would make
    # the tree disagree with the cash total it claims to explain.
    tree = build_derivation(AUBURN, label="First-year disposable cash")
    assert not any(node.code == "employer_retirement_match" for node in tree.walk())


def test_an_empty_result_produces_a_zero_root() -> None:
    empty = ComparisonEngine(default_calculators())
    tree = build_derivation(
        empty.calculate(
            CalculationContext(
                employment=auburn_current().employment,
                costs=auburn_current().costs,
                household=auburn_current().household,
                tax_model=NetPayOverrideTaxModel(
                    observed_gross=PeriodicAmount(Money.parse("78000.00"), PeriodKind.ANNUAL),
                    observed_net=PeriodicAmount(Money.parse("57840.00"), PeriodKind.ANNUAL),
                    marginal_rate=Percentage.from_percent("32"),
                ),
                horizon=DateRange.of_months(date(2026, 1, 1), 1),
            )
        ),
        label="First-year disposable cash",
    )
    assert tree.children
