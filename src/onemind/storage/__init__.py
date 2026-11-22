"""OneMind immutable storage package."""

from onemind.storage.base import (
    ConflictingDedupKeyError,
    DuplicateRecordError,
    ImmutableRecordError,
    InMemoryStore,
    OutcomeSink,
    Store,
    StoreError,
)
from onemind.storage.git_store import GitRecordStore
from onemind.storage.mutation import (
    TrustedBaseReport,
    get_head_commit,
    git_ledger_sequence,
    is_git_repo,
    resolve_git_commit,
    verify_trusted_base,
)

__all__ = [
    "ConflictingDedupKeyError",
    "DuplicateRecordError",
    "GitRecordStore",
    "ImmutableRecordError",
    "InMemoryStore",
    "OutcomeSink",
    "Store",
    "StoreError",
    "TrustedBaseReport",
    "get_head_commit",
    "git_ledger_sequence",
    "is_git_repo",
    "resolve_git_commit",
    "verify_trusted_base",
]
