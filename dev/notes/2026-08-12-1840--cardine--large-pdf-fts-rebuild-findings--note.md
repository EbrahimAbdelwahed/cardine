# Note: large PDF admission and FTS rebuild findings

Date: 2026-08-12 18:40
Area: cardine-documents / retrieval

## Context

The real source
`/Users/ebrahimabdelwahed/Desktop/Med/20_Progetti/audio-to-sbobina/data/00_sources/sbobine_prec/BIOCHIMICA UNIFICATO.pdf`
was exercised through Cardine's AnyDoc admission path.

Source facts:

- 203,812,587 bytes (approximately 194 MiB).
- 559 pages.
- Not encrypted.
- Mixed document: 11 pages have no extractable text; the remaining pages are
  text-bearing.

AnyDoc initially failed with `pdf_unsupported` after 2.6 seconds because page 27
has no extractable text. The worker aborted the entire document on the first
`UnsupportedError`. A full diagnostic scan identified the no-text pages as:

`27, 121, 155, 201, 219, 259, 272, 302, 465, 557, 558`.

The production worker was changed locally to preserve text-bearing pages and
emit one canonical, page-bound `OCR non disponibile` marker for isolated pages
without extractable text. A fully image-only PDF remains unsupported. The
default timeout was raised from 30 to the existing validated 120-second ceiling.
The runtime also allowlists only the exact known benign CoreGraphics stderr line
emitted by this PDF; all other stderr remains a protocol failure.

Real conversion after the change:

- 559 page spans.
- 1,421,590 Markdown bytes.
- 11 explicit no-OCR markers.
- 46.35 seconds elapsed.
- AnyDoc artifact, sandbox, memory, output, page-count and digest checks remained
  active.

The browser admission then committed the source successfully to
`cardine-wave-a-live`:

- Source ID:
  `source-pdf-sha256:bbf8a1d112ba223cd2d17352584e229ec0bd9ea62fbfd040f42914aaefdd9754`
- Revision ID:
  `revision-sha256:ef65364402c8502fc038911c8a92cfe09cbb036c7e69292e7da864b0a4c1c008`
- Canonical chunks: 3,676.
- PageIndex status immediately after admission: `queued`.

The UI remained on `Salvo e indicizzo la fonte…` after canonical admission.
The server process was healthy and using approximately 94–98% CPU. The delay is
in `LocalRepository.rebuild_retrieval()` / `SQLiteFtsRetrieval.rebuild()`, not in
AnyDoc or canonical ingestion.

The exact performance problem is quadratic validation:

1. `SQLiteFtsRetrieval.rebuild()` validates every `RetrievalDocument`.
2. `_validate_document()` calls the catalog's `canonical_document(chunk_id)`.
3. `_RepositorySourceCatalog.canonical_document()` reconstructs and scans
   `all_documents(include_superseded=True)` for every chunk.
4. For 3,676 chunks this repeatedly reconstructs and scans the complete event
   projection and blob-backed catalog, producing approximately O(n²) work before
   the SQLite batch is committed.

Observed while waiting:

- `events.sqlite3` had already grown to approximately 4.8 MB, proving canonical
  admission completed.
- `retrieval.sqlite3` retained its old timestamp and size while validation ran,
  consistent with pre-transaction catalog validation.
- PageIndex was queued with attempt `0`; it was not the active bottleneck.

## Implication

Do not change AnyDoc, rechunk the source externally, or re-upload the PDF to fix
this latency. The source is already canonical and safe.

The future fix should preserve the public `SQLiteFtsRetrieval.rebuild()` seam and
all integrity checks, but build one immutable canonical lookup per rebuild:

- materialize the complete canonical catalog exactly once;
- index it by `ChunkId` once;
- validate each batch document against that lookup in O(1);
- reuse the same materialized catalog for complete-batch comparison, checksum,
  citation-resolution validation and final audit where possible;
- keep one atomic SQLite transaction and never expose a partially rebuilt index;
- retain source retirement filtering and historical canonical citation
  resolution semantics;
- add a behavior/performance regression using thousands of chunks and an
  instrumented catalog proving `documents()` is called a bounded number of
  times, without asserting wall-clock timing.

The browser should also decouple the response from the discardable derived-index
rebuild: after the canonical source commit, return a truthful `indexing`/`queued`
receipt and reconcile FTS/PageIndex asynchronously or through an explicit bounded
operator action. A derived-index failure must not make a successfully admitted
canonical source appear unsaved.

## References

- `src/cardine/cli/repository.py`: `_RepositorySourceCatalog.canonical_document`,
  `LocalRepository.rebuild_retrieval`.
- `src/study_agent/adapters/sqlite/fts_retrieval.py`:
  `SQLiteFtsRetrieval.rebuild`, `_validate_batch`, `_validate_document`.
- `src/cardine/documents/anydoc_worker_child.py`: per-page AnyDoc conversion.
- `src/cardine/documents/anydoc_runtime.py`: worker execution and response checks.
- `dev/plans/2026-08-12-1830--cardine--anydoc-large-mixed-pdf--plan.md`.
- `dev/logs/2026-08-12-1900--cardine--anydoc-large-mixed-pdf--log.md`.

Verification recorded during diagnosis:

- AnyDoc focused + PDF admission tests: 11 passed, 27 deselected.
- Ruff on document source/tests: passed.
- Mypy on `cardine.documents`: passed.
- Real Biochimica production-worker conversion: passed as detailed above.
