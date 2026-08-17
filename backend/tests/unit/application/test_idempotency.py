"""The idempotency contract.

The convention the payments industry settled on, and the reason it settled
there: a client that retries after a timeout must not create a second thing, and
a client that reuses a key with different content has a bug that should surface
loudly rather than silently overwrite.

    same key + same body       -> replay the original response
    same key + different body  -> 409, the key is already spoken for
    key still in flight        -> 409, the first request has not finished
    no key                     -> proceed, nothing to guard

Time is injected rather than read from the clock, so expiry is testable and the
rule that nothing in this codebase reads the clock implicitly holds here too.
"""

from datetime import UTC, datetime, timedelta

import pytest

from offerdelta.application.idempotency import (
    IdempotencyOutcome,
    IdempotencyService,
    fingerprint,
)
from offerdelta.infrastructure.memory.idempotency import InMemoryIdempotencyStore

T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def service() -> IdempotencyService:
    return IdempotencyService(InMemoryIdempotencyStore())


# --- Fingerprinting --------------------------------------------------------


def test_the_same_body_fingerprints_identically() -> None:
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"a": 1, "b": 2})


def test_key_order_does_not_change_the_fingerprint() -> None:
    # A client that serialises its JSON differently on retry is still sending
    # the same request, and must not be told it conflicts.
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_a_different_body_fingerprints_differently() -> None:
    assert fingerprint({"salary": "100000"}) != fingerprint({"salary": "120000"})


def test_a_nested_difference_is_detected() -> None:
    assert fingerprint({"o": {"x": 1}}) != fingerprint({"o": {"x": 2}})


# --- The four cases --------------------------------------------------------


def test_a_request_without_a_key_proceeds() -> None:
    service = IdempotencyService(InMemoryIdempotencyStore())
    outcome = service.begin(key=None, body={"a": 1}, now=T0)
    assert outcome.kind is IdempotencyOutcome.Kind.PROCEED


def test_a_first_request_with_a_key_proceeds(service: IdempotencyService) -> None:
    outcome = service.begin(key="k1", body={"a": 1}, now=T0)
    assert outcome.kind is IdempotencyOutcome.Kind.PROCEED


def test_an_unfinished_request_conflicts(service: IdempotencyService) -> None:
    # The first call is still running. A second must not start the work again.
    service.begin(key="k1", body={"a": 1}, now=T0)
    outcome = service.begin(key="k1", body={"a": 1}, now=T0)
    assert outcome.kind is IdempotencyOutcome.Kind.CONFLICT
    assert "in flight" in outcome.reason


def test_a_completed_request_replays_its_response(service: IdempotencyService) -> None:
    service.begin(key="k1", body={"a": 1}, now=T0)
    service.complete(key="k1", response='{"id":"abc"}')

    outcome = service.begin(key="k1", body={"a": 1}, now=T0)
    assert outcome.kind is IdempotencyOutcome.Kind.REPLAY
    assert outcome.response == '{"id":"abc"}'


def test_a_replay_is_byte_identical(service: IdempotencyService) -> None:
    # The client must not be able to tell a replay from the original except by
    # the header, or retrying would mean reconciling two different answers.
    original = '{"id":"abc","total":"1234.56"}'
    service.begin(key="k1", body={"a": 1}, now=T0)
    service.complete(key="k1", response=original)
    assert service.begin(key="k1", body={"a": 1}, now=T0).response == original


def test_the_same_key_with_a_different_body_conflicts(
    service: IdempotencyService,
) -> None:
    # The case that matters: a client reusing a key for different content has a
    # bug, and silently returning the first answer would hide it.
    service.begin(key="k1", body={"salary": "100000"}, now=T0)
    service.complete(key="k1", response='{"id":"abc"}')

    outcome = service.begin(key="k1", body={"salary": "120000"}, now=T0)
    assert outcome.kind is IdempotencyOutcome.Kind.CONFLICT
    assert "different" in outcome.reason


def test_different_keys_do_not_interfere(service: IdempotencyService) -> None:
    service.begin(key="k1", body={"a": 1}, now=T0)
    assert service.begin(key="k2", body={"a": 1}, now=T0).kind is (IdempotencyOutcome.Kind.PROCEED)


# --- Expiry ----------------------------------------------------------------


def test_a_key_expires_after_its_ttl(service: IdempotencyService) -> None:
    service.begin(key="k1", body={"a": 1}, now=T0)
    service.complete(key="k1", response='{"id":"abc"}')

    later = T0 + timedelta(hours=25)
    assert service.begin(key="k1", body={"a": 1}, now=later).kind is (
        IdempotencyOutcome.Kind.PROCEED
    )


def test_a_key_is_still_held_just_inside_its_ttl(service: IdempotencyService) -> None:
    service.begin(key="k1", body={"a": 1}, now=T0)
    service.complete(key="k1", response='{"id":"abc"}')

    later = T0 + timedelta(hours=23)
    assert service.begin(key="k1", body={"a": 1}, now=later).kind is (
        IdempotencyOutcome.Kind.REPLAY
    )


def test_an_expired_key_may_be_reused_with_a_different_body(
    service: IdempotencyService,
) -> None:
    service.begin(key="k1", body={"a": 1}, now=T0)
    service.complete(key="k1", response="{}")

    later = T0 + timedelta(hours=25)
    assert service.begin(key="k1", body={"a": 999}, now=later).kind is (
        IdempotencyOutcome.Kind.PROCEED
    )


# --- Failure handling ------------------------------------------------------


def test_an_abandoned_request_releases_its_key(service: IdempotencyService) -> None:
    # If the work fails, the key must not stay locked forever — the client's
    # whole reason for retrying is that the first attempt did not finish.
    service.begin(key="k1", body={"a": 1}, now=T0)
    service.abandon(key="k1")
    assert service.begin(key="k1", body={"a": 1}, now=T0).kind is (IdempotencyOutcome.Kind.PROCEED)


def test_completing_an_unknown_key_is_rejected(service: IdempotencyService) -> None:
    with pytest.raises(KeyError):
        service.complete(key="never-started", response="{}")
