"""Read-only GitHub PR review-history RemoteSourceAdapter (M2A)."""

from __future__ import annotations

import copy
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from onemind.adapters.github.coverage import (
    CoverageSummary,
    EndpointGap,
    EvidenceDigestEntry,
    IngestionRunManifest,
)
from onemind.adapters.github.normalize import (
    GITHUB_SOURCE_TYPE_ISSUE_COMMENT,
    GITHUB_SOURCE_TYPE_REVIEW,
    GITHUB_SOURCE_TYPE_REVIEW_COMMENT,
    NormalizedObservation,
    normalize_issue_comment,
    normalize_review,
    normalize_review_comment,
    normalize_unavailable_observation,
    validate_repository_slug,
)
from onemind.adapters.github.policy import DataCapturePolicy
from onemind.adapters.github.transport import (
    DEFAULT_GITHUB_API_ORIGIN,
    GitHubRestClient,
    GitHubTransportError,
    HttpTransport,
)
from onemind.adapters.interfaces import (
    IngestionResult,
    RemoteSourceAdapter,
    SourceInventoryItem,
    SourceRequest,
    Store,
)
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import (
    RecordDict,
    RecordKind,
    generate_record_id,
    is_valid_rfc3339,
    parse_rfc3339,
    utc_now_iso,
)
from onemind.storage.git_store import GitRecordStore

VALID_PR_STATES: frozenset[str] = frozenset({"open", "closed", "all", "merged"})


def _parse_date_or_rfc3339(value: str, *, end_of_day: bool = False) -> datetime:
    """Parse YYYY-MM-DD or full RFC 3339 timestamp into a UTC datetime."""
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        suffix = "T23:59:59Z" if end_of_day else "T00:00:00Z"
        return parse_rfc3339(text + suffix)
    return parse_rfc3339(text)


@dataclass(frozen=True)
class UnavailableProbe:
    """Explicit target to verify for reliable deletion/unavailability (HTTP 404/410)."""

    source_type: str
    source_id: str
    source_uri: str
    api_path: str
    prior_record_id: str | None = None


@dataclass(frozen=True)
class GitHubIngestionRunResult:
    """Full structured result of running GitHubReviewSourceAdapter.ingest_github()."""

    manifest: IngestionRunManifest
    written: bool
    evidence_records: tuple[RecordDict, ...]
    new_or_edited_records: tuple[RecordDict, ...]
    manifest_path: str | None = None

    def to_safe_summary_dict(self) -> dict[str, Any]:
        """Safe console/JSON summary emitting ONLY counts, IDs, and coverage — NEVER raw bodies or tokens."""
        return {
            "written": self.written,
            "dry_run": not self.written,
            "manifest_path": self.manifest_path,
            "run_id": self.manifest.run_id,
            "schema_version": self.manifest.schema_version,
            "adapter_name": self.manifest.adapter_name,
            "adapter_version": self.manifest.adapter_version,
            "observed_at": self.manifest.observed_at,
            "query": self.manifest.query,
            "coverage": self.manifest.coverage.to_dict(),
            "persisted_evidence_ids": [e.record_id for e in self.manifest.evidence_digests],
            "disclaimer": self.manifest.disclaimer,
        }


