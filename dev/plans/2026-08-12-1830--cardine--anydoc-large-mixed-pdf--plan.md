# Plan: AnyDoc large mixed PDF admission

Date: 2026-08-12 18:30
Area: cardine-documents

## Goal

Admit `BIOCHIMICA UNIFICATO.pdf` through Cardine's verified AnyDoc worker without
using pre-existing chunked text. Preserve page-bound canonical provenance while
tolerating isolated pages that contain no extractable text.

## Scope

- In scope: mixed text/image-only page handling, bounded worker timeout, focused
  worker tests, and one real-book conversion verification.
- Out of scope: OCR, alternate converters, source chunking, PageIndex changes,
  and weakening artifact or sandbox verification.

## Approach

1. Add a failing public worker test for a PDF containing one text page and one
   image-only/empty page.
2. Preserve all-text and all-image-only behavior while emitting a canonical
   no-OCR marker for isolated unsupported pages.
3. Raise the default worker timeout to the existing validated 120-second ceiling;
   the real 559-page scan takes approximately 51 seconds on this host.
4. Run focused worker tests, then convert the real Biochimica PDF and verify its
   page map and bounded output.

## Risks

- A placeholder must never be represented as extracted source text.
- A fully scanned/image-only PDF must remain unsupported because OCR is absent.
- Large-input execution must retain memory, output, page, sandbox, and artifact
  bounds.

## Verification

- `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/cardine/documents/test_anydoc_worker.py`
- `PYTHONPATH=src .venv/bin/python -m ruff check src/cardine/documents tests/unit/cardine/documents/test_anydoc_worker.py`
- `MYPYPATH=src .venv/bin/python -m mypy -p cardine.documents`
- Real conversion of `BIOCHIMICA UNIFICATO.pdf` through `convert_pdf_in_worker`.
- `git diff --check`
