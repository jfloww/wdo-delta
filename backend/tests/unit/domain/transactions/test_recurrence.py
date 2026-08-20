"""Detecting recurring payments.

Deliberately deterministic. This is the baseline an LLM has to beat, and
without it a reported F1 means nothing — "the model scores 0.91" is only
interesting next to "and simple interval matching scores 0.87".

Two signals: the gaps between occurrences are regular, and the amounts are
stable. Both are needed. Groceries every few days at wildly varying amounts are
not a subscription; an annual insurance premium at a fixed amount is, despite
appearing once a year.
"""

from datetime import date, timedelta
from decimal import Decimal

from offerdelta.domain.common.money import Money
from offerdelta.domain.costs.categories import CostCategory
from offerdelta.domain.transactions.entities import Transaction, TransactionKind
from offerdelta.domain.transactions.recurrence import (
    Cadence,
    detect_recurring,
)


def _series(
    description: str,
    amount: str,
    start: date,
    step_days: int,
    count: int,
    *,
    jitter: tuple[int, ...] = (),
    amounts: tuple[str, ...] = (),
) -> list[Transaction]:
    txns = []
    for i in range(count):
        offset = step_days * i + (jitter[i] if i < len(jitter) else 0)
        value = amounts[i] if i < len(amounts) else amount
        txns.append(
            Transaction(
                posted_on=start + timedelta(days=offset),
                description=description,
                amount=Money.parse(value),
                account="checking",
                kind=TransactionKind.SPENDING,
                category=CostCategory.LIVING_SUBSCRIPTIONS,
            )
        )
    return txns


START = date(2026, 1, 15)


# --- Cadence ---------------------------------------------------------------


def test_a_monthly_subscription_is_detected() -> None:
    found = detect_recurring(_series("NETFLIX", "-15.99", START, 30, 6))
    assert len(found) == 1
    assert found[0].cadence is Cadence.MONTHLY


def test_a_weekly_charge_is_detected() -> None:
    found = detect_recurring(_series("GYM", "-25.00", START, 7, 8))
    assert found[0].cadence is Cadence.WEEKLY


def test_an_annual_premium_is_detected() -> None:
    # Appears once a year and is still a commitment worth surfacing.
    found = detect_recurring(_series("INSURANCE", "-840.00", START, 365, 3))
    assert found[0].cadence is Cadence.ANNUAL


def test_billing_day_drift_is_tolerated() -> None:
    # Real billing lands on business days, so a monthly charge wobbles by a few
    # days. Requiring exact 30-day gaps would find almost nothing.
    found = detect_recurring(
        _series("SPOTIFY", "-11.99", START, 30, 6, jitter=(0, 2, -1, 3, -2, 1))
    )
    assert found
    assert found[0].cadence is Cadence.MONTHLY


# --- What is not recurring -------------------------------------------------


def test_two_occurrences_are_not_enough() -> None:
    # Two points define an interval but not a pattern; calling that a
    # subscription would flag every pair of coffees.
    assert detect_recurring(_series("BLUE BOTTLE", "-4.50", START, 30, 2)) == []


def test_irregular_gaps_are_not_recurring() -> None:
    txns = _series("GROCERY", "-80.00", START, 3, 8, jitter=(0, 4, 11, 2, 19, 5, 27, 8))
    assert detect_recurring(txns) == []


def test_wildly_varying_amounts_are_not_recurring() -> None:
    # Regular grocery runs at different amounts are a habit, not a commitment.
    amounts = ("-40.00", "-190.00", "-65.00", "-220.00", "-95.00", "-310.00")
    assert detect_recurring(_series("GROCERY", "-100.00", START, 7, 6, amounts=amounts)) == []


def test_a_small_price_rise_does_not_break_detection() -> None:
    # Subscriptions raise prices; the series is still the same subscription.
    amounts = ("-15.99", "-15.99", "-15.99", "-17.99", "-17.99", "-17.99")
    found = detect_recurring(_series("NETFLIX", "-15.99", START, 30, 6, amounts=amounts))
    assert found
    assert found[0].cadence is Cadence.MONTHLY


# --- Reporting -------------------------------------------------------------


def test_the_merchant_key_is_the_normalised_description() -> None:
    found = detect_recurring(_series("SQ *NETFLIX 4412", "-15.99", START, 30, 6))
    assert found[0].merchant == "NETFLIX"


def test_variants_of_one_merchant_group_together() -> None:
    # The reason normalisation exists: without it these are six merchants.
    txns = []
    for i, variant in enumerate(
        ["NETFLIX 01/15", "NETFLIX 02/14", "NETFLIX 03/16", "NETFLIX 04/15", "NETFLIX 05/15"]
    ):
        txns.extend(_series(variant, "-15.99", START + timedelta(days=30 * i), 30, 1))
    found = detect_recurring(txns)
    assert len(found) == 1
    assert found[0].occurrences == 5


def test_the_typical_amount_is_reported() -> None:
    found = detect_recurring(_series("NETFLIX", "-15.99", START, 30, 6))
    assert found[0].typical_amount == Money.parse("-15.99")


def test_the_latest_amount_is_reported_after_a_rise() -> None:
    # What the user will actually be charged next.
    amounts = ("-15.99", "-15.99", "-15.99", "-17.99", "-17.99", "-17.99")
    found = detect_recurring(_series("NETFLIX", "-15.99", START, 30, 6, amounts=amounts))
    assert found[0].latest_amount == Money.parse("-17.99")


def test_the_annual_cost_is_projected() -> None:
    found = detect_recurring(_series("NETFLIX", "-15.99", START, 30, 6))
    assert found[0].annual_cost == Money.parse("-191.88")


def test_confidence_rises_with_more_occurrences() -> None:
    few = detect_recurring(_series("NETFLIX", "-15.99", START, 30, 3))
    many = detect_recurring(_series("NETFLIX", "-15.99", START, 30, 12))
    assert many[0].confidence.as_fraction() > few[0].confidence.as_fraction()


def test_confidence_never_exceeds_certainty() -> None:
    found = detect_recurring(_series("NETFLIX", "-15.99", START, 30, 36))
    assert found[0].confidence.as_fraction() <= Decimal(1)


def test_results_are_ordered_by_annual_cost() -> None:
    # A review list is read top-down, and the expensive subscription is the one
    # worth cancelling.
    txns = _series("CHEAP", "-2.99", START, 30, 6) + _series("PRICEY", "-49.99", START, 30, 6)
    found = detect_recurring(txns)
    assert [f.merchant for f in found] == ["PRICEY", "CHEAP"]


def test_transfers_are_never_recurring_charges() -> None:
    # A monthly savings transfer is regular and stable, and is not a
    # subscription anyone should be prompted to cancel.
    txns = [
        Transaction(
            posted_on=START + timedelta(days=30 * i),
            description="TRANSFER TO SAVINGS",
            amount=Money.parse("-500.00"),
            account="checking",
            kind=TransactionKind.TRANSFER,
        )
        for i in range(6)
    ]
    assert detect_recurring(txns) == []


def test_nothing_is_detected_in_an_empty_ledger() -> None:
    assert detect_recurring([]) == []
