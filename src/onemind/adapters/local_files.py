"""Offline, read-only local repository source scanner & per-line provenance capture.

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

CAPTURE_MODE_LEGEND: dict[str, str] = {
    "heuristic-scan": (
        "Automated offline keyword scan of local docs; unverified, may miss context "
        "or produce false positives, and never activates practices."
    ),
    "explicit-capture": (
        "Human-authored statement explicitly linked to a file/line anchor via `onemind propose` "
        "or `propose_from_source()`; stored as an inert candidate and never activated automatically."
    ),
}

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
class LineProvenance:
    """Exact per-line provenance anchor for a local repository file."""

    line_number: int
    source_uri: str
    line_sha256: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "source_uri": self.source_uri,
            "line_sha256": self.line_sha256,
            "text": self.text,
        }


@dataclass(frozen=True)
class CandidateStatement:
    """A heuristically extracted normative statement from a local source file (always unverified)."""

    source_path: str
    line_number: int
    text: str
    excerpt_sha256: str
    source_uri: str = ""
    line_end: int | None = None
    file_sha256: str | None = None
    source_revision: str | None = None
    matched_signal: str = "normative-keyword"
    capture_mode: str = "heuristic-scan"
    provenance: str = "heuristic-unverified"
    authority_class: str = AuthorityClass.UNVERIFIED_SOURCE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_uri": self.source_uri or f"repo://{self.source_path}#L{self.line_number}",
            "line_number": self.line_number,
            "line_end": self.line_end or self.line_number,
            "text": self.text,
            "excerpt_sha256": self.excerpt_sha256,
            "file_sha256": self.file_sha256,
            "source_revision": self.source_revision,
            "matched_signal": self.matched_signal,
            "capture_mode": self.capture_mode,
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
    capture_mode_legend: dict[str, str] = field(default_factory=lambda: dict(CAPTURE_MODE_LEGEND))
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
            "capture_mode_legend": self.capture_mode_legend,
            "assurances": list(self.assurances),
        }


def _sanitize_display_text(text: str) -> str:
    """Strip non-printable control characters (ANSI escapes, NULs) from untrusted text."""
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", cleaned)
    return cleaned.strip()


def _is_within_root(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def parse_source_spec(target: str) -> tuple[str, int | None, int | None]:
    """Parse `path/to/file.md[:line[-end]]` into (path, line_start, line_end)."""
    if ":" not in target:
        return target, None, None
    path_part, line_part = target.rsplit(":", 1)
    if "-" in line_part:
        a, b = line_part.split("-", 1)
        if a.isdigit() and b.isdigit():
            return path_part, int(a), int(b)
    elif line_part.isdigit():
        num = int(line_part)
        return path_part, num, num
    return target, None, None


def _classify_source_type(rel_posix: str) -> str | None:
    name = Path(rel_posix).name
    if name in _INSTRUCTION_FILENAMES or rel_posix == ".github/copilot-instructions.md":
        return "instruction-file"
    if rel_posix.endswith(".md") and any(rel_posix.startswith(p) for p in _ADR_DIR_PREFIXES):
        return "adr"
    return None


def _extract_normative_statements(
    rel_posix: str,
    text: str,
    *,
    file_sha256: str | None = None,
    source_revision: str | None = None,
) -> tuple[list[CandidateStatement], str | None]:
    """Heuristically locate normative sentences outside fenced code blocks with per-line anchors."""
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

        if stripped.startswith("#") or stripped.startswith("<!--"):
            continue
        clean_line = re.sub(r"^(?:[-*+]|\d+\.)\s+", "", stripped)
        clean_line = _sanitize_display_text(clean_line)
        if len(clean_line) < 12 or len(clean_line) > 400:
            continue
        kw_match = _NORMATIVE_RE.search(clean_line)
        if kw_match:
            statements.append(
                CandidateStatement(
                    source_path=rel_posix,
                    source_uri=f"repo://{rel_posix}#L{lineno}",
                    line_number=lineno,
                    line_end=lineno,
                    text=clean_line,
                    excerpt_sha256=excerpt_sha256(clean_line),
                    file_sha256=file_sha256,
                    source_revision=source_revision,
                    matched_signal=f"normative-keyword:{kw_match.group(1).upper()}",
                    capture_mode="heuristic-scan",
                )
            )

    return statements, owner


def inspect_source_lines(
    repo_root: Path,
    source_spec: str,
) -> dict[str, Any]:
    """Read a repository source file (or line slice) safely with per-line SHA-256 provenance."""
    raw_path, line_start, line_end = parse_source_spec(source_spec)
    cand_path = Path(raw_path)
    abs_path = cand_path if cand_path.is_absolute() else (repo_root / cand_path)
    if ".." in Path(raw_path).parts or not _is_within_root(repo_root, abs_path):
        raise ValueError(f"Source path {raw_path!r} escapes repository root {repo_root}.")
    if abs_path.is_symlink() and not _is_within_root(repo_root, abs_path):
        raise ValueError(f"Symlink {raw_path!r} escapes repository root {repo_root}.")
    if not abs_path.is_file():
        raise FileNotFoundError(f"Source file not found: {abs_path}")

    rel_posix = abs_path.resolve().relative_to(repo_root).as_posix()
    text = abs_path.read_text(encoding="utf-8")
    all_lines = text.splitlines()
    file_hash = excerpt_sha256(text)
    head_commit = get_head_commit(repo_root)

    if line_start is not None:
        end = line_end if line_end is not None else line_start
        if line_start < 1 or end < line_start or end > len(all_lines):
            raise ValueError(
                f"Line range {line_start}-{end} out of bounds for {rel_posix} ({len(all_lines)} lines)."
            )
        start_idx = line_start
        end_idx = end
    else:
        start_idx = 1
        end_idx = len(all_lines)

    line_items: list[LineProvenance] = []
    selected_raw_lines: list[str] = []
    for ln in range(start_idx, end_idx + 1):
        raw_ln = all_lines[ln - 1]
        selected_raw_lines.append(raw_ln)
        line_items.append(
            LineProvenance(
                line_number=ln,
                source_uri=f"repo://{rel_posix}#L{ln}",
                line_sha256=excerpt_sha256(raw_ln.strip()),
                text=_sanitize_display_text(raw_ln),
            )
        )

    excerpt = "\n".join(selected_raw_lines).strip()
    stmts, owner = _extract_normative_statements(
        rel_posix, text, file_sha256=file_hash, source_revision=head_commit
    )
    anchor = (
        f"repo://{rel_posix}#L{start_idx}"
        if start_idx == end_idx
        else f"repo://{rel_posix}#L{start_idx}-L{end_idx}"
    ) if line_start is not None else f"repo://{rel_posix}"

    return {
        "source_path": rel_posix,
        "source_uri": anchor,
        "source_id": f"{rel_posix}:{start_idx}" if line_start is not None else rel_posix,
        "line_start": start_idx,
        "line_end": end_idx,
        "file_sha256": file_hash,
        "excerpt_sha256": excerpt_sha256(excerpt),
        "excerpt": excerpt,
        "source_revision": head_commit,
        "owner": owner or "unspecified",
        "lines": line_items,
        "heuristic_statements": stmts,
    }


def capture_source_evidence(
    repo_root: Path,
    source_spec: str,
    *,
    actor_id: str = "local-developer",
    source_author: str | None = None,
    created_at: str | None = None,
    record_id: str | None = None,
) -> RecordDict:
    """Explicitly capture an inert Evidence record with per-line provenance from a real local file."""
    info = inspect_source_lines(repo_root, source_spec)
    now = created_at or utc_now_iso()
    excerpt_text = str(info["excerpt"])
    if info["line_start"] == 1 and info["line_end"] > 20 and ":" not in source_spec:
        excerpt_text = excerpt_text[:500]
    ev_body: dict[str, Any] = {
        "source_type": "local-file",
        "source_uri": info["source_uri"],
        "source_id": info["source_id"],
        "observed_at": now,
        "source_author": source_author or info["owner"] or "repo-maintainer",
        "excerpt_sha256": excerpt_sha256(excerpt_text),
        "source_state": "available",
        "redacted_snapshot": excerpt_text,
    }
    if info["source_revision"]:
        ev_body["source_revision"] = info["source_revision"]

    line_provenance = [lp.to_dict() for lp in info["lines"][:50]]
    return make_envelope(
        kind=RecordKind.EVIDENCE,
        body=ev_body,
        actor_type=ActorType.HUMAN,
        actor_id=actor_id,
        record_id=record_id or generate_record_id("ev"),
        created_at=now,
        extensions={
            "source_provenance": {
                "capture_mode": "explicit-capture",
                "file_sha256": info["file_sha256"],
                "line_start": info["line_start"],
                "line_end": info["line_end"],
                "lines": line_provenance,
            }
        },
    )


def scan_repository(
    repo_path: Path | str,
    *,
    include_paths: Sequence[str] = (),
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> ScanReport:
    """Perform an offline, read-only scan of local instruction files and ADRs with per-line provenance."""
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repository path does not exist or is not a directory: {repo_path}")

    head_commit = get_head_commit(root)
    warnings: list[str] = []
    candidate_paths: list[tuple[Path, str, str]] = []
    seen_rel: set[str] = set()

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

        stmts, owner = _extract_normative_statements(
            rel_posix, text, file_sha256=file_hash, source_revision=head_commit
        )
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
                    "capture_mode": "heuristic-scan",
                    "matched_lines": [s.line_number for s in stmts],
                    "authority_class": AuthorityClass.UNVERIFIED_SOURCE.value,
                },
            )
        )

    for digest, paths in sorted(content_hashes.items()):
        if len(paths) > 1:
            warnings.append(
                f"Duplicate source content detected (sha256={digest[:12]}...): {', '.join(paths)}"
            )

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
                "source_uri": stmt.source_uri or f"repo://{stmt.source_path}#L{stmt.line_number}",
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
                    extensions={
                        "source_provenance": {
                            "capture_mode": "heuristic-scan",
                            "matched_signal": stmt.matched_signal,
                            "line_start": stmt.line_number,
                            "line_end": stmt.line_number,
                            "file_sha256": stmt.file_sha256,
                        }
                    },
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
