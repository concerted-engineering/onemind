"""OneMind adapter interfaces and built-in offline adapters."""

from onemind.adapters.interfaces import (
    ApplicationAdapter,
    AuthorityVerificationResult,
    AuthorityVerifier,
    CheckFinding,
    CheckResult,
    Extractor,
    IngestionResult,
    OutcomeSink,
    RemoteSourceAdapter,
    Reporter,
    SourceAdapter,
    SourceInventoryItem,
    SourceRequest,
    Store,
)
from onemind.adapters.json_observation import (
    SUPPORTED_OBSERVATION_SCHEMA,
    JsonObservationAdapter,
)
from onemind.adapters.local_files import (
    CAPTURE_MODE_LEGEND,
    DEFAULT_MAX_FILE_BYTES,
    CandidateStatement,
    LineProvenance,
    LocalFilesSourceAdapter,
    ScanReport,
    capture_source_evidence,
    inspect_source_lines,
    parse_source_spec,
    scan_repository,
)
from onemind.adapters.markdown_retro import MarkdownRetroAdapter
from onemind.adapters.reporter import SummaryReporter

__all__ = [
    "CAPTURE_MODE_LEGEND",
    "DEFAULT_MAX_FILE_BYTES",
    "SUPPORTED_OBSERVATION_SCHEMA",
    "ApplicationAdapter",
    "AuthorityVerificationResult",
    "AuthorityVerifier",
    "CandidateStatement",
    "CheckFinding",
    "CheckResult",
    "Extractor",
    "IngestionResult",
    "JsonObservationAdapter",
    "LineProvenance",
    "LocalFilesSourceAdapter",
    "MarkdownRetroAdapter",
    "OutcomeSink",
    "RemoteSourceAdapter",
    "Reporter",
    "ScanReport",
    "SourceAdapter",
    "SourceInventoryItem",
    "SourceRequest",
    "Store",
    "SummaryReporter",
    "capture_source_evidence",
    "inspect_source_lines",
    "parse_source_spec",
    "scan_repository",
]
