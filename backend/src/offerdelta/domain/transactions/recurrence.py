"""Detecting recurring payments.

Deliberately deterministic, and deliberately first. This is the baseline an LLM
has to beat: "the model scores 0.91 F1" is not a result until it sits next to
"and interval matching scores 0.87". Shipping the model without the baseline
means never learning whether it earned its cost and latency.

Two signals, both required:

- **Regular gaps.** The intervals between occurrences cluster around a cadence.
- **Stable amounts.** Grocery runs every few days at wildly different amounts
  are a habit, not a commitment; an annual premium at a fixed amount is a
  commitment despite appearing once a year.

Transfers are excluded outright. A monthly transfer to savings is perfectly
regular and perfectly stable, and prompting someone to cancel it would be
actively bad advice.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from statistics import median
from typing import Final

from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.transactions.entities import Transaction, TransactionKind

#: Three points is the minimum that can show a *pattern* rather than a single
#: interval. Two coffees a month apart are not a subscription.
_MIN_OCCURRENCES: Final = 3

#: How far a median gap may sit from a nominal cadence and still match. Real
#: billing lands on business days, so a monthly charge wobbles by several days.
_CADENCE_TOLERANCE: Final = Decimal("0.25")

#: How far individual gaps may stray from their own median. Above this the
#: series is irregular rather than jittered.
_REGULARITY_TOLERANCE: Final = Decimal("0.35")

#: Permitted spread in amount, as a share of the median. Wide enough for a
#: price rise, narrow enough to exclude variable spending.
_AMOUNT_TOLERANCE: Final = Decimal("0.25")

#: Occurrences at which confidence saturates.
_CONFIDENCE_CEILING: Final = Decimal(12)


class Cadence(StrEnum):
    WEEKLY = "WEEKLY"
    FORTNIGHTLY = "FORTNIGHTLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


_NOMINAL_DAYS: Final[dict[Cadence, int]] = {
    Cadence.WEEKLY: 7,
    Cadence.FORTNIGHTLY: 14,
    Cadence.MONTHLY: 30,
    Cadence.QUARTERLY: 91,
    Cadence.ANNUAL: 365,
}

_PER_YEAR: Final[dict[Cadence, int]] = {
    Cadence.WEEKLY: 52,
    Cadence.FORTNIGHTLY: 26,
    Cadence.MONTHLY: 12,
    Cadence.QUARTERLY: 4,
    Cadence.ANNUAL: 1,
}


@dataclass(frozen=True)
class RecurringCharge:
    """A merchant billing on a regular cadence."""

    merchant: str
    cadence: Cadence
    occurrences: int
    typical_amount: Money
    latest_amount: Money
    annual_cost: Money
    confidence: Percentage

    #: True when the most recent charge differs from the typical one — the
    #: signal behind "this went up and you may not have noticed".
    price_changed: bool


def detect_recurring(transactions: Iterable[Transaction]) -> list[RecurringCharge]:
    """Find recurring charges, most expensive first."""
    grouped: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        # A savings transfer is regular and stable and must never be offered up
        # for cancellation.
        if txn.kind is TransactionKind.TRANSFER:
            continue
        grouped[txn.normalised_description].append(txn)

    found = [
        charge
        for merchant, group in grouped.items()
        if (charge := _classify(merchant, sorted(group, key=lambda t: t.posted_on)))
    ]

    # A review list is read top-down, and the expensive one is worth acting on.
    found.sort(key=lambda c: abs(c.annual_cost.amount), reverse=True)
    return found


def _classify(merchant: str, group: Sequence[Transaction]) -> RecurringCharge | None:
    if len(group) < _MIN_OCCURRENCES:
        return None

    gaps = [
        Decimal((later.posted_on - earlier.posted_on).days) for earlier, later in pairwise(group)
    ]
    if any(gap <= 0 for gap in gaps):
        return None  # same-day repeats are not a cadence

    median_gap = Decimal(median(gaps))
    if not _is_regular(gaps, median_gap):
        return None

    cadence = _nearest_cadence(median_gap)
    if cadence is None:
        return None

    amounts = [txn.amount.amount for txn in group]
    typical = Decimal(median(amounts))
    if not _amounts_stable(amounts, typical):
        return None

    currency = group[0].amount.currency
    typical_money = Money(typical, currency)
    latest_money = group[-1].amount

    return RecurringCharge(
        merchant=merchant,
        cadence=cadence,
        occurrences=len(group),
        typical_amount=typical_money,
        latest_amount=latest_money,
        annual_cost=latest_money * _PER_YEAR[cadence],
        confidence=_confidence(len(group), gaps, median_gap),
        price_changed=latest_money != typical_money,
    )


def _is_regular(gaps: Sequence[Decimal], median_gap: Decimal) -> bool:
    """Whether the intervals cluster tightly enough to call a cadence."""
    if median_gap <= 0:
        return False
    return all(abs(gap - median_gap) / median_gap <= _REGULARITY_TOLERANCE for gap in gaps)


def _nearest_cadence(median_gap: Decimal) -> Cadence | None:
    for cadence, nominal in _NOMINAL_DAYS.items():
        if abs(median_gap - nominal) / Decimal(nominal) <= _CADENCE_TOLERANCE:
            return cadence
    return None


def _amounts_stable(amounts: Sequence[Decimal], typical: Decimal) -> bool:
    """Whether the charges are consistent enough to be one commitment.

    Tolerant enough to survive a price rise, tight enough to exclude variable
    spending at a regular merchant.
    """
    if typical == 0:
        return all(amount == 0 for amount in amounts)
    return all(abs(amount - typical) / abs(typical) <= _AMOUNT_TOLERANCE for amount in amounts)


def _confidence(occurrences: int, gaps: Sequence[Decimal], median_gap: Decimal) -> Percentage:
    """More occurrences and tighter intervals mean more confidence.

    Capped below certainty: this is an inference from a pattern, and a detector
    that ever claims 100% is one nobody will question when it is wrong.
    """
    depth = min(Decimal(occurrences), _CONFIDENCE_CEILING) / _CONFIDENCE_CEILING
    drift = max(abs(gap - median_gap) / median_gap for gap in gaps)
    tightness = max(Decimal(0), Decimal(1) - drift / _REGULARITY_TOLERANCE)
    score = (depth * Decimal("0.7")) + (tightness * Decimal("0.3"))
    return Percentage(min(score, Decimal("0.99")))
