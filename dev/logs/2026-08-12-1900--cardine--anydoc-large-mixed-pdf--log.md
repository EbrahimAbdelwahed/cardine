# Log: AnyDoc large mixed PDF admission

Date: 2026-08-12 19:00
Area: cardine-documents
Publication status: verified local work; implementation not included in `095fb75`

## Summary

Cardine's verified AnyDoc worker now admits large text-bearing PDFs that contain
isolated pages without extractable text. Such pages receive a canonical,
page-bound `OCR non disponibile` marker; a wholly image-only PDF remains
unsupported. The default worker timeout moved from 30 to the existing validated
120-second ceiling. All artifact, sandbox, input, output, memory, page-count and
page-map checks remain active.

The real `BIOCHIMICA UNIFICATO.pdf` was converted through the production worker:
559 pages, 1,421,590 Markdown bytes, 11 no-OCR page markers, 559 exact page spans,
and 46.35 seconds elapsed.

## Files Changed

- `src/cardine/documents/config.py`: use the 120-second bounded default timeout.
- `src/cardine/documents/anydoc_worker_child.py`: continue across isolated
  no-text pages while rejecting fully image-only documents.
- `src/cardine/documents/anydoc_runtime.py`: allow only the exact known benign
  CoreGraphics stderr warning emitted by the real PDF; other stderr remains a
  protocol failure.
- `tests/unit/cardine/documents/test_anydoc_worker.py`: cover mixed text/no-text
  conversion and exact page-map behavior.

## Verification

- Red test: mixed text/no-text PDF failed with `pdf_unsupported` before the fix.
- `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/cardine/documents/test_anydoc_worker.py tests/integration/demo/TUT08/test_repository_backed_chat.py -k 'anydoc or pdf_import_is_canonical'`: 11 passed, 27 deselected.
- Real production-worker conversion of `BIOCHIMICA UNIFICATO.pdf`: passed with
  559 pages, 11 markers and 559 page spans in 46.35 seconds.
- `PYTHONPATH=src .venv/bin/python -m ruff check src/cardine/documents tests/unit/cardine/documents/test_anydoc_worker.py`: passed.
- `MYPYPATH=src .venv/bin/python -m mypy -p cardine.documents`: passed.
- `git diff --check`: passed.

## Notes

- At the 2026-08-13 memory consolidation, the corresponding source/test changes
  remained deliberately uncommitted in the recovery checkout. This log preserves
  their verification evidence but does not claim they are present on the remote branch.
- OCR is still intentionally absent. The marker is not represented as extracted
  PDF text and makes the limitation visible in canonical content.
- Restart the running Cardine process before retrying the browser upload so it
  loads the updated worker code and timeout.
