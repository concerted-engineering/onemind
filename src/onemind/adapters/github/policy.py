"""Privacy capture policy and deterministic secret/PII redaction for M2A ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from onemind.protocol.canonical import excerpt_sha256

CaptureMode = Literal["metadata-only", "redacted-excerpt"]
VALID_CAPTURE_MODES: frozenset[str] = frozenset({"metadata-only", "redacted-excerpt"})

REDACTOR_VERSION = "onemind-redactor/0.1-draft"
DEFAULT_MAX_EXCERPT_CHARS = 500
MAX_ALLOWED_EXCERPT_CHARS = 4000

REDACTION_LIMITATIONS: tuple[str, ...] = (
    "Heuristic pattern redaction targets common tokens, keys, credentials, and email addresses.",
    "It does not guarantee 100% removal of domain-specific secrets, obfuscated credentials, or contextual PII.",
    "Default capture policy is 'metadata-only', which omits source text completely.",
)

# Ordered redaction rules: (rule_name, compiled_regex, replacement)
_TOKEN_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "pem-private-key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
        "[REDACTED:PRIVATE_KEY]",
    ),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
        "[REDACTED:GITHUB_TOKEN]",
    ),
    (
        "aws-access-key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[REDACTED:AWS_KEY]",
    ),
    (
        "provider-api-token",
        re.compile(
            r"\b(?:xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{16,}|pk_(?:live|test)_[A-Za-z0-9]{16,})\b"
        ),
        "[REDACTED:API_TOKEN]",
    ),
    (
        "auth-header-credential",
        re.compile(
            r"(?i)\b((?:authorization\s*:\s*)?(?:bearer|token|basic)\s+)[A-Za-z0-9._~+/=-]{8,}"
        ),
        r"\1[REDACTED:CREDENTIAL]",
    ),
    (
        "url-embedded-credentials",
        re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"),
        r"\1[REDACTED:URL_CREDS]@",
    ),
    (
        "inline-secret-assignment",
        re.compile(
            r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key|client[_-]?secret|aws_secret_access_key)"
            r"(\s*[:=]\s*['\"]?)([^\s'\",;]{6,})(['\"]?)"
        ),
        r"\1\2[REDACTED:SECRET]\4",
    ),
)

_EMAIL_RULE: tuple[str, re.Pattern[str], str] = (
    "email-address",
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "[REDACTED:EMAIL]",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class DataCapturePolicy:
    """Explicit privacy policy governing how GitHub source text is stored in Evidence."""

    mode: CaptureMode = "metadata-only"
    max_excerpt_chars: int = DEFAULT_MAX_EXCERPT_CHARS
    redact_tokens: bool = True
    redact_emails: bool = True

    def __post_init__(self) -> None:
        if self.mode not in VALID_CAPTURE_MODES:
            raise ValueError(
                f"Invalid capture policy mode {self.mode!r}; expected one of {sorted(VALID_CAPTURE_MODES)}."
            )
        if self.max_excerpt_chars < 32 or self.max_excerpt_chars > MAX_ALLOWED_EXCERPT_CHARS:
            raise ValueError(
                f"max_excerpt_chars must be between 32 and {MAX_ALLOWED_EXCERPT_CHARS}, got {self.max_excerpt_chars}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "max_excerpt_chars": self.max_excerpt_chars,
            "redact_tokens": self.redact_tokens,
            "redact_emails": self.redact_emails,
            "redactor_version": REDACTOR_VERSION,
        }


@dataclass(frozen=True)
class RedactionResult:
    """Deterministic output and provenance metadata from applying DataCapturePolicy."""

    redacted_text: str | None
    redaction_count: int
    patterns_applied: tuple[str, ...]
    truncated: bool
    original_length: int
    raw_sha256: str
    redacted_sha256: str | None

    def to_provenance_dict(self, policy: DataCapturePolicy) -> dict[str, Any]:
        out: dict[str, Any] = {
            "policy": policy.mode,
            "redactor_version": REDACTOR_VERSION,
            "snapshot_omitted": self.redacted_text is None,
            "redaction_count": self.redaction_count,
            "patterns_applied": list(self.patterns_applied),
            "truncated": self.truncated,
            "original_length": self.original_length,
            "limitations": list(REDACTION_LIMITATIONS),
        }
        if self.redacted_sha256 is not None:
            out["redacted_sha256"] = self.redacted_sha256
        return out


def sanitize_untrusted_text(text: str) -> str:
    """Strip ANSI escape sequences and non-printable control characters from untrusted text."""
    cleaned = _ANSI_RE.sub("", text)
    cleaned = _CTRL_RE.sub(" ", cleaned)
    return cleaned


def apply_capture_policy(raw_text: str, policy: DataCapturePolicy) -> RedactionResult:
    """Apply DataCapturePolicy deterministically to untrusted source text.

    In `metadata-only` mode, never returns any text snapshot (only computes raw_sha256).
    In `redacted-excerpt` mode, redacts secrets/PII, strips control sequences, and truncates.
    """
    normalized_raw = raw_text.replace("\r\n", "\n")
    raw_digest = excerpt_sha256(normalized_raw)
    original_len = len(normalized_raw)

    if policy.mode == "metadata-only":
        return RedactionResult(
            redacted_text=None,
            redaction_count=0,
            patterns_applied=(),
            truncated=False,
            original_length=original_len,
            raw_sha256=raw_digest,
            redacted_sha256=None,
        )

    working = sanitize_untrusted_text(normalized_raw).strip()
    applied: list[str] = []
    total_redactions = 0

    if policy.redact_tokens:
        for rule_name, pattern, repl in _TOKEN_RULES:
            working, count = pattern.subn(repl, working)
            if count > 0:
                total_redactions += count
                applied.append(rule_name)

    if policy.redact_emails:
        rule_name, pattern, repl = _EMAIL_RULE
        working, count = pattern.subn(repl, working)
        if count > 0:
            total_redactions += count
            applied.append(rule_name)

    truncated = False
    if len(working) > policy.max_excerpt_chars:
        working = working[: policy.max_excerpt_chars - 3].rstrip() + "..."
        truncated = True

    return RedactionResult(
        redacted_text=working,
        redaction_count=total_redactions,
        patterns_applied=tuple(applied),
        truncated=truncated,
        original_length=original_len,
        raw_sha256=raw_digest,
        redacted_sha256=excerpt_sha256(working),
    )


def scrub_token(message: str, token: str | None) -> str:
    """Remove any literal occurrence of a configured credential from diagnostics/exceptions."""
    cleaned = message
    if token and len(token) >= 4:
        cleaned = cleaned.replace(token, "[REDACTED:TOKEN]")
    # Also run token patterns defensively in case a token fragment or header leaked
    for _, pattern, repl in _TOKEN_RULES:
        cleaned = pattern.sub(repl, cleaned)
    return cleaned
