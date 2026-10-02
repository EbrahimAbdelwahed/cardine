# Log: Live flashcard structured-schema rejection

Date: 2026-08-15 00:55
Area: cardine

## Summary

Diagnosed the fresh live turn at course sequence 196 after the page-aware locator fix. The turn selected `propose_flashcards`, prepared canonical evidence successfully, and started three Luna generation jobs. All three failed immediately at `generate_hybrid_flashcards` with `model_failure_reason=protocol_error`; Cardine then published the misleading generic insufficient-evidence fallback at sequence 197.

The fixed Luna Chat Completions adapter forwards the capability's strict schema without a provider projection. `HYBRID_FLASHCARDS_MODEL_SCHEMA` contains `uniqueItems: true`, and the exact outgoing body preserves it. OpenAI Structured Outputs documents only `minItems` and `maxItems` as supported array constraints and states that unsupported schemas with `strict: true` return an error. The failure is therefore at the model transport/schema boundary, before Luna generates any card content.

## Files Changed

- `dev/logs/2026-08-15-0055--cardine--live-flashcard-structured-schema-rejection--log.md`: diagnosis only; no production change.

## Verification

- Live SQLite replay assertion over capability rows 58, 60, and 62: three of three model steps failed with `protocol_error`.
- Live lesson-worker row 56: all three bundles reached `child_terminal` with child task and wrapper bytes present, proving locator/evidence preparation succeeded.
- Adapter body inspection with the real hybrid schema: outgoing `candidate_keys` schema contains `{'maxItems': 24, 'uniqueItems': True, ...}`.
- Official OpenAI Structured Outputs documentation: strict schemas support only a subset; supported array properties list `minItems` and `maxItems`, and unsupported strict schemas return an error.

## Notes

- Historical diagnostics intentionally discard provider response bodies and retain only the safe error category, so the exact provider error code cannot be recovered from this completed turn.
- A minimal fix should project the provider schema by removing unsupported validation-only keywords while retaining the full local post-generation validator, or move this adapter to the already-used Responses transport with the same provider-safe projection. The public capability schema and local integrity validation should remain unchanged.
- Error presentation should separately stop mapping provider protocol failures to `insufficient_evidence`.
