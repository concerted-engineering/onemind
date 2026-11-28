"""Trusted-base Git mutation validation for immutable OneMind record stores."""

from __future__ import annotations

import contextlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.validator import ValidationIssue


@dataclass(frozen=True)
class TrustedBaseReport:
    """Result of comparing current store records against a separately established trusted Git base."""

    status: str  # "verified" | "failed" | "unavailable"
    trusted_base: str | None
    resolved_commit: str | None
    checked_files: int
    issues: list[ValidationIssue] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "trusted_base": self.trusted_base,
            "resolved_commit": self.resolved_commit,
            "checked_files": self.checked_files,
        }
        if self.reason is not None:
            out["reason"] = self.reason
        if self.issues:
            out["issues"] = [i.to_dict() for i in self.issues]
        return out


def _run_git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        capture_output=True,
        check=False,
    )


def is_git_repo(repo_path: Path) -> bool:
    """Return True if repo_path is inside a Git working tree."""
    if not repo_path.is_dir():
        return False
    proc = _run_git(repo_path, ["rev-parse", "--is-inside-work-tree"])
    return proc.returncode == 0 and proc.stdout.strip() == b"true"


def get_head_commit(repo_path: Path) -> str | None:
    """Return the resolved HEAD commit SHA, or None if not in a repo or no commits."""
    if not repo_path.is_dir():
        return None
    proc = _run_git(repo_path, ["rev-parse", "--verify", "HEAD^{commit}"])
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace").strip()


