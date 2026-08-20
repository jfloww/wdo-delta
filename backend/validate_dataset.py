"""Check an annotation file and report everything wrong with it at once.

    uv run python validate_dataset.py
    uv run python validate_dataset.py data/eval/transactions.example.csv

Run it while annotating rather than at the end. A misspelled label found at row
40 costs a moment; the same label found after four hundred rows costs a
re-read of all of them.

Exits non-zero when the file is unusable, so it can gate a commit hook or CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

from offerdelta.evaluation.validation import validate_labelled_csv

DEFAULT_PATH = Path("data/eval/transactions.csv")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    report = validate_labelled_csv(path)
    print(report.render())
    return 0 if report.usable else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
