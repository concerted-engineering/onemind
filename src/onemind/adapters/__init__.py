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
    Reporter,
    SourceAdapter,
    SourceInventoryItem,
    Store,
)
from onemind.adapters.json_observation import (
    SUPPORTED_OBSERVATION_SCHEMA,
    JsonObservationAdapter,
)
from onemind.adapters.local_files import (
    DEFAULT_MAX_FILE_BYTES,
    CandidateStatement,
    LocalFilesSourceAdapter,
    ScanReport,
    scan_repository,
)
from onemind.adapters.markdown_retro import MarkdownRetroAdapter
from onemind.adapters.reporter import SummaryReporter

__all__ = [
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
    "LocalFilesSourceAdapter",
    "MarkdownRetroAdapter",
    "OutcomeSink",
    "Reporter",
    "ScanReport",
    "SourceAdapter",
    "SourceInventoryItem",
    "Store",
    "SummaryReporter",
    "scan_repository",
]
