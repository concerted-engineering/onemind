"""Deterministic independent-PR occurrence grouping & scope-aware clustering (M2B).

COUNTING UNIT:
  Independent PR occurrences — distinct (repository, pr_number) pairs, NOT raw comment count.
  Multiple comments in the same PR thread or across revisions of the same PR count as 1.

NON-MERGER INVARIANT:
  Opposite polarities ("always use X" vs "never use X") or incompatible conditions NEVER
  merge into one cluster; contradictory clusters are explicitly cross-linked for review.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from onemind.discovery.extractor import (
    EXTRACTOR_CONFIG_SHA256,
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    DeterministicReviewExtractor,
    ExtractedCorrection,
    ExtractionBatchResult,
)
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import (
    ActorType,
    RecordDict,
    RecordKind,
    make_envelope,
    parse_rfc3339,
    utc_now_iso,
)
from onemind.protocol.scope import scopes_may_overlap

DEFAULT_MIN_INDEPENDENT_PRS = 2
CLUSTERING_VERSION = "onemind-clusterer/0.1-draft"


@dataclass(frozen=True)
class SupportMetrics:
    """Explicitly separated support, concentration, and auditability metrics."""

    independent_pr_count: int
    total_source_comments: int
    distinct_prs: tuple[int, ...]
    distinct_reviewers: tuple[str, ...]
    earliest_source_timestamp: str | None
    latest_source_timestamp: str | None
    path_concentration: bool
    dominant_path_prefix: str | None
    time_concentration: bool
    timespan_hours: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "counting_unit": "independent_pr_occurrences",
            "independent_pr_count": self.independent_pr_count,
            "total_source_comments": self.total_source_comments,
            "distinct_prs": list(self.distinct_prs),
            "distinct_reviewers": list(self.distinct_reviewers),
            "earliest_source_timestamp": self.earliest_source_timestamp,
            "latest_source_timestamp": self.latest_source_timestamp,
            "path_concentration": self.path_concentration,
            "dominant_path_prefix": self.dominant_path_prefix,
            "time_concentration": self.time_concentration,
            "timespan_hours": self.timespan_hours,
        }


@dataclass(frozen=True)
class CorrectionCluster:
    """A deterministic cluster of recurring corrections with an inert PracticeCandidate proposal."""

    cluster_id: str
    cluster_key: str
    repository: str
    category_label: str
    polarity: str
    topic_key: str
    statement: str
    proposed_scope: dict[str, Any]
    support: SupportMetrics
    meets_min_support: bool
    evidence_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_uris: tuple[str, ...]
    uncertainty_level: str  # "low" | "medium" | "high"
    uncertainty_reasons: tuple[str, ...]
    incomplete: bool
    needs_manual_partitioning: bool
    contradicts_cluster_ids: tuple[str, ...]
    candidate_record: RecordDict

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "cluster_key": self.cluster_key,
            "repository": self.repository,
            "category_label": self.category_label,
            "polarity": self.polarity,
            "topic_key": self.topic_key,
            "statement": self.statement,
            "proposed_scope": self.proposed_scope,
            "support": self.support.to_dict(),
            "meets_min_support": self.meets_min_support,
            "evidence_ids": list(self.evidence_ids),
            "source_ids": list(self.source_ids),
            "source_uris": list(self.source_uris),
            "uncertainty_level": self.uncertainty_level,
            "uncertainty_reasons": list(self.uncertainty_reasons),
            "incomplete": self.incomplete,
            "needs_manual_partitioning": self.needs_manual_partitioning,
            "contradicts_cluster_ids": list(self.contradicts_cluster_ids),
            "candidate_record": self.candidate_record,
        }


@dataclass(frozen=True)
class DiscoveryReport:
    """Complete deterministic output of M2B recurring correction discovery."""

    repository: str | None
    source_filter: str
    min_independent_prs: int
    corpus_complete: bool
    incomplete_reasons: tuple[str, ...]
    extraction: ExtractionBatchResult
    clusters: tuple[CorrectionCluster, ...]
    proposed_candidates: tuple[RecordDict, ...]
    insufficient_support_clusters: tuple[CorrectionCluster, ...]
    contradictory_cluster_pairs: tuple[tuple[str, str], ...]
    coverage_warnings: tuple[str, ...]
    clustering_version: str = CLUSTERING_VERSION
    disclaimer: str = field(
        default=(
            "Discovered clusters and PracticeCandidates are strictly inert proposals derived "
            "from deterministic lexical/structural rules. Meeting min_independent_prs is a "
            "configurable discovery threshold, NOT organizational policy approval."
        )
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "source_filter": self.source_filter,
            "min_independent_prs": self.min_independent_prs,
            "clustering_version": self.clustering_version,
            "corpus_complete": self.corpus_complete,
            "incomplete_reasons": list(self.incomplete_reasons),
            "coverage_warnings": list(self.coverage_warnings),
            "extraction_summary": {
                "total_input_evidence": self.extraction.total_input_evidence,
                "extracted_count": len(self.extraction.corrections),
                "filtered_count": len(self.extraction.filtered),
                "superseded_count": len(self.extraction.superseded_record_ids),
                "missing_redacted_text_count": self.extraction.missing_redacted_text_count,
                "unavailable_count": self.extraction.unavailable_count,
                "config_sha256": self.extraction.config_sha256,
            },
            "filtered_items": [f.to_dict() for f in self.extraction.filtered],
            "cluster_count": len(self.clusters),
            "promoted_candidate_count": len(self.proposed_candidates),
            "insufficient_support_count": len(self.insufficient_support_clusters),
            "contradictory_cluster_pairs": [list(p) for p in self.contradictory_cluster_pairs],
            "clusters": [c.to_dict() for c in self.clusters],
            "candidates": list(self.proposed_candidates),
            "disclaimer": self.disclaimer,
        }


def _common_scope_paths(items: Sequence[ExtractedCorrection]) -> tuple[list[str], bool, str | None]:
    """Compute tightest valid scope path globs and path-concentration flag."""
    paths = sorted({c.file_path for c in items if c.file_path})
    if not paths:
        return [], False, None

    dirs = sorted({c.scope_glob for c in items if c.scope_glob})
    # Check if all evidence comes from a single exact file or single directory glob
    path_concentrated = len(paths) == 1 or len(dirs) == 1

    if len(dirs) == 1:
        return [dirs[0]], path_concentrated, dirs[0]

    # Try computing longest common directory prefix across paths
    split_dirs = [p.split("/")[:-1] for p in paths]
    if all(split_dirs):
        common: list[str] = []
        for parts in zip(*split_dirs, strict=False):
            if len(set(parts)) == 1:
                common.append(parts[0])
            else:
                break
        if common:
            prefix_glob = "/".join(common) + "/**"
            return [prefix_glob], False, prefix_glob

    return dirs, False, dirs[0] if dirs else None


def _compute_support_metrics(items: Sequence[ExtractedCorrection]) -> SupportMetrics:
    # Independent PR occurrences: distinct PR numbers (fallback to distinct source_id when pr_number is None)
    distinct_pr_nums = sorted({c.pr_number for c in items if c.pr_number is not None})
    non_pr_sources = sorted({c.source_id for c in items if c.pr_number is None})
    independent_count = len(distinct_pr_nums) + len(non_pr_sources)

    distinct_reviewers = sorted({c.reviewer for c in items if c.reviewer})
    timestamps = sorted(c.source_timestamp for c in items if c.source_timestamp)
    earliest = timestamps[0] if timestamps else None
    latest = timestamps[-1] if timestamps else None

    timespan_hours: float | None = None
    time_concentrated = False
    if earliest and latest:
        try:
            delta = (parse_rfc3339(latest) - parse_rfc3339(earliest)).total_seconds() / 3600.0
            timespan_hours = round(max(delta, 0.0), 2)
            # Flag concentrated bursts when >= 2 PRs occurred within a single 24-hour window
            time_concentrated = independent_count >= 2 and timespan_hours <= 24.0
        except ValueError:
            pass

    _, path_conc, dom_prefix = _common_scope_paths(items)

    return SupportMetrics(
        independent_pr_count=independent_count,
        total_source_comments=len(items),
        distinct_prs=tuple(distinct_pr_nums),
        distinct_reviewers=tuple(distinct_reviewers),
        earliest_source_timestamp=earliest,
        latest_source_timestamp=latest,
        path_concentration=path_conc and independent_count >= 2,
        dominant_path_prefix=dom_prefix,
        time_concentration=time_concentrated,
        timespan_hours=timespan_hours,
    )


def _topic_jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _make_cluster_key(item: ExtractedCorrection) -> str:
    """Stable clustering key incorporating repository, scope bucket, category, polarity, conditions, and topic."""
    cond_part = ",".join(item.conditions) if item.conditions else "unconditional"
    return (
        f"{item.repository}|{item.scope_bucket}|{item.category}|"
        f"{item.polarity}|{cond_part}|{item.topic_key}"
    )


def _select_representative_statement(items: Sequence[ExtractedCorrection]) -> str:
    """Deterministically pick the clearest representative normative statement in a cluster."""
    # Prefer review submissions over inline comments over issue comments, then shortest clear statement
    rank_by_type = {
        "github-pr-review": 0,
        "github-pr-review-comment": 1,
        "github-pr-issue-comment": 2,
    }
    ordered = sorted(
        items,
        key=lambda c: (
            rank_by_type.get(c.source_type, 3),
            len(c.statement),
            c.evidence_record_id,
        ),
    )
    return ordered[0].statement


def discover_recurring_corrections(
    evidence_records: Sequence[RecordDict],
    *,
    repository: str | None = None,
    source_filter: str = "all",
    min_independent_prs: int = DEFAULT_MIN_INDEPENDENT_PRS,
    ingestion_runs: Sequence[dict[str, Any]] = (),
    created_at: str | None = None,
) -> DiscoveryReport:
    """Run deterministic extraction + independent-PR clustering over inert Evidence records."""
    if min_independent_prs < 1:
        raise ValueError(f"min_independent_prs must be >= 1, got {min_independent_prs}.")

    extractor = DeterministicReviewExtractor(source_filter=source_filter)
    extraction = extractor.extract_corrections(evidence_records, repository=repository)

    # Check corpus completeness from ingestion_runs + missing_redacted_text
    incomplete_reasons: list[str] = []
    coverage_warnings: list[str] = []

    for run in ingestion_runs:
        run_repo = run.get("query", {}).get("repository")
        if repository is not None and run_repo and run_repo != repository:
            continue
        cov = run.get("coverage", {})
        if cov.get("complete") is False:
            run_id = run.get("run_id", "<unknown>")
            reasons = cov.get("incomplete_reasons") or ["incomplete-ingestion-run"]
            incomplete_reasons.extend(str(r) for r in reasons)
            coverage_warnings.append(
                f"Ingestion run {run_id!r} was incomplete ({', '.join(str(r) for r in reasons)}); "
                "absence of a cluster does not imply absence of recurring corrections."
            )

    if extraction.missing_redacted_text_count > 0:
        incomplete_reasons.append("missing_redacted_text")
        coverage_warnings.append(
            f"{extraction.missing_redacted_text_count} evidence record(s) omitted redacted_snapshot "
            "(metadata-only capture); opt into --capture-policy redacted-excerpt to enable text extraction."
        )

    # Group corrections deterministically by stable cluster key
    grouped: dict[str, list[ExtractedCorrection]] = {}
    for corr in sorted(extraction.corrections, key=lambda c: c.evidence_record_id):
        ckey = _make_cluster_key(corr)
        grouped.setdefault(ckey, []).append(corr)

    # Also merge clusters within the same (repository, scope_bucket, category, polarity, conditions)
    # when normalized concept Jaccard >= 0.50 (handles different phrasings of the same correction!)
    # NEVER merges opposite polarities, different categories, or incompatible conditions.
    merged_groups: list[tuple[str, list[ExtractedCorrection]]] = []
    for ckey in sorted(grouped.keys()):
        items = grouped[ckey]
        rep = items[0]
        merged_into = False
        for idx, (existing_key, existing_items) in enumerate(merged_groups):
            ex_rep = existing_items[0]
            if (
                ex_rep.repository == rep.repository
                and ex_rep.scope_bucket == rep.scope_bucket
                and ex_rep.category == rep.category
                and ex_rep.polarity == rep.polarity
                and ex_rep.conditions == rep.conditions
            ):
                sim = _topic_jaccard(ex_rep.concepts, rep.concepts)
                if sim >= 0.50:
                    merged_groups[idx] = (existing_key, sorted([*existing_items, *items], key=lambda c: c.evidence_record_id))
                    merged_into = True
                    break
        if not merged_into:
            merged_groups.append((ckey, items))

    # Build preliminary cluster metadata so we can detect cross-cluster contradictions & partitioning needs
    emit_ts = created_at or utc_now_iso()
    prelim: list[dict[str, Any]] = []
    for ckey, items in sorted(merged_groups, key=lambda pair: pair[0]):
        rep = items[0]
        support = _compute_support_metrics(items)
        scope_paths, _, _ = _common_scope_paths(items)
        proposed_scope: dict[str, Any] = {"repository": rep.repository}
        if scope_paths:
            proposed_scope["paths"] = scope_paths
        if rep.conditions:
            proposed_scope["conditions"] = list(rep.conditions)

        # Deterministic cluster_id and candidate_id derived purely from stable cluster key + evidence IDs
        ev_ids = tuple(sorted({c.evidence_record_id for c in items}))
        src_ids = tuple(sorted({c.source_id for c in items}))
        src_uris = tuple(sorted({c.source_uri for c in items}))
        cluster_digest = canonical_sha256(
            {
                "cluster_key": ckey,
                "repository": rep.repository,
                "evidence_ids": list(ev_ids),
            }
        )
        cluster_id = f"cluster-{cluster_digest[:16]}"
        cand_id = f"cand-disc-{cluster_digest[:20]}"

        # Check if multiple distinct path globs or mixed exception qualifiers require manual partitioning
        distinct_globs = {c.scope_glob for c in items if c.scope_glob}
        needs_partition = len(distinct_globs) > 1 or any(c.has_exception_qualifier for c in items)

        # Collect uncertainty reasons transparently
        unc_reasons: list[str] = []
        if not scope_paths:
            unc_reasons.append("scope-inferred-repo-wide-no-file-paths")
        if support.independent_pr_count < min_independent_prs:
            unc_reasons.append(
                f"insufficient-independent-pr-support ({support.independent_pr_count} < {min_independent_prs})"
            )
        if support.path_concentration:
            unc_reasons.append("evidence-concentrated-in-single-path")
        if support.time_concentration:
            unc_reasons.append("evidence-concentrated-within-24h")
        if len(support.distinct_reviewers) == 1 and support.independent_pr_count >= 2:
            unc_reasons.append("single-reviewer-source")
        if incomplete_reasons:
            unc_reasons.append("underlying-corpus-incomplete")
        for c in items:
            for ur in c.uncertainty_reasons:
                if ur not in unc_reasons:
                    unc_reasons.append(ur)

        incomplete_flag = bool(
            not scope_paths
            or incomplete_reasons
            or any("truncated" in u for u in unc_reasons)
        )
        unc_level = (
            "high"
            if (incomplete_flag or support.independent_pr_count < min_independent_prs)
            else ("medium" if unc_reasons else "low")
        )

        prelim.append(
            {
                "cluster_id": cluster_id,
                "cand_id": cand_id,
                "cluster_key": ckey,
                "rep": rep,
                "items": items,
                "support": support,
                "proposed_scope": proposed_scope,
                "ev_ids": ev_ids,
                "src_ids": src_ids,
                "src_uris": src_uris,
                "needs_partition": needs_partition,
                "unc_reasons": tuple(unc_reasons),
                "incomplete_flag": incomplete_flag,
                "unc_level": unc_level,
                "concepts": tuple(sorted({tok for c in items for tok in c.concepts})),
                "statement": _select_representative_statement(items),
            }
        )

    # Detect cross-cluster contradictions (same repo, overlapping scope, opposite polarity, shared topic!)
    contradictions_map: dict[str, set[str]] = {p["cluster_id"]: set() for p in prelim}
    contradictory_pairs: list[tuple[str, str]] = []
    for i in range(len(prelim)):
        for j in range(i + 1, len(prelim)):
            a, b = prelim[i], prelim[j]
            if a["rep"].repository != b["rep"].repository:
                continue
            if a["rep"].polarity == b["rep"].polarity:
                continue
            if not scopes_may_overlap(a["proposed_scope"], b["proposed_scope"]):
                continue
            sim = _topic_jaccard(a["concepts"], b["concepts"])
            if sim >= 0.40 or a["rep"].topic_key == b["rep"].topic_key:
                contradictions_map[a["cluster_id"]].add(b["cluster_id"])
                contradictions_map[b["cluster_id"]].add(a["cluster_id"])
                contradictory_pairs.append((a["cluster_id"], b["cluster_id"]))

    clusters: list[CorrectionCluster] = []
    promoted_candidates: list[RecordDict] = []
    insufficient_clusters: list[CorrectionCluster] = []

    for p in prelim:
        cid = p["cluster_id"]
        contra_ids = tuple(sorted(contradictions_map[cid]))
        p_support: SupportMetrics = p["support"]
        meets_min = p_support.independent_pr_count >= min_independent_prs
        final_unc_reasons: tuple[str, ...] = p["unc_reasons"]
        if contra_ids:
            final_unc_reasons = (*final_unc_reasons, f"contradicts-clusters:{','.join(contra_ids)}")
        final_unc_level: str = "high" if contra_ids else p["unc_level"]

        cand_record = make_envelope(
            kind=RecordKind.PRACTICE_CANDIDATE,
            record_id=p["cand_id"],
            created_at=emit_ts,
            actor_type=ActorType.SERVICE,
            actor_id=f"extractor:{EXTRACTOR_NAME}",
            body={
                "statement": p["statement"],
                "proposed_scope": p["proposed_scope"],
                "evidence_ids": list(p["ev_ids"]),
                "generator": {
                    "name": EXTRACTOR_NAME,
                    "version": EXTRACTOR_VERSION,
                    "config_sha256": EXTRACTOR_CONFIG_SHA256,
                    "mode": "deterministic-rules",
                    "category_label": p["rep"].category,
                    "cluster_id": cid,
                    "support": p_support.to_dict(),
                    "uncertainty": {
                        "level": final_unc_level,
                        "incomplete": p["incomplete_flag"],
                        "reasons": list(final_unc_reasons),
                    },
                },
            },
        )

        cluster = CorrectionCluster(
            cluster_id=cid,
            cluster_key=p["cluster_key"],
            repository=p["rep"].repository,
            category_label=p["rep"].category,
            polarity=p["rep"].polarity,
            topic_key=p["rep"].topic_key,
            statement=p["statement"],
            proposed_scope=p["proposed_scope"],
            support=p_support,
            meets_min_support=meets_min,
            evidence_ids=p["ev_ids"],
            source_ids=p["src_ids"],
            source_uris=p["src_uris"],
            uncertainty_level=final_unc_level,
            uncertainty_reasons=final_unc_reasons,
            incomplete=p["incomplete_flag"],
            needs_manual_partitioning=p["needs_partition"] or bool(contra_ids),
            contradicts_cluster_ids=contra_ids,
            candidate_record=cand_record,
        )
        clusters.append(cluster)
        if meets_min:
            promoted_candidates.append(cand_record)
        else:
            insufficient_clusters.append(cluster)

    dedup_incomplete = tuple(dict.fromkeys(incomplete_reasons))
    corpus_complete = len(dedup_incomplete) == 0

    return DiscoveryReport(
        repository=repository,
        source_filter=source_filter,
        min_independent_prs=min_independent_prs,
        corpus_complete=corpus_complete,
        incomplete_reasons=dedup_incomplete,
        extraction=extraction,
        clusters=tuple(clusters),
        proposed_candidates=tuple(promoted_candidates),
        insufficient_support_clusters=tuple(insufficient_clusters),
        contradictory_cluster_pairs=tuple(contradictory_pairs),
        coverage_warnings=tuple(coverage_warnings),
    )
