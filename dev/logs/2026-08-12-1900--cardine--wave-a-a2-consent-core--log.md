# Log: Wave A A2 provider-consent core

Date: 2026-08-12 19:00
Area: cardine-policy

## Summary

Added HUMAN-owned provider consent and revocation to the canonical per-course event stream. Provider-backed repository composition now wraps both grounded ask and tutor/flashcard model adapters in one fail-closed consent boundary.

## Files Changed

- `src/cardine/integrations/study_agent/course_policy.py`: strict consent codecs, reducer, projection view, command service, and model firewall.
- `src/cardine/cli/repository.py`: register policy events, compose consent service/view, and gate both model construction paths.
- `tests/unit/cardine/test_course_consent_policy.py`: replay, idempotency, authority, and provider-zero regressions.

## Verification

- `pytest -q tests/unit/cardine/test_course_consent_policy.py tests/unit/cli/test_repository.py`: 24 passed.
- `ruff check <changed Python paths>`: passed.
- `mypy --explicit-package-bases src/cardine/integrations/study_agent/course_policy.py`: passed.
- `python3 -m compileall -q <changed Python paths>`: passed.

## Notes

- No Harness source, public API, event schema, or storage implementation changed.
- Consent state retains request intent history so an old exact retry remains stable after later policy events.
- Terra review findings were resolved: absent courses cannot be poisoned, identical concurrent commands converge, and generate/stream/cancel all enforce current consent before provider delegation.
- CLI/browser controls and source retirement are intentionally deferred to the next A2 pass.
