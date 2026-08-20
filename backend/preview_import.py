"""Show what importing a bank export would produce, without importing it.

    uv run python preview_import.py path/to/export.csv
    uv run python preview_import.py export.csv --day-first
    uv run python preview_import.py export.csv --map date=Posted,description=Details,amount=Value

Writes nothing. Run it before an import, because the cheapest moment to catch a
day-first/month-first mistake is before four hundred rows carry it.

Exits non-zero when nothing could be parsed.
"""

from __future__ import annotations

import sys
from pathlib import Path

from offerdelta.domain.common.errors import ValidationError
from offerdelta.ingest.dates import DateOrder
from offerdelta.ingest.mapping import ColumnMapping
from offerdelta.ingest.preview import preview_csv

#: argv[0] is the script itself, so a path means at least two entries.
_MIN_ARGS = 2

_ORDERS = {
    "--day-first": DateOrder.DAY_FIRST,
    "--month-first": DateOrder.MONTH_FIRST,
    "--iso": DateOrder.ISO,
}


def _parse_map(spec: str) -> ColumnMapping:
    pairs = dict(part.split("=", 1) for part in spec.split(",") if "=" in part)
    return ColumnMapping(
        date=pairs.get("date", ""),
        description=pairs.get("description", ""),
        merchant=pairs.get("merchant"),
        amount=pairs.get("amount"),
        debit=pairs.get("debit"),
        credit=pairs.get("credit"),
    )


def main(argv: list[str]) -> int:
    if len(argv) < _MIN_ARGS:
        print(__doc__)
        return 2

    path = Path(argv[1])
    order = next((_ORDERS[a] for a in argv[2:] if a in _ORDERS), None)
    mapping = next(
        (_parse_map(a.removeprefix("--map=")) for a in argv[2:] if a.startswith("--map=")),
        None,
    )

    try:
        preview = preview_csv(path, mapping=mapping, date_order=order)
    except ValidationError as error:
        print(error)
        return 1

    print(preview.render())
    return 0 if preview.importable else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
