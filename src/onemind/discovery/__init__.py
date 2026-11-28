"""M2B recurring correction discovery, independent grouping, reconciliation, and review digests."""

from onemind.discovery.clustering import (
    CLUSTERING_VERSION,
    DEFAULT_MIN_INDEPENDENT_PRS,
    CorrectionCluster,
    DiscoveryReport,
    SupportMetrics,
    discover_recurring_corrections,
)
from onemind.discovery.digest import render_review_digest_markdown
from onemind.discovery.evaluation import (
    CorpusEvaluationReport,
    PrecisionRecall,
    evaluate_corpus_directory,
)
from onemind.discovery.extractor import (
    EXTRACTOR_CONFIG,
    EXTRACTOR_CONFIG_SHA256,
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    DeterministicReviewExtractor,
    ExtractedCorrection,
    ExtractionBatchResult,
    FilteredEvidenceItem,
    OptionalModelExtractor,
    normalize_statement_concepts,
)
from onemind.discovery.reconcile import (
    RECONCILER_CONFIG_SHA256,
    RECONCILER_NAME,
    RECONCILER_VERSION,
    ReconciliationFinding,
    ReconciliationRelation,
    ReconciliationReport,
    reconcile_candidates,
)

__all__ = [
    "CLUSTERING_VERSION",
    "DEFAULT_MIN_INDEPENDENT_PRS",
    "EXTRACTOR_CONFIG",
    "EXTRACTOR_CONFIG_SHA256",
    "EXTRACTOR_NAME",
    "EXTRACTOR_VERSION",
    "RECONCILER_CONFIG_SHA256",
    "RECONCILER_NAME",
    "RECONCILER_VERSION",
    "CorpusEvaluationReport",
    "CorrectionCluster",
    "DeterministicReviewExtractor",
    "DiscoveryReport",
    "ExtractedCorrection",
    "ExtractionBatchResult",
    "FilteredEvidenceItem",
    "OptionalModelExtractor",
    "PrecisionRecall",
    "ReconciliationFinding",
    "ReconciliationRelation",
    "ReconciliationReport",
    "SupportMetrics",
    "discover_recurring_corrections",
    "evaluate_corpus_directory",
    "normalize_statement_concepts",
    "reconcile_candidates",
    "render_review_digest_markdown",
]
