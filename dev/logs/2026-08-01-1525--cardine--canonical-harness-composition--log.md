# Log: Cardine canonical Harness composition

Date: 2026-08-01 15:25
Area: cardine

## Summary

Implemented the repository-backed browser path and a private canonical
`HarnessToolSurface` shared by UI and tutor-host composition. The released
seven-tool public registry remains unchanged. The tutor now receives the
surface manifests in redacted context and can issue `invoke_tool`; the runner
derives service authority server-side and records a real presentation after a
successful operation.

## Files Changed

- `src/study_agent/application/tool_surface.py`: closed typed product tools.
- `src/study_agent/cli/repository.py`: repository composition and tutor gateway.
- `src/study_agent/hosts/{contracts,context,runner}.py`: tutor tool decision lane.
- `src/study_agent/demo/{browser,ui_application,browser.js}.py`: repository-only browser and UI adapter migration.
- `tests/...`: focused surface, tutor, and browser regression coverage.

## Verification

- `pytest -q tests/integration/demo/TUT08/test_repository_backed_chat.py tests/unit/application/test_harness_tool_surface.py tests/unit/hosts/test_tutor_host_contracts.py tests/unit/adapters/model/test_tutor_decision.py tests/e2e/test_cardine_repository_browser_journey.py`: 52 passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `git diff --check`: passed.

## Notes

- The browser runtime no longer constructs `DemoUiApplication`, exposes legacy
  `/api/state` or `/api/entry`, or accepts `--public-demo`.
- Legacy offline demo source/tests/docs still exist elsewhere in the dirty
  checkout and require a separate final cleanup/migration before claiming
  literal repository-wide demo deletion.
