"""Deciding whether a column holds money.

Used to confirm a header guess against the values. A column called `amount`
full of reference numbers is not an amount column, and accepting it on its name
would put reference numbers into somebody's balance.

Reference numbers are the reason this is stricter than "does it parse". Long
digit runs with no separator, no sign and no decimal point parse perfectly well
as numbers and are not money.
"""

from __future__ import annotations

from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.transactions.parsing import parse_amount

#: Share of non-blank values that must parse for the column to count.
_THRESHOLD: Final = 0.7

#: Beyond this many digits with no decimal point or separator, a value is far
#: more likely an account or reference number than an amount.
_REFERENCE_DIGITS: Final = 9


def looks_like_amounts(values: list[str]) -> bool:
    candidates = [value for value in values if value and value.strip()]
    if not candidates:
        return False

    money_like = 0
    for raw in candidates:
        text = raw.strip()
        try:
            parse_amount(text)
        except ValidationError:
            continue

        digits = "".join(c for c in text if c.isdigit())
        looks_like_reference = (
            len(digits) >= _REFERENCE_DIGITS
            and "." not in text
            and "," not in text
            and not text.startswith(("-", "("))
        )
        if not looks_like_reference:
            money_like += 1

    return money_like / len(candidates) >= _THRESHOLD
