"""Classification metrics.

Written by hand rather than pulled from scikit-learn, for two reasons. The
dataset has behaviour no standard implementation knows about — acceptable-label
sets and abstention that must not count as error — and a metric you cannot
explain in an interview is not evidence of anything.

Every number here is checked against a worked example small enough to verify on
paper, because a metrics bug produces plausible numbers rather than a crash.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.evaluation.metrics import (
    ClassificationReport,
    cohens_kappa,
    score,
)

A, B, C = "LIVING_GROCERY", "LIVING_DINING", "LIVING_OTHER"
ABSTAIN = "UNKNOWN"


def _pairs(*pairs: tuple[str, str]) -> list[tuple[str, str]]:
    """(gold, predicted) pairs."""
    return list(pairs)


# --- Per-class precision, recall, F1 --------------------------------------


def test_a_perfect_classifier_scores_one() -> None:
    report = score(_pairs((A, A), (B, B), (C, C)))
    assert report.macro_f1 == Decimal(1)
    assert report.weighted_f1 == Decimal(1)


def test_precision_and_recall_on_a_worked_example() -> None:
    # Class A: 2 predicted, 1 right -> precision 1/2.
    #          2 actual,    1 right -> recall    1/2.  F1 = 0.5
    report = score(_pairs((A, A), (A, B), (B, A), (B, B)))
    a = report.per_class[A]
    assert a.precision == Decimal("0.5")
    assert a.recall == Decimal("0.5")
    assert a.f1 == Decimal("0.5")


def test_a_class_never_predicted_scores_zero_rather_than_erroring() -> None:
    # Zero predictions means precision has no denominator. Convention is zero,
    # and it must not raise: a model ignoring a rare class is a result, not a
    # crash.
    report = score(_pairs((A, B), (A, B)))
    assert report.per_class[A].precision == Decimal(0)
    assert report.per_class[A].recall == Decimal(0)
    assert report.per_class[A].f1 == Decimal(0)


def test_support_counts_actual_occurrences() -> None:
    report = score(_pairs((A, A), (A, B), (B, B)))
    assert report.per_class[A].support == 2
    assert report.per_class[B].support == 1


# --- Macro versus weighted -------------------------------------------------


def test_macro_and_weighted_differ_on_imbalanced_data() -> None:
    # Macro treats a class of 1 like a class of 99. That gap is the point of
    # reporting both: a model can look strong on weighted and be useless on
    # every category that matters least by volume and most by decision.
    pairs = _pairs(*([(A, A)] * 9), (B, A))
    report = score(pairs)
    assert report.weighted_f1 > report.macro_f1


def test_macro_f1_is_the_unweighted_mean_of_class_f1() -> None:
    # Quantised to the same four places the report uses everywhere else, so a
    # published figure never carries more precision than the sample supports.
    report = score(_pairs((A, A), (B, B), (C, A)))
    mean = sum((c.f1 for c in report.per_class.values()), Decimal(0)) / len(report.per_class)
    assert report.macro_f1 == mean.quantize(Decimal("0.0001"))


# --- Confusion matrix ------------------------------------------------------


def test_the_confusion_matrix_records_what_became_what() -> None:
    report = score(_pairs((A, B), (A, B), (B, B)))
    assert report.confusion[(A, B)] == 2
    assert report.confusion[(B, B)] == 1


def test_the_confusion_matrix_omits_pairs_that_never_occurred() -> None:
    # A dense matrix over 30 labels is 900 mostly-zero cells and unreadable.
    report = score(
        _pairs(
            (A, A),
        )
    )
    assert (B, C) not in report.confusion


def test_the_top_confusions_are_ordered_by_frequency() -> None:
    # The actionable output: which pair does the model actually mix up.
    report = score(_pairs((A, B), (A, B), (A, B), (B, C), (C, C)))
    assert report.top_confusions(2)[0] == ((A, B), 3)


def test_correct_predictions_are_excluded_from_top_confusions() -> None:
    report = score(_pairs((A, A), (A, A), (A, B)))
    assert report.top_confusions(5) == [((A, B), 1)]


# --- Abstention ------------------------------------------------------------


def test_abstention_is_not_counted_as_a_wrong_answer() -> None:
    # A model declining to guess has not made a mistake. Folding that into the
    # error rate punishes the behaviour worth rewarding.
    report = score(_pairs((A, A), (B, ABSTAIN)))
    assert report.per_class[B].recall == Decimal(0)
    assert report.abstentions == 1


def test_coverage_is_the_share_of_rows_answered() -> None:
    report = score(_pairs((A, A), (B, ABSTAIN), (C, C), (A, ABSTAIN)))
    assert report.coverage == Decimal("0.5")


def test_accuracy_on_answered_rows_is_reported_separately() -> None:
    # The pair that matters: a model can be 100% accurate on 20% coverage, and
    # both halves of that sentence are needed to judge it.
    report = score(_pairs((A, A), (B, B), (C, ABSTAIN), (A, ABSTAIN)))
    assert report.coverage == Decimal("0.5")
    assert report.accuracy_when_answered == Decimal(1)


def test_abstaining_on_everything_leaves_accuracy_undefined() -> None:
    report = score(_pairs((A, ABSTAIN), (B, ABSTAIN)))
    assert report.coverage == Decimal(0)
    assert report.accuracy_when_answered is None


# --- Acceptable-label sets -------------------------------------------------


def test_an_acceptable_alternative_counts_as_correct() -> None:
    # Ambiguous rows carry a set of defensible answers; scoring one of them
    # wrong manufactures an error that teaches nothing.
    report = score(
        _pairs((A, C)),
        acceptable={0: frozenset({A, C})},
    )
    assert report.accuracy == Decimal(1)


def test_a_label_outside_the_acceptable_set_is_still_wrong() -> None:
    report = score(_pairs((A, B)), acceptable={0: frozenset({A, C})})
    assert report.accuracy == Decimal(0)


# --- Guards ----------------------------------------------------------------


def test_scoring_nothing_is_rejected() -> None:
    # An empty run reporting a perfect score is the worst possible failure mode.
    with pytest.raises(ValidationError, match="at least one"):
        score([])


def test_the_report_counts_what_it_scored() -> None:
    report = score(_pairs((A, A), (B, B)))
    assert report.total == 2


def test_a_report_is_serialisable_without_floats() -> None:
    # Metrics land in a published report; a float there would drift between
    # runs and undermine the comparison it exists to support.
    report = score(_pairs((A, A), (A, B)))
    assert isinstance(report.macro_f1, Decimal)
    assert all(isinstance(c.f1, Decimal) for c in report.per_class.values())


# --- Cohen's kappa ---------------------------------------------------------


def test_perfect_agreement_gives_kappa_one() -> None:
    assert cohens_kappa([(A, A), (B, B), (C, C)]) == Decimal(1)


def test_kappa_on_a_worked_example() -> None:
    # 4 rows, agree on 3 -> observed 0.75.
    # Annotator 1: A,A,B,B   Annotator 2: A,A,B,A
    # p(A) = 0.5 * 0.75 = 0.375 ; p(B) = 0.5 * 0.25 = 0.125 ; expected = 0.5
    # kappa = (0.75 - 0.5) / (1 - 0.5) = 0.5
    kappa = cohens_kappa([(A, A), (A, A), (B, B), (B, A)])
    assert kappa == Decimal("0.5")


def test_kappa_is_lower_than_raw_agreement_when_one_class_dominates() -> None:
    # The reason kappa is reported alongside raw agreement: two annotators who
    # both say GROCERY almost always will agree ~90% by luck alone.
    pairs = [(A, A)] * 9 + [(B, A)]
    kappa = cohens_kappa(pairs)
    assert kappa is not None
    assert kappa < Decimal("0.9")


def test_kappa_of_chance_agreement_is_zero_or_below() -> None:
    pairs = [(A, A), (A, B), (B, A), (B, B)]
    kappa = cohens_kappa(pairs)
    assert kappa is not None
    assert kappa <= Decimal(0)


def test_kappa_needs_data() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        cohens_kappa([])


def test_kappa_is_undefined_when_both_annotators_used_one_label() -> None:
    # Expected agreement is 1, so the denominator vanishes. Reporting None is
    # honest; reporting 0 would imply measured disagreement that never happened.
    assert cohens_kappa([(A, A), (A, A)]) is None


def test_a_report_renders_as_text() -> None:
    # The harness prints this; an unreadable report is one nobody checks.
    rendered = score(_pairs((A, A), (A, B))).render()
    assert "macro F1" in rendered
    assert isinstance(rendered, str)


def test_the_report_type_is_exported() -> None:
    assert ClassificationReport is not None
