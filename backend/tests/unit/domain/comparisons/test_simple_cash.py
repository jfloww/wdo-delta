"""The walking skeleton's calculation.

Deliberately the smallest real calculation that produces a derivation tree:
monthly net pay less housing, commute, and living costs. It exists so the
skeleton renders a figure that came from domain code rather than a literal.

Milestone 3 replaces this with the composed calculator engine, and milestone 2
replaces the loose arguments with cost items that carry their own category,
period, and evidence.
"""

from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodKind
from offerdelta.domain.comparisons.simple_cash import monthly_disposable_cash


def _tree() -> object:
    return monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.parse("1150.00"),
        utilities=Money.parse("185.00"),
        commute=Money.parse("240.00"),
        living=Money.parse("910.00"),
    )


def test_disposable_cash_is_net_pay_less_every_cost() -> None:
    result = monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.parse("1150.00"),
        utilities=Money.parse("185.00"),
        commute=Money.parse("240.00"),
        living=Money.parse("910.00"),
    )
    assert result.amount == Money.parse("2335.00")


def test_the_root_is_monthly() -> None:
    result = monthly_disposable_cash(
        net_pay=Money.parse("1000.00"),
        rent=Money.zero(),
        utilities=Money.zero(),
        commute=Money.zero(),
        living=Money.zero(),
    )
    assert result.period is PeriodKind.MONTHLY


def test_with_no_costs_disposable_cash_is_all_of_net_pay() -> None:
    result = monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.zero(),
        utilities=Money.zero(),
        commute=Money.zero(),
        living=Money.zero(),
    )
    assert result.amount == Money.parse("4820.00")


def test_costs_appear_as_negative_children() -> None:
    result = monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.parse("1150.00"),
        utilities=Money.parse("185.00"),
        commute=Money.parse("240.00"),
        living=Money.parse("910.00"),
    )
    by_code = {node.code: node for node in result.walk()}
    assert by_code["commute"].amount == Money.parse("-240.00")


def test_housing_groups_rent_and_utilities() -> None:
    result = monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.parse("1150.00"),
        utilities=Money.parse("185.00"),
        commute=Money.parse("240.00"),
        living=Money.parse("910.00"),
    )
    by_code = {node.code: node for node in result.walk()}
    assert by_code["housing"].amount == Money.parse("-1335.00")
    assert {child.code for child in by_code["housing"].children} == {"rent", "utilities"}


def test_verified_net_pay_is_marked_user_confirmed() -> None:
    result = monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.parse("1150.00"),
        utilities=Money.parse("185.00"),
        commute=Money.parse("240.00"),
        living=Money.parse("910.00"),
    )
    by_code = {node.code: node for node in result.walk()}
    assert by_code["net_pay"].evidence is Evidence.USER_CONFIRMED


def test_estimated_costs_are_marked_assumed() -> None:
    result = monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.parse("1150.00"),
        utilities=Money.parse("185.00"),
        commute=Money.parse("240.00"),
        living=Money.parse("910.00"),
    )
    by_code = {node.code: node for node in result.walk()}
    assert by_code["utilities"].evidence is Evidence.ASSUMED


def test_every_node_carries_a_formula() -> None:
    result = monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.parse("1150.00"),
        utilities=Money.parse("185.00"),
        commute=Money.parse("240.00"),
        living=Money.parse("910.00"),
    )
    assert all(node.formula for node in result.walk())


def test_the_tree_is_internally_consistent() -> None:
    # DerivationNode rejects a parent that disagrees with its children, so a
    # tree that constructs at all has already proved it adds up. This asserts
    # the property explicitly so the guarantee is visible in the test names.
    result = monthly_disposable_cash(
        net_pay=Money.parse("4820.00"),
        rent=Money.parse("1150.00"),
        utilities=Money.parse("185.00"),
        commute=Money.parse("240.00"),
        living=Money.parse("910.00"),
    )
    for node in result.walk():
        if node.children:
            total = Money.zero()
            for child in node.children:
                total = total + child.amount
            assert total == node.amount
