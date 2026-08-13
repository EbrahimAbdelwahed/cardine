"""Discardable SQLite FTS5 lexical retrieval over canonical source spans."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from study_agent.domain.identifiers import ChunkId, CourseId, RevisionId, SourceId
from study_agent.domain.source import Citation, SourceChunk
from study_agent.ports.retrieval import (
    EvidenceStatus,
    IndexReceipt,
    RetrievalCatalogPort,
    RetrievalDocument,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    RetrievalQuery,
    retrieval_catalog_fingerprint,
    retrieval_read_set_fingerprint,
)

from .event_store import SQLiteConnectionGuard, _writable_nofollow_uri

INDEX_VERSION = "sqlite-fts5-unicode61-v1"
RETRIEVAL_STRATEGY_ID = "sqlite_fts5_bm25"
RETRIEVAL_STRATEGY_VERSION = "1.1.0"
_MAX_RELEVANCE_QUERY_TERMS = 6
_MAX_RELEVANCE_SOURCE_TERMS = 32
_RELEVANCE_CANDIDATE_LIMIT = 64
_QUERY_STOP_WORDS = frozenset(
    {
        "a", "about", "and", "briefly", "by", "can", "could", "di", "e", "explain",
        "fonte", "fonti", "from", "il", "in", "instructions", "la", "le", "materiale",
        "materiali", "of", "or", "please", "prompt", "source", "spiega", "spiegami",
        "the", "to", "uploaded", "what", "with", "ignore", "previous", "developer",
        "assistant", "drop", "table", "column", "value",
    }
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS retrieval_documents (
    chunk_id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    section_path TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    chunker_version TEXT NOT NULL,
    chunk_metadata TEXT NOT NULL,
    title TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_role TEXT NOT NULL,
    trust_level INTEGER NOT NULL,
    is_current_revision INTEGER NOT NULL CHECK (is_current_revision IN (0, 1))
) STRICT;

CREATE INDEX IF NOT EXISTS retrieval_filter_idx ON retrieval_documents (
    course_id, is_current_revision, trust_level, source_kind, source_role, revision_id
);

CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(
    chunk_id UNINDEXED,
    text,
    tokenize = 'unicode61'
);
"""


class RetrievalIndexIntegrityError(RuntimeError):
    """Derived index content cannot be resolved to canonical source text."""


def compile_literal_query(text: str) -> str | None:
    """Compile untrusted input to an AND of quoted tokenizer literals."""

    with closing(sqlite3.connect(":memory:")) as connection:
        return _compile_literal_query(connection, text)


def _compile_literal_query(connection: sqlite3.Connection, text: str) -> str | None:
    """Tokenize with the same SQLite FTS5 unicode61 configuration as the index."""

    return _quote_query_tokens(_literal_query_tokens(connection, text))


def _literal_query_tokens(connection: sqlite3.Connection, text: str) -> tuple[str, ...]:
    """Return inert unicode61 terms in learner order."""

    connection.execute(
        "CREATE VIRTUAL TABLE temp.retrieval_query_tokens "
        "USING fts5(text, tokenize='unicode61')"
    )
    connection.execute(
        "CREATE VIRTUAL TABLE temp.retrieval_query_vocab "
        "USING fts5vocab(retrieval_query_tokens, 'instance')"
    )
    connection.execute("INSERT INTO retrieval_query_tokens(text) VALUES (?)", (text,))
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT term FROM retrieval_query_vocab ORDER BY doc, offset"
        )
    )


def _quote_query_tokens(tokens: tuple[str, ...]) -> str | None:
    if not tokens:
        return None
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def normalize_bm25_score(raw_score: float) -> float:
    """Map adapter BM25 direction to a finite portable relevance score in [0, 1]."""

    if raw_score <= 0:
        strength = -raw_score
        return strength / (1 + strength)
    return 1 / (1 + raw_score)


