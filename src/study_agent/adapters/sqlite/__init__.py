"""SQLite persistence adapters."""

from .event_store import (
    EventBatchError,
    ProjectionConsistencyError,
    SequenceConflictError,
    SQLiteConnectionIdentityError,
    SQLiteConnectionIdentityGuard,
    SQLiteEventStore,
    UnsupportedSQLiteDatabaseError,
)
from .fts_retrieval import (
    INDEX_VERSION,
    RetrievalIndexIntegrityError,
    SQLiteFtsRetrieval,
    compile_literal_query,
    normalize_bm25_score,
)
from .lifecycle_observer import observe_local_repository
from .namespaced_run_store import NamespacedSQLiteRunStore
from .run_store import (
    RunStoreCorruptionError,
    SQLiteRunStore,
    UnsupportedSQLiteRunDatabaseError,
)
from .tutor_continuations import (
    MAX_TUTOR_CONTINUATION_BYTES,
    SQLiteTutorContinuationStore,
    TutorContinuationCorruptionError,
    TutorContinuationStore,
    UnsupportedSQLiteTutorContinuationDatabaseError,
)

__all__ = [
    "INDEX_VERSION",
    "MAX_TUTOR_CONTINUATION_BYTES",
    "EventBatchError",
    "NamespacedSQLiteRunStore",
    "ProjectionConsistencyError",
    "RetrievalIndexIntegrityError",
    "RunStoreCorruptionError",
    "SQLiteConnectionIdentityError",
    "SQLiteConnectionIdentityGuard",
    "SQLiteEventStore",
    "SQLiteFtsRetrieval",
    "SQLiteRunStore",
    "SQLiteTutorContinuationStore",
    "SequenceConflictError",
    "TutorContinuationCorruptionError",
    "TutorContinuationStore",
    "UnsupportedSQLiteDatabaseError",
    "UnsupportedSQLiteRunDatabaseError",
    "UnsupportedSQLiteTutorContinuationDatabaseError",
    "compile_literal_query",
    "normalize_bm25_score",
    "observe_local_repository",
]
