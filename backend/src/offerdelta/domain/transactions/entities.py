"""Transactions and what they mean.

The classification that matters most is `TRANSFER`. Moving $2,000 from checking
to savings appears in an export twice — once out, once in — and a system that
reads those as spending and income will report that you spent $2,000 you still
have and earned $2,000 you already had. Both totals are then wrong, in opposite
directions, and they look plausible.

That is the same double-counting the cost taxonomy guards against arriving
through a different door, so it gets the same treatment: transfers are their own
kind and are excluded from both totals. Refunds get the same reasoning — money
back from a returned purchase reduces spending; calling it income would inflate
both sides of the ledger at once.

Sign convention: money out is negative, money in is positive, exactly as banks
export it. Enforcing that at construction means no calculator downstream has to
guess which way a row points.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import cached_property

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.costs.categories import CostCategory
from offerdelta.domain.transactions.parsing import normalise_description


class TransactionKind(StrEnum):
    """What a row actually represents."""

    #: Money genuinely leaving. Carries a cost category.
    SPENDING = "SPENDING"

    #: Money genuinely arriving — salary, interest, a gift.
    INCOME = "INCOME"

    #: Movement between the user's own accounts. Real in the export, invisible
    #: in every total, because nothing was earned or spent.
    TRANSFER = "TRANSFER"

    #: Money back from a returned purchase. Offsets spending, is not income.
    REFUND = "REFUND"


@dataclass(frozen=True)
class Transaction:
    """One row of a bank or card export."""

    posted_on: date
    description: str
    amount: Money
    account: str
    kind: TransactionKind
    category: CostCategory | None = None

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValidationError("a transaction needs a description")

        if self.kind is TransactionKind.SPENDING:
            if self.category is None:
                raise ValidationError(
                    "a SPENDING transaction needs a category; an uncategorised "
                    "outflow vanishes from every total, which is worse than "
                    "refusing it"
                )
            if self.amount.amount > 0:
                raise ValidationError(
                    f"a SPENDING transaction is money out and must not be "
                    f"positive, got {self.amount}"
                )
        elif self.category is not None:
            raise ValidationError(
                f"only SPENDING carries a cost category; {self.kind} was given {self.category}"
            )

        if self.kind is TransactionKind.INCOME and self.amount.amount < 0:
            raise ValidationError(
                f"an INCOME transaction is money in and must not be negative, got {self.amount}"
            )

    @cached_property
    def normalised_description(self) -> str:
        """A key that survives re-billing, for recurrence matching.

        The raw description is kept because normalisation is lossy and the
        original is what a user recognises on their statement.
        """
        return normalise_description(self.description)


def total_spending(transactions: Iterable[Transaction]) -> Money:
    """What actually left, as a positive magnitude, net of refunds.

    Transfers are excluded by construction.
    """
    total = Money.zero()
    for txn in transactions:
        if txn.kind is TransactionKind.SPENDING:
            total = total + abs(txn.amount)
        elif txn.kind is TransactionKind.REFUND:
            total = total - abs(txn.amount)
    return total


def total_income(transactions: Iterable[Transaction]) -> Money:
    """What actually arrived. Transfers and refunds are not income."""
    total = Money.zero()
    for txn in transactions:
        if txn.kind is TransactionKind.INCOME:
            total = total + txn.amount
    return total


def net_cash_flow(transactions: Iterable[Transaction]) -> Money:
    """Income less spending. A transfer pair nets to nothing, as it should."""
    materialised = tuple(transactions)
    return total_income(materialised) - total_spending(materialised)