class GitHubReviewSourceAdapter(RemoteSourceAdapter):
    """Opt-in, read-only GitHub PR review-history SourceAdapter.

    Produces strictly inert 0.1-draft Evidence records and complete or explicitly
    incomplete IngestionRun coverage manifests.
    """

    name: str = "github-review-history"
    version: str = "0.1.0"

    def __init__(
        self,
        *,
        client: GitHubRestClient | None = None,
        transport: HttpTransport | None = None,
        api_origin: str = DEFAULT_GITHUB_API_ORIGIN,
        token: str | None = None,
        token_env_name: str | None = None,
        capture_policy: DataCapturePolicy | None = None,
        per_page: int = 100,
    ) -> None:
        self.client = client or GitHubRestClient(
            transport=transport,
            api_origin=api_origin,
            token=token,
        )
        self.token_env_name = token_env_name
        self.capture_policy = capture_policy or DataCapturePolicy(mode="metadata-only")
        self.per_page = max(1, min(per_page, 100))

    def _index_existing_evidence(
        self, store: Store | None
    ) -> tuple[set[str], dict[tuple[str, str, str], str]]:
        """Build lookup sets from existing store Evidence for deduplication and edit linking."""
        existing_ids: set[str] = set()
        latest_by_logical: dict[tuple[str, str, str], str] = {}
        if store is None:
            return existing_ids, latest_by_logical

        for rec in store.list_records(kind=RecordKind.EVIDENCE):
            rid = str(rec.get("record_id", ""))
            existing_ids.add(rid)
            body = rec.get("body", {})
            src_type = str(body.get("source_type", ""))
            src_id = str(body.get("source_id", ""))
            repo = str(rec.get("extensions", {}).get("github", {}).get("repository", ""))
            if not repo and "#" in src_id:
                repo = src_id.split("#", 1)[0]
            if repo and src_type and src_id:
                latest_by_logical[(repo, src_type, src_id)] = rid
        return existing_ids, latest_by_logical

    def ingest_github(
        self,
        request: SourceRequest,
        *,
        store: Store | None = None,
        write: bool = False,
        run_id: str | None = None,
        observed_at: str | None = None,
        created_at: str | None = None,
        unavailable_probes: Sequence[UnavailableProbe] = (),
    ) -> GitHubIngestionRunResult:
        """Execute one bounded, read-only GitHub PR review ingestion run."""
        owner, repo_name = validate_repository_slug(request.repository)
        repository = f"{owner}/{repo_name}"

        since_dt = _parse_date_or_rfc3339(request.since, end_of_day=False) if request.since else None
        until_dt = _parse_date_or_rfc3339(request.until, end_of_day=True) if request.until else None
        if since_dt is not None and until_dt is not None and since_dt > until_dt:
            raise ValueError(
                f"Invalid time window: since ({request.since}) is later than until ({request.until})."
            )

        requested_states = tuple(s.lower() for s in (request.states or ("all",)))
        for st in requested_states:
            if st not in VALID_PR_STATES:
                raise ValueError(
                    f"Invalid PR state {st!r}; expected one of {sorted(VALID_PR_STATES)}."
                )
        if request.max_items < 1:
            raise ValueError(f"max_prs must be >= 1, got {request.max_items}.")
        if request.max_pages < 1:
            raise ValueError(f"max_pages must be >= 1, got {request.max_pages}.")

        # GitHub REST /pulls supports state=open|closed|all; "merged" is a filter over closed PRs
        api_state = "closed" if requested_states == ("merged",) else (
            requested_states[0] if len(requested_states) == 1 else "all"
        )
        only_merged = requested_states == ("merged",)

        obs_ts = observed_at or utc_now_iso()
        crt_ts = created_at or obs_ts
        effective_run_id = run_id or generate_record_id("ghrun")

        existing_ids, latest_by_logical = self._index_existing_evidence(store)

        gaps: list[EndpointGap] = []
        incomplete_reasons: list[str] = []
        pages_attempted = 0
        pages_fetched = 0
        retries_performed = 0
        filtered_records = 0

        # 1. Enumerate PRs in bounded window
        pr_query = urllib.parse.urlencode(
            {
                "state": api_state,
                "sort": "updated",
                "direction": "desc",
                "per_page": str(self.per_page),
            }
        )
        pulls_url = f"{self.client.api_origin}/repos/{owner}/{repo_name}/pulls?{pr_query}"

        def _stop_past_since(page_items: list[Any]) -> bool:
            if since_dt is None or not page_items:
                return False
            # Sorted by updated desc: once the last valid PR on a page has updated_at < since,
            # all subsequent pages are strictly older than since.
            last = page_items[-1]
            if isinstance(last, dict):
                upd = last.get("updated_at")
                if isinstance(upd, str) and is_valid_rfc3339(upd):
                    return parse_rfc3339(upd) < since_dt
            return False

        pr_pages = self.client.paginate_json_list(
            pulls_url,
            max_pages=request.max_pages,
            stop_predicate=_stop_past_since,
        )
        pages_attempted += pr_pages.pages_attempted
        pages_fetched += pr_pages.pages_fetched
        retries_performed += pr_pages.retries_performed

        if not pr_pages.complete:
            code = pr_pages.error_code or "pr_list_incomplete"
            incomplete_reasons.append(code)
            gaps.append(
                EndpointGap(
                    endpoint=f"/repos/{owner}/{repo_name}/pulls",
                    reason_code=code,
                    message=pr_pages.error_message or "Pull request listing incomplete.",
                    http_status=pr_pages.http_status,
                )
            )

        # Filter PRs by window, state, and max_items (truncation marks incomplete!)
        selected_prs: list[dict[str, Any]] = []
        seen_pr_numbers: set[int] = set()
        truncated_prs = False

        for raw_pr in pr_pages.items:
            if not isinstance(raw_pr, dict) or not isinstance(raw_pr.get("number"), int):
                filtered_records += 1
                incomplete_reasons.append("malformed_pr_entry")
                continue
            pr_num = int(raw_pr["number"])
            if pr_num in seen_pr_numbers:
                continue
            seen_pr_numbers.add(pr_num)

            upd_str = raw_pr.get("updated_at") or raw_pr.get("created_at")
            if not isinstance(upd_str, str) or not is_valid_rfc3339(upd_str):
                filtered_records += 1
                incomplete_reasons.append("malformed_pr_timestamp")
                continue
            upd_dt = parse_rfc3339(upd_str)
            if since_dt is not None and upd_dt < since_dt:
                continue
            if until_dt is not None and upd_dt > until_dt:
                continue
            if only_merged and not raw_pr.get("merged_at"):
                continue

            if len(selected_prs) >= request.max_items:
                truncated_prs = True
                break
            selected_prs.append(raw_pr)

        if truncated_prs:
            incomplete_reasons.append("max_prs_truncated")
            gaps.append(
                EndpointGap(
                    endpoint=f"/repos/{owner}/{repo_name}/pulls",
                    reason_code="max_prs_truncated",
                    message=(
                        f"Selected PR count reached max_prs={request.max_items}; "
                        "additional matching PRs were not fetched."
                    ),
                )
            )

        # Sort selected PRs deterministically by PR number regardless of API traversal order
        selected_prs.sort(key=lambda pr: int(pr["number"]))

        # 2. Fetch reviews, inline review comments, and conversation comments for each selected PR
        all_observations: list[NormalizedObservation] = []
        prs_completed = 0
        prs_incomplete = 0
        reviews_collected = 0
        review_comments_collected = 0
        issue_comments_collected = 0

        for pr in selected_prs:
            pr_num = int(pr["number"])
            head_sha = (
                pr.get("head", {}).get("sha")
                if isinstance(pr.get("head"), dict) and isinstance(pr.get("head", {}).get("sha"), str)
                else None
            )
            base_sha = (
                pr.get("base", {}).get("sha")
                if isinstance(pr.get("base"), dict) and isinstance(pr.get("base", {}).get("sha"), str)
                else None
            )
            pr_ok = True

            endpoints = (
                (
                    f"/repos/{owner}/{repo_name}/pulls/{pr_num}/reviews",
                    f"{self.client.api_origin}/repos/{owner}/{repo_name}/pulls/{pr_num}/reviews?per_page={self.per_page}",
                    GITHUB_SOURCE_TYPE_REVIEW,
                ),
                (
                    f"/repos/{owner}/{repo_name}/pulls/{pr_num}/comments",
                    f"{self.client.api_origin}/repos/{owner}/{repo_name}/pulls/{pr_num}/comments?per_page={self.per_page}",
                    GITHUB_SOURCE_TYPE_REVIEW_COMMENT,
                ),
                (
                    f"/repos/{owner}/{repo_name}/issues/{pr_num}/comments",
                    f"{self.client.api_origin}/repos/{owner}/{repo_name}/issues/{pr_num}/comments?per_page={self.per_page}",
                    GITHUB_SOURCE_TYPE_ISSUE_COMMENT,
                ),
            )

            for ep_label, ep_url, src_type in endpoints:
                ep_res = self.client.paginate_json_list(ep_url, max_pages=request.max_pages)
                pages_attempted += ep_res.pages_attempted
                pages_fetched += ep_res.pages_fetched
                retries_performed += ep_res.retries_performed

                if not ep_res.complete:
                    pr_ok = False
                    code = ep_res.error_code or "endpoint_incomplete"
                    incomplete_reasons.append(code)
                    gaps.append(
                        EndpointGap(
                            endpoint=ep_label,
                            reason_code=code,
                            message=ep_res.error_message or f"Failed fetching {ep_label}.",
                            pr_number=pr_num,
                            http_status=ep_res.http_status,
                        )
                    )

                for raw_item in ep_res.items:
                    # Explicit tombstone marker check (reliable unavailability signal)
                    if isinstance(raw_item, dict) and raw_item.get("deleted") is True and raw_item.get("id") is not None:
                        sid = f"{repository}#pr{pr_num}:{src_type.split('-')[-1]}:{raw_item['id']}"
                        all_observations.append(
                            normalize_unavailable_observation(
                                repository=repository,
                                source_type=src_type,
                                source_id=sid,
                                source_uri=f"github://{repository}/pull/{pr_num}#deleted-{raw_item['id']}",
                                reason="explicit-deleted-marker",
                                http_status=410,
                                observed_at=obs_ts,
                                created_at=crt_ts,
                                prior_record_id=latest_by_logical.get((repository, src_type, sid)),
                                adapter_name=self.name,
                                adapter_version=self.version,
                            )
                        )
                        continue

                    obs: NormalizedObservation | None
                    if src_type == GITHUB_SOURCE_TYPE_REVIEW:
                        obs = normalize_review(
                            raw_item,
                            repository=repository,
                            pr_number=pr_num,
                            pr_head_sha=head_sha,
                            pr_base_sha=base_sha,
                            observed_at=obs_ts,
                            created_at=crt_ts,
                            policy=self.capture_policy,
                            adapter_name=self.name,
                            adapter_version=self.version,
                        )
                    elif src_type == GITHUB_SOURCE_TYPE_REVIEW_COMMENT:
                        obs = normalize_review_comment(
                            raw_item,
                            repository=repository,
                            pr_number=pr_num,
                            pr_head_sha=head_sha,
                            pr_base_sha=base_sha,
                            observed_at=obs_ts,
                            created_at=crt_ts,
                            policy=self.capture_policy,
                            adapter_name=self.name,
                            adapter_version=self.version,
                        )
                    else:
                        obs = normalize_issue_comment(
                            raw_item,
                            repository=repository,
                            pr_number=pr_num,
                            pr_head_sha=head_sha,
                            pr_base_sha=base_sha,
                            observed_at=obs_ts,
                            created_at=crt_ts,
                            policy=self.capture_policy,
                            adapter_name=self.name,
                            adapter_version=self.version,
                        )

                    if obs is None:
                        filtered_records += 1
                        pr_ok = False
                        incomplete_reasons.append("malformed_source_item")
                        gaps.append(
                            EndpointGap(
                                endpoint=ep_label,
                                reason_code="malformed_source_item",
                                message=f"Filtered malformed item in {ep_label} on PR #{pr_num}.",
                                pr_number=pr_num,
                            )
                        )
                        continue

                    all_observations.append(obs)
                    if src_type == GITHUB_SOURCE_TYPE_REVIEW:
                        reviews_collected += 1
                    elif src_type == GITHUB_SOURCE_TYPE_REVIEW_COMMENT:
                        review_comments_collected += 1
                    else:
                        issue_comments_collected += 1

            if pr_ok:
                prs_completed += 1
            else:
                prs_incomplete += 1

        # 3. Reliable deletion/unavailability probes (explicit 404/410 only; never inferred from partial lists)
        for probe in unavailable_probes:
            probe_url = f"{self.client.api_origin}{probe.api_path}"
            pages_attempted += 1
            try:
                _, _, retries = self.client.get_json(probe_url)
                pages_fetched += 1
                retries_performed += retries
            except GitHubTransportError as exc:
                if exc.http_status in (404, 410):
                    all_observations.append(
                        normalize_unavailable_observation(
                            repository=repository,
                            source_type=probe.source_type,
                            source_id=probe.source_id,
                            source_uri=probe.source_uri,
                            reason=f"http-{exc.http_status}",
                            http_status=exc.http_status,
                            observed_at=obs_ts,
                            created_at=crt_ts,
                            prior_record_id=probe.prior_record_id
                            or latest_by_logical.get((repository, probe.source_type, probe.source_id)),
                            adapter_name=self.name,
                            adapter_version=self.version,
                        )
                    )
                else:
                    incomplete_reasons.append(exc.reason_code)
                    gaps.append(
                        EndpointGap(
                            endpoint=probe.api_path,
                            reason_code=exc.reason_code,
                            message=str(exc),
                            http_status=exc.http_status,
                        )
                    )

        # 4. Deterministic sorting, deduplication, and edit versioning (independent of page ordering)
        unique_by_id: dict[str, NormalizedObservation] = {}
        for obs in all_observations:
            rid = str(obs.record["record_id"])
            if rid not in unique_by_id:
                unique_by_id[rid] = obs

        ordered_obs = sorted(
            unique_by_id.values(),
            key=lambda o: (
                o.logical_item_key[1],
                o.logical_item_key[2],
                str(o.record["body"].get("source_revision", "")),
                str(o.record["record_id"]),
            ),
        )

        to_persist: list[RecordDict] = []
        digest_entries: list[EvidenceDigestEntry] = []
        new_observations = 0
        unchanged_deduplicated = 0
        edited_observations = 0
        unavailable_observations = 0
        source_timestamps: list[str] = []

        for obs in ordered_obs:
            rec = copy.deepcopy(obs.record)
            rid = str(rec["record_id"])
            src_state = rec["body"]["source_state"]
            if src_state == "available":
                source_timestamps.append(obs.source_timestamp)

            if rid in existing_ids:
                unchanged_deduplicated += 1
                continue

            prior_id = latest_by_logical.get(obs.logical_item_key)
            if src_state == "source-unavailable":
                unavailable_observations += 1
                status_label = "unavailable"
            elif prior_id is not None and prior_id != rid:
                # Edited source item: append NEW immutable Evidence linking prior observation
                rec.setdefault("extensions", {}).setdefault("github", {})[
                    "supersedes_evidence_id"
                ] = prior_id
                edited_observations += 1
                status_label = "edited"
            else:
                new_observations += 1
                status_label = "new"

            to_persist.append(rec)
            digest_entries.append(
                EvidenceDigestEntry(
                    record_id=rid,
                    record_sha256=canonical_sha256(rec),
                    source_type=str(rec["body"]["source_type"]),
                    source_id=str(rec["body"]["source_id"]),
                    status=status_label,
                )
            )

        oldest_ts: str | None = None
        newest_ts: str | None = None
        if source_timestamps:
            sorted_ts = sorted(source_timestamps, key=parse_rfc3339)
            oldest_ts = sorted_ts[0]
            newest_ts = sorted_ts[-1]

        dedup_reasons = tuple(dict.fromkeys(incomplete_reasons))
        is_complete = len(dedup_reasons) == 0 and len(gaps) == 0

        coverage = CoverageSummary(
            complete=is_complete,
            incomplete_reasons=dedup_reasons,
            pages_attempted=pages_attempted,
            pages_fetched=pages_fetched,
            prs_enumerated=len(selected_prs),
            prs_completed=prs_completed,
            prs_incomplete=prs_incomplete,
            reviews_collected=reviews_collected,
            review_comments_collected=review_comments_collected,
            issue_comments_collected=issue_comments_collected,
            unavailable_observations=unavailable_observations,
            filtered_records=filtered_records,
            new_observations=new_observations,
            unchanged_deduplicated=unchanged_deduplicated,
            edited_observations=edited_observations,
            retries_performed=retries_performed,
            oldest_source_timestamp=oldest_ts,
            newest_source_timestamp=newest_ts,
            endpoint_gaps=tuple(gaps),
        )

        query_meta: dict[str, Any] = {
            "repository": repository,
            "api_origin": self.client.api_origin,
            "since": request.since,
            "until": request.until,
            "states": list(requested_states),
            "max_prs": request.max_items,
            "max_pages": request.max_pages,
            "capture_policy": self.capture_policy.to_dict(),
            "authenticated": self.client.authenticated,
            "token_env": self.token_env_name,
        }

        manifest = IngestionRunManifest(
            run_id=effective_run_id,
            adapter_name=self.name,
            adapter_version=self.version,
            created_at=crt_ts,
            observed_at=obs_ts,
            query=query_meta,
            coverage=coverage,
            evidence_digests=tuple(digest_entries),
        )

        manifest_path_str: str | None = None
        if write:
            if store is None:
                raise ValueError("store is required when write=True.")
            # Atomically persist Evidence first, then atomically persist IngestionRun manifest
            if to_persist:
                store.append_many(to_persist)
            if isinstance(store, GitRecordStore):
                mpath = store.append_ingestion_run(manifest.to_dict())
                manifest_path_str = str(mpath.relative_to(store.repo_path))

        return GitHubIngestionRunResult(
            manifest=manifest,
            written=bool(write),
            evidence_records=tuple(obs.record for obs in ordered_obs),
            new_or_edited_records=tuple(to_persist),
            manifest_path=manifest_path_str,
        )

    def ingest_remote(self, request: SourceRequest) -> IngestionResult:
        """RemoteSourceAdapter interface entrypoint (always read-only dry run)."""
        run = self.ingest_github(request, store=None, write=False)
        items = [
            SourceInventoryItem(
                source_type=str(rec["body"]["source_type"]),
                source_uri=str(rec["body"]["source_uri"]),
                source_id=str(rec["body"]["source_id"]),
                source_state=str(rec["body"]["source_state"]),
                excerpt_sha256=str(rec["body"]["excerpt_sha256"]),
                source_revision=str(rec["body"].get("source_revision", "")),
                source_author=str(rec["body"]["source_author"]),
                metadata={
                    "pr_number": rec.get("extensions", {}).get("github", {}).get("pr_number"),
                },
            )
            for rec in run.evidence_records
        ]
        warnings = [g.message for g in run.manifest.coverage.endpoint_gaps]
        return IngestionResult(
            adapter_name=self.name,
            adapter_version=self.version,
            evidence_records=list(run.evidence_records),
            items=items,
            warnings=warnings,
            complete=run.manifest.coverage.complete,
            unsupported_reason=(
                ",".join(run.manifest.coverage.incomplete_reasons)
                if not run.manifest.coverage.complete
                else None
            ),
        )
