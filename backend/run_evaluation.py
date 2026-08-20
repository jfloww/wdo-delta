"""Score the rule baseline, the LLM, and the hybrid on one frozen holdout.

    uv run python run_evaluation.py
    uv run python run_evaluation.py data/eval/transactions.example.csv

Without an API key the LLM slot is filled by a scripted stand-in, so the
pipeline, the split, and the report can all be exercised before any model
exists. The report says which provider ran, so a stand-in result can never be
mistaken for a real one.

Rules are fitted on the development split and every system is scored on the
holdout, which no merchant crosses. The dataset checksum appears in the header
and is verified after the run: if any system mutated the labels it was scored
against, no report is produced.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from offerdelta.domain.common.errors import ValidationError
from offerdelta.evaluation.csv_loader import load_labelled_csv
from offerdelta.evaluation.llm_categoriser import HybridCategoriser, LLMCategoriser
from offerdelta.evaluation.providers import LLMResponse, ScriptedProvider
from offerdelta.evaluation.report import evaluate
from offerdelta.evaluation.rule_baseline import fit_rules
from offerdelta.evaluation.splitting import merchant_disjoint_split
from offerdelta.evaluation.validation import validate_labelled_csv

DEFAULT_PATH = Path("data/eval/transactions.csv")
HOLDOUT_FRACTION = 0.3

#: Published per-million token prices. Change these to match the model actually
#: used; leaving them None reports tokens without pretending to a cost.
INPUT_PRICE = Decimal(3)
OUTPUT_PRICE = Decimal(15)


def _stand_in() -> ScriptedProvider:
    """A provider that always abstains.

    Deliberately useless. It proves the pipeline runs without pretending to a
    result, and its abstentions show up as coverage rather than as accuracy.
    """
    return ScriptedProvider(
        default=LLMResponse(
            label="UNKNOWN",
            confidence=Decimal(0),
            reason="no model configured; scripted stand-in",
            latency_ms=0,
        ),
        model_name="stand-in(no-key)",
    )


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH

    report = validate_labelled_csv(path)
    if not report.usable:
        print(report.render())
        print("\nrefusing to evaluate an unusable dataset")
        return 1
    if report.warnings:
        print(report.render())
        print()

    dataset = load_labelled_csv(path, dataset_version=path.stem)

    try:
        split = merchant_disjoint_split(dataset, holdout_fraction=HOLDOUT_FRACTION)
    except ValidationError as error:
        print(f"cannot split: {error}")
        return 1

    print(
        f"split by merchant: {split.development_merchants} development / "
        f"{split.holdout_merchants} holdout, "
        f"{len(split.development)} / {len(split.holdout)} rows"
    )
    overlap = split.development.merchants & split.holdout.merchants
    print(f"merchants on both sides: {len(overlap)}\n")

    rules = fit_rules(split.development)
    llm = LLMCategoriser(_stand_in())
    hybrid = HybridCategoriser(fit_rules(split.development), LLMCategoriser(_stand_in()))

    result = evaluate(
        split.holdout,
        [rules, llm, hybrid],
        input_price_per_million=INPUT_PRICE,
        output_price_per_million=OUTPUT_PRICE,
    )
    print(result.render())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
