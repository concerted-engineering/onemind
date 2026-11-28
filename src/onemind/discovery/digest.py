"""Human-facing Markdown Review Digest renderer for M2B proposals (read-only)."""

from __future__ import annotations

import json
from collections import Counter

from onemind.discovery.clustering import DiscoveryReport
from onemind.discovery.reconcile import ReconciliationReport


def render_review_digest_markdown(
    discovery: DiscoveryReport,
    reconciliation: ReconciliationReport | None = None,
    *,
    repository_label: str | None = None,
) -> str:
    """Render a deterministic, human-reviewable Markdown proposal digest.

    Clearly separates observed evidence, inferred candidate statements, proposed scopes,
    sources, conflicts, uncertainty, extraction coverage, and suggested human decisions.
    Generating a digest NEVER creates a PR, mutates canonical records, or implies approval.
    """
    repo_name = repository_label or discovery.repository or "(all repositories)"
    lines: list[str] = [
        f"# OneMind Proposal Review Digest — `{repo_name}`",
        "",
        "> **IMPORTANT — NON-AUTHORITATIVE REVIEW DIGEST**  ",
        "> This document summarizes **inert `PracticeCandidate` proposals** and **`Conflict` findings** ",
        "> generated deterministically from opted-in redacted evidence.  ",
        "> Generating this digest does **not** create a pull request, activate any check, modify ",
        "> any approved `PracticeVersion`, or imply organizational approval.",
        "",
        "## 1. Extraction & Corpus Coverage",
        "",
        f"- **Corpus complete:** `{'yes' if discovery.corpus_complete else 'NO (INCOMPLETE)'}`",
        f"- **Minimum independent PR threshold (configurable demo heuristic):** `{discovery.min_independent_prs}`",
        f"- **Evidence records inspected (latest revisions):** `{discovery.extraction.total_input_evidence}`",
        f"- **Superseded older edit revisions excluded from double-counting:** `{len(discovery.extraction.superseded_record_ids)}`",
        f"- **Normative corrective signals extracted:** `{len(discovery.extraction.corrections)}`",
        f"- **Filtered items:** `{len(discovery.extraction.filtered)}`",
        f"- **Missing redacted text (`metadata-only` skipped):** `{discovery.extraction.missing_redacted_text_count}`",
        f"- **Unavailable / withdrawn sources excluded:** `{discovery.extraction.unavailable_count}`",
        f"- **Extractor config digest:** `{discovery.extraction.config_sha256[:16]}...`",
        "",
    ]

    if discovery.incomplete_reasons:
        lines.append("### Coverage Gaps & Warnings")
        lines.append("")
        for reason in discovery.incomplete_reasons:
            lines.append(f"- **Incomplete reason:** `{reason}`")
        for warn in discovery.coverage_warnings:
            lines.append(f"- {warn}")
        lines.append("")

    if discovery.extraction.filtered:
        reason_counts = Counter(f.reason for f in discovery.extraction.filtered)
        lines.append("### Filtered Source Breakdown")
        lines.append("")
        lines.append("| Filter Reason | Count |")
        lines.append("|---|---:|")
        for reason, cnt in sorted(reason_counts.items()):
            lines.append(f"| `{reason}` | {cnt} |")
        lines.append("")

    lines.append("## 2. Candidate Proposals Meeting Support Threshold (Inert)")
    lines.append("")
    promoted = [c for c in discovery.clusters if c.meets_min_support]
    if not promoted:
        lines.append(
            "_No clusters met the configured `--min-independent-prs` threshold._"
            + (
                " **(Note: corpus is incomplete; absence of candidates does not establish absence of recurring corrections.)**"
                if not discovery.corpus_complete
                else ""
            )
        )
        lines.append("")
    else:
        for cluster in promoted:
            lines.extend(_render_cluster_section(cluster))

    if discovery.insufficient_support_clusters:
        lines.append("## 3. Below-Threshold Observations (Insufficient Independent Support)")
        lines.append("")
        for cluster in discovery.insufficient_support_clusters:
            lines.append(
                f"- **`{cluster.cluster_id}`** (`{cluster.category_label}`, polarity=`{cluster.polarity}`, "
                f"independent_prs=`{cluster.support.independent_pr_count}`, "
                f"total_comments=`{cluster.support.total_source_comments}`): {cluster.statement}"
            )
            lines.append(f"  - Sources: `{', '.join(cluster.source_ids)}`")
        lines.append("")

    lines.append("## 4. Reconciliation & Conflicts Against Existing Records")
    lines.append("")
    if reconciliation is None or not reconciliation.findings:
        lines.append("_No overlapping candidates, existing practices, or conflicts detected._")
        lines.append("")
    else:
        lines.append(f"> {reconciliation.non_interference_notice}")
        lines.append("")
        lines.append(
            "| Candidate | Existing Record | Kind | Classification | Similarity | Conflict ID | Rationale |"
        )
        lines.append("|---|---|---|---|---:|---|---|")
        for f in reconciliation.findings:
            conf_cell = f"`{f.conflict_record_id}`" if f.conflict_record_id else "—"
            lines.append(
                f"| `{f.candidate_id}` | `{f.existing_record_id}` | `{f.existing_kind}` | "
                f"**`{f.classification}`** | `{f.similarity:.2f}` | {conf_cell} | {f.rationale} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 5. Suggested Human Reviewer Decisions",
            "",
            "For each candidate above, an authorized maintainer may choose one of:",
            "",
            "1. **Reject / Leave Inert:** Keep the candidate inert or ignore if overgeneralized or noisy.",
            "2. **Refine & Author `PracticeVersion`:** Manually author a versioned normative statement with exact scope and owner, then review and approve via a qualified `GovernanceDecision`.",
            "3. **Partition or Scope Exception:** Split clusters flagged `needs_manual_partitioning` or record an explicit scoped exception if an existing practice applies differently to a narrow path.",
            "4. **Adjudicate Conflict:** For contradictory clusters or existing practice conflicts, record an explicit human `adjudicate` decision rather than relying on frequency or recency.",
            "",
        ]
    )

    return "\n".join(lines)


