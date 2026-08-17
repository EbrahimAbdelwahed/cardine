# Log: Flashcard provider-schema projection fix

Date: 2026-08-17 14:18 CEST
Area: Cardine / model adapter / flashcards

## Summary

Fixed the live flashcard generation failure caused by forwarding the local-only
`minLength` constraint in the strict OpenAI Structured Outputs schema. The
provider projection now removes `minLength` together with `uniqueItems`, while
the immutable local schema and post-generation validation retain both
constraints.

## Files Changed

- `src/study_agent/adapters/model/openai_compatible.py`: remove `minLength`
  from the schema sent to the provider.
- `tests/unit/adapters/model/test_openai_provider_schema_projection.py`: cover
  the real hybrid-flashcard schema and verify that provider-only projection
  does not weaken local validation.

## Verification

- Red test before the fix:
  `pytest -q tests/unit/adapters/model/test_openai_provider_schema_projection.py::test_hybrid_flashcard_schema_sent_to_provider_excludes_local_validation_keywords`:
  failed because the provider schema still contained `minLength`.
- Adapter and failure-taxonomy tests: 41 passed.
- Flashcard and Wave A flow tests: 57 passed.
- `ruff check` on changed source and test files: passed.
- `mypy src/study_agent/adapters/model/openai_compatible.py`: passed.
- `git diff --check`: passed.

## Notes

- A live replay was not submitted automatically because it would create
  canonical flashcard proposals and incur a provider call. Restart the local
  server and retry the original request from the browser for the final live
  confirmation.
- Targeted mypy over the test file still exposes a pre-existing nested
  `JsonValue` indexing error in the older synthetic projection test at its
  local-schema assertion; the new test introduces no additional type error.
