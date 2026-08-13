# Log: Wave A A2 policy surface

Date: 2026-08-12 20:30
Area: cardine-policy

## Summary

Completed the private-product controls for provider consent and source lifetime. Consent and source retirement/restoration replay on the canonical course stream; active retrieval and material views exclude retired sources while raw canonical history remains resolvable.

## Files Changed

- `src/cardine/integrations/study_agent/course_policy.py`: strict source lifetime codecs, projection, view, and HUMAN command service.
- `src/cardine/cli/*`: consent and source-lifetime commands, truthful retry/index failure mapping, and active source listing.
- `src/cardine/demo/ui_application.py`, `browser.py`, `browser.js`: status/mutation routes, visible provider-consent control, active material view, and private endpoint classification.
- Focused policy, repository, CLI, and TUT08 tests: restart/history/provider-zero coverage and explicit provider consent in provider-backed fixtures.
- Ownership audit/overlay: bind changed CA-02 paths while limiting the frozen audit to its reviewed universe.

## Verification

- Focused policy/repository/CLI/TUT08/Luna suite: 44 passed.
- Source-lifetime restart/history focused suite: passed.
- Ruff on changed Python paths: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- Mypy on policy source: passed.
- Ownership audit: OK, 322 rows.
- `git diff --check`: passed.

## Notes

- Two pre-existing flashcard proposal integration failures remain and are classified separately; provider consent itself is now explicit in those fixtures.
- Socket-bound browser tests remain unavailable in the sandbox. Static browser syntax and application-surface tests pass.
