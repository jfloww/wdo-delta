"""Parsing amounts and descriptions out of bank exports.

Bank CSVs are hostile input. Amounts arrive as `$1,234.56`, `(45.00)` for a
debit, `1.234,56` from a European export, and occasionally with the sign
trailing. Every one is a place a float could slip in, and a float in a balance
is the defect this project exists to avoid — so parsing is string surgery into
`Decimal`, never `float()`.

Descriptions are worse. One merchant appears as `SQ *BLUE BOTTLE 4412`,
`BLUE BOTTLE COFFEE #221` and `TST* BLUE BOTTLE`. Recurring-payment detection
depends entirely on collapsing those to a stable key, so normalisation strips
the parts that vary per transaction: processor prefixes, store numbers,
reference digits, and embedded dates.
"""

from __future__ import annotations

import re
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money

#: Anything that is decoration rather than magnitude.
_CURRENCY_NOISE: Final = re.compile(r"[$£€¥\s]")

#: Payment processors prepend a tag that varies per transaction. Left in place
#: it would make one merchant look like many.
_PROCESSOR_PREFIX: Final = re.compile(r"^(SQ|TST|SP|PAYPAL|PP|IC|WPY|EB)\s*\*\s*", re.IGNORECASE)

#: `#221`, `STORE 4412`, and bare trailing reference digits.
_TRAILING_STORE: Final = re.compile(r"\s*#\s*\d+\s*$")
_TRAILING_DIGITS: Final = re.compile(r"\s+\d{2,}\s*$")

#: `08/17`, `08-17-2026`. Banks append the posting date, which would make a
#: monthly subscription look like twelve separate merchants.
_EMBEDDED_DATE: Final = re.compile(r"\s+\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\s*$")

_COLLAPSE_SPACE: Final = re.compile(r"\s{2,}")


def parse_amount(raw: str, *, decimal_comma: bool = False) -> Money:
    """Read a monetary amount from a bank export field.

    `decimal_comma` selects the European convention, where `1.234,56` is one
    thousand two hundred. It is a parameter rather than a guess: `1.234` is
    genuinely ambiguous, and inferring the wrong convention silently changes an
    amount by three orders of magnitude.
    """
    if not raw or not raw.strip():
        raise ValidationError("cannot parse an empty amount")

    text = raw.strip()

    # Accounting convention: parentheses mean a debit. Handled before the sign
    # search so "(45.00)" is not mistaken for a positive amount.
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]

    text = _CURRENCY_NOISE.sub("", text)

    # Some exports, notably older Quicken formats, trail the sign.
    if text.endswith("-"):
        negative = True
        text = text[:-1]
    elif text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]

    text = text.replace(".", "").replace(",", ".") if decimal_comma else text.replace(",", "")

    if not text or not re.fullmatch(r"\d*\.?\d+", text):
        raise ValidationError(f"{raw!r} is not a valid amount")

    return Money.parse(f"-{text}" if negative else text)


def normalise_description(raw: str) -> str:
    """Collapse a merchant description to a key that survives re-billing.

    Never returns empty: stripping every token would produce a blank key that
    collides with every other blank one, silently merging unrelated merchants.
    """
    if not raw or not raw.strip():
        raise ValidationError("cannot normalise an empty description")

    text = raw.strip().upper()
    text = _PROCESSOR_PREFIX.sub("", text)

    for pattern in (_EMBEDDED_DATE, _TRAILING_STORE, _TRAILING_DIGITS):
        stripped = pattern.sub("", text).strip()
        if stripped:  # never strip the whole description away
            text = stripped

    text = _COLLAPSE_SPACE.sub(" ", text).strip()
    return text or raw.strip().upper()
