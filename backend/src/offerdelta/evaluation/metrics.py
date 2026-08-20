"""Classification metrics.

Written by hand rather than taken from scikit-learn, for two reasons. This
dataset has behaviour no standard implementation knows about — acceptable-label
sets for ambiguous rows, and abstention that must not be scored as error — and a
metric you cannot explain is not evidence of anything.

Everything is `Decimal`. These numbers land in a published report that compares
three systems, and a float there would drift between runs and undermine the
comparison it exists to support.

Four things are reported together because each is misleading alone:

- **Macro F1** treats a class of 1 like a class of 99, so a model that ignores
  rare categories cannot hide.
- **Weighted F1** says what a typical row experiences.
- **Coverage and accuracy-when-answered** separate declining to guess from
  guessing wrong. A model can be perfectly accurate on 20% of rows, and both
  halves of that sentence are needed to judge it.
- **Cohen's kappa** discounts the agreement two annotators would reach by
  chance. Where one category dominates, raw agreement of 90% can mean nothing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from offerdelta.domain.common.errors import ValidationError
from offerdelta.evaluation.labels import ABSTAIN

#: Enough places that a ratio survives rendering without pretending to more
#: precision than the sample size supports.
_PLACES = Decimal("0.0001")


def _ratio(numerator: int, denominator: int) -> Decimal:
    """Zero when there is nothing to divide by.

    A class the model never predicted has no precision denominator. The
    convention is zero, and it must not raise: a model ignoring a rare class is
    a finding, not a crash.
    """
    if denominator == 0:
        return Decimal(0)
    return (Decimal(numerator) / Decimal(denominator)).quantize(_PLACES)


@dataclass(frozen=True)
class ClassScore:
    """Precision, recall and F1 for one label."""

    label: str
    precision: Decimal
    recall: Decimal
    f1: Decimal
    support: int
    predicted: int


@dataclass(frozen=True)
class ClassificationReport:
    """Everything one system scored on one frozen dataset."""

    total: int
    correct: int
    abstentions: int
    per_class: Mapping[str, ClassScore]
    confusion: Mapping[tuple[str, str], int]

    @property
    def accuracy(self) -> Decimal:
        """Share of all rows answered correctly, abstentions included as wrong."""
        return _ratio(self.correct, self.total)

    @property
    def coverage(self) -> Decimal:
        """Share of rows the system was willing to answer."""
        return _ratio(self.total - self.abstentions, self.total)

    @property
    def accuracy_when_answered(self) -> Decimal | None:
        """Accuracy over answered rows only. None when it answered nothing."""
        answered = self.total - self.abstentions
        if answered == 0:
            return None
        return _ratio(self.correct, answered)

    @property
    def macro_f1(self) -> Decimal:
        """Unweighted mean across classes: every category counts the same."""
        if not self.per_class:
            return Decimal(0)
        total = sum((c.f1 for c in self.per_class.values()), Decimal(0))
        return (total / Decimal(len(self.per_class))).quantize(_PLACES)

    @property
    def weighted_f1(self) -> Decimal:
        """Mean weighted by support: what a typical row experiences."""
        support = sum(c.support for c in self.per_class.values())
        if support == 0:
            return Decimal(0)
        total = sum((c.f1 * Decimal(c.support) for c in self.per_class.values()), Decimal(0))
        return (total / Decimal(support)).quantize(_PLACES)

    def top_confusions(self, limit: int = 10) -> list[tuple[tuple[str, str], int]]:
        """The most frequent mistakes, correct predictions excluded.

        The actionable half of a confusion matrix: which pair the system
        actually mixes up, rather than 900 mostly-zero cells.
        """
        mistakes = {pair: count for pair, count in self.confusion.items() if pair[0] != pair[1]}
        return sorted(mistakes.items(), key=lambda item: (-item[1], item[0]))[:limit]

    def render(self) -> str:
        """A plain-text summary, because a report nobody reads is not evidence."""
        answered = self.accuracy_when_answered
        lines = [
            f"scored {self.total} rows",
            f"  macro F1              {self.macro_f1}",
            f"  weighted F1           {self.weighted_f1}",
            f"  accuracy (all rows)   {self.accuracy}",
            f"  coverage              {self.coverage}",
            f"  accuracy when answered {answered if answered is not None else 'n/a'}",
            f"  abstentions           {self.abstentions}",
            "",
            f"  {'label':<32}{'P':>8}{'R':>8}{'F1':>8}{'n':>6}",
        ]
        for label in sorted(self.per_class):
            c = self.per_class[label]
            lines.append(f"  {label:<32}{c.precision:>8}{c.recall:>8}{c.f1:>8}{c.support:>6}")
        if confusions := self.top_confusions(5):
            lines.append("")
            lines.append("  most frequent confusions")
            for (gold, predicted), count in confusions:
                lines.append(f"    {gold} -> {predicted}: {count}")
        return "\n".join(lines)


def score(
    pairs: Sequence[tuple[str, str]],
    *,
    acceptable: Mapping[int, frozenset[str]] | None = None,
) -> ClassificationReport:
    """Score (gold, predicted) pairs.

    `acceptable` maps a row index to the set of labels that count as correct for
    it, for genuinely ambiguous transactions. Rows absent from the mapping are
    scored against their gold label alone.
    """
    if not pairs:
        raise ValidationError(
            "scoring needs at least one row; an empty run reporting a perfect "
            "score is the worst available failure mode"
        )

    acceptable = acceptable or {}

    correct = 0
    abstentions = 0
    confusion: Counter[tuple[str, str]] = Counter()
    true_positive: Counter[str] = Counter()
    predicted_count: Counter[str] = Counter()
    support: Counter[str] = Counter()

    for index, (gold, predicted) in enumerate(pairs):
        support[gold] += 1
        confusion[(gold, predicted)] += 1

        if predicted == ABSTAIN:
            abstentions += 1
            continue

        predicted_count[predicted] += 1

        allowed = acceptable.get(index)
        hit = predicted in allowed if allowed else predicted == gold
        if hit:
            correct += 1
            # Credited to the gold class so per-class recall reflects the row
            # that was actually there, not the alternative that was accepted.
            true_positive[gold] += 1

    labels = sorted(set(support) | set(predicted_count))
    per_class = {}
    for label in labels:
        precision = _ratio(true_positive[label], predicted_count[label])
        recall = _ratio(true_positive[label], support[label])
        denominator = precision + recall
        f1 = (
            Decimal(0)
            if denominator == 0
            else (Decimal(2) * precision * recall / denominator).quantize(_PLACES)
        )
        per_class[label] = ClassScore(
            label=label,
            precision=precision,
            recall=recall,
            f1=f1,
            support=support[label],
            predicted=predicted_count[label],
        )

    return ClassificationReport(
        total=len(pairs),
        correct=correct,
        abstentions=abstentions,
        per_class=per_class,
        confusion=dict(confusion),
    )


def cohens_kappa(pairs: Sequence[tuple[str, str]]) -> Decimal | None:
    """Agreement between two annotators, discounted for chance.

    Returns None when expected agreement is 1 — both annotators used a single
    label, so the denominator vanishes. Reporting None is honest; reporting 0
    would imply measured disagreement that never happened.
    """
    if not pairs:
        raise ValidationError("kappa needs at least one annotated pair")

    n = Decimal(len(pairs))
    observed = Decimal(sum(1 for a, b in pairs if a == b)) / n

    first: Counter[str] = Counter(a for a, _ in pairs)
    second: Counter[str] = Counter(b for _, b in pairs)
    labels = set(first) | set(second)

    expected = sum((Decimal(first[label]) / n) * (Decimal(second[label]) / n) for label in labels)

    if expected == 1:
        return None
    return ((observed - expected) / (Decimal(1) - expected)).quantize(_PLACES)
