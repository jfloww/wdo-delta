"""The monthly cash-flow reconciliation invariant.

This is a cash-flow identity, **not** double-entry bookkeeping. It has no
accounts and no debit/credit pairs, and calling it double-entry would invite a
question it cannot answer.

The check has teeth because it computes a month twice. Once as the plain sum of
every cash impact, and once by classifying each impact into exactly one bucket
and summing the buckets. An impact that lands in two buckets, or in none, makes
the two disagree — and that is the shape of the real defect: a cost consumed by
two calculators, or dropped by all of them.

Two rules make it work:

1. **Employer contributions never appear.** Employer match, employer HSA, and
   the employer share of premiums are not employee cash. They belong to the
   wealth track alone, and including them is the likeliest cause of a residual.
2. **Pre-tax deductions are counted once.** A 401(k) contribution is both a
   deduction and a saving; it belongs to one bucket, never both.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodKind
from offerdelta.domain.comparisons.impacts import CostImpact


class CashBucket(StrEnum):
    """Exactly one classification per impact."""

    INCOME = "INCOME"
    SPENDING = "SPENDING"
    ONE_TIME = "ONE_TIME"

    #: Wealth and time impacts. Deliberately outside the cash identity: employer
    #: money never passes through the employee's account.
    NOT_CASH = "NOT_CASH"


def classify(impact: CostImpact) -> CashBucket:
    """Assign an impact to exactly one bucket.

    Total by construction — every impact gets a bucket — and disjoint, because
    the branches are ordered and mutually exclusive. Those two properties are
    what make the residual meaningful.
    """
    if impact.cash_amount.is_zero():
        return CashBucket.NOT_CASH
    if impact.period is PeriodKind.ONE_TIME:
        return CashBucket.ONE_TIME
    if impact.cash_amount.amount > 0:
        return CashBucket.INCOME
    return CashBucket.SPENDING


def reconcile(impacts: Iterable[CostImpact], stated_total: Money) -> Money:
    """The residual between the bucketed total and the stated total.

    Zero means the month balances. Anything else means an impact was counted
    twice, dropped, or misclassified, and the engine refuses to return the
    result.
    """
    materialised = tuple(impacts)
    currency = stated_total.currency

    bucketed = Money.zero(currency)
    for impact in materialised:
        if classify(impact) is CashBucket.NOT_CASH:
            continue
        bucketed = bucketed + impact.cash_amount

    return bucketed - stated_total
