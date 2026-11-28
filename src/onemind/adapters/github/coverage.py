"""Run-level coverage accounting and append-only IngestionRun manifest structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

INGESTION_RUN_SCHEMA_VERSION = "0.1-draft-ingestion-run"


@dataclass(frozen=True)
class EndpointGap:
    """Structured record of an incomplete, truncated, or failed GitHub endpoint fetch."""

    endpoint: str
    reason_code: str
    message: str
    pr_number: int | None = None
    http_status: int | None = None
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "endpoint": self.endpoint,
            "reason_code": self.reason_code,
            "message": self.message,
            "attempts": self.attempts,
        }
        if self.pr_number is not None:
            out["pr_number"] = self.pr_number
        if self.http_status is not None:
            out["http_status"] = self.http_status
        return out


@dataclass(frozen=True)
class EvidenceDigestEntry:
    """Safe summary reference to a persisted Evidence record (never includes raw text)."""

    record_id: str
    record_sha256: str
    source_type: str
    source_id: str
    status: str  # "new" | "edited" | "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_sha256": self.record_sha256,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class CoverageSummary:
    """Complete or explicitly incomplete coverage accounting for one ingestion run."""

    complete: bool
    incomplete_reasons: tuple[str, ...] = ()
    pages_attempted: int = 0
    pages_fetched: int = 0
    prs_enumerated: int = 0
    prs_completed: int = 0
    prs_incomplete: int = 0
    reviews_collected: int = 0
    review_comments_collected: int = 0
    issue_comments_collected: int = 0
    unavailable_observations: int = 0
    filtered_records: int = 0
    new_observations: int = 0
    unchanged_deduplicated: int = 0
    edited_observations: int = 0
    retries_performed: int = 0
    oldest_source_timestamp: str | None = None
    newest_source_timestamp: str | None = None
    endpoint_gaps: tuple[EndpointGap, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "incomplete_reasons": list(self.incomplete_reasons),
            "pages_attempted": self.pages_attempted,
            "pages_fetched": self.pages_fetched,
            "prs_enumerated": self.prs_enumerated,
            "prs_completed": self.prs_completed,
            "prs_incomplete": self.prs_incomplete,
            "reviews_collected": self.reviews_collected,
            "review_comments_collected": self.review_comments_collected,
            "issue_comments_collected": self.issue_comments_collected,
            "unavailable_observations": self.unavailable_observations,
            "filtered_records": self.filtered_records,
            "new_observations": self.new_observations,
            "unchanged_deduplicated": self.unchanged_deduplicated,
            "edited_observations": self.edited_observations,
            "retries_performed": self.retries_performed,
            "oldest_source_timestamp": self.oldest_source_timestamp,
            "newest_source_timestamp": self.newest_source_timestamp,
            "endpoint_gaps": [g.to_dict() for g in self.endpoint_gaps],
        }


@dataclass(frozen=True)
class IngestionRunManifest:
    """Immutable run-level manifest persisted under `.onemind/ingestions/<run_id>/manifest.json`.

    Stores bounded query provenance, coverage accounting, and persisted Evidence digests
    without fabricating PracticeVersion/Application IDs or logging raw review text or tokens.
    """

    run_id: str
    adapter_name: str
    adapter_version: str
    created_at: str
    observed_at: str
    query: dict[str, Any]
    coverage: CoverageSummary
    evidence_digests: tuple[EvidenceDigestEntry, ...] = ()
    schema_version: str = INGESTION_RUN_SCHEMA_VERSION
    disclaimer: str = field(
        default=(
            "Ingested GitHub observations are inert Evidence only. They confer no governance "
            "authority, activate no practices, and do not measure engineering effectiveness. "
            "Zero observed comments on an incomplete run never establishes absence of review corrections."
        )
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "created_at": self.created_at,
            "observed_at": self.observed_at,
            "query": dict(self.query),
            "coverage": self.coverage.to_dict(),
            "evidence_digests": [e.to_dict() for e in self.evidence_digests],
            "disclaimer": self.disclaimer,
        }
