# Log: Wave A A0 runtime seam recovery

Date: 2026-08-12 18:00
Area: cardine-integrations

## Summary

Recovered the minimal Cardine-owned repository lifecycle seam on the durable
CA-02 parent. CLI initialization, CLI repository commands, and the repository UI
now create one private legacy runtime adapter and use only its
`initialize_repository()` or context-managed `open_repository()` operations.
The adapter exposes no raw repository property, backend selector, global
registry, discovery, or dual execution path.

Foreign lifecycle exceptions are mapped to a small Cardine failure vocabulary
without rendering backend details. Process-control exceptions are not captured.
The CA-02 ownership audit remains a frozen namespace-transition audit; the new
`cardine.integrations` package is guarded by its own focused contract tests.

## Files Changed

- `src/cardine/integrations/study_agent/`: minimal config, adapter, error, and composition owners.
- `src/cardine/cli/commands.py`: init/open repository through the seam.
- `src/cardine/cli/main.py`: stable Cardine runtime error mapping.
- `src/cardine/demo/ui_application.py`: one runtime instance; `_open()` delegates exclusively.
- Focused contract and corrected post-CA02 browser-resource tests.

## Verification

- Focused runtime/CLI/repository/TUT08 suite: 76 passed, 2 sandbox socket skips.
- Ruff on changed source/tests: passed.
- Mypy on nine changed source/test files: passed.
- `scripts/audit_harness_ownership.py --check`: `OK (322 rows)`.
- `git diff --check`: passed.

## Notes

- This is the temporary sole legacy backend. It does not pivot to the released
  Harness package and does not add consent, PageIndex, PDF, or bulk decisions.
- Removal condition: the adoption pivot replaces this backend behind the same
  lifecycle seam; it must never introduce per-request backend selection.
