# Log: AnyDoc PDF admission

Date: 2026-08-12 22:00
Area: cardine-wave-a

## Summary

Added a bounded PDF admission path for Cardine using the explicitly approved
AnyDoc 0.1.7 macOS arm64 artifact. The browser streams the PDF to a private
temporary file, an isolated worker converts it to normalized Markdown, and the
canonical source ledger stores both the immutable original PDF and the derived
Markdown with a replayable conversion receipt. Text and Markdown admission are
unchanged.

The converter fails closed outside native macOS arm64 on CPython 3.12/3.13. It
verifies the vendored wheel and every archive member before native import, runs
under a deny-by-default macOS sandbox with networking and process creation
blocked, bounds input/output/pages/time/descriptors/CPU, and uses a parent-side
RSS watchdog. Scanned or image-only PDFs remain a truthful unsupported case;
OCR is not implemented.

## Files Changed

- `src/cardine/documents/`: verified AnyDoc artifact, resource policy, isolated parent/child runtime.
- `src/cardine/demo/browser.py` and `browser.js`: bounded binary PDF transport and visible PDF upload flow.
- `src/cardine/demo/ui_application.py`: canonical PDF admission and conversion receipt.
- `src/study_agent/domain/` and `src/study_agent/ingestion/`: immutable original/derived provenance persisted in the existing source revision event.
- `tests/unit/cardine/documents/`, `tests/integration/demo/TUT08/test_repository_backed_chat.py`, and `tests/e2e/test_cardine_browser_contract.py`: worker, restart, blob, and HTTP streaming coverage.
- `pyproject.toml`: includes the exact reviewed AnyDoc wheel as Cardine package data.

## Verification

- `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/cardine/documents/test_anydoc_worker.py tests/integration/test_text_ingestion.py tests/integration/demo/TUT08/test_repository_backed_chat.py -k 'anydoc or pdf_import_is_canonical or text_ingestion'`: 20 passed, 27 deselected.
- `PYTHONPATH=src .venv/bin/python -m pytest -q tests/e2e/test_cardine_browser_contract.py`: 10 passed using loopback HTTP.
- `.venv/bin/ruff check src/cardine/documents src/cardine/demo/ui_application.py tests/unit/cardine/documents`: passed.
- `MYPYPATH=src .venv/bin/mypy -p cardine.documents -p cardine.demo.ui_application`: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- `python3 scripts/audit_harness_ownership.py --check`: passed, 322 rows.
- `.venv/bin/python -m build --no-isolation --outdir /private/tmp/cardine-pdf-dist`: wheel and sdist built; the wheel contains the exact 3,169,866-byte artifact with SHA-256 `4ab410d12eb7d339e60356eecf2375786acd2d3c6660dcbe1e079eb60addc004`.
- `git diff --check`: passed.

## Notes

- The RSS watchdog is enforceable kill containment, not a strict kernel memory quota; macOS can overshoot between samples.
- The current verified artifact supports macOS arm64 only. Other platforms fail closed.
- Deterministic per-page citation mapping and OCR remain outside this checkpoint; the canonical receipt records structural page count and the limitations explicitly.
