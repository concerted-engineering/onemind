"""Generic SourceAdapter + Extractor for structured JSON engineering observations.

Demonstrates an independent, non-Markdown structured representation sharing the
exact same OneMind adapter contract and governance boundaries.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from onemind.adapters.interfaces import (
    Extractor,
    IngestionResult,
    SourceAdapter,
    SourceInventoryItem,
)
from onemind.protocol.canonical import excerpt_sha256
from onemind.protocol.models import (
    ActorType,
    RecordDict,
    RecordKind,
    generate_record_id,
    make_envelope,
    utc_now_iso,
)

SUPPORTED_OBSERVATION_SCHEMA = "structured-observation/v1"


class JsonObservationAdapter(SourceAdapter, Extractor):
    """Ingests structured JSON observation logs into inert Evidence and PracticeCandidates."""

    name: str = "json-observation"
    version: str = "0.1.0"

    def ingest(self, source_path: Path, *, repository: str) -> IngestionResult:
        path = Path(source_path)
        if not path.is_file():
            return IngestionResult(
                adapter_name=self.name,
                adapter_version=self.version,
                evidence_records=[],
                items=[],
                warnings=[f"Observation file not found: {path}"],
                complete=False,
                unsupported_reason="missing-source-file",
            )

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return IngestionResult(
                adapter_name=self.name,
                adapter_version=self.version,
                evidence_records=[],
                items=[],
                warnings=[f"Failed to parse JSON observation file: {exc}"],
                complete=False,
                unsupported_reason="invalid-json",
            )

        if not isinstance(payload, dict):
            return IngestionResult(
                adapter_name=self.name,
                adapter_version=self.version,
                evidence_records=[],
                items=[],
                warnings=["Observation payload must be a JSON object."],
                complete=False,
                unsupported_reason="invalid-payload-type",
            )

        schema = payload.get("observation_schema")
        if schema != SUPPORTED_OBSERVATION_SCHEMA:
            return IngestionResult(
                adapter_name=self.name,
                adapter_version=self.version,
                evidence_records=[],
                items=[],
                warnings=[
                    f"Unsupported observation_schema {schema!r}; expected {SUPPORTED_OBSERVATION_SCHEMA!r}."
                ],
                complete=False,
                unsupported_reason=f"unsupported-schema:{schema}",
            )

        observations = payload.get("observations")
        if not isinstance(observations, list):
            return IngestionResult(
                adapter_name=self.name,
                adapter_version=self.version,
                evidence_records=[],
                items=[],
                warnings=["Observation payload missing 'observations' list."],
                complete=False,
                unsupported_reason="missing-observations-list",
            )

        now = utc_now_iso()
        ev_records: list[RecordDict] = []
        items: list[SourceInventoryItem] = []
        warnings: list[str] = []

        for idx, entry in enumerate(observations):
            if not isinstance(entry, dict):
                warnings.append(f"Skipped non-object observation at index {idx}.")
                continue
            obs_id = str(entry.get("id") or f"obs-{idx + 1}")
            summary = entry.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                warnings.append(f"Skipped observation {obs_id!r}: missing non-empty summary.")
                continue

            author = str(entry.get("reporter") or "ci-telemetry-export")
            observed_at = str(entry.get("observed_at") or now)
            scope_paths = entry.get("paths")
            digest = excerpt_sha256(summary.strip())
            src_uri = f"observation://{path.name}#{obs_id}"

            # Explicitly ignore any authority/auto-approve claims embedded in external JSON!
            if entry.get("approved") or entry.get("auto_activate"):
                warnings.append(
                    f"Ignored non-authoritative approval claim inside observation {obs_id!r}; "
                    "imported observations remain strictly inert."
                )

            items.append(
                SourceInventoryItem(
                    source_type="json-observation",
                    source_uri=src_uri,
                    source_id=obs_id,
                    source_state="available",
                    excerpt_sha256=digest,
                    source_author=author,
                    redacted_snapshot=summary.strip(),
                    metadata={
                        "category": entry.get("category", "general"),
                        "paths": scope_paths if isinstance(scope_paths, list) else [],
                        "repository": repository,
                    },
                )
            )

            ev_ext: dict[str, Any] = {
                "workflow_source": {
                    "adapter": self.name,
                    "adapter_version": self.version,
                    "category": entry.get("category", "general"),
                }
            }
            if isinstance(scope_paths, list) and all(isinstance(p, str) for p in scope_paths):
                ev_ext["workflow_source"]["suggested_paths"] = scope_paths

            ev_records.append(
                make_envelope(
                    kind=RecordKind.EVIDENCE,
                    record_id=generate_record_id("evobs"),
                    created_at=now,
                    actor_type=ActorType.SERVICE,
                    actor_id=f"adapter:{self.name}",
                    body={
                        "source_type": "json-observation",
                        "source_uri": src_uri,
                        "source_id": obs_id,
                        "observed_at": observed_at,
                        "source_author": author,
                        "excerpt_sha256": digest,
                        "source_state": "available",
                        "redacted_snapshot": summary.strip(),
                    },
                    extensions=ev_ext,
                )
            )

        return IngestionResult(
            adapter_name=self.name,
            adapter_version=self.version,
            evidence_records=ev_records,
            items=items,
            warnings=warnings,
            complete=True,
        )

    def extract(
        self,
        evidence_records: Sequence[RecordDict],
        *,
        repository: str,
    ) -> list[RecordDict]:
        """Convert JSON observation Evidence records into inert PracticeCandidates."""
        now = utc_now_iso()
        candidates: list[RecordDict] = []
        for ev in evidence_records:
            if ev.get("kind") != RecordKind.EVIDENCE:
                continue
            body: dict[str, Any] = ev.get("body", {})
            if body.get("source_type") != "json-observation":
                continue
            snapshot = body.get("redacted_snapshot")
            if not isinstance(snapshot, str) or not snapshot.strip():
                continue

            proposed_scope: dict[str, Any] = {"repository": repository}
            wf_ext = ev.get("extensions", {}).get("workflow_source", {})
            suggested_paths = wf_ext.get("suggested_paths")
            if isinstance(suggested_paths, list) and suggested_paths:
                proposed_scope["paths"] = [str(p) for p in suggested_paths]

            candidates.append(
                make_envelope(
                    kind=RecordKind.PRACTICE_CANDIDATE,
                    record_id=generate_record_id("candobs"),
                    created_at=now,
                    actor_type=ActorType.SERVICE,
                    actor_id=f"extractor:{self.name}",
                    body={
                        "statement": snapshot.strip(),
                        "proposed_scope": proposed_scope,
                        "evidence_ids": [str(ev["record_id"])],
                        "generator": {
                            "name": self.name,
                            "version": self.version,
                        },
                    },
                )
            )
        return candidates
