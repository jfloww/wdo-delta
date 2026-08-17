"""Costs before the move.

The gap this closes: a candidate profile's costs start on the move date, so
without this the engine models someone who lives nowhere until July — no rent,
no groceries, no utilities. That artificial saving made the New Jersey offer
look cheap enough to match Auburn on a *lower* salary, which is nonsense for a
location with more than double the rent.

You still live somewhere before you move. The candidate side therefore inherits
the current side's recurring costs up to the move date, then switches to its
own. Inherited items are marked so a derivation can show where they came from.
"""

from datetime import date

from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.pre_move import inherit_costs_until_move
from offerdelta.domain.costs.categories import CashFlowType, CostCategory
from offerdelta.domain.costs.items import CostItem, CostProfile

MOVE = date(2026, 7, 1)
START = date(2026, 1, 1)


def _recurring(category: CostCategory, amount: str, effective: date) -> CostItem:
    return CostItem(
        category=category,
        amount=PeriodicAmount(Money.parse(amount), PeriodKind.MONTHLY),
        cash_flow_type=CashFlowType.RECURRING_CASH,
        effective_date=effective,
        evidence=Evidence.USER_CONFIRMED,
    )


def _one_time(category: CostCategory, amount: str) -> CostItem:
    return CostItem(
        category=category,
        amount=PeriodicAmount(Money.parse(amount), PeriodKind.ONE_TIME),
        cash_flow_type=CashFlowType.ONE_TIME_CASH,
        effective_date=MOVE,
        evidence=Evidence.ASSUMED,
    )


CURRENT = CostProfile(
    items=(
        _recurring(CostCategory.HOUSING_RENT_OR_MORTGAGE, "1150.00", START),
        _recurring(CostCategory.LIVING_GROCERY, "420.00", START),
    )
)

CANDIDATE = CostProfile(
    items=(
        _recurring(CostCategory.HOUSING_RENT_OR_MORTGAGE, "2850.00", MOVE),
        _recurring(CostCategory.LIVING_GROCERY, "520.00", MOVE),
        _one_time(CostCategory.RELOCATION_DEPOSIT, "5700.00"),
    )
)


def test_the_candidate_keeps_its_own_costs_from_the_move_onward() -> None:
    merged = inherit_costs_until_move(current=CURRENT, candidate=CANDIDATE, move_date=MOVE)
    rents = [
        item for item in merged.items if item.category is CostCategory.HOUSING_RENT_OR_MORTGAGE
    ]
    assert any(item.amount.money == Money.parse("2850.00") for item in rents)


def test_the_candidate_inherits_the_current_costs_before_the_move() -> None:
    merged = inherit_costs_until_move(current=CURRENT, candidate=CANDIDATE, move_date=MOVE)
    rents = [
        item for item in merged.items if item.category is CostCategory.HOUSING_RENT_OR_MORTGAGE
    ]
    assert any(item.amount.money == Money.parse("1150.00") for item in rents)


def test_an_inherited_cost_ends_when_the_new_one_begins() -> None:
    merged = inherit_costs_until_move(current=CURRENT, candidate=CANDIDATE, move_date=MOVE)
    inherited = next(item for item in merged.items if item.amount.money == Money.parse("1150.00"))
    assert inherited.effective_date == START
    assert inherited.ends_before == MOVE


def test_an_inherited_cost_is_marked_as_inherited() -> None:
    # So a derivation can say "this is your Auburn rent, still being paid".
    merged = inherit_costs_until_move(current=CURRENT, candidate=CANDIDATE, move_date=MOVE)
    inherited = next(item for item in merged.items if item.amount.money == Money.parse("1150.00"))
    assert inherited.is_inherited is True


def test_one_time_costs_are_never_inherited() -> None:
    # A relocation deposit belongs to the move, not to the life before it.
    merged = inherit_costs_until_move(current=CURRENT, candidate=CANDIDATE, move_date=MOVE)
    deposits = [item for item in merged.items if item.category is CostCategory.RELOCATION_DEPOSIT]
    assert len(deposits) == 1


def test_a_move_on_the_horizon_start_inherits_nothing() -> None:
    # Moving immediately means there is no "before".
    merged = inherit_costs_until_move(current=CURRENT, candidate=CANDIDATE, move_date=START)
    assert not any(item.is_inherited for item in merged.items)


def test_the_merge_never_loses_a_candidate_item() -> None:
    merged = inherit_costs_until_move(current=CURRENT, candidate=CANDIDATE, move_date=MOVE)
    for original in CANDIDATE.items:
        assert original in merged.items


def test_an_item_active_before_and_after_is_not_duplicated() -> None:
    # A candidate cost that already starts at the horizon start needs no
    # stand-in, or the month before the move would be charged twice.
    candidate = CostProfile(items=(_recurring(CostCategory.LIVING_PHONE, "55.00", START),))
    merged = inherit_costs_until_move(
        current=CostProfile(items=(_recurring(CostCategory.LIVING_PHONE, "45.00", START),)),
        candidate=candidate,
        move_date=MOVE,
    )
    phones = [item for item in merged.items if item.category is CostCategory.LIVING_PHONE]
    assert len(phones) == 1
