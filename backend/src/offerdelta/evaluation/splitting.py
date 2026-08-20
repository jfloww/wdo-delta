"""Splitting the dataset by merchant.

The leak this prevents is specific and easy to miss. If NETFLIX appears both in
the data the rules were written against and in the evaluation set, the rule
"contains NETFLIX therefore subscriptions" scores perfectly on a merchant it was
written for. The reported F1 then measures memorisation rather than
generalisation, and it looks excellent right until an unanticipated merchant
arrives.

So the unit of splitting is the **normalised merchant**, never the transaction.
A merchant lands wholly on one side.

Assignment is by hash of the merchant name rather than by shuffling, which makes
it deterministic and order-independent: the same merchant lands in the same
split on every machine and in every run, so results stay comparable across time.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.evaluation.dataset import LabelledDataset

#: Resolution of the hash bucket. Fine enough that a fraction like 0.3 is
#: honoured closely without depending on the number of merchants.
_BUCKETS: Final = 10_000


@dataclass(frozen=True)
class MerchantSplit:
    """Two disjoint halves, and the provenance to prove they came from one set."""

    development: LabelledDataset
    holdout: LabelledDataset

    #: Checksum of the dataset that was split, so a report can show that the
    #: evaluation half really came from the frozen set it names.
    source_checksum: str
    holdout_fraction: float
    salt: str

    @property
    def development_merchants(self) -> int:
        return len(self.development.merchants)

    @property
    def holdout_merchants(self) -> int:
        return len(self.holdout.merchants)


def _bucket(merchant: str, salt: str) -> int:
    digest = hashlib.sha256(f"{salt}\x1f{merchant}".encode()).hexdigest()
    return int(digest[:8], 16) % _BUCKETS


def merchant_disjoint_split(
    dataset: LabelledDataset,
    *,
    holdout_fraction: float = 0.3,
    salt: str = "offerdelta-merchant-split-v1",
) -> MerchantSplit:
    """Split so that no merchant appears on both sides.

    `salt` gives a second independent split when one is wanted, without giving
    up determinism.
    """
    if not 0 < holdout_fraction < 1:
        raise ValidationError(
            f"holdout_fraction must be between 0 and 1 exclusive, got {holdout_fraction}"
        )

    threshold = holdout_fraction * _BUCKETS
    holdout_merchants = {
        merchant for merchant in dataset.merchants if _bucket(merchant, salt) < threshold
    }

    development = tuple(
        r for r in dataset.records if r.normalised_merchant not in holdout_merchants
    )
    holdout = tuple(r for r in dataset.records if r.normalised_merchant in holdout_merchants)

    if not development or not holdout:
        raise ValidationError(
            f"a holdout fraction of {holdout_fraction} leaves one side empty for "
            f"{len(dataset.merchants)} merchants; an empty benchmark reporting a "
            f"perfect score is worse than no benchmark"
        )

    return MerchantSplit(
        development=LabelledDataset(
            dataset_version=f"{dataset.dataset_version}+dev",
            records=development,
            schema_version=dataset.schema_version,
        ),
        holdout=LabelledDataset(
            dataset_version=f"{dataset.dataset_version}+holdout",
            records=holdout,
            schema_version=dataset.schema_version,
        ),
        source_checksum=dataset.checksum,
        holdout_fraction=holdout_fraction,
        salt=salt,
    )
