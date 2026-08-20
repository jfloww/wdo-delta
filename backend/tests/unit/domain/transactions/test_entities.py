"""Transactions and what they mean.

The classification that matters most is TRANSFER. Moving $2,000 from checking
to savings appears in an export twice — once as money out, once as money in —
and a system that treats those as spending and income respectively will report
that you spent $2,000 you still have and earned $2,000 you already had.

That is the same double-counting the cost taxonomy guards against, arriving
through a different door, so it gets the same treatment: transfers are their
own kind and are excluded from both totals.
"""

from datetime import date

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.costs.categories import CostCategory
from offerdelta.domain.transactions.entities import (
    Transaction,
    TransactionKind,
    net_cash_flow,
    total_income,
    total_spending,
)


def _txn(
    amount: str,
    kind: TransactionKind = TransactionKind.SPENDING,
    description: str = "BLUE BOTTLE",
    category: CostCategory | None = CostCategory.LIVING_DINING,
    posted: date = date(2026, 8, 1),
) -> Transaction:
    return Transaction(
        posted_on=posted,
        description=description,
        amount=Money.parse(amount),
        account="checking",
        kind=kind,
        category=category if kind is TransactionKind.SPENDING else None,
    )


# --- Shape -----------------------------------------------------------------


def test_a_transaction_normalises_its_description() -> None:
    txn = _txn("-4.50", description="SQ *BLUE BOTTLE 4412")
    assert txn.normalised_description == "BLUE BOTTLE"


def test_the_raw_description_is_kept() -> None:
    # Normalisation is lossy, and the original is what a user recognises on
    # their statement.
    txn = _txn("-4.50", description="SQ *BLUE BOTTLE 4412")
    assert txn.description == "SQ *BLUE BOTTLE 4412"


def test_spending_is_negative_by_convention() -> None:
    assert _txn("-4.50").amount.amount < 0


def test_a_transaction_is_immutable() -> None:
    txn = _txn("-4.50")
    with pytest.raises(AttributeError):
        txn.amount = Money.zero()  # type: ignore[misc]


# --- Kind rules ------------------------------------------------------------


def test_spending_requires_a_category() -> None:
    # An uncategorised outflow silently vanishes from every total, which is
    # worse than refusing it.
    with pytest.raises(ValidationError, match="category"):
        Transaction(
            posted_on=date(2026, 8, 1),
            description="MYSTERY",
            amount=Money.parse("-4.50"),
            account="checking",
            kind=TransactionKind.SPENDING,
            category=None,
        )


def test_income_carries_no_cost_category() -> None:
    with pytest.raises(ValidationError, match="category"):
        Transaction(
            posted_on=date(2026, 8, 1),
            description="PAYROLL",
            amount=Money.parse("3000.00"),
            account="checking",
            kind=TransactionKind.INCOME,
            category=CostCategory.LIVING_OTHER,
        )


def test_spending_must_not_be_positive() -> None:
    with pytest.raises(ValidationError, match="SPENDING"):
        _txn("4.50")


def test_income_must_not_be_negative() -> None:
    with pytest.raises(ValidationError, match="INCOME"):
        _txn("-3000.00", kind=TransactionKind.INCOME, category=None)


def test_a_transfer_may_go_either_way() -> None:
    # Both legs are transfers; neither is spending or income.
    assert _txn("-2000.00", kind=TransactionKind.TRANSFER, category=None)
    assert _txn("2000.00", kind=TransactionKind.TRANSFER, category=None)


# --- Totals ----------------------------------------------------------------


def test_spending_totals_ignore_transfers() -> None:
    # The whole reason TRANSFER exists.
    txns = [
        _txn("-4.50"),
        _txn("-2000.00", kind=TransactionKind.TRANSFER, category=None),
    ]
    assert total_spending(txns) == Money.parse("4.50")


def test_income_totals_ignore_transfers() -> None:
    txns = [
        _txn("3000.00", kind=TransactionKind.INCOME, category=None),
        _txn("2000.00", kind=TransactionKind.TRANSFER, category=None),
    ]
    assert total_income(txns) == Money.parse("3000.00")


def test_a_transfer_pair_nets_to_nothing() -> None:
    # Money moved between your own accounts changed neither what you earned nor
    # what you spent.
    txns = [
        _txn("-2000.00", kind=TransactionKind.TRANSFER, category=None),
        _txn("2000.00", kind=TransactionKind.TRANSFER, category=None, posted=date(2026, 8, 2)),
    ]
    assert net_cash_flow(txns).is_zero()
    assert total_spending(txns).is_zero()
    assert total_income(txns).is_zero()


def test_net_cash_flow_is_income_less_spending() -> None:
    txns = [
        _txn("3000.00", kind=TransactionKind.INCOME, category=None),
        _txn("-1200.00", category=CostCategory.HOUSING_RENT_OR_MORTGAGE),
    ]
    assert net_cash_flow(txns) == Money.parse("1800.00")


def test_totals_of_nothing_are_zero() -> None:
    assert total_spending([]).is_zero()
    assert total_income([]).is_zero()
    assert net_cash_flow([]).is_zero()


def test_a_refund_reduces_spending_rather_than_adding_income() -> None:
    # A returned purchase is not earnings. Counting it as income would inflate
    # both sides of the ledger.
    txns = [_txn("-50.00"), _txn("20.00", kind=TransactionKind.REFUND, category=None)]
    assert total_spending(txns) == Money.parse("30.00")
    assert total_income(txns).is_zero()
