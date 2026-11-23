"""Base storage exceptions and thread-safe, atomic in-memory Store implementation."""

from __future__ import annotations

import copy
import threading
from collections.abc import Sequence

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
    """Thread-safe, append-only in-memory record store with atomic multi-record writes."""

    def __init__(self, initial_records: list[RecordDict] | None = None) -> None:
        self._lock = threading.RLock()
        self._records: list[RecordDict] = []
        self._by_id: dict[str, RecordDict] = {}
        self._order: dict[str, int] = {}
        self._dedup_digests: dict[str, tuple[str, str]] = {}
        if initial_records:
            self.append_many(initial_records)

    def list_records(self, kind: str | None = None) -> list[RecordDict]:
        with self._lock:
            if kind is None:
                return [copy.deepcopy(r) for r in self._records]
            return [copy.deepcopy(r) for r in self._records if r.get("kind") == kind]

    def get_record(self, record_id: str) -> RecordDict | None:
        with self._lock:
            rec = self._by_id.get(record_id)
            return copy.deepcopy(rec) if rec is not None else None

    def _validate_single_for_append(
        self,
        record: RecordDict,
        seen_ids: set[str],
        seen_dedups: dict[str, tuple[str, str]],
    ) -> tuple[str, RecordDict, str | None, str | None]:
        errors = validate_record_schema(record)
        if errors:
            raise StoreError(
                f"Refusing to append invalid record: {'; '.join(e.message for e in errors)}"
            )
        rec_id = str(record["record_id"])
        if not is_safe_record_id(rec_id):
            raise StoreError(f"Unsafe record_id: {rec_id!r}")
        if rec_id in self._by_id or rec_id in seen_ids:
            raise DuplicateRecordError(f"Record {rec_id!r} already exists in store.")

        stored = copy.deepcopy(record)
        dedup_key: str | None = None
        body_digest: str | None = None
        if stored["kind"] == RecordKind.OUTCOME_EVENT:
            dedup_key = str(stored["body"]["dedup_key"])
            body_digest = canonical_sha256(stored["body"])
            existing = seen_dedups.get(dedup_key) or self._dedup_digests.get(dedup_key)
            if existing is not None:
                prev_id, prev_digest = existing
                if prev_digest != body_digest:
                    raise ConflictingDedupKeyError(
                        f"dedup_key {dedup_key!r} already exists in {prev_id!r} with different payload."
                    )
                return prev_id, stored, dedup_key, None  # None signals idempotent dedup hit
        return rec_id, stored, dedup_key, body_digest

    def append(self, record: RecordDict) -> str:
        ids = self.append_many([record])
        return ids[0]

    def append_many(self, records: Sequence[RecordDict]) -> list[str]:
        """Atomically validate and append multiple records (all-or-nothing)."""
        if not records:
            return []
        with self._lock:
            staged: list[tuple[str, RecordDict, str | None, str | None]] = []
            seen_ids: set[str] = set()
            seen_dedups: dict[str, tuple[str, str]] = {}

            # Phase 1: Validate all records before mutating any state
            for rec in records:
                rec_id, stored, dedup_key, body_digest = self._validate_single_for_append(
                    rec, seen_ids, seen_dedups
                )
                staged.append((rec_id, stored, dedup_key, body_digest))
                if dedup_key is None or body_digest is not None:
                    seen_ids.add(rec_id)
                if dedup_key is not None and body_digest is not None:
                    seen_dedups[dedup_key] = (rec_id, body_digest)

            # Phase 2: Commit all staged records atomically
            result_ids: list[str] = []
            for rec_id, stored, dedup_key, body_digest in staged:
                if dedup_key is not None and body_digest is None:
                    # Idempotent dedup hit
                    result_ids.append(rec_id)
                    continue
                self._order[rec_id] = len(self._records)
                self._records.append(stored)
                self._by_id[rec_id] = stored
                if dedup_key is not None and body_digest is not None:
                    self._dedup_digests[dedup_key] = (rec_id, body_digest)
                result_ids.append(rec_id)
            return result_ids

    def record_outcome(
        self, event: RecordDict, *, batch_id: str | None = None
    ) -> tuple[bool, str]:
        if event.get("kind") != RecordKind.OUTCOME_EVENT:
            raise StoreError("OutcomeSink only accepts 'outcome_event' records.")
        errors = validate_record_schema(event)
        if errors:
            raise StoreError(f"Invalid outcome_event: {'; '.join(e.message for e in errors)}")
        with self._lock:
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
        with self._lock:
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
