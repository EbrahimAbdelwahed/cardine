# Handoff: collapsed sources

Date: 2026-08-13 15:31 CEST
Area: Cardine / chat presentation

## Current State

Assistant sources render as one collapsed `N fonti verificate` control by
default. Expanding it reveals the existing compact locator chips. The runtime
repository remains `../cardine-wave-a-live`, course `course-wave-a`, session
`session-live`.

## Completed

- Removed the default multi-row source footprint.
- Preserved accessible native disclosure behavior and visible caret state.
- Correctly folded backend `Altre N citazioni verificate.` metadata into the
  displayed total.
- Prevented expanded chip content from widening the answer column.

## Remaining

- `Visualizza fonti` and its richer source drawer remain deferred.
- The expanded inline view is still metadata-dense and repeats the common
  source-title prefix; address that in the source drawer rather than adding
  more complexity to this compact disclosure.
- Fix 5 remains optional: message copy/edit and any history-scroll defect.

## Important Context

- Do not replace the native disclosure with local-only state unless a richer
  interaction requires it.
- Do not render omission metadata as if it were a source locator.
- Recovery bundles in the repository root remain untracked.

## Verification

- UI tests: 21 passed.
- Browser contract remainder: 4 passed, 6 socket skips.
- Visual closed/open states verified with no measured horizontal overflow.
- Node syntax, Ruff, and diff checks: passed.
