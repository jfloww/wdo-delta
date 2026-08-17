"""The idempotency contract.

A client that retries after a timeout must not create a second thing. A client
that reuses a key with different content has a bug, and that bug should surface
loudly rather than silently return the first answer.

    same key + same body       -> replay the original response
    same key + different body  -> 409, the key is already spoken for
    key still in flight        -> 409, the first request has not finished
    no key                     -> proceed, nothing to guard

Time is injected rather than read from the clock, so expiry is testable and the
codebase-wide rule that nothing reads the clock implicitly holds here too.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Final, Protocol

#: Long enough to cover any realistic client retry window, short enough that a
#: key is not held against a caller forever.
DEFAULT_TTL: Final = timedelta(hours=24)


def fingerprint(body: object) -> str:
    """A stable digest of a request body.

    Sorted keys and fixed separators, so a client that serialises its JSON
    differently on retry is recognised as sending the same request rather than
    told it conflicts.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RecordState(Enum):
    IN_FLIGHT = auto()
    COMPLETED = auto()


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    fingerprint: str
    state: RecordState
    created_at: datetime
    response: str | None = None


@dataclass(frozen=True)
class IdempotencyOutcome:
    """What the caller should do with this request."""

    class Kind(Enum):
        PROCEED = auto()
        REPLAY = auto()
        CONFLICT = auto()

    kind: Kind
    response: str | None = None
    reason: str = ""


class IdempotencyStore(Protocol):
    """Where in-flight and completed keys live.

    In-memory today; DynamoDB with conditional writes when the async path
    arrives, which is the whole reason this is a port.
    """

    def get(self, key: str) -> IdempotencyRecord | None: ...
    def put(self, record: IdempotencyRecord) -> None: ...
    def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class IdempotencyService:
    """Applies the contract to a request."""

    store: IdempotencyStore
    ttl: timedelta = DEFAULT_TTL

    def begin(self, *, key: str | None, body: object, now: datetime) -> IdempotencyOutcome:
        """Decide whether this request may run, replay, or must be refused."""
        if key is None:
            return IdempotencyOutcome(IdempotencyOutcome.Kind.PROCEED)

        digest = fingerprint(body)
        existing = self.store.get(key)

        if existing is not None and now - existing.created_at >= self.ttl:
            # Expired. The key is free again, including for different content.
            self.store.delete(key)
            existing = None

        if existing is None:
            self.store.put(
                IdempotencyRecord(
                    key=key,
                    fingerprint=digest,
                    state=RecordState.IN_FLIGHT,
                    created_at=now,
                )
            )
            return IdempotencyOutcome(IdempotencyOutcome.Kind.PROCEED)

        if existing.fingerprint != digest:
            return IdempotencyOutcome(
                IdempotencyOutcome.Kind.CONFLICT,
                reason=(f"idempotency key {key!r} was already used for a different request body"),
            )

        if existing.state is RecordState.IN_FLIGHT:
            return IdempotencyOutcome(
                IdempotencyOutcome.Kind.CONFLICT,
                reason=(
                    f"a request with idempotency key {key!r} is still in flight; "
                    f"retry once it completes"
                ),
            )

        return IdempotencyOutcome(IdempotencyOutcome.Kind.REPLAY, response=existing.response)

    def complete(self, *, key: str, response: str) -> None:
        """Record the response so a retry replays it byte for byte.

        Takes no timestamp: the TTL runs from when the key was *claimed*, not
        from when the work finished, so a slow request does not extend its own
        hold on the key.
        """
        existing = self.store.get(key)
        if existing is None:
            raise KeyError(f"idempotency key {key!r} was never started")
        self.store.put(
            IdempotencyRecord(
                key=key,
                fingerprint=existing.fingerprint,
                state=RecordState.COMPLETED,
                created_at=existing.created_at,
                response=response,
            )
        )

    def abandon(self, *, key: str) -> None:
        """Release a key whose work failed.

        The caller's whole reason for retrying is that the first attempt did not
        finish, so a failed request must not hold its key until the TTL expires.
        """
        self.store.delete(key)
