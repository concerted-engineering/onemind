"""Pure normalization from raw GitHub REST JSON payloads to source-neutral 0.1-draft Evidence.

Separated cleanly from HTTP transport, pagination, storage, and candidate extraction.
All produced records are strictly inert Evidence and NEVER confer governance authority.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from onemind.adapters.github.policy import DataCapturePolicy, apply_capture_policy
from onemind.protocol.canonical import canonical_sha256, excerpt_sha256
from onemind.protocol.models import (
    ActorType,
    RecordDict,
    RecordKind,
    is_valid_rfc3339,
    make_envelope,
)

GITHUB_SOURCE_TYPE_REVIEW = "github-pr-review"
GITHUB_SOURCE_TYPE_REVIEW_COMMENT = "github-pr-review-comment"
GITHUB_SOURCE_TYPE_ISSUE_COMMENT = "github-pr-issue-comment"

_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass(frozen=True)
class NormalizedObservation:
    """Normalized inert Evidence record alongside deterministic deduplication metadata."""

    record: RecordDict
    observation_key: str
    logical_item_key: tuple[str, str, str]  # (repository, source_type, source_id)
    source_timestamp: str


def validate_repository_slug(repository: str) -> tuple[str, str]:
    """Validate GitHub OWNER/REPO format safely without path traversal."""
    if not isinstance(repository, str) or ".." in repository or not _REPO_RE.match(repository):
        raise ValueError(
            f"Invalid GitHub repository {repository!r}; expected 'OWNER/REPO' with safe characters."
        )
    owner, repo = repository.split("/", 1)
    return owner, repo


def _sanitize_github_url(raw_url: Any, fallback_uri: str) -> str:
    """Accept only https:// URLs without control characters or credentials; else return fallback."""
    if not isinstance(raw_url, str) or not raw_url.strip():
        return fallback_uri
    cleaned = raw_url.strip()
    if any(ch in cleaned for ch in ("\r", "\n", "\x00", " ")):
        return fallback_uri
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return fallback_uri
    if parsed.username is not None or parsed.password is not None:
        return fallback_uri
    return cleaned


def compute_observation_key(
    *,
    repository: str,
    source_type: str,
    source_id: str,
    source_revision: str,
    content_digest: str,
) -> str:
    """Compute a deterministic SHA-256 fingerprint for deduplication & edit detection."""
    payload = {
        "repository": repository,
        "source_type": source_type,
        "source_id": source_id,
        "source_revision": source_revision,
        "content_digest": content_digest,
    }
    return canonical_sha256(payload)


def deterministic_record_id(observation_key: str) -> str:
    """Derive a stable, safe, deterministic record_id from an observation fingerprint."""
    return f"gh-ev-{observation_key[:24]}"


def _extract_author(raw_item: dict[str, Any]) -> str:
    user = raw_item.get("user")
    if isinstance(user, dict):
        login = user.get("login")
        if isinstance(login, str) and login.strip():
            return login.strip()
    return "ghost"


def _build_evidence(
    *,
    repository: str,
    source_type: str,
    source_id: str,
    source_uri: str,
    source_author: str,
    source_revision: str,
    raw_text: str,
    source_created_at: str,
    source_updated_at: str | None,
    observed_at: str,
    created_at: str,
    policy: DataCapturePolicy,
    github_meta: dict[str, Any],
    adapter_name: str,
    adapter_version: str,
) -> NormalizedObservation:
    redacted = apply_capture_policy(raw_text, policy)
    obs_key = compute_observation_key(
        repository=repository,
        source_type=source_type,
        source_id=source_id,
        source_revision=source_revision,
        content_digest=redacted.raw_sha256,
    )
    rec_id = deterministic_record_id(obs_key)

    body: dict[str, Any] = {
        "source_type": source_type,
        "source_uri": source_uri,
        "source_id": source_id,
        "source_revision": source_revision,
        "observed_at": observed_at,
        "source_author": source_author,
        "excerpt_sha256": redacted.raw_sha256,
        "source_state": "available",
        "redaction": redacted.to_provenance_dict(policy),
    }
    if redacted.redacted_text is not None:
        body["redacted_snapshot"] = redacted.redacted_text

    ext_gh = dict(github_meta)
    ext_gh.update(
        {
            "repository": repository,
            "source_created_at": source_created_at,
            "source_updated_at": source_updated_at,
            "observation_key": obs_key,
            "adapter": adapter_name,
            "adapter_version": adapter_version,
        }
    )

    rec = make_envelope(
        kind=RecordKind.EVIDENCE,
        record_id=rec_id,
        created_at=created_at,
        actor_type=ActorType.SERVICE,
        actor_id=f"adapter:{adapter_name}",
        body=body,
        extensions={"github": ext_gh},
    )
    return NormalizedObservation(
        record=rec,
        observation_key=obs_key,
        logical_item_key=(repository, source_type, source_id),
        source_timestamp=source_updated_at or source_created_at,
    )


