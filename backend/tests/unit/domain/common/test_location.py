"""Locations and tax jurisdictions.

A location's job is to produce the jurisdiction code the tax layer keys on, so
residence and work jurisdiction are derived from one place rather than typed as
free text at each call site.

Location deliberately does *not* restrict which states exist. Rejecting an
unsupported state is the tax registry's job, and it should say "no rule set for
US-TX" rather than "invalid location" — the state is perfectly valid, the
project simply has no rules for it yet.
"""

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.location import Location


def test_a_location_produces_its_jurisdiction_code() -> None:
    assert Location(state="AL", locality="Auburn").jurisdiction_code == "US-AL"


def test_the_jurisdiction_code_ignores_the_locality() -> None:
    # State income tax keys on the state; localities matter separately, and NYC
    # is the case that makes the distinction load-bearing.
    assert Location(state="NY", locality="New York City").jurisdiction_code == "US-NY"


def test_a_locality_is_optional() -> None:
    assert Location(state="NJ").locality is None


def test_locations_compare_by_value() -> None:
    assert Location(state="AL", locality="Auburn") == Location(state="AL", locality="Auburn")


def test_a_different_locality_is_a_different_location() -> None:
    assert Location(state="NY", locality="Yonkers") != Location(state="NY", locality="Albany")


def test_the_state_must_be_two_letters() -> None:
    with pytest.raises(ValidationError, match="two-letter"):
        Location(state="Alabama")


def test_the_state_must_be_uppercase() -> None:
    with pytest.raises(ValidationError, match="two-letter"):
        Location(state="al")


def test_an_unsupported_state_is_still_a_valid_location() -> None:
    # Texas has no rule set in this project, but "Austin, TX" is a real place.
    # The tax registry raises the unsupported-jurisdiction error, not this type.
    assert Location(state="TX", locality="Austin").jurisdiction_code == "US-TX"


def test_a_non_us_country_keeps_its_own_prefix() -> None:
    assert Location(state="ON", country="CA").jurisdiction_code == "CA-ON"


def test_the_country_must_be_two_letters() -> None:
    with pytest.raises(ValidationError, match="two-letter"):
        Location(state="AL", country="USA")


def test_renders_for_a_human() -> None:
    assert str(Location(state="AL", locality="Auburn")) == "Auburn, AL"
    assert str(Location(state="NJ")) == "NJ"


def test_is_immutable() -> None:
    location = Location(state="AL", locality="Auburn")
    with pytest.raises(AttributeError):
        location.state = "NJ"  # type: ignore[misc]
