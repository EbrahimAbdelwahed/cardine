# Log: TUT-08C2 materials, artifacts, and context

Date: 2026-07-30 15:38
Area: adaptive tutor / Cardine product shell

## Summary

Completed the private Cardine composition for canonical materials, artifact
proposal/decision reads and writes, and intrinsic study-context conflict
resolution. Existing `ProjectionArtifactView`, `ArtifactService`,
`ProjectionStudyContextView`, and `StudyContextService` remain the only state
owners. Artifact rows are selected by batch session, and browser decisions map
explicitly from `accepted`/`rejected` to domain accept/reject. Context candidates
carry opaque `StatementId` values; display values are never used for mutation.

An exact-retry defect was found in the UI decision adapter: after an initial
accept, recomputing the current accepted predecessor changed the service
fingerprint. The adapter now recovers the predecessor from the persisted
command fingerprint before retrying, so no duplicate event is emitted.
The follow-up revision path now considers every canonical historical revision
ID in the artifact lineage, including superseded predecessors, while never
accepting IDs outside that lineage.

## Files Changed

- `src/study_agent/demo/ui_application.py`: typed bounded DTO composition,
  truthful empty material state, artifact selected-session mapping, and exact
  retry predecessor recovery.
- `src/study_agent/demo/browser.js`: only proposed revisions expose decisions;
  conflict choices require an opaque `statement_id` and submit only
  `selected_statement_id`.
- `tests/integration/demo/TUT08/test_repository_materials_artifacts_context.py`:
  repository-backed material/artifact/context lifecycle coverage.
- `specs/adaptive-tutor/beads/TUT-08C2-materials-artifacts-context.md`: marked
  C2 acceptance criteria complete.

## Verification

- `PYTHONPATH=src:. pytest -q tests/integration/demo/TUT08/test_repository_materials_artifacts_context.py tests/integration/demo/TUT08/test_repository_backed_chat.py tests/unit/demo/test_ui_application.py tests/unit/demo/test_browser_assets.py tests/e2e/test_cardine_browser_contract.py tests/unit/artifacts tests/contract/study_context tests/integration/test_artifact_repository_replay.py tests/integration/test_progressive_study_context.py`: 197 passed, 6 skipped because local sockets are unavailable in the sandbox.
- `PYTHONPATH=src:. pytest -q tests/unit/cli/test_repository.py tests/integration/test_tutor_snapshot_replay.py tests/integration/test_tutor_host_runner.py`: 62 passed.
- `python -m ruff check src/study_agent/cli/repository.py src/study_agent/demo/ui_application.py tests/integration/demo/TUT08/test_repository_materials_artifacts_context.py`: passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `git diff --check`: passed.
- Superseded-predecessor regression: v1 accepted, v2 proposed/accepted,
  restart + exact retry byte-identical, stale different request commits no
  event.

## Notes

- No generation owner was invented; generated-batch capability remains
  unavailable until a verified production owner is composed.
- Public-demo mode remains stateless and mutation-free.
- Socket-dependent HTTP tests remain skipped only due sandbox policy.
