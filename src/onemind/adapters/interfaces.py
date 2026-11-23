"""Minimal, transport-independent adapter and plugin interfaces for OneMind."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from onemind.protocol.models import AuthorityClass, RecordDict


@dataclass(frozen=True)
class SourceInventoryItem:
    """A single discovered source artifact with provenance and availability."""

    source_type: str
    source_uri: str
    source_id: str
    source_state: str  # "available" | "source-unavailable" | "skipped-*"
    excerpt_sha256: str | None = None
    source_revision: str | None = None
    source_author: str = "unknown"
    line_start: int | None = None
    line_end: int | None = None
    redacted_snapshot: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_uri": self.source_uri,
            "source_id": self.source_id,
            "source_state": self.source_state,
            "excerpt_sha256": self.excerpt_sha256,
            "source_revision": self.source_revision,
            "source_author": self.source_author,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "redacted_snapshot": self.redacted_snapshot,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class IngestionResult:
    """Output of a SourceAdapter: immutable inert evidence records and completeness metadata."""

    adapter_name: str
    adapter_version: str
    evidence_records: list[RecordDict]
    items: list[SourceInventoryItem]
    warnings: list[str] = field(default_factory=list)
    complete: bool = True
    unsupported_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "complete": self.complete,
            "unsupported_reason": self.unsupported_reason,
            "evidence_count": len(self.evidence_records),
            "warnings": self.warnings,
            "items": [i.to_dict() for i in self.items],
        }


@dataclass(frozen=True)
class AuthorityVerificationResult:
    """Result of verifying a GovernanceDecision against an external authority policy."""

    decision_record_id: str
    valid_proof: bool
    authority_class: AuthorityClass
    effective_for_policy: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_record_id": self.decision_record_id,
            "valid_proof": self.valid_proof,
            "authority_class": self.authority_class.value,
            "effective_for_policy": self.effective_for_policy,
            "reason": self.reason,
            "details": self.details,
        }


@dataclass(frozen=True)
class CheckFinding:
    """A single deterministic finding emitted by an ApplicationAdapter/checker."""

    code: str
    severity: str  # "error" | "warning" | "info"
    message: str
    location: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.location is not None:
            out["location"] = self.location
        if self.details:
            out["details"] = self.details
        return out


@dataclass(frozen=True)
class CheckResult:
    """Result of executing an advisory deterministic check."""

    checker_id: str
    checker_version: str
    status: str  # "pass" | "fail" | "indeterminate"
    summary: str
    findings: list[CheckFinding] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    inputs_evaluated: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checker_id": self.checker_id,
            "checker_version": self.checker_version,
            "status": self.status,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "capabilities": self.capabilities,
            "known_limitations": self.known_limitations,
            "inputs_evaluated": self.inputs_evaluated,
        }


@runtime_checkable
class SourceAdapter(Protocol):
    """Ingests external or local artifacts as immutable, inert Evidence records."""

    name: str
    version: str

    def ingest(self, source_path: Path, *, repository: str) -> IngestionResult: ...


@runtime_checkable
class Extractor(Protocol):
    """Produces inert PracticeCandidate records linked to existing Evidence records."""

    name: str
    version: str

    def extract(
        self,
        evidence_records: Sequence[RecordDict],
        *,
        repository: str,
    ) -> list[RecordDict]: ...


@runtime_checkable
class AuthorityVerifier(Protocol):
    """Verifies GovernanceDecision claims against an external authority policy."""

    def verify(
        self,
        decision: RecordDict,
        target: RecordDict | None,
        policy: Any,
    ) -> AuthorityVerificationResult: ...


@runtime_checkable
class Store(Protocol):
    """Append-only canonical record store with snapshot and reference retrieval."""

    def list_records(self, kind: str | None = None) -> list[RecordDict]: ...

    def get_record(self, record_id: str) -> RecordDict | None: ...

    def append(self, record: RecordDict) -> str: ...

    def append_many(self, records: Sequence[RecordDict]) -> list[str]: ...

    def snapshot_commit(self) -> str | None: ...

    def ledger_order(self) -> dict[str, int]: ...


@runtime_checkable
class ApplicationAdapter(Protocol):
    """Evaluates a deterministic, read-only check for an approved or previewed practice."""

    checker_id: str
    checker_version: str
    description: str
    capabilities: tuple[str, ...]
    known_limitations: tuple[str, ...]

    def evaluate(self, inputs: dict[str, Any]) -> CheckResult: ...


@runtime_checkable
class OutcomeSink(Protocol):
    """Appends deduplicated, immutable OutcomeEvent observations without altering governance."""

    def record_outcome(self, event: RecordDict, *, batch_id: str | None = None) -> tuple[bool, str]: ...


@runtime_checkable
class Reporter(Protocol):
    """Pure, deterministic projection from a pinned record snapshot to a structured report."""

    report_version: str

    def render(
        self,
        records: Sequence[RecordDict],
        *,
        commit_ref: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]: ...