def normalize_review(
    raw: Any,
    *,
    repository: str,
    pr_number: int,
    pr_head_sha: str | None,
    pr_base_sha: str | None,
    observed_at: str,
    created_at: str,
    policy: DataCapturePolicy,
    adapter_name: str = "github-review-history",
    adapter_version: str = "0.1.0",
) -> NormalizedObservation | None:
    """Normalize a GitHub PR review submission into an inert Evidence record."""
    if not isinstance(raw, dict):
        return None
    raw_id = raw.get("id")
    if raw_id is None or isinstance(raw_id, bool):
        return None
    submitted_at = raw.get("submitted_at")
    if not isinstance(submitted_at, str) or not is_valid_rfc3339(submitted_at):
        return None

    gh_id = str(raw_id)
    source_id = f"{repository}#pr{pr_number}:review:{gh_id}"
    fallback_uri = f"github://{repository}/pull/{pr_number}#pullrequestreview-{gh_id}"
    source_uri = _sanitize_github_url(raw.get("html_url"), fallback_uri)
    commit_id = raw.get("commit_id") if isinstance(raw.get("commit_id"), str) and raw.get("commit_id") else (pr_head_sha or "unknown")
    state = str(raw.get("state") or "UNKNOWN")
    raw_body = raw.get("body")
    body_text: str = raw_body if isinstance(raw_body, str) else ""
    # Include state in content hashed if body is empty so state-only reviews have stable digests
    canonical_text = body_text if body_text.strip() else f"[review state={state}]"
    source_revision = f"{commit_id}@{submitted_at}"

    return _build_evidence(
        repository=repository,
        source_type=GITHUB_SOURCE_TYPE_REVIEW,
        source_id=source_id,
        source_uri=source_uri,
        source_author=_extract_author(raw),
        source_revision=source_revision,
        raw_text=canonical_text,
        source_created_at=submitted_at,
        source_updated_at=submitted_at,
        observed_at=observed_at,
        created_at=created_at,
        policy=policy,
        github_meta={
            "github_id": gh_id,
            "pr_number": pr_number,
            "pr_head_sha": pr_head_sha,
            "pr_base_sha": pr_base_sha,
            "commit_id": commit_id,
            "review_state": state,
            "body_present": bool(body_text.strip()),
        },
        adapter_name=adapter_name,
        adapter_version=adapter_version,
    )


def normalize_review_comment(
    raw: Any,
    *,
    repository: str,
    pr_number: int,
    pr_head_sha: str | None,
    pr_base_sha: str | None,
    observed_at: str,
    created_at: str,
    policy: DataCapturePolicy,
    adapter_name: str = "github-review-history",
    adapter_version: str = "0.1.0",
) -> NormalizedObservation | None:
    """Normalize a GitHub inline PR review comment into an inert Evidence record."""
    if not isinstance(raw, dict):
        return None
    raw_id = raw.get("id")
    if raw_id is None or isinstance(raw_id, bool):
        return None
    src_created = raw.get("created_at")
    src_updated = raw.get("updated_at") or src_created
    if not isinstance(src_created, str) or not is_valid_rfc3339(src_created):
        return None
    if not isinstance(src_updated, str) or not is_valid_rfc3339(src_updated):
        return None

    gh_id = str(raw_id)
    source_id = f"{repository}#pr{pr_number}:review_comment:{gh_id}"
    fallback_uri = f"github://{repository}/pull/{pr_number}#discussion_r{gh_id}"
    source_uri = _sanitize_github_url(raw.get("html_url"), fallback_uri)
    commit_id = (
        raw.get("commit_id")
        if isinstance(raw.get("commit_id"), str) and raw.get("commit_id")
        else (pr_head_sha or "unknown")
    )
    raw_body = raw.get("body")
    body_text: str = raw_body if isinstance(raw_body, str) else ""
    source_revision = f"{commit_id}@{src_updated}"

    gh_meta: dict[str, Any] = {
        "github_id": gh_id,
        "pr_number": pr_number,
        "pr_head_sha": pr_head_sha,
        "pr_base_sha": pr_base_sha,
        "commit_id": commit_id,
        "body_present": bool(body_text.strip()),
    }
    if isinstance(raw.get("path"), str):
        gh_meta["path"] = raw["path"]
    if isinstance(raw.get("line"), int):
        gh_meta["line"] = raw["line"]
    if isinstance(raw.get("original_line"), int):
        gh_meta["original_line"] = raw["original_line"]
    if raw.get("in_reply_to_id") is not None:
        gh_meta["in_reply_to_id"] = str(raw["in_reply_to_id"])
    if raw.get("pull_request_review_id") is not None:
        gh_meta["pull_request_review_id"] = str(raw["pull_request_review_id"])

    return _build_evidence(
        repository=repository,
        source_type=GITHUB_SOURCE_TYPE_REVIEW_COMMENT,
        source_id=source_id,
        source_uri=source_uri,
        source_author=_extract_author(raw),
        source_revision=source_revision,
        raw_text=body_text,
        source_created_at=src_created,
        source_updated_at=src_updated,
        observed_at=observed_at,
        created_at=created_at,
        policy=policy,
        github_meta=gh_meta,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
    )


