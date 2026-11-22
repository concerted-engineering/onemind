"""Core protocol constants, enums, and record construction helpers (0.1-draft experimental)."""

from __future__ import annotations

import re
import secrets
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "0.1-draft"
PROTOCOL_STATUS = "experimental"
COMPUTATION_VERSION = "onemind-projection/0.1-draft"

RecordDict = dict[str, Any]

_SAFE_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


class RecordKind(StrEnum):
    """The seven immutable OneMind v0.1-draft record kinds."""

    EVIDENCE = "evidence"
    PRACTICE_CANDIDATE = "practice_candidate"
    PRACTICE_VERSION = "practice_version"
    GOVERNANCE_DECISION = "governance_decision"
    CONFLICT = "conflict"
    APPLICATION = "application"
    OUTCOME_EVENT = "outcome_event"


ALL_RECORD_KINDS: frozenset[str] = frozenset(k.value for k in RecordKind)


class ActorType(StrEnum):
    HUMAN = "human"
    SERVICE = "service"
    AGENT = "agent"


class GovernanceAction(StrEnum):
    APPROVE = "approve"
    SUSPEND = "suspend"
    RESTORE = "restore"
    RETIRE = "retire"
    ADJUDICATE = "adjudicate"


class ConflictRelation(StrEnum):
    CONTRADICTS = "contradicts"
    SCOPE_OVERLAP = "scope-overlap"
    POSSIBLE_EXCEPTION = "possible-exception"


class AdjudicationDisposition(StrEnum):
    PREFER_LEFT = "prefer-left"
    PREFER_RIGHT = "prefer-right"
    SCOPED_EXCEPTION = "scoped-exception"
    MAINTAIN_BOTH = "maintain-both"
    SUSPEND_BOTH = "suspend-both"


class AuthorityClass(StrEnum):
    """Explicit trust and provenance labels for records and sources."""

    UNVERIFIED_SOURCE = "unverified-source-document"
    INERT_EVIDENCE = "inert-evidence"
    INERT_CANDIDATE = "inert-candidate"
    UNAPPROVED = "unapproved"
    LOCAL_OWNER_ASSERTION = "local-owner-assertion"
    VERIFIED_REPOSITORY_APPROVAL = "verified-repository-approval"
    INVALID_AUTHORITY = "invalid-authority"


class ResolutionState(StrEnum):
    """Explicit outcome states for effective-state resolution."""

    NONE = "none"
    ACTIVE = "active"
    AMBIGUOUS = "ambiguous"
    SUSPENDED = "suspended"
    INDETERMINATE = "indeterminate"


def is_safe_record_id(record_id: str) -> bool:
    """Return True if record_id is safe for filesystem path mapping and free of traversal."""
    if not isinstance(record_id, str):
        return False
    if ".." in record_id or "/" in record_id or "\\" in record_id or "\x00" in record_id:
        return False
    return bool(_SAFE_RECORD_ID_RE.match(record_id))


def is_valid_rfc3339(value: str) -> bool:
    """Validate a strict RFC 3339 timestamp with explicit timezone designator."""
    if not isinstance(value, str) or not _RFC3339_RE.match(value):
        return False
    try:
        parse_rfc3339(value)
    except ValueError:
        return False
    return True


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC 3339 timestamp string into a timezone-aware UTC datetime."""
    if not isinstance(value, str) or not _RFC3339_RE.match(value):
        raise ValueError(f"Invalid RFC 3339 timestamp: {value!r}")
    normalized = value
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    # Truncate sub-microsecond fractional digits if > 6 digits
    dot_idx = normalized.find(".")
    if dot_idx != -1:
        tz_idx = max(normalized.find("+", dot_idx), normalized.find("-", dot_idx))
        frac = normalized[dot_idx + 1 : tz_idx]
        if len(frac) > 6:
            normalized = normalized[: dot_idx + 1] + frac[:6] + normalized[tz_idx:]
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp missing timezone offset: {value!r}")
    return dt.astimezone(UTC)


def utc_now_iso() -> str:
    """Return current UTC time formatted as RFC 3339."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_record_id(prefix: str = "rec") -> str:
    """Generate a time-ordered unique opaque record ID safe for filenames."""
    clean_prefix = re.sub(r"[^A-Za-z0-9_-]", "", prefix) or "rec"
    ms = int(time.time() * 1000)
    rand = secrets.token_hex(6)
    return f"{clean_prefix}-{ms:012x}-{rand}"


def make_envelope(
    *,
    kind: RecordKind | str,
    body: dict[str, Any],
    actor_type: ActorType | str = ActorType.HUMAN,
    actor_id: str = "local-developer",
    record_id: str | None = None,
    created_at: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> RecordDict:
    """Construct a standard v0.1-draft record envelope."""
    kind_str = kind.value if isinstance(kind, RecordKind) else str(kind)
    actor_type_str = actor_type.value if isinstance(actor_type, ActorType) else str(actor_type)
    rec: RecordDict = {
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id or generate_record_id(kind_str[:4]),
        "kind": kind_str,
        "created_at": created_at or utc_now_iso(),
        "actor": {
            "type": actor_type_str,
            "id": actor_id,
        },
        "body": body,
    }
    if extensions is not None:
        rec["extensions"] = extensions
    return rec
