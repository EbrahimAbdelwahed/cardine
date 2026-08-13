# Log: Wave A product closure

Date: 2026-08-13 02:00
Area: cardine-wave-a

## Summary

Closed the private single-user study journey across provider consent, text/PDF
admission, PageIndex navigation with lexical fallback, explicit lesson pins,
grounded citations, selected-lesson flashcards, atomic HUMAN decisions, and
accepted-only recall. Policy events now export semantically without leaking
browser retry identifiers, and forged policy histories fail closed.

## Files Changed

- `src/cardine/documents/`: corrected PDF temporary-file limits and added an
  adversarial conversion matrix.
- `src/study_agent/application/export.py`: added one ordered Cardine policy
  history validator and redacted policy correlations.
- `src/cardine/cli/repository.py`: distinguished missing canonical blobs from
  integrity failures during PageIndex reconciliation.
- `tests/integration/demo/TUT08/test_wave_a_study_journey.py`: proves the
  aggregate Markdown study journey through restart, fallback, retirement, and
  historical resolution.

## Verification

- Focused export/runtime/aggregate regressions: 36 passed.
- AnyDoc adversarial worker matrix outside the nested macOS sandbox: 9 passed.
- Broad non-PDF Wave A product matrix: 176 passed, 9 socket/CPython skips; the
  two PDF cases failed only because that run intentionally remained inside the
  nested sandbox, while their privileged worker matrix passed separately.
- Ruff and focused mypy: passed.
- `scripts/audit_harness_ownership.py --check`: `OK (322 rows)`.
- `git diff --check`: passed.

## Notes

- PDF conversion remains deliberately limited to the verified macOS arm64
  AnyDoc 0.1.7 artifact and text-bearing PDFs; OCR stays fail-closed.
- PageIndex remains navigation-only. Canonical source bytes and citations are
  still owned by the source ledger.
