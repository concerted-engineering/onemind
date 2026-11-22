"""Offline, read-only local repository source scanner (instruction files and ADRs).

SECURITY INVARIANTS:
- Read-only: never modifies the repository or working tree.
- Offline: zero network calls, zero external models, zero telemetry.
- Safe paths: blocks path traversal and symlink escapes outside the repository root.
- Bounded reads: skips oversized or binary/malformed files with explicit warnings.
- Inert: imported documentation NEVER creates or activates approved practices.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from onemind.adapters.interfaces import IngestionResult, SourceAdapter, SourceInventoryItem
from onemind.protocol.canonical import excerpt_sha256
from onemind.protocol.models import (
    ActorType,
    AuthorityClass,
    RecordDict,
    RecordKind,
    generate_record_id,
    make_envelope,
    utc_now_iso,
)
from onemind.storage.mutation import get_head_commit

DEFAULT_MAX_FILE_BYTES = 1_048_576  # 1 MiB

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".onemind",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)

_INSTRUCTION_FILENAMES: frozenset[str] = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        ".cursorrules",
        "SKILL.md",
    }
)

_ADR_DIR_PREFIXES: tuple[str, ...] = (
    "docs/adr/",
    "docs/decisions/",
    "docs/architecture/decisions/",
    "adr/",
)

_NORMATIVE_RE = re.compile(
    r"\b(MUST NOT|MUST|SHALL NOT|SHALL|NEVER|ALWAYS|REQUIRED|DO NOT)\b",
    re.IGNORECASE,
)

_OWNER_RE = re.compile(
    r"^(?:[-*+]\s+)?(?:\*\*)?(?:owner|author|deciders?|maintainer)(?:\*\*)?\s*:\s*(?:\*\*)?(.+?)(?:\*\*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CandidateStatement:
    """A heuristically extracted normative statement from a local source file (always unverified)."""

    source_path: str
    line_number: int
    text: str
    excerpt_sha256: str
    provenance: str = "heuristic-unverified"
    authority_class: str = AuthorityClass.UNVERIFIED_SOURCE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "line_number": self.line_number,
            "text": self.text,
            "excerpt_sha256": self.excerpt_sha256,
            "provenance": self.provenance,
            "authority_class": self.authority_class,
        }


@dataclass(frozen=True)
class ScanReport:
    """Complete read-only offline inventory produced by `onemind scan`."""

    repo_path: str
    head_commit: str | None
    sources: list[SourceInventoryItem]
    candidate_statements: list[CandidateStatement]
    warnings: list[str]
    existing_store_summary: dict[str, Any] = field(default_factory=dict)
    assurances: tuple[str, ...] = (
        "No practices activated.",
        "No files modified.",
        "No network or external model calls made.",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "head_commit": self.head_commit,
            "source_count": len(self.sources),
            "available_source_count": sum(1 for s in self.sources if s.source_state == "available"),
            "candidate_statement_count": len(self.candidate_statements),
            "sources": [s.to_dict() for s in self.sources],
            "candidate_statements": [c.to_dict() for c in self.candidate_statements],
            "warnings": self.warnings,
            "existing_store_summary": self.existing_store_summary,
            "assurances": list(self.assurances),
        }


def _sanitize_display_text(text: str) -> str:
    """Strip non-printable control characters (ANSI escapes, NULs) from untrusted text."""
    # Remove ANSI escape sequences
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    # Replace remaining control characters except space/tab
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", cleaned)
    return cleaned.strip()


def _is_within_root(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _classify_source_type(rel_posix: str) -> str | None:
    name = Path(rel_posix).name
    if name in _INSTRUCTION_FILENAMES or rel_posix == ".github/copilot-instructions.md":
        return "instruction-file"
    if rel_posix.endswith(".md") and any(rel_posix.startswith(p) for p in _ADR_DIR_PREFIXES):
        return "adr"
    return None


def _extract_normative_statements(
    rel_posix: str, text: str
) -> tuple[list[CandidateStatement], str | None]:
    """Heuristically locate normative sentences outside fenced code blocks."""
    statements: list[CandidateStatement] = []
    owner: str | None = None
    in_code_fence = False

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith(("```", "~~~")):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence or not stripped:
            continue

        owner_match = _OWNER_RE.match(stripped)
        if owner_match and owner is None:
            owner = _sanitize_display_text(owner_match.group(1))

        # Skip markdown headings & HTML comments
        if stripped.startswith("#") or stripped.startswith("<!--"):
            continue
        # Strip leading list bullet/number
        clean_line = re.sub(r"^(?:[-*+]|\d+\.)\s+", "", stripped)
        clean_line = _sanitize_display_text(clean_line)
        if len(clean_line) < 12 or len(clean_line) > 400:
            continue
        if _NORMATIVE_RE.search(clean_line):
            statements.append(
                CandidateStatement(
                    source_path=rel_posix,
                    line_number=lineno,
                    text=clean_line,
                    excerpt_sha256=excerpt_sha256(clean_line),
                )
            )

    return statements, owner


def scan_repository(
    repo_path: Path | str,
    *,
    include_paths: Sequence[str] = (),
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> ScanReport:
    """Perform an offline, read-only scan of local instruction files and ADRs."""
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repository path does not exist or is not a directory: {repo_path}")

    head_commit = get_head_commit(root)
    warnings: list[str] = []
    candidate_paths: list[tuple[Path, str, str]] = []  # (abs_path, rel_posix, source_type)
    seen_rel: set[str] = set()

    # Walk repository without following directory symlinks (prevents symlink loops/escapes)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        cur_dir = Path(dirpath)
        for fname in sorted(filenames):
            fpath = cur_dir / fname
            try:
                rel_posix = fpath.relative_to(root).as_posix()
            except ValueError:
                continue
            src_type = _classify_source_type(rel_posix)
            if src_type is not None and rel_posix not in seen_rel:
                seen_rel.add(rel_posix)
                candidate_paths.append((fpath, rel_posix, src_type))

    # Add explicit user-specified paths (strictly checked against traversal)
    for inc in include_paths:
        inc_path = Path(inc)
        abs_inc = inc_path if inc_path.is_absolute() else root / inc_path
        if ".." in Path(inc).parts or not _is_within_root(root, abs_inc):
            warnings.append(
                f"Rejected --include path {inc!r}: path traversal or escape outside repository root."
            )
            continue
        if not abs_inc.exists():
            warnings.append(f"Included path {inc!r} does not exist.")
            continue
        if abs_inc.is_file():
            rel_posix = abs_inc.resolve().relative_to(root).as_posix()
            if rel_posix not in seen_rel:
                seen_rel.add(rel_posix)
                candidate_paths.append((abs_inc, rel_posix, "custom-file"))
        elif abs_inc.is_dir():
            for p in sorted(abs_inc.rglob("*")):
                if p.is_file() and _is_within_root(root, p):
                    rel_posix = p.resolve().relative_to(root).as_posix()
                    if rel_posix not in seen_rel:
                        seen_rel.add(rel_posix)
                        candidate_paths.append((p, rel_posix, "custom-file"))

    candidate_paths.sort(key=lambda item: item[1])

    sources: list[SourceInventoryItem] = []
    all_statements: list[CandidateStatement] = []
    content_hashes: dict[str, list[str]] = {}

    for fpath, rel_posix, src_type in candidate_paths:
        # Symlink escape check
        if fpath.is_symlink():
            if not _is_within_root(root, fpath):
                warnings.append(
                    f"Skipped symlink {rel_posix!r}: target escapes repository root."
                )
                sources.append(
                    SourceInventoryItem(
                        source_type=src_type,
                        source_uri=f"repo://{rel_posix}",
                        source_id=rel_posix,
                        source_state="skipped-symlink-escape",
                        metadata={"reason": "Symlink target resolves outside repository root."},
                    )
                )
                continue
            if not fpath.exists():
                warnings.append(f"Skipped broken symlink {rel_posix!r}.")
                sources.append(
                    SourceInventoryItem(
                        source_type=src_type,
                        source_uri=f"repo://{rel_posix}",
                        source_id=rel_posix,
                        source_state="source-unavailable",
                        metadata={"reason": "Broken symlink."},
                    )
                )
                continue

        try:
            size = fpath.stat().st_size
        except OSError as exc:
            warnings.append(f"Could not stat {rel_posix!r}: {exc}")
            sources.append(
                SourceInventoryItem(
                    source_type=src_type,
                    source_uri=f"repo://{rel_posix}",
                    source_id=rel_posix,
                    source_state="source-unavailable",
                )
            )
            continue

        if size > max_file_bytes:
            warnings.append(
                f"Skipped oversized file {rel_posix!r} ({size} bytes > limit {max_file_bytes} bytes)."
            )
            sources.append(
                SourceInventoryItem(
                    source_type=src_type,
                    source_uri=f"repo://{rel_posix}",
                    source_id=rel_posix,
                    source_state="skipped-too-large",
                    metadata={"size_bytes": size, "limit_bytes": max_file_bytes},
                )
            )
            continue

        try:
            raw_bytes = fpath.read_bytes()
        except OSError as exc:
            warnings.append(f"Could not read {rel_posix!r}: {exc}")
            sources.append(
                SourceInventoryItem(
                    source_type=src_type,
                    source_uri=f"repo://{rel_posix}",
                    source_id=rel_posix,
                    source_state="source-unavailable",
                )
            )
            continue

        if b"\x00" in raw_bytes:
            warnings.append(f"Skipped binary file {rel_posix!r} (contains NUL bytes).")
            sources.append(
                SourceInventoryItem(
                    source_type=src_type,
                    source_uri=f"repo://{rel_posix}",
                    source_id=rel_posix,
                    source_state="skipped-unreadable",
                    metadata={"reason": "Binary content detected."},
                )
            )
            continue

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            warnings.append(f"Skipped non-UTF-8 file {rel_posix!r}.")
            sources.append(
                SourceInventoryItem(
                    source_type=src_type,
                    source_uri=f"repo://{rel_posix}",
                    source_id=rel_posix,
                    source_state="skipped-unreadable",
                    metadata={"reason": "Invalid UTF-8 encoding."},
                )
            )
            continue

        file_hash = excerpt_sha256(raw_bytes)
        content_hashes.setdefault(file_hash, []).append(rel_posix)
        line_count = len(text.splitlines())

        stmts, owner = _extract_normative_statements(rel_posix, text)
        all_statements.extend(stmts)

        if stmts and owner is None:
            warnings.append(
                f"Source {rel_posix!r} contains {len(stmts)} normative statement(s) without a cited Owner/Author."
            )

        sources.append(
            SourceInventoryItem(
                source_type=src_type,
                source_uri=f"repo://{rel_posix}",
                source_id=rel_posix,
                source_state="available",
                excerpt_sha256=file_hash,
                source_revision=head_commit,
                source_author=owner or "unspecified",
                line_start=1,
                line_end=line_count,
                metadata={
                    "size_bytes": size,
                    "line_count": line_count,
                    "candidate_statement_count": len(stmts),
                    "authority_class": AuthorityClass.UNVERIFIED_SOURCE.value,
                },
            )
        )

    for digest, paths in sorted(content_hashes.items()):
        if len(paths) > 1:
            warnings.append(
                f"Duplicate source content detected (sha256={digest[:12]}...): {', '.join(paths)}"
            )

    # Summarize any existing .onemind/ records without modifying anything
    store_summary: dict[str, Any] = {"present": False, "record_files": 0}
    records_dir = root / ".onemind" / "records"
    if records_dir.is_dir():
        rec_files = list(records_dir.rglob("*.json"))
        store_summary = {
            "present": True,
            "record_files": len(rec_files),
            "note": "Run `onemind validate` to verify canonical records and governance invariants.",
        }

    return ScanReport(
        repo_path=str(root),
        head_commit=head_commit,
        sources=sources,
        candidate_statements=all_statements,
        warnings=warnings,
        existing_store_summary=store_summary,
    )


class LocalFilesSourceAdapter(SourceAdapter):
    """SourceAdapter implementation that ingests local repo docs as inert Evidence records."""

    name: str = "local-files"
    version: str = "0.1.0"

    def __init__(self, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        self.max_file_bytes = max_file_bytes

    def ingest(self, source_path: Path, *, repository: str) -> IngestionResult:
        report = scan_repository(source_path, max_file_bytes=self.max_file_bytes)
        now = utc_now_iso()
        ev_records: list[RecordDict] = []

        for stmt in report.candidate_statements:
            body: dict[str, Any] = {
                "source_type": "local-file",
                "source_uri": f"repo://{stmt.source_path}#L{stmt.line_number}",
                "source_id": f"{stmt.source_path}:{stmt.line_number}",
                "observed_at": now,
                "source_author": "unspecified",
                "excerpt_sha256": stmt.excerpt_sha256,
                "source_state": "available",
                "redacted_snapshot": stmt.text,
            }
            if report.head_commit:
                body["source_revision"] = report.head_commit
            ev_records.append(
                make_envelope(
                    kind=RecordKind.EVIDENCE,
                    body=body,
                    actor_type=ActorType.SERVICE,
                    actor_id=f"adapter:{self.name}",
                    record_id=generate_record_id("ev"),
                    created_at=now,
                )
            )

        return IngestionResult(
            adapter_name=self.name,
            adapter_version=self.version,
            evidence_records=ev_records,
            items=report.sources,
            warnings=report.warnings,
            complete=all(s.source_state == "available" for s in report.sources),
        )
