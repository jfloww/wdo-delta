"""The override-backed tax model.

Resolves the conflict recorded in blueprint section 6.8.2. `base_salary` is in
the override's locked set, so an override goes stale the moment salary changes —
but the equivalent-salary solver works precisely by varying salary. Taken
literally, the override that was meant to defer the tax engine also makes the
headline solver unusable.

The resolution: solvers depend on a `TaxModel` port, not on the override. This
implementation calibrates an effective rate at one observed (gross, net) point
and extrapolates with a user-supplied marginal rate. It is honest about being an
approximation, and reports how far a query sits from its calibration point so a
result can say when it is being trusted too far.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.common.rounding import CURRENCY_DISPLAY
from offerdelta.domain.taxes.override_model import NetPayOverrideTaxModel

# Calibrated at 78,000 gross / 57,840 net — an effective rate of about 25.85%.
MODEL = NetPayOverrideTaxModel(
    observed_gross=PeriodicAmount(Money.parse("78000.00"), PeriodKind.ANNUAL),
    observed_net=PeriodicAmount(Money.parse("57840.00"), PeriodKind.ANNUAL),
    marginal_rate=Percentage.from_percent("32"),
)


def _annual(amount: str) -> PeriodicAmount:
    return PeriodicAmount(Money.parse(amount), PeriodKind.ANNUAL)


def test_at_the_calibration_point_it_returns_the_observed_figure() -> None:
    # The whole reason the override exists: a verified paystub number must come
    # back unchanged, not approximated.
    result = MODEL.after_tax_cash(_annual("78000.00"))
    assert result.after_tax.money == Money.parse("57840.00")


def test_the_effective_rate_is_derived_from_the_calibration_point() -> None:
    # 57,840 / 78,000 does not terminate, so the rate is kept at full precision
    # and quantised only for display — the same rule applied everywhere else.
    assert MODEL.effective_rate.as_percent().quantize(Decimal("0.01")) == Decimal("25.85")


def test_the_effective_rate_keeps_full_precision() -> None:
    assert MODEL.effective_rate.as_percent() != Decimal("25.85")


def test_above_the_calibration_point_it_extrapolates_at_the_marginal_rate() -> None:
    # 10,000 more gross keeps 68% of it at a 32% marginal rate.
    result = MODEL.after_tax_cash(_annual("88000.00"))
    assert result.after_tax.money == Money.parse("64640.00")


def test_below_the_calibration_point_it_extrapolates_downward() -> None:
    result = MODEL.after_tax_cash(_annual("68000.00"))
    assert result.after_tax.money == Money.parse("51040.00")


def test_the_result_names_the_model_that_produced_it() -> None:
    # Every solver result must state which tax model ran, so a reader knows
    # whether they are looking at computed brackets or an extrapolation.
    assert MODEL.after_tax_cash(_annual("78000.00")).model_name == "NET_PAY_OVERRIDE"


def test_the_calibration_point_is_not_flagged_as_extrapolated() -> None:
    assert MODEL.after_tax_cash(_annual("78000.00")).is_extrapolated is False


def test_a_different_salary_is_flagged_as_extrapolated() -> None:
    assert MODEL.after_tax_cash(_annual("88000.00")).is_extrapolated is True


def test_the_result_reports_how_far_it_is_from_calibration() -> None:
    # 88,000 against a 78,000 calibration is about 12.8% away.
    distance = MODEL.after_tax_cash(_annual("88000.00")).calibration_distance
    assert distance.as_percent().quantize(Decimal("0.1")) == Decimal("12.8")


def test_a_nearby_query_is_within_tolerance() -> None:
    assert MODEL.after_tax_cash(_annual("80000.00")).is_far_from_calibration is False


def test_a_distant_query_is_flagged_as_unreliable() -> None:
    # Doubling the salary makes a single-point linear model untrustworthy, and
    # the result should say so rather than look as confident as any other.
    assert MODEL.after_tax_cash(_annual("160000.00")).is_far_from_calibration is True


def test_monthly_queries_are_normalised_to_the_calibration_period() -> None:
    monthly = PeriodicAmount(Money.parse("6500.00"), PeriodKind.MONTHLY)
    result = MODEL.after_tax_cash(monthly)
    assert result.after_tax.period is PeriodKind.MONTHLY
    assert result.after_tax.money.quantize(CURRENCY_DISPLAY) == Money.parse("4820.00")


def test_after_tax_never_exceeds_gross() -> None:
    # Even a nonsensical marginal rate cannot make take-home exceed the gross it
    # came from.
    model = NetPayOverrideTaxModel(
        observed_gross=_annual("78000.00"),
        observed_net=_annual("57840.00"),
        marginal_rate=Percentage.zero(),
    )
    result = model.after_tax_cash(_annual("500000.00"))
    assert result.after_tax.money <= Money.parse("500000.00")


def test_after_tax_never_goes_negative() -> None:
    result = MODEL.after_tax_cash(_annual("0.00"))
    assert result.after_tax.money.amount >= Decimal(0)


def test_a_zero_gross_calibration_is_rejected() -> None:
    with pytest.raises(ValidationError, match="positive"):
        NetPayOverrideTaxModel(
            observed_gross=_annual("0.00"),
            observed_net=_annual("0.00"),
            marginal_rate=Percentage.from_percent("32"),
        )


def test_net_above_gross_at_calibration_is_rejected() -> None:
    with pytest.raises(ValidationError, match="exceed"):
        NetPayOverrideTaxModel(
            observed_gross=_annual("78000.00"),
            observed_net=_annual("90000.00"),
            marginal_rate=Percentage.from_percent("32"),
        )


def test_a_marginal_rate_above_one_hundred_percent_is_rejected() -> None:
    with pytest.raises(ValidationError, match="between 0% and 100%"):
        NetPayOverrideTaxModel(
            observed_gross=_annual("78000.00"),
            observed_net=_annual("57840.00"),
            marginal_rate=Percentage.from_percent("120"),
        )


def test_a_one_time_amount_cannot_be_taxed_as_a_rate() -> None:
    with pytest.raises(ValidationError, match="rate"):
        MODEL.after_tax_cash(PeriodicAmount(Money.parse("8000.00"), PeriodKind.ONE_TIME))


def test_no_tax_breakdown_is_available() -> None:
    # Blueprint section 6.8: with an override in play the breakdown stays
    # unavailable rather than being invented from an effective rate.
    assert MODEL.after_tax_cash(_annual("78000.00")).breakdown is None
