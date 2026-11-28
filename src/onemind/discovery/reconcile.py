"""Scope-aware reconciliation of discovered PracticeCandidates against existing records (M2B).

NON-INTERFERENCE INVARIANT:
  Reconciliation NEVER approves, suspends, retires, modifies, or changes routing of any
  existing PracticeVersion or Application. It produces strictly inert ReconciliationFindings
  and valid 0.1-draft Conflict records (persisted ONLY on explicit --write after validation).
  Ambiguity is NEVER resolved by recency, popularity, or score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from onemind.discovery.extractor import normalize_statement_concepts
from onemind.protocol.canonical import canonical_sha256
from onemind.protocol.models import (
    ActorType,
    ConflictRelation,
    RecordDict,
    RecordKind,
    make_envelope,
    utc_now_iso,
)
from onemind.protocol.scope import (
    is_scope_subset,
    scopes_may_overlap,
    validate_scope_structure,
)

RECONCILER_NAME = "onemind-scope-reconciler"
RECONCILER_VERSION = "0.1.0"
RECONCILER_CONFIG_SHA256 = canonical_sha256(
    {
        "name": RECONCILER_NAME,
        "version": RECONCILER_VERSION,
        "duplicate_jaccard_threshold": 0.65,
        "overlap_jaccard_threshold": 0.35,
        "contradiction_jaccard_threshold": 0.35,
    }
)

ReconciliationRelation = Literal[
    "identical",
    "possible-duplicate",
    "compatible-refinement",
    "scope-overlap-review",
    "contradictory",
    "insufficient-context",
]


@dataclass(frozen=True)
class ReconciliationFinding:
    """Structured scope-aware comparison between a candidate and an existing record."""

    candidate_id: str
    existing_record_id: str
    existing_kind: str  # "practice_candidate" | "practice_version"
    classification: ReconciliationRelation
    similarity: float
    rationale: str
    evidence_ids: tuple[str, ...]
    conflict_record_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "existing_record_id": self.existing_record_id,
            "existing_kind": self.existing_kind,
            "classification": self.classification,
            "similarity": round(self.similarity, 4),
            "rationale": self.rationale,
            "evidence_ids": list(self.evidence_ids),
            "conflict_record_id": self.conflict_record_id,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    """Complete read-only reconciliation result with proposed inert Conflict records."""

    candidates_evaluated: int
    existing_compared: int
    findings: tuple[ReconciliationFinding, ...]
    proposed_conflicts: tuple[RecordDict, ...]
    counts_by_classification: dict[str, int]
    non_interference_notice: str = field(
        default=(
            "Reconciliation is strictly read-only and non-interfering: no existing "
            "PracticeVersion, GovernanceDecision, or Application was modified or deactivated."
        )
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates_evaluated": self.candidates_evaluated,
            "existing_compared": self.existing_compared,
            "counts_by_classification": self.counts_by_classification,
            "finding_count": len(self.findings),
            "proposed_conflict_count": len(self.proposed_conflicts),
            "findings": [f.to_dict() for f in self.findings],
            "proposed_conflicts": list(self.proposed_conflicts),
            "non_interference_notice": self.non_interference_notice,
        }


def _jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _scopes_equal(scope_a: dict[str, Any], scope_b: dict[str, Any]) -> bool:
    return canonical_sha256(scope_a) == canonical_sha256(scope_b)


def _classify_pair(
    cand: RecordDict,
    existing: RecordDict,
) -> tuple[ReconciliationRelation, float, str, ConflictRelation | None]:
    """Classify relationship between one candidate and one existing candidate/practice."""
    c_body = cand["body"]
    e_body = existing["body"]
    c_scope = c_body.get("proposed_scope") or {}
    e_scope = e_body.get("scope") or e_body.get("proposed_scope") or {}

    c_ok, c_reason = validate_scope_structure(c_scope)
    e_ok, e_reason = validate_scope_structure(e_scope)
    if not c_ok or not e_ok:
        return (
            "insufficient-context",
            0.0,
            f"Scope structure ambiguous/incomplete ({c_reason or e_reason}).",
            None,
        )

    if not scopes_may_overlap(c_scope, e_scope):
        return ("insufficient-context", 0.0, "Scopes do not overlap.", None)

    c_stmt = str(c_body.get("statement", ""))
    e_stmt = str(e_body.get("statement", ""))
    c_pol, c_cat, c_concepts, c_topic, c_conds = normalize_statement_concepts(c_stmt)
    e_pol, e_cat, e_concepts, e_topic, e_conds = normalize_statement_concepts(e_stmt)

    # Check if either candidate is explicitly marked incomplete with no path scope
    c_unc = c_body.get("generator", {}).get("uncertainty", {})
    if c_pol is None or e_pol is None:
        return (
            "insufficient-context",
            0.0,
            "One or both statements lack clear normative polarity for deterministic reconciliation.",
            None,
        )

    sim = _jaccard(c_concepts, e_concepts)
    # Also check shared core domain concepts (ignore unrelated extra words)
    shared = set(c_concepts) & set(e_concepts)
    same_topic = bool(c_topic and c_topic == e_topic) or sim >= 0.35 or len(shared) >= 2

    if not same_topic:
        # Unrelated topic in overlapping repo/paths -> no conflict
        return ("insufficient-context", sim, "No substantive topic overlap.", None)

    # 1. Opposite polarity in overlapping scope -> CONTRADICTORY (never auto-resolved!)
    if c_pol != e_pol:
        return (
            "contradictory",
            sim,
            (
                f"Contradictory polarity ({c_pol!r} vs {e_pol!r}) over overlapping scope "
                f"and shared concepts {sorted(shared)!r}."
            ),
            ConflictRelation.CONTRADICTS,
        )

    # 2. Same polarity: check exact equivalence, scope refinement/exception, duplicate, or overlap
    same_scope = _scopes_equal(c_scope, e_scope)
    c_sub_e, _ = is_scope_subset(c_scope, e_scope)
    e_sub_c, _ = is_scope_subset(e_scope, c_scope)

    c_norm_text = " ".join(c_stmt.lower().split())
    e_norm_text = " ".join(e_stmt.lower().split())

    if same_scope and (c_norm_text == e_norm_text or (sim >= 0.85 and c_conds == e_conds)):
        return (
            "identical",
            sim,
            "Equivalent normative statement, polarity, and scope already captured.",
            None,
        )

    # Check for compatible scope-specific refinement or explicit exception qualifier
    if (c_sub_e and not same_scope) or (e_sub_c and not same_scope) or (c_conds != e_conds):
        has_exc = "has-exception" in c_conds or "has-exception" in e_conds or any(
            x.startswith("scope:") for x in (*c_conds, *e_conds)
        )
        rel = ConflictRelation.POSSIBLE_EXCEPTION if has_exc else ConflictRelation.SCOPE_OVERLAP
        return (
            "compatible-refinement",
            sim,
            (
                "Compatible scope refinement or conditional exception relative to existing "
                f"{existing['kind']} {existing['record_id']!r}; requires human review."
            ),
            rel,
        )

    if sim >= 0.65:
        return (
            "possible-duplicate",
            sim,
            (
                f"High lexical/concept similarity ({sim:.2f}) with same polarity ({c_pol!r}) "
                "over matching scope; review before creating duplicate."
            ),
            ConflictRelation.SCOPE_OVERLAP,
        )

    if c_unc.get("incomplete") is True and not c_scope.get("paths"):
        return (
            "insufficient-context",
            sim,
            "Candidate has incomplete context and repo-wide inferred scope; manual clarification needed.",
            None,
        )

    if c_cat == e_cat or sim >= 0.35:
        return (
            "scope-overlap-review",
            sim,
            (
                f"Overlapping scope and related topic ({sorted(shared)!r}) with existing "
                f"{existing['kind']} {existing['record_id']!r}; requires human review."
            ),
            ConflictRelation.SCOPE_OVERLAP,
        )

    return ("insufficient-context", sim, "Insufficient context to classify definitively.", None)


def reconcile_candidates(
    candidates: Sequence[RecordDict],
    existing_records: Sequence[RecordDict],
    *,
    created_at: str | None = None,
) -> ReconciliationReport:
    """Reconcile candidate proposals against existing PracticeCandidates and PracticeVersions.

    Also compares candidates against each other (for pairwise contradictions among new clusters).
    Strictly read-only and non-interfering: emits inert findings and valid 0.1-draft Conflict records.
    """
    existing_targets = sorted(
        [
            r
            for r in existing_records
            if r.get("kind") in (RecordKind.PRACTICE_CANDIDATE, RecordKind.PRACTICE_VERSION)
        ],
        key=lambda r: str(r["record_id"]),
    )
    existing_conflicts_keys: set[tuple[str, str, str]] = set()
    for r in existing_records:
        if r.get("kind") == RecordKind.CONFLICT:
            b = r.get("body", {})
            pair = tuple(sorted([str(b.get("left_record_id", "")), str(b.get("right_record_id", ""))]))
            existing_conflicts_keys.add((pair[0], pair[1], str(b.get("relation", ""))))

    ordered_cands = sorted(candidates, key=lambda r: str(r["record_id"]))
    emit_ts = created_at or utc_now_iso()

    findings: list[ReconciliationFinding] = []
    proposed_conflicts: list[RecordDict] = []
    seen_conflict_ids: set[str] = set()
    counts: dict[str, int] = {
        "identical": 0,
        "possible-duplicate": 0,
        "compatible-refinement": 0,
        "scope-overlap-review": 0,
        "contradictory": 0,
        "insufficient-context": 0,
    }

    def _emit_conflict(
        left_id: str,
        right_id: str,
        relation: ConflictRelation,
        evidence_ids: tuple[str, ...],
        rationale: str,
    ) -> str | None:
        ordered_pair = sorted([left_id, right_id])
        dedup_key = (ordered_pair[0], ordered_pair[1], relation.value)
        if dedup_key in existing_conflicts_keys:
            return None
        conf_hash = canonical_sha256(
            {
                "left_record_id": ordered_pair[0],
                "right_record_id": ordered_pair[1],
                "relation": relation.value,
                "detector": RECONCILER_NAME,
            }
        )
        conf_id = f"conf-rec-{conf_hash[:20]}"
        if conf_id in seen_conflict_ids:
            return conf_id
        seen_conflict_ids.add(conf_id)

        conf_rec = make_envelope(
            kind=RecordKind.CONFLICT,
            record_id=conf_id,
            created_at=emit_ts,
            actor_type=ActorType.SERVICE,
            actor_id=f"detector:{RECONCILER_NAME}",
            body={
                "left_record_id": ordered_pair[0],
                "right_record_id": ordered_pair[1],
                "relation": relation.value,
                "evidence_ids": list(evidence_ids),
                "detector": {
                    "name": RECONCILER_NAME,
                    "version": RECONCILER_VERSION,
                    "config_sha256": RECONCILER_CONFIG_SHA256,
                },
                "rationale": rationale,
            },
        )
        proposed_conflicts.append(conf_rec)
        return conf_id

    # 1. Compare each candidate against every existing PracticeCandidate / PracticeVersion
    for cand in ordered_cands:
        cid = str(cand["record_id"])
        c_ev = tuple(str(e) for e in cand["body"].get("evidence_ids", []))
        for existing in existing_targets:
            eid = str(existing["record_id"])
            if eid == cid:
                continue
            classification, sim, rationale, conf_rel = _classify_pair(cand, existing)
            # Skip non-overlapping unrelated pairs to keep reports high-signal
            if classification == "insufficient-context" and rationale in (
                "Scopes do not overlap.",
                "No substantive topic overlap.",
            ):
                continue

            e_ev = tuple(str(e) for e in existing["body"].get("evidence_ids", []))
            combined_ev = tuple(sorted(set(c_ev) | set(e_ev)))
            conf_id: str | None = None
            if conf_rel is not None:
                conf_id = _emit_conflict(cid, eid, conf_rel, combined_ev, rationale)

            counts[classification] += 1
            findings.append(
                ReconciliationFinding(
                    candidate_id=cid,
                    existing_record_id=eid,
                    existing_kind=str(existing["kind"]),
                    classification=classification,
                    similarity=sim,
                    rationale=rationale,
                    evidence_ids=combined_ev,
                    conflict_record_id=conf_id,
                )
            )

    # 2. Compare candidate-vs-candidate pairs for newly discovered contradictions / refinements
    for i in range(len(ordered_cands)):
        for j in range(i + 1, len(ordered_cands)):
            left, right = ordered_cands[i], ordered_cands[j]
            lid, rid = str(left["record_id"]), str(right["record_id"])
            classification, sim, rationale, conf_rel = _classify_pair(left, right)
            if classification not in ("contradictory", "compatible-refinement", "possible-duplicate"):
                continue
            combined_ev = tuple(
                sorted(
                    set(str(e) for e in left["body"].get("evidence_ids", []))
                    | set(str(e) for e in right["body"].get("evidence_ids", []))
                )
            )
            conf_id = None
            if conf_rel is not None:
                conf_id = _emit_conflict(lid, rid, conf_rel, combined_ev, rationale)
            counts[classification] += 1
            findings.append(
                ReconciliationFinding(
                    candidate_id=lid,
                    existing_record_id=rid,
                    existing_kind=RecordKind.PRACTICE_CANDIDATE.value,
                    classification=classification,
                    similarity=sim,
                    rationale=rationale,
                    evidence_ids=combined_ev,
                    conflict_record_id=conf_id,
                )
            )

    return ReconciliationReport(
        candidates_evaluated=len(ordered_cands),
        existing_compared=len(existing_targets),
        findings=tuple(findings),
        proposed_conflicts=tuple(proposed_conflicts),
        counts_by_classification=counts,
    )
