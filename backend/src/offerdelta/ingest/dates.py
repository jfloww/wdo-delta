"""Reading dates out of bank exports.

The trap this exists for: `03/04/2026` is 4 March in most of the world and
3 April in the United States, and nothing in the value says which. Guessing
wrong moves a transaction by up to eleven months, silently, and every monthly
total that touches it is then wrong in a way no downstream check can detect.

So the order is inferred from the **whole column** rather than one value: a
single day above twelve settles it for every other row. When the column
genuinely cannot settle — every value reads both ways — the answer is
`AMBIGUOUS`, and parsing under it refuses. The importer asks instead of picking.

Contradictory evidence is also `AMBIGUOUS` rather than majority rule. A column
containing both `17/08` and `08/17` is mixed or broken, and quietly adopting the
more common reading would bury that.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from offerdelta.domain.common.errors import ValidationError

#: Share of non-blank values that must parse for a column to be called dates.
#: Below 1.0 because a stray "PENDING" row should not disqualify a column.
_DATE_COLUMN_THRESHOLD: Final = 0.7

_MAX_MONTH: Final = 12
_CENTURY: Final = 2000

#: A four-digit leading group can only be a year.
_YEAR_DIGITS: Final = 4

#: Two-digit years belong to this century; exports predating 2000 are not a
#: case worth supporting, and reading 26 as 1926 would be worse than refusing.
_TWO_DIGIT_MAX: Final = 99

#: Formats that carry their own order and need no convention.
_UNAMBIGUOUS_FORMATS: Final = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
)

_NUMERIC = re.compile(r"^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{2,4})$")

#: Exports sometimes carry a posting time nobody needs.
_TIME_SUFFIX = re.compile(r"[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


class DateOrder(StrEnum):
    """Which convention a date column follows."""

    ISO = "ISO"
    MONTH_FIRST = "MONTH_FIRST"
    DAY_FIRST = "DAY_FIRST"

    #: Every value reads both ways, or the column contradicts itself. Parsing
    #: under this refuses rather than guessing.
    AMBIGUOUS = "AMBIGUOUS"


def _clean(value: str) -> str:
    return _TIME_SUFFIX.sub("", value.strip())


def parse_date(value: str, order: DateOrder) -> date:
    """Read one date under a known convention."""
    if not value or not value.strip():
        raise ValidationError("cannot parse an empty date")

    text = _clean(value)

    for pattern in _UNAMBIGUOUS_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()  # noqa: DTZ007
        except ValueError:
            continue

    match = _NUMERIC.match(text)
    if match is None:
        raise ValidationError(f"{value!r} is not a date")

    first, second, third = (int(part) for part in match.groups())

    # A four-digit leading group can only be a year.
    if len(match.group(1)) == _YEAR_DIGITS:
        return _build(first, second, third, value)

    if order is DateOrder.AMBIGUOUS:
        raise ValidationError(
            f"{value!r} cannot be read because the column's date order is "
            f"ambiguous; state it explicitly rather than risk an eleven-month "
            f"error"
        )

    year = third if third > _TWO_DIGIT_MAX else _CENTURY + third
    if order is DateOrder.DAY_FIRST:
        day, month = first, second
    else:
        month, day = first, second
    return _build(year, month, day, value)


def _build(year: int, month: int, day: int, original: str) -> date:
    try:
        return date(year, month, day)
    except ValueError as error:
        raise ValidationError(f"{original!r} is not a date: {error}") from error


def detect_date_order(values: list[str]) -> DateOrder:
    """Infer the convention from a whole column.

    One value with a day above twelve settles the column. Contradictory
    evidence yields AMBIGUOUS rather than a majority vote, because a mixed
    column is a problem the importer should surface, not smooth over.
    """
    saw_self_describing = False
    day_first = False
    month_first = False

    for raw in values:
        if not raw or not raw.strip():
            continue
        verdict = _classify_value(_clean(raw))
        if verdict is None:
            continue
        described, first_evidence, second_evidence = verdict
        saw_self_describing = saw_self_describing or described
        day_first = day_first or first_evidence
        month_first = month_first or second_evidence

    if day_first and month_first:
        # The column disagrees with itself.
        return DateOrder.AMBIGUOUS
    if day_first:
        return DateOrder.DAY_FIRST
    if month_first:
        return DateOrder.MONTH_FIRST
    if saw_self_describing:
        return DateOrder.ISO
    return DateOrder.AMBIGUOUS


def _classify_value(text: str) -> tuple[bool, bool, bool] | None:
    """What one value says about the column, or None if it says nothing.

    Returns (carries its own order, suggests day-first, suggests month-first).
    """
    if any(_parses(text, pattern) for pattern in _UNAMBIGUOUS_FORMATS):
        return (True, False, False)

    match = _NUMERIC.match(text)
    if match is None:
        return None
    if len(match.group(1)) == _YEAR_DIGITS:
        return (True, False, False)

    first, second = int(match.group(1)), int(match.group(2))
    return (False, first > _MAX_MONTH, second > _MAX_MONTH)


def looks_like_dates(values: list[str]) -> bool:
    """Whether a column plausibly holds dates.

    Used to confirm a header guess against the actual values, so a column named
    "date" full of merchant names is not accepted on its name alone.
    """
    candidates = [value for value in values if value and value.strip()]
    if not candidates:
        return False

    parsed = 0
    for raw in candidates:
        text = _clean(raw)
        if any(_parses(text, pattern) for pattern in _UNAMBIGUOUS_FORMATS) or _NUMERIC.match(text):
            parsed += 1

    return parsed / len(candidates) >= _DATE_COLUMN_THRESHOLD


def _parses(text: str, pattern: str) -> bool:
    try:
        datetime.strptime(text, pattern)  # noqa: DTZ007
    except ValueError:
        return False
    return True
