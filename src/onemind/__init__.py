"""OneMind: open-source engineering-intelligence substrate (0.1-draft experimental)."""

from onemind.api import (
    AnnotatedRecord,
    ImmutableRecord,
    ObservationResult,
    OneMindEngine,
    ProposalResult,
    QueryCriteria,
    QueryResult,
    ResolutionContext,
    ResolutionResult,
    observe,
    propose,
    propose_from_source,
    query,
    resolve,
)
from onemind.protocol.models import COMPUTATION_VERSION, PROTOCOL_STATUS, SCHEMA_VERSION

__version__ = "0.1.0a1"
__all__ = [
    "__version__",
    "COMPUTATION_VERSION",
    "PROTOCOL_STATUS",
    "SCHEMA_VERSION",
    "AnnotatedRecord",
    "ImmutableRecord",
    "ObservationResult",
    "OneMindEngine",
    "ProposalResult",
    "QueryCriteria",
    "QueryResult",
    "ResolutionContext",
    "ResolutionResult",
    "observe",
    "propose",
    "propose_from_source",
    "query",
    "resolve",
]
