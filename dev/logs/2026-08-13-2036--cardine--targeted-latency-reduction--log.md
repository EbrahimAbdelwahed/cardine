# Log: targeted latency reduction

Date: 2026-08-13 20:36 CEST
Area: Cardine / runtime / retrieval / chat UI

## Summary

Applied five measured, local latency fixes without changing canonical events,
citations, study history, or public response schemas:

- `TutorSnapshotReader` reuses the SQLite event store's persisted projection
  only when it exactly matches the captured event high-water sequence; otherwise
  it retains the replay fallback.
- A settled tutor receipt is rendered before the advisory bootstrap-count
  refresh. Count refresh remains asynchronous for tutor turns.
- The decision model receives the latest 24 interleaved learner/tutor entries
  instead of the complete, ever-growing conversation. The canonical UI history
  remains complete.
- FTS search reuses the canonical catalog materialized by its integrity audit
  when resolving result chunks, avoiding a complete catalog scan per hit.
- Coherent GET routes no longer wait for the long mutation/model lock. Mutations
  remain serialized and the existing sequence-coherence retry remains active.

Initial-history pagination was not bundled: truncating the current session DTO
would make old messages inaccessible, while a truthful cursor API and browser
"load previous" flow is a separate public contract.

## Measured Impact

Measurements use the durable `../cardine-wave-a-live` repository, course
`course-wave-a`, session `session-live`:

- Tutor snapshot: approximately 10.8–12.3 s before, 0.227 s after.
- Decision conversation JSON: 78,176 bytes / 153 entries before bounding,
  12,193 bytes / 24 entries after bounding (84.4% fewer bytes).
- Two-hit FTS query: 3.647 s before, 2.093 s after. The remaining full-catalog
  integrity audit is intentionally preserved.
- Settled tutor output no longer waits for a second snapshot/bootstrap read
  before it is rendered.

## Files Changed

- `src/study_agent/tutor_snapshot/reader.py`: coherent persisted-projection fast path.
- `src/cardine/hosts/context.py`: bounded decision-only conversation tail.
- `src/cardine/cli/repository.py`: reuse the verified FTS catalog snapshot.
- `src/cardine/demo/ui_application.py`: lock-independent coherent GET reads.
- `src/cardine/demo/browser.js`: render tutor receipt before advisory counts.
- Focused contract, integration, repository, and browser tests for each seam.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q <40 focused tests>`: 40 passed.
- Broader host/demo/snapshot/retrieval/chat slice: 221 passed, 2 skipped,
  3 failed. Two failures are environment-bound (local socket bind forbidden;
  AnyDoc worker unavailable). The third, Tool Chips activity records not
  surviving an application restart, also fails unchanged `HEAD` in an isolated
  archive and is not introduced by this latency work.
- `.venv/bin/ruff check <changed Python files>`: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- `git diff --check`: passed.
- `.venv/bin/python -m mypy`: blocked before analysis by the repository's
  existing duplicate-module discovery (`hosts.contracts` and
  `cardine.hosts.contracts`). With `MYPYPATH=src --explicit-package-bases`, the
  changed snapshot/context modules report no errors; eight existing errors
  remain in Tool Chips/repository/UI code outside the changed lines.

## Notes

- No live data, event history, or derived index was mutated during measurement.
- No dependency, streaming protocol, new database, or background framework was
  introduced.
- The live server must be restarted or redeployed from this worktree before the
  runtime gains these fixes.
