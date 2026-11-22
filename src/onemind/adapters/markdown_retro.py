"""Generic SourceAdapter + Extractor for Markdown engineering retrospectives.

Demonstrates workflow-agnostic ingestion without importing any external
workflow engine assumptions or conferring governance authority.
"""

from __future__ import annotations

import re
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

_ACTION_ITEM_RE = re.compile(r"^\s*[-*+]\s+\[(?:ACTION|PRACTICE|FOLLOWUP)\]\s+(.+)$", re.IGNORECASE)
_META_LINE_RE = re.compile(r"^\s*(?:[-*+]\s+)?([A-Za-z_]+)\s*:\s*(.+)$")


class MarkdownRetroAdapter(SourceAdapter, Extractor):
    """Ingests Markdown retrospective notes into inert Evidence and PracticeCandidate records."""

    name: str = "markdown-retrospective"
    version: str = "0.1.0"

    def ingest(self, source_path: Path, *, repository: str) -> IngestionResult:
        path = Path(source_path)
        if not path.is_file():
            return IngestionResult(
                adapter_name=self.name,
                adapter_version=self.version,
                evidence_records=[],
                items=[],
                warnings=[f"Retrospective file not found: {path}"],
                complete=False,
                unsupported_reason="missing-source-file",
            )

        if path.suffix.lower() not in (".md", ".markdown"):
            return IngestionResult(
                adapter_name=self.name,
                adapter_version=self.version,
                evidence_records=[],
                items=[],
                warnings=[f"Unsupported file extension for Markdown retrospective: {path.suffix}"],
                complete=False,
                unsupported_reason=f"unsupported-format:{path.suffix}",
            )

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        now = utc_now_iso()

        author = "retro-facilitator"
        sprint_id = path.stem
        for line in lines[:15]:
            m = _META_LINE_RE.match(line)
            if not m:
                continue
            key, val = m.group(1).lower(), m.group(2).strip()
            if key in ("facilitator", "author", "owner"):
                author = val
            elif key in ("sprint", "retro_id", "session"):
                sprint_id = val

        ev_records: list[RecordDict] = []
        items: list[SourceInventoryItem] = []

        for lineno, line in enumerate(lines, start=1):
            match = _ACTION_ITEM_RE.match(line)
            if not match:
                continue
            excerpt = match.group(1).strip()
            digest = excerpt_sha256(excerpt)
            src_uri = f"retro://{path.name}#L{lineno}"
            src_id = f"{sprint_id}:L{lineno}"

            items.append(
                SourceInventoryItem(
                    source_type="markdown-retrospective",
                    source_uri=src_uri,
                    source_id=src_id,
                    source_state="available",
                    excerpt_sha256=digest,
                    source_author=author,
                    line_start=lineno,
                    line_end=lineno,
                    redacted_snapshot=excerpt,
                    metadata={"sprint": sprint_id, "repository": repository},
                )
            )

            ev_records.append(
                make_envelope(
                    kind=RecordKind.EVIDENCE,
                    record_id=generate_record_id("evretro"),
                    created_at=now,
                    actor_type=ActorType.SERVICE,
                    actor_id=f"adapter:{self.name}",
                    body={
                        "source_type": "markdown-retrospective",
                        "source_uri": src_uri,
                        "source_id": src_id,
                        "observed_at": now,
                        "source_author": author,
                        "excerpt_sha256": digest,
                        "source_state": "available",
                        "redacted_snapshot": excerpt,
                    },
                    extensions={
                        "workflow_source": {
                            "adapter": self.name,
                            "adapter_version": self.version,
                            "sprint": sprint_id,
                        }
                    },
                )
            )

        return IngestionResult(
            adapter_name=self.name,
            adapter_version=self.version,
            evidence_records=ev_records,
            items=items,
            warnings=[] if ev_records else ["No [ACTION]/[PRACTICE] entries found in retrospective."],
            complete=True,
        )

    def extract(
        self,
        evidence_records: Sequence[RecordDict],
        *,
        repository: str,
    ) -> list[RecordDict]:
        """Convert retrospective Evidence records into inert PracticeCandidates (never active)."""
        now = utc_now_iso()
        candidates: list[RecordDict] = []
        for ev in evidence_records:
            if ev.get("kind") != RecordKind.EVIDENCE:
                continue
            body: dict[str, Any] = ev.get("body", {})
            if body.get("source_type") != "markdown-retrospective":
                continue
            snapshot = body.get("redacted_snapshot")
            if not isinstance(snapshot, str) or not snapshot.strip():
                continue
            candidates.append(
                make_envelope(
                    kind=RecordKind.PRACTICE_CANDIDATE,
                    record_id=generate_record_id("candretro"),
                    created_at=now,
                    actor_type=ActorType.SERVICE,
                    actor_id=f"extractor:{self.name}",
                    body={
                        "statement": snapshot.strip(),
                        "proposed_scope": {
                            "repository": repository,
                        },
                        "evidence_ids": [str(ev["record_id"])],
                        "generator": {
                            "name": self.name,
                            "version": self.version,
                        },
                    },
                )
            )
        return candidates
