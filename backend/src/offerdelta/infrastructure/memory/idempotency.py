"""An in-memory idempotency store.

Process-local, so it guards a single instance only. That is honest for a
single-container deployment and inadequate the moment there are two, which is
why the store is a port: DynamoDB with conditional writes replaces this when
the async path arrives, without the service above changing.
"""

from __future__ import annotations

from offerdelta.application.idempotency import IdempotencyRecord


class InMemoryIdempotencyStore:
    """Keys held in a dictionary for the life of the process."""

    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}

    def get(self, key: str) -> IdempotencyRecord | None:
        return self._records.get(key)

    def put(self, record: IdempotencyRecord) -> None:
        self._records[record.key] = record

    def delete(self, key: str) -> None:
        self._records.pop(key, None)