class SQLiteFtsRetrieval:
    def __init__(
        self,
        database: str | Path,
        content: RetrievalCatalogPort,
        *,
        read_only: bool = False,
        connection_identity_guard: SQLiteConnectionGuard | None = None,
    ) -> None:
        self._database = str(database)
        if self._database == ":memory:":
            raise ValueError("SQLiteFtsRetrieval requires a path-backed database")
        if type(read_only) is not bool:
            raise TypeError("read_only must be a boolean")
        self._read_only = read_only
        self._connection_identity_guard = connection_identity_guard
        self._content = content
        if not read_only:
            with closing(self._connect()) as connection:
                connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        database = self._database
        uri = False
        if self._read_only:
            database = (
                Path(database).absolute().as_uri() + "?mode=ro&immutable=1"
            )
            uri = True
        elif self._connection_identity_guard is not None:
            database = _writable_nofollow_uri(database)
            uri = True
        def opener() -> sqlite3.Connection:
            return sqlite3.connect(database, timeout=30, uri=uri)
        connection = (
            opener()
            if self._connection_identity_guard is None
            else self._connection_identity_guard.connect(opener)
        )
        if not self._read_only:
            connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _require_write(self) -> None:
        if self._read_only:
            raise PermissionError("read-only retrieval adapter cannot mutate the index")

    def index(self, documents: Sequence[RetrievalDocument]) -> IndexReceipt:
        self._require_write()
        batch = tuple(documents)
        current = self._validate_batch(batch)
        with closing(self._connect()) as connection, connection:
            self._write_batch(connection, batch, current)
        canonical = self._audit_integrity()
        return IndexReceipt(
            len(canonical),
            self._current_index_version(),
            retrieval_catalog_fingerprint(canonical),
        )

    def _validate_batch(
        self,
        batch: tuple[RetrievalDocument, ...],
        *,
        canonical: tuple[RetrievalDocument, ...] | None = None,
    ) -> dict[tuple[str, str], str]:
        chunk_ids = tuple(document.chunk.chunk_id for document in batch)
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("index batch must not contain duplicate chunk ids")
        validate_resolution_with_catalog = canonical is None
        if canonical is None:
            canonical_documents = tuple(
                self._content.documents(include_superseded=True)
            )
        else:
            canonical_documents = canonical
        canonical_by_chunk = {
            document.chunk.chunk_id: document for document in canonical_documents
        }
        if len(canonical_by_chunk) != len(canonical_documents):
            raise RetrievalIndexIntegrityError(
                "canonical catalog contains duplicate chunks"
            )
        canonical_by_revision: dict[
            tuple[CourseId, SourceId, RevisionId], set[ChunkId]
        ] = {}
        for canonical_document in canonical_documents:
            revision_key = (
                canonical_document.course_id,
                canonical_document.source_id,
                canonical_document.revision_id,
            )
            canonical_by_revision.setdefault(revision_key, set()).add(
                canonical_document.chunk.chunk_id
            )
        batch_by_revision: dict[
            tuple[CourseId, SourceId, RevisionId], set[ChunkId]
        ] = {}
        current: dict[tuple[str, str], str] = {}
        for document in batch:
            self._validate_document(
                document,
                canonical_by_chunk,
                validate_resolution_with_catalog=validate_resolution_with_catalog,
            )
            revision_key = (
                document.course_id,
                document.source_id,
                document.revision_id,
            )
            batch_by_revision.setdefault(revision_key, set()).add(
                document.chunk.chunk_id
            )
            if document.is_current_revision:
                current_key = (str(document.course_id), str(document.source_id))
                existing = current.setdefault(current_key, str(document.revision_id))
                if existing != str(document.revision_id):
                    raise ValueError("one index batch cannot declare two current source revisions")
        for key, batch_chunks in batch_by_revision.items():
            if batch_chunks != canonical_by_revision.get(key, set()):
                raise ValueError("index batch must contain the complete canonical revision")
        return current

    def _write_batch(
        self,
        connection: sqlite3.Connection,
        batch: tuple[RetrievalDocument, ...],
        current: dict[tuple[str, str], str],
    ) -> None:
        for (course_id, source_id), revision_id in sorted(current.items()):
            connection.execute(
                """
                UPDATE retrieval_documents SET is_current_revision = 0
                WHERE course_id = ? AND source_id = ? AND revision_id <> ?
                """,
                (course_id, source_id, revision_id),
            )
        for document in batch:
            self._upsert(connection, document)

    def _validate_document(
        self,
        document: RetrievalDocument,
        canonical_by_chunk: dict[ChunkId, RetrievalDocument],
        *,
        validate_resolution_with_catalog: bool,
    ) -> None:
        try:
            canonical = canonical_by_chunk[document.chunk.chunk_id]
        except KeyError as error:
            raise RetrievalIndexIntegrityError(
                "document chunk is absent from the canonical catalog"
            ) from error
        if canonical != document:
            raise RetrievalIndexIntegrityError(
                "document metadata or text differs from the canonical catalog"
            )
        digest = sha256(document.text.encode("utf-8")).hexdigest()
        if digest != document.chunk.checksum_sha256:
            raise RetrievalIndexIntegrityError("document text checksum does not match chunk")
        citation = Citation(
            document.source_id,
            document.revision_id,
            document.chunk.chunk_id,
            document.chunk.start_offset,
            document.chunk.end_offset,
            "index-validation",
            document.text,
        )
        if validate_resolution_with_catalog:
            try:
                resolved = self._content.resolve(citation)
            except Exception as error:
                raise RetrievalIndexIntegrityError(
                    "document does not resolve to canonical source content"
                ) from error
            if resolved.text != document.text:
                raise RetrievalIndexIntegrityError(
                    "document differs from canonical source content"
                )
            return
        if (
            citation.source_id != canonical.source_id
            or citation.revision_id != canonical.revision_id
            or citation.chunk_id != canonical.chunk.chunk_id
            or citation.start_offset != canonical.chunk.start_offset
            or citation.end_offset != canonical.chunk.end_offset
            or citation.quoted_snippet != canonical.text
        ):
            raise RetrievalIndexIntegrityError("document differs from canonical source content")

    @staticmethod
    def _upsert(connection: sqlite3.Connection, document: RetrievalDocument) -> None:
        chunk = document.chunk
        chunk_id = str(chunk.chunk_id)
        connection.execute("DELETE FROM retrieval_fts WHERE chunk_id = ?", (chunk_id,))
        connection.execute(
            "INSERT INTO retrieval_fts (chunk_id, text) VALUES (?, ?)",
            (chunk_id, document.text),
        )
        connection.execute(
            """
            INSERT INTO retrieval_documents (
                chunk_id, course_id, source_id, revision_id, start_offset, end_offset,
                section_path, ordinal, checksum_sha256, chunker_version, chunk_metadata,
                title, source_kind, source_role, trust_level, is_current_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                course_id=excluded.course_id, source_id=excluded.source_id,
                revision_id=excluded.revision_id, start_offset=excluded.start_offset,
                end_offset=excluded.end_offset, section_path=excluded.section_path,
                ordinal=excluded.ordinal, checksum_sha256=excluded.checksum_sha256,
                chunker_version=excluded.chunker_version,
                chunk_metadata=excluded.chunk_metadata, title=excluded.title,
                source_kind=excluded.source_kind, source_role=excluded.source_role,
                trust_level=excluded.trust_level,
                is_current_revision=excluded.is_current_revision
            """,
            (
                chunk_id,
                str(document.course_id),
                str(document.source_id),
                str(document.revision_id),
                chunk.start_offset,
                chunk.end_offset,
                json.dumps(chunk.section_path, separators=(",", ":")),
                chunk.ordinal,
                chunk.checksum_sha256,
                chunk.chunker_version,
                json.dumps(dict(chunk.metadata), sort_keys=True, separators=(",", ":")),
                document.title,
                document.source_kind.value,
                document.source_role,
                document.trust_level,
                int(document.is_current_revision),
            ),
        )

    def search(self, query: RetrievalQuery) -> RetrievalEvidenceSet:
        canonical = self._audit_integrity()
        fingerprint = _query_fingerprint(query)
        index_version = _content_index_version(canonical)
        title_evidence = self._exact_title_evidence(query, canonical)
        if title_evidence:
            return _evidence_set(
                EvidenceStatus.SUFFICIENT, title_evidence, fingerprint, index_version
            )
        with closing(self._connect()) as connection:
            tokens = _literal_query_tokens(connection, query.text)
            informative_tokens = _informative_query_tokens(tokens)
            if not informative_tokens:
                return _evidence_set(
                    EvidenceStatus.INSUFFICIENT, (), fingerprint, index_version
                )
            rows: tuple[tuple[object, ...], ...] = ()
            if len(informative_tokens) <= _MAX_RELEVANCE_QUERY_TERMS:
                compiled = _quote_query_tokens(informative_tokens)
                if compiled is not None:
                    sql, parameters = _search_sql(query, compiled)
                    rows = tuple(connection.execute(sql, parameters).fetchall())
            evidence = tuple(self._resolve_row(row) for row in rows)
            if not evidence:
                relevance_rows = _bounded_relevance_rows(
                    connection, query, informative_tokens
                )
                evidence = tuple(self._resolve_row(row) for row in relevance_rows)
        status = EvidenceStatus.SUFFICIENT if evidence else EvidenceStatus.INSUFFICIENT
        return _evidence_set(status, evidence, fingerprint, index_version)

    def _exact_title_evidence(
        self, query: RetrievalQuery, canonical: tuple[RetrievalDocument, ...]
    ) -> tuple[RetrievalEvidence, ...]:
        requested_title = query.text.strip().casefold()
        matches = tuple(
            document
            for document in canonical
            if document.course_id == query.course_id
            and document.title.strip().casefold() == requested_title
            and (query.include_superseded or document.is_current_revision)
            and (not query.revision_ids or document.revision_id in query.revision_ids)
            and (not query.source_kinds or document.source_kind in query.source_kinds)
            and (not query.source_roles or document.source_role in query.source_roles)
            and document.trust_level >= query.minimum_trust_level
        )
        ordered = sorted(
            matches,
            key=lambda item: (
                str(item.source_id),
                str(item.revision_id),
                item.chunk.ordinal,
                str(item.chunk.chunk_id),
            ),
        )[: query.limit]
        return tuple(self._resolve_document(document) for document in ordered)

    def _resolve_document(self, document: RetrievalDocument) -> RetrievalEvidence:
        chunk = document.chunk
        resolved = self._content.resolve(
            Citation(
                chunk.source_id,
                chunk.revision_id,
                chunk.chunk_id,
                chunk.start_offset,
                chunk.end_offset,
                "retrieval-title-match",
            )
        )
        if resolved.text != document.text:
            raise RetrievalIndexIntegrityError(
                "title-matched candidate does not resolve to canonical source content"
            )
        return RetrievalEvidence(chunk, resolved.citation, resolved.text, 1.0)

    def _resolve_row(self, row: tuple[object, ...]) -> RetrievalEvidence:
        try:
            (
                indexed_text,
                raw_score,
                chunk_id,
                source_id,
                revision_id,
                start,
                end,
                section_path,
                ordinal,
                checksum,
                chunker_version,
                metadata,
            ) = row
            chunk = SourceChunk(
                ChunkId(str(chunk_id)),
                SourceId(str(source_id)),
                RevisionId(str(revision_id)),
                int(str(start)),
                int(str(end)),
                tuple(json.loads(str(section_path))),
                int(str(ordinal)),
                str(checksum),
                str(chunker_version),
                json.loads(str(metadata)),
            )
            citation = Citation(
                chunk.source_id,
                chunk.revision_id,
                chunk.chunk_id,
                chunk.start_offset,
                chunk.end_offset,
                "retrieval-candidate",
            )
            resolved = self._content.resolve(citation)
        except Exception as error:
            raise RetrievalIndexIntegrityError(
                "retrieval candidate does not resolve to canonical source content"
            ) from error
        if resolved.text != str(indexed_text):
            raise RetrievalIndexIntegrityError("indexed candidate text is stale or tampered")
        return RetrievalEvidence(
            chunk,
            resolved.citation,
            resolved.text,
            normalize_bm25_score(float(str(raw_score))),
        )

    def rebuild(self, documents: Sequence[RetrievalDocument]) -> IndexReceipt:
        self._require_write()
        batch = tuple(documents)
        chunk_ids = tuple(item.chunk.chunk_id for item in batch)
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("rebuild batch must not contain duplicate chunk ids")
        canonical = tuple(self._content.documents(include_superseded=True))
        if {item.chunk.chunk_id: item for item in batch} != {
            item.chunk.chunk_id: item for item in canonical
        }:
            raise ValueError("rebuild requires the complete canonical catalog")
        current = self._validate_batch(batch, canonical=canonical)
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM retrieval_fts")
            connection.execute("DELETE FROM retrieval_documents")
            self._write_batch(connection, batch, current)
        indexed = self._audit_integrity()
        return IndexReceipt(
            len(indexed),
            self._current_index_version(),
            retrieval_catalog_fingerprint(indexed),
        )

    def audit(self) -> IndexReceipt:
        """Return an integrity receipt without modifying derived index state."""

        canonical = self._audit_integrity()
        return IndexReceipt(
            len(canonical),
            self._current_index_version(),
            retrieval_catalog_fingerprint(canonical),
        )

    def _current_index_version(self) -> str:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT m.chunk_id, m.course_id, m.source_id, m.revision_id,
                       m.start_offset, m.end_offset, m.section_path, m.ordinal,
                       m.checksum_sha256, m.chunker_version, m.chunk_metadata,
                       m.title, m.source_kind, m.source_role, m.trust_level,
                       m.is_current_revision, f.text
                FROM retrieval_documents AS m
                JOIN retrieval_fts AS f ON f.chunk_id = m.chunk_id
                ORDER BY m.chunk_id
                """
            ).fetchall()
        return _index_rows_version(rows)

    def _audit_integrity(self) -> tuple[RetrievalDocument, ...]:
        canonical = tuple(self._content.documents(include_superseded=True))
        canonical_ids = tuple(item.chunk.chunk_id for item in canonical)
        if len(set(canonical_ids)) != len(canonical_ids):
            raise RetrievalIndexIntegrityError("canonical catalog contains duplicate chunks")
        expected_metadata = sorted(_metadata_tuple(item) for item in canonical)
        expected_fts = sorted((str(item.chunk.chunk_id), item.text) for item in canonical)
        with closing(self._connect()) as connection:
            metadata = connection.execute(
                """
                SELECT chunk_id, course_id, source_id, revision_id, start_offset, end_offset,
                       section_path, ordinal, checksum_sha256, chunker_version,
                       chunk_metadata, title, source_kind, source_role, trust_level,
                       is_current_revision
                FROM retrieval_documents ORDER BY chunk_id
                """
            ).fetchall()
            fts = connection.execute(
                "SELECT chunk_id, text FROM retrieval_fts ORDER BY chunk_id, text"
            ).fetchall()
        if metadata != expected_metadata:
            raise RetrievalIndexIntegrityError(
                "derived retrieval metadata differs from the canonical catalog"
            )
        if fts != expected_fts:
            raise RetrievalIndexIntegrityError(
                "derived FTS rows differ from the canonical catalog"
            )
        return canonical


def _search_sql(query: RetrievalQuery, compiled: str) -> tuple[str, tuple[object, ...]]:
    conditions = [
        "retrieval_fts MATCH ?",
        "m.course_id = ?",
        "m.trust_level >= ?",
    ]
    parameters: list[object] = [compiled, str(query.course_id), query.minimum_trust_level]
    if not query.include_superseded:
        conditions.append("m.is_current_revision = 1")
    for column, values in (
        ("m.revision_id", tuple(str(item) for item in query.revision_ids)),
        ("m.source_kind", tuple(item.value for item in query.source_kinds)),
        ("m.source_role", query.source_roles),
    ):
        if values:
            conditions.append(f"{column} IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
    sql = f"""
        SELECT f.text, bm25(retrieval_fts), m.chunk_id, m.source_id, m.revision_id,
               m.start_offset, m.end_offset, m.section_path, m.ordinal,
               m.checksum_sha256, m.chunker_version, m.chunk_metadata
        FROM retrieval_fts AS f
        JOIN retrieval_documents AS m ON m.chunk_id = f.chunk_id
        WHERE {' AND '.join(conditions)}
        ORDER BY bm25(retrieval_fts) ASC, m.chunk_id ASC
        LIMIT ?
    """
    parameters.append(query.limit)
    return sql, tuple(parameters)


def _bounded_relevance_rows(
    connection: sqlite3.Connection,
    query: RetrievalQuery,
    tokens: tuple[str, ...],
) -> tuple[tuple[object, ...], ...]:
    """Recover concise agent queries without broadening arbitrary learner text."""

    unique_tokens = tuple(dict.fromkeys(tokens))[:_MAX_RELEVANCE_SOURCE_TERMS]
    if len(unique_tokens) < 2:
        return ()
    candidate_query = RetrievalQuery(
        query.course_id,
        query.text,
        limit=max(query.limit, _RELEVANCE_CANDIDATE_LIMIT),
        revision_ids=query.revision_ids,
        minimum_trust_level=query.minimum_trust_level,
        source_kinds=query.source_kinds,
        source_roles=query.source_roles,
        include_superseded=query.include_superseded,
    )
    token_rows: list[tuple[int, str, tuple[tuple[object, ...], ...]]] = []
    for position, token in enumerate(unique_tokens):
        compiled = _quote_query_tokens((token,))
        if compiled is None:  # pragma: no cover - non-empty token contract
            continue
        sql, parameters = _search_sql(candidate_query, compiled)
        rows = tuple(connection.execute(sql, parameters).fetchall())
        if rows:
            token_rows.append((position, token, rows))
    selected = tuple(
        sorted(token_rows, key=lambda item: (len(item[2]), item[0], item[1]))[
            :_MAX_RELEVANCE_QUERY_TERMS
        ]
    )
    if len(selected) < 2:
        return ()
    matches: dict[str, list[tuple[object, ...]]] = {}
    for _position, _token, rows in selected:
        for row in rows:
            matches.setdefault(str(row[2]), []).append(row)
    minimum_coverage = 2
    ranked = tuple(
        sorted(
            (
                (len(rows), sum(float(str(row[1])) for row in rows), rows[0])
                for rows in matches.values()
                if len(rows) >= minimum_coverage
            ),
            key=lambda item: (-item[0], item[1], str(item[2][2])),
        )
    )
    return tuple(item[2] for item in ranked[: query.limit])


def _informative_query_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        token
        for token in dict.fromkeys(tokens)
        if token not in _QUERY_STOP_WORDS
    )


def _metadata_tuple(document: RetrievalDocument) -> tuple[object, ...]:
    chunk = document.chunk
    return (
        str(chunk.chunk_id),
        str(document.course_id),
        str(document.source_id),
        str(document.revision_id),
        chunk.start_offset,
        chunk.end_offset,
        json.dumps(chunk.section_path, separators=(",", ":")),
        chunk.ordinal,
        chunk.checksum_sha256,
        chunk.chunker_version,
        json.dumps(dict(chunk.metadata), sort_keys=True, separators=(",", ":")),
        document.title,
        document.source_kind.value,
        document.source_role,
        document.trust_level,
        int(document.is_current_revision),
    )


def _query_fingerprint(query: RetrievalQuery) -> str:
    payload = json.dumps(
        {
            "course_id": str(query.course_id),
            "text": query.text,
            "limit": query.limit,
            "revision_ids": [str(item) for item in query.revision_ids],
            "source_kinds": [item.value for item in query.source_kinds],
            "source_roles": query.source_roles,
            "minimum_trust_level": query.minimum_trust_level,
            "include_superseded": query.include_superseded,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(payload).hexdigest()


def _evidence_set(
    status: EvidenceStatus,
    evidence: tuple[RetrievalEvidence, ...],
    query_fingerprint: str,
    index_version: str,
) -> RetrievalEvidenceSet:
    return RetrievalEvidenceSet(
        status,
        evidence,
        query_fingerprint,
        RETRIEVAL_STRATEGY_ID,
        RETRIEVAL_STRATEGY_VERSION,
        index_version,
        retrieval_read_set_fingerprint(evidence),
    )


def _content_index_version(documents: tuple[RetrievalDocument, ...]) -> str:
    rows = [
        (*_metadata_tuple(item), item.text)
        for item in sorted(documents, key=lambda value: str(value.chunk.chunk_id))
    ]
    return _index_rows_version(rows)


def _index_rows_version(rows: Sequence[Sequence[object]]) -> str:
    payload = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(b"study-agent-retrieval-index-v1\0" + payload).hexdigest()