def normalize_issue_comment(
    raw: Any,
    *,
    repository: str,
    pr_number: int,
    pr_head_sha: str | None,
    pr_base_sha: str | None,
    observed_at: str,
    created_at: str,
    policy: DataCapturePolicy,
    adapter_name: str = "github-review-history",
    adapter_version: str = "0.1.0",
) -> NormalizedObservation | None:
    """Normalize a GitHub PR conversation (issue) comment into an inert Evidence record."""
    if not isinstance(raw, dict):
        return None
    raw_id = raw.get("id")
    if raw_id is None or isinstance(raw_id, bool):
        return None
    src_created = raw.get("created_at")
    src_updated = raw.get("updated_at") or src_created
    if not isinstance(src_created, str) or not is_valid_rfc3339(src_created):
        return None
    if not isinstance(src_updated, str) or not is_valid_rfc3339(src_updated):
        return None

    gh_id = str(raw_id)
    source_id = f"{repository}#pr{pr_number}:issue_comment:{gh_id}"
    fallback_uri = f"github://{repository}/pull/{pr_number}#issuecomment-{gh_id}"
    source_uri = _sanitize_github_url(raw.get("html_url"), fallback_uri)
    raw_body = raw.get("body")
    body_text: str = raw_body if isinstance(raw_body, str) else ""
    source_revision = f"issue-comment@{src_updated}"

    return _build_evidence(
        repository=repository,
        source_type=GITHUB_SOURCE_TYPE_ISSUE_COMMENT,
        source_id=source_id,
        source_uri=source_uri,
        source_author=_extract_author(raw),
        source_revision=source_revision,
        raw_text=body_text,
        source_created_at=src_created,
        source_updated_at=src_updated,
        observed_at=observed_at,
        created_at=created_at,
        policy=policy,
        github_meta={
            "github_id": gh_id,
            "pr_number": pr_number,
            "pr_head_sha": pr_head_sha,
            "pr_base_sha": pr_base_sha,
            "body_present": bool(body_text.strip()),
        },
        adapter_name=adapter_name,
        adapter_version=adapter_version,
    )


def normalize_unavailable_observation(
    *,
    repository: str,
    source_type: str,
    source_id: str,
    source_uri: str,
    reason: str,
    http_status: int | None,
    observed_at: str,
    created_at: str,
    prior_record_id: str | None = None,
    adapter_name: str = "github-review-history",
    adapter_version: str = "0.1.0",
) -> NormalizedObservation:
    """Create an explicit source-unavailable Evidence observation ONLY with reliable unavailability proof."""
    unavailable_digest = hashlib.sha256(
        f"source-unavailable:{source_id}:{http_status}:{reason}".encode()
    ).hexdigest()
    source_revision = f"unavailable@{http_status or 'unknown'}"
    obs_key = compute_observation_key(
        repository=repository,
        source_type=source_type,
        source_id=source_id,
        source_revision=source_revision,
        content_digest=unavailable_digest,
    )
    rec_id = deterministic_record_id(obs_key)

    ext_gh: dict[str, Any] = {
        "repository": repository,
        "observation_key": obs_key,
        "unavailability_reason": reason,
        "http_status": http_status,
        "adapter": adapter_name,
        "adapter_version": adapter_version,
    }
    if prior_record_id is not None:
        ext_gh["supersedes_evidence_id"] = prior_record_id

    rec = make_envelope(
        kind=RecordKind.EVIDENCE,
        record_id=rec_id,
        created_at=created_at,
        actor_type=ActorType.SERVICE,
        actor_id=f"adapter:{adapter_name}",
        body={
            "source_type": source_type,
            "source_uri": source_uri,
            "source_id": source_id,
            "source_revision": source_revision,
            "observed_at": observed_at,
            "source_author": "unavailable",
            "excerpt_sha256": excerpt_sha256(""),
            "source_state": "source-unavailable",
        },
        extensions={"github": ext_gh},
    )
    return NormalizedObservation(
        record=rec,
        observation_key=obs_key,
        logical_item_key=(repository, source_type, source_id),
        source_timestamp=observed_at,
    )