def _render_cluster_section(cluster: object) -> list[str]:
    from onemind.discovery.clustering import CorrectionCluster

    assert isinstance(cluster, CorrectionCluster)
    s = cluster.support
    out = [
        f"### `{cluster.candidate_record['record_id']}` (Cluster `{cluster.cluster_id}`)",
        "",
        f"- **Inferred Candidate Statement (inert):** {cluster.statement}",
        f"- **Descriptive Category Label (not policy proof):** `{cluster.category_label}` (polarity: `{cluster.polarity}`)",
        f"- **Proposed Scope:** `{json.dumps(cluster.proposed_scope, sort_keys=True)}`",
        f"- **Independent Support:** `{s.independent_pr_count}` independent PR(s) across `{s.total_source_comments}` total comment(s) (PRs: `{list(s.distinct_prs)}`, reviewers: `{list(s.distinct_reviewers)}`)",
        f"- **Source Timespan:** `{s.earliest_source_timestamp or 'n/a'}` .. `{s.latest_source_timestamp or 'n/a'}`",
        f"- **Concentration Flags:** path_concentrated=`{s.path_concentration}` (`{s.dominant_path_prefix}`), time_concentrated=`{s.time_concentration}` (`{s.timespan_hours}h`)",
        f"- **Uncertainty:** `{cluster.uncertainty_level}` (incomplete=`{cluster.incomplete}`, needs_manual_partitioning=`{cluster.needs_manual_partitioning}`)",
    ]
    if cluster.uncertainty_reasons:
        out.append(f"  - Uncertainty reasons: `{', '.join(cluster.uncertainty_reasons)}`")
    if cluster.contradicts_cluster_ids:
        out.append(f"- **Contradicts Clusters:** `{', '.join(cluster.contradicts_cluster_ids)}` (requires human adjudication)")
    out.append("- **Supporting Observed Evidence (source-linked):**")
    for eid, sid, suri in zip(
        cluster.evidence_ids,
        cluster.source_ids,
        cluster.source_uris,
        strict=False,
    ):
        out.append(f"  - `{eid}` — `{sid}` ({suri})")
    out.append("")
    return out
