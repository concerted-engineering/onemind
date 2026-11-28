"""Offline synthetic gold-corpus and held-out-corpus evaluation metrics (M2B).

Reports precision and recall separately for:
  1. Detection (corrective normative evidence vs filtered noise)
  2. Clustering (pairwise independent-PR co-clustering without merging contradictions)
  3. Evidence linking (exact supporting evidence_id traceability per expected cluster)
  4. Scope correctness (repository + path glob match against expected scope)

IMPORTANT: Metrics are strictly scoped to fixed synthetic fixtures and NEVER support
generalized model-quality or real-world engineering effectiveness claims.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from onemind.discovery.clustering import discover_recurring_corrections
from onemind.discovery.reconcile import reconcile_candidates
from onemind.protocol.models import RecordDict


@dataclass(frozen=True)
class PrecisionRecall:
    """Standard precision / recall / F1 metrics for a single evaluation axis."""

    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass(frozen=True)
class CorpusEvaluationReport:
    """Multi-axis evaluation report against an independently authored expected-label manifest."""

    corpus_name: str
    detection: PrecisionRecall
    clustering: PrecisionRecall
    evidence_linking: PrecisionRecall
    scope_correctness: PrecisionRecall
    contradictions_detected: int
    expected_contradictions: int
    corpus_complete: bool
    disclaimer: str = (
        "Synthetic offline fixture benchmark only. These metrics verify deterministic "
        "extractor/clusterer regression behavior and do NOT represent generalized model "
        "accuracy or real-world engineering improvement."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_name": self.corpus_name,
            "detection": self.detection.to_dict(),
            "clustering": self.clustering.to_dict(),
            "evidence_linking": self.evidence_linking.to_dict(),
            "scope_correctness": self.scope_correctness.to_dict(),
            "contradictions_detected": self.contradictions_detected,
            "expected_contradictions": self.expected_contradictions,
            "corpus_complete": self.corpus_complete,
            "disclaimer": self.disclaimer,
        }


def _pr_from_sets(predicted: set[Any], expected: set[Any]) -> PrecisionRecall:
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
    rec = tp / (tp + fn) if (tp + fn) > 0 else (1.0 if fp == 0 else 0.0)
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
    return PrecisionRecall(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=prec,
        recall=rec,
        f1=f1,
    )


def _pairwise_links(groups: list[set[str]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for g in groups:
        for a, b in combinations(sorted(g), 2):
            pairs.add((a, b))
    return pairs


def evaluate_corpus_directory(corpus_dir: Path | str) -> CorpusEvaluationReport:
    """Evaluate discovery + reconciliation against a synthetic corpus directory.

    Expected files inside corpus_dir:
      - records.json: list of Evidence + optional existing PracticeVersion/GovernanceDecision records
      - ingestion_runs.json (optional): list of IngestionRun manifests
      - expected_labels.json: independently authored expected detection/cluster/evidence/scope manifest
    """
    root = Path(corpus_dir)
    records: list[RecordDict] = json.loads((root / "records.json").read_text(encoding="utf-8"))
    runs_path = root / "ingestion_runs.json"
    runs: list[dict[str, Any]] = (
        json.loads(runs_path.read_text(encoding="utf-8")) if runs_path.is_file() else []
    )
    labels: dict[str, Any] = json.loads((root / "expected_labels.json").read_text(encoding="utf-8"))

    repo = labels.get("repository")
    min_prs = int(labels.get("min_independent_prs", 2))

    evidence_recs = [r for r in records if r.get("kind") == "evidence"]
    discovery = discover_recurring_corrections(
        evidence_recs,
        repository=repo,
        min_independent_prs=min_prs,
        ingestion_runs=runs,
    )
    reconciliation = reconcile_candidates(discovery.proposed_candidates, records)

    # 1. Detection precision/recall (predicted extracted evidence IDs vs expected normative evidence IDs)
    pred_detected = {c.evidence_record_id for c in discovery.extraction.corrections}
    exp_detected = set(labels.get("expected_detected_evidence_ids", []))
    det_metrics = _pr_from_sets(pred_detected, exp_detected)

    # 2. Clustering precision/recall (pairwise co-clustering over expected + predicted clusters)
    pred_groups = [set(c.evidence_ids) for c in discovery.clusters]
    exp_clusters = labels.get("expected_clusters", [])
    exp_groups = [set(ec["evidence_ids"]) for ec in exp_clusters]
    clust_metrics = _pr_from_sets(_pairwise_links(pred_groups), _pairwise_links(exp_groups))

    # 3. Evidence linking & 4. Scope correctness (match each expected cluster to best overlapping predicted cluster)
    ev_tp = ev_fp = ev_fn = 0
    scope_tp = scope_fp = scope_fn = 0
    matched_pred_indices: set[int] = set()

    for ec in exp_clusters:
        exp_eids = set(ec["evidence_ids"])
        exp_scope = ec["expected_scope"]
        best_idx = -1
        best_overlap = 0
        for idx, pc in enumerate(discovery.clusters):
            if idx in matched_pred_indices:
                continue
            ov = len(exp_eids & set(pc.evidence_ids))
            if ov > best_overlap:
                best_overlap = ov
                best_idx = idx
        if best_idx == -1:
            ev_fn += len(exp_eids)
            scope_fn += 1
            continue
        matched_pred_indices.add(best_idx)
        pc = discovery.clusters[best_idx]
        pred_eids = set(pc.evidence_ids)
        ev_tp += len(pred_eids & exp_eids)
        ev_fp += len(pred_eids - exp_eids)
        ev_fn += len(exp_eids - pred_eids)

        # Scope match check (repository + paths match)
        pred_repo = pc.proposed_scope.get("repository")
        pred_paths = sorted(pc.proposed_scope.get("paths", []))
        exp_paths = sorted(exp_scope.get("paths", []))
        if pred_repo == exp_scope.get("repository") and pred_paths == exp_paths:
            scope_tp += 1
        else:
            scope_fp += 1
            scope_fn += 1

    for idx, pc in enumerate(discovery.clusters):
        if idx not in matched_pred_indices:
            ev_fp += len(pc.evidence_ids)
            scope_fp += 1

    ev_prec = ev_tp / (ev_tp + ev_fp) if (ev_tp + ev_fp) > 0 else 1.0
    ev_rec = ev_tp / (ev_tp + ev_fn) if (ev_tp + ev_fn) > 0 else 1.0
    ev_f1 = (2 * ev_prec * ev_rec / (ev_prec + ev_rec)) if (ev_prec + ev_rec) > 0 else 0.0
    ev_metrics = PrecisionRecall(ev_tp, ev_fp, ev_fn, ev_prec, ev_rec, ev_f1)

    sc_prec = scope_tp / (scope_tp + scope_fp) if (scope_tp + scope_fp) > 0 else 1.0
    sc_rec = scope_tp / (scope_tp + scope_fn) if (scope_tp + scope_fn) > 0 else 1.0
    sc_f1 = (2 * sc_prec * sc_rec / (sc_prec + sc_rec)) if (sc_prec + sc_rec) > 0 else 0.0
    scope_metrics = PrecisionRecall(scope_tp, scope_fp, scope_fn, sc_prec, sc_rec, sc_f1)

    contradictions_found = len(discovery.contradictory_cluster_pairs) + sum(
        1
        for f in reconciliation.findings
        if f.classification == "contradictory" and f.existing_kind == "practice_version"
    )

    return CorpusEvaluationReport(
        corpus_name=str(labels.get("corpus_name", root.name)),
        detection=det_metrics,
        clustering=clust_metrics,
        evidence_linking=ev_metrics,
        scope_correctness=scope_metrics,
        contradictions_detected=contradictions_found,
        expected_contradictions=int(labels.get("expected_contradictions", 0)),
        corpus_complete=discovery.corpus_complete,
    )