def resolve_git_commit(repo_path: Path, ref: str) -> str | None:
    """Resolve a Git ref to a full commit SHA, or None if invalid."""
    if not repo_path.is_dir():
        return None
    proc = _run_git(repo_path, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace").strip()


def git_ledger_sequence(repo_path: Path, store_rel_dir: str = ".onemind") -> dict[str, int]:
    """Derive deterministic commit-addition order for records from Git history.

    Maps record_id -> monotonically increasing integer sequence based on when
    each record file was first added to Git history. Never relies on host clock.
    """
    if not is_git_repo(repo_path):
        return {}
    proc = _run_git(
        repo_path,
        [
            "log",
            "--reverse",
            "--diff-filter=A",
            "--name-only",
            "--pretty=format:",
            "--",
            f"{store_rel_dir}/records",
            f"{store_rel_dir}/batches",
        ],
    )
    if proc.returncode != 0:
        return {}
    order: dict[str, int] = {}
    seq = 0
    for raw_line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or not line.endswith(".json") or line.endswith("/manifest.json"):
            continue
        rec_id = Path(line).stem
        if rec_id not in order:
            order[rec_id] = seq
            seq += 1
    return order


def verify_trusted_base(
    repo_path: Path,
    trusted_base: str | None,
    *,
    store_rel_dir: str = ".onemind",
) -> TrustedBaseReport:
    """Validate that no immutable record present in trusted_base was modified or deleted.

    If trusted_base is None, returns status='unavailable' explicitly.
    If trusted_base is specified but cannot be resolved, returns status='failed' with an error.
    """
    if trusted_base is None:
        return TrustedBaseReport(
            status="unavailable",
            trusted_base=None,
            resolved_commit=None,
            checked_files=0,
            reason=(
                "No trusted Git base was specified. Pass --trusted-base <commit-or-ref> "
                "to verify record immutability against historical commits."
            ),
        )

    repo_root = repo_path.resolve()
    if not is_git_repo(repo_root):
        issue = ValidationIssue(
            code="TRUSTED_BASE_NO_GIT",
            severity="error",
            message=f"Cannot verify trusted base {trusted_base!r}: {repo_root} is not a Git repository.",
        )
        return TrustedBaseReport(
            status="failed",
            trusted_base=trusted_base,
            resolved_commit=None,
            checked_files=0,
            issues=[issue],
            reason=issue.message,
        )

    resolved_commit = resolve_git_commit(repo_root, trusted_base)
    if resolved_commit is None:
        issue = ValidationIssue(
            code="TRUSTED_BASE_UNRESOLVABLE",
            severity="error",
            message=f"Trusted Git base reference {trusted_base!r} could not be resolved to a commit.",
        )
        return TrustedBaseReport(
            status="failed",
            trusted_base=trusted_base,
            resolved_commit=None,
            checked_files=0,
            issues=[issue],
            reason=issue.message,
        )

    # Enumerate all record, batch, and ingestion-run manifest files present at trusted_base
    ls_proc = _run_git(
        repo_root,
        [
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            resolved_commit,
            "--",
            f"{store_rel_dir}/records",
            f"{store_rel_dir}/batches",
            f"{store_rel_dir}/ingestions",
        ],
    )
    if ls_proc.returncode != 0:
        issue = ValidationIssue(
            code="TRUSTED_BASE_READ_ERROR",
            severity="error",
            message=f"Failed to list immutable store tree at {resolved_commit}: {ls_proc.stderr.decode('utf-8', errors='replace')}",
        )
        return TrustedBaseReport(
            status="failed",
            trusted_base=trusted_base,
            resolved_commit=resolved_commit,
            checked_files=0,
            issues=[issue],
            reason=issue.message,
        )

    rel_paths = [
        p.decode("utf-8", errors="surrogateescape")
        for p in ls_proc.stdout.split(b"\x00")
        if p
    ]

    issues: list[ValidationIssue] = []
    checked = 0

    for rel in rel_paths:
        if not rel.endswith(".json"):
            continue
        checked += 1
        rec_id_hint = Path(rel).stem

        show_proc = _run_git(repo_root, ["show", f"{resolved_commit}:{rel}"])
        if show_proc.returncode != 0:
            issues.append(
                ValidationIssue(
                    code="TRUSTED_BASE_READ_ERROR",
                    severity="error",
                    message=f"Could not read {rel} at trusted base {resolved_commit}.",
                    record_id=rec_id_hint,
                )
            )
            continue
        base_bytes = show_proc.stdout

        current_path = repo_root / rel
        if current_path.is_symlink():
            try:
                resolved_target = current_path.resolve(strict=True)
                resolved_target.relative_to(repo_root)
            except (OSError, ValueError):
                issues.append(
                    ValidationIssue(
                        code="IMMUTABLE_RECORD_SYMLINK_ESCAPE",
                        severity="error",
                        message=f"Immutable record path {rel} was replaced with an unsafe symlink.",
                        record_id=rec_id_hint,
                    )
                )
                continue

        if not current_path.exists():
            issues.append(
                ValidationIssue(
                    code="IMMUTABLE_RECORD_DELETED",
                    severity="error",
                    message=(
                        f"Immutable record file {rel} existed at trusted base "
                        f"{resolved_commit[:12]} but was deleted."
                    ),
                    record_id=rec_id_hint,
                    details={"path": rel, "trusted_base": resolved_commit},
                )
            )
            continue

        current_bytes = current_path.read_bytes()
        if current_bytes != base_bytes:
            # Also compare canonical digests for diagnostic detail
            base_digest: str | None = None
            curr_digest: str | None = None
            with contextlib.suppress(Exception):
                base_digest = canonical_sha256(json.loads(base_bytes.decode("utf-8")))
            with contextlib.suppress(Exception):
                curr_digest = canonical_sha256(json.loads(current_bytes.decode("utf-8")))
            issues.append(
                ValidationIssue(
                    code="IMMUTABLE_RECORD_MODIFIED",
                    severity="error",
                    message=(
                        f"Immutable record file {rel} was modified relative to trusted base "
                        f"{resolved_commit[:12]}."
                    ),
                    record_id=rec_id_hint,
                    details={
                        "path": rel,
                        "trusted_base": resolved_commit,
                        "base_sha256": base_digest,
                        "current_sha256": curr_digest,
                    },
                )
            )

    return TrustedBaseReport(
        status="failed" if issues else "verified",
        trusted_base=trusted_base,
        resolved_commit=resolved_commit,
        checked_files=checked,
        issues=issues,
        reason=None if not issues else f"Detected {len(issues)} immutability violation(s) against {resolved_commit[:12]}.",
    )
