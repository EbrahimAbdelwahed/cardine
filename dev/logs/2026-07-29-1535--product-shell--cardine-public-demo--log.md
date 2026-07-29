# Log: Cardine Referto public-demo milestone

Date: 2026-07-29 15:35
Area: product-shell

## Summary

Connected the selected Cardine Referto mockup to the Study Agent Harness
offline product-shell seam through a versioned, transport-independent API.
Every visible navigation area now has a working interface, a real projection,
or an explicit unavailable state. Added a hardened public-demo boundary and
target-neutral container packaging. Added the accepted durable tutor
presentation contract required for the later repository-backed conversation
flow.

## Files Changed

- `src/study_agent/demo/browser.html`: accessible Cardine shell.
- `src/study_agent/demo/browser.css`: Referto visual system and responsive
  layouts.
- `src/study_agent/demo/browser.js`: navigation, API reads, primary study
  command, loading/error/conflict/unavailable states.
- `src/study_agent/demo/ui_application.py`: stateless sanitized v1 demo API.
- `src/study_agent/demo/browser.py`: packaged assets, API transport, public
  bind gate, legacy-route isolation, and security headers.
- `Dockerfile`, `.dockerignore`: non-root public-demo image.
- `src/study_agent/domain/`, `hosts/`, `sessions/`, and `ports/`: validated
  tutor presentation receipt and session-owned persistence contract.
- `dev/plans/assets/referto-ui/`: source, final implementation, comparison,
  core-flow screenshot, and design QA record.

## Verification

- `python -m pytest -q`: 1,916 passed, 12 skipped.
- `python -m ruff check src tests`: passed.
- `/private/tmp/study-agent-pdf-check/bin/mypy src/study_agent`: passed, 264
  source files.
- `node --check src/study_agent/demo/browser.js`: passed.
- `python -m pytest -q tests/integration/demo/TUT08/test_browser_surface.py`:
  2 passed, including public-mode route isolation and security headers.
- `uv build --wheel`: passed.
- Wheel contents: `browser.html`, `browser.css`, `browser.js`, `browser.py`,
  and `ui_application.py` present.
- Clean temporary virtualenv install: CLI help and packaged HTML/CSS/JS smoke
  passed.
- Independent visual gate: PASS, no remaining P1/P2 issues.
- Independent semantic review: PASS after binding replayed presentation fields
  back to the derived host receipt fingerprint.
- Security review: no remaining P0/P1 findings after hardening; pathological
  JSON is mapped to a bounded `400`.

## Notes

- Docker is not installed in this environment, so the image could not be
  started locally.
- Mobile responsive code is covered by the asset contract, but the in-app
  browser did not honor a viewport override; no mobile screenshot is claimed.
- Public deployment awaits the user's hosting target.
- Repository-backed continuation storage and application orchestration remain
  the next implementation pass.
