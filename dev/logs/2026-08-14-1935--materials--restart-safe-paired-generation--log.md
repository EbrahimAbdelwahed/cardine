# Log: restart-safe paired material generation

Date: 2026-08-14 19:35 CEST
Area: Cardine / materials

## Summary

Completed authorized Slice 02. One exact current text/Markdown transcript now
drives a Luna-only, CAS-checkpointed complete/study workflow. Generated Markdown
is stored as immutable blobs and both outputs enter the existing artifact ledger
in one atomic proposal batch. No approval, publication, indexing, browser or chat
surface from later slices was implemented.

The production repository composition validates the active session, exact
current non-retired original/extracted revision, canonical chunk commitments,
provider consent, configured `openai-gpt-5.6-luna` adapter and
`OPENAI_API_KEY` reference before adapter construction and before every stage.
The proposal step binds its event sequence before the final preflight so a
concurrent retirement or supersession conflicts and retries safely.

## Files Changed

- `src/cardine/materials/generation_contracts.py`: strict pin, request, CAS
  state, claim, receipt and limitation contracts with first-version bounds.
- `src/cardine/materials/planning.py`: exact deterministic unit manifest and
  strict gap-free boundary validation.
- `src/cardine/materials/prompts.py`: versioned strict structured Luna prompts.
- `src/cardine/materials/validation.py`: bounded Markdown, structure, marker,
  ancestry and limitation validation.
- `src/cardine/materials/generation_service.py`: restart-safe paired stage
  coordinator and atomic proposal reconciliation.
- `src/cardine/materials/verified_batch.py`: exact two-output verified batch
  recovery with canonical commitments and receipt-derived provenance.
- `src/cardine/materials/__init__.py`: narrow public exports.
- `src/cardine/cli/repository.py`: canonical pin and Luna-only production
  composition.
- `tests/unit/materials/`: strict codec and identity coverage.
- `tests/integration/test_material_generation_workflow.py`: structured output,
  retry, stale, receipt, fresh-service multi-segment recovery and provider
  attempt-bound coverage.
- `tests/integration/test_material_generation_repository_composition.py`:
  provider-zero preflight, real repository/artifact atomicity and restart.
- `specs/material-generation-workflow/`: Slice 02 status and evidence.

## Verification

- `.venv/bin/ruff check src/cardine/materials src/cardine/cli/repository.py tests/unit/materials tests/integration/test_material_generation_workflow.py tests/integration/test_material_generation_repository_composition.py`: passed.
- `.venv/bin/mypy --strict src/cardine/materials`: passed, 9 files.
- Strict mypy on both new integration modules with `MYPYPATH=src --explicit-package-bases`: passed.
- Final focused material, artifact materializer, package and existing
  worker-recovery pytest gate: 30 passed.
- `tests/unit/test_cardine_package_contract.py`: 2 passed.
- `.venv/bin/python -m build --wheel --no-isolation --outdir /tmp/cardine-material-wheel`: built `cardine-0.2.0-py3-none-any.whl`; material Python modules are included.
- `git diff --check`: passed.
- Independent semantic and security re-reviews: no residual blockers.
- Full `.venv/bin/python -m pytest -q`: 2380 passed, 31 skipped, 27 failed. The failures are pre-existing/unrelated dirty-worktree and sandbox issues: stale CLI discovery goldens, socket-denied browser tests, AnyDoc worker containment, existing UI tooltip/host snapshot changes, and parity/golden drift. No Slice 02 focused test failed.

## Notes

- `list(...)` remains deliberately deferred because the current namespaced run
  store is key-addressed and has no truthful durable enumeration contract.
- Exact-once cannot be promised for a crash after a provider response but
  before receipt CAS; completed receipts prevent later duplicate calls, and
  blob writes are content-idempotent.
- First-version bounds: 512,000 transcript characters, 2 MiB source blobs, 256
  units, 16 segments, 1.5M request characters and 24 provider attempts.
- Slices 03 and 04 remain unauthorized.
