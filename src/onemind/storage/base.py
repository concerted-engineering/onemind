"""Base storage exceptions and in-memory Store implementation."""

from __future__ import annotations

import copy

from onemind.adapters.interfaces import OutcomeSink, Store
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import RecordDict, RecordKind, is_safe_record_id
from onemind.protocol.schema import validate_record_schema


class StoreError(Exception):
    """Base exception for OneMind storage operations."""


class DuplicateRecordError(StoreError):
    """Raised when attempting to append a record with an already-existing record_id."""


class ImmutableRecordError(StoreError):
    """Raised when an operation attempts to modify or delete an immutable record."""


class ConflictingDedupKeyError(StoreError):
    """Raised when an outcome event reuses a dedup_key with different content."""


class InMemoryStore(Store, OutcomeSink):
    """Append-only in-memory record store for testing and offline replay."""

    def __init__(self, initial_records: list[RecordDict] | None = None) -> None:
        self._records: list[RecordDict] = []
        self._by_id: dict[str, RecordDict] = {}
        self._order: dict[str, int] = {}
        self._dedup_digests: dict[str, tuple[str, str]] = {}
        if initial_records:
            for rec in initial_records:
                self.append(rec)

    def list_records(self, kind: str | None = None) -> list[RecordDict]:
        if kind is None:
            return [copy.deepcopy(r) for r in self._records]
        return [copy.deepcopy(r) for r in self._records if r.get("kind") == kind]

    def get_record(self, record_id: str) -> RecordDict | None:
        rec = self._by_id.get(record_id)
        return copy.deepcopy(rec) if rec is not None else None

    def append(self, record: RecordDict) -> str:
        errors = validate_record_schema(record)
        if errors:
            raise StoreError(
                f"Refusing to append invalid record: {'; '.join(e.message for e in errors)}"
            )
        rec_id = str(record["record_id"])
        if not is_safe_record_id(rec_id):
            raise StoreError(f"Unsafe record_id: {rec_id!r}")
        if rec_id in self._by_id:
            raise DuplicateRecordError(f"Record {rec_id!r} already exists in store.")

        stored = copy.deepcopy(record)
        if stored["kind"] == RecordKind.OUTCOME_EVENT:
            dedup_key = str(stored["body"]["dedup_key"])
            digest = canonical_sha256(stored["body"])
            if dedup_key in self._dedup_digests:
                prev_id, prev_digest = self._dedup_digests[dedup_key]
                if prev_digest != digest:
                    raise ConflictingDedupKeyError(
                        f"dedup_key {dedup_key!r} already exists in {prev_id!r} with different payload."
                    )
                return prev_id
            self._dedup_digests[dedup_key] = (rec_id, digest)

        self._order[rec_id] = len(self._records)
        self._records.append(stored)
        self._by_id[rec_id] = stored
        return rec_id

    def record_outcome(
        self, event: RecordDict, *, batch_id: str | None = None
    ) -> tuple[bool, str]:
        if event.get("kind") != RecordKind.OUTCOME_EVENT:
            raise StoreError("OutcomeSink only accepts 'outcome_event' records.")
        errors = validate_record_schema(event)
        if errors:
            raise StoreError(f"Invalid outcome_event: {'; '.join(e.message for e in errors)}")
        dedup_key = str(event["body"]["dedup_key"])
        digest = canonical_sha256(event["body"])
        if dedup_key in self._dedup_digests:
            prev_id, prev_digest = self._dedup_digests[dedup_key]
            if prev_digest != digest:
                raise ConflictingDedupKeyError(
                    f"Conflicting outcome_event for dedup_key {dedup_key!r} (existing {prev_id!r})."
                )
            return False, prev_id
        rec_id = self.append(event)
        return True, rec_id

    def snapshot_commit(self) -> str | None:
        return None

    def ledger_order(self) -> dict[str, int]:
        return dict(self._order)


__all__ = [
    "ConflictingDedupKeyError",
    "DuplicateRecordError",
    "ImmutableRecordError",
    "InMemoryStore",
    "OutcomeSink",
    "Store",
    "StoreError",
]
