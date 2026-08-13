# Log: A2 retirement and consent fixer

Date: 2026-08-12 20:35
Area: cardine-wave-a-recovery

## Summary

Retired sources are now excluded from tutor lesson planning, flashcard evidence,
active source fingerprints, and UI grounding availability while canonical
historical source resolution remains available. Provider consent failures remain
provider-zero and map to a truthful consent-required conversation/UI result.

## Files Changed

- `src/cardine/application/flashcard_proposals.py`: filter active non-retired source inputs while retaining raw canonical content.
- `src/cardine/cli/repository.py`: inject retirement view and enforce consent before capability execution.
- `src/cardine/hosts/runner.py`: carry the closed consent-required failure reason.
- `src/cardine/application/conversation_turn.py`: expose consent-required application error.
- `src/cardine/demo/ui_application.py`: filter grounding status and map consent-required UI errors.
- `src/cardine/integrations/study_agent/course_policy.py`: annotate the consent exception with its stable reason.
- `tests/unit/cardine/test_a2_regressions.py`: focused retirement, grounding, and consent regressions.

## Verification

- `python3 -m compileall -q src/cardine/...`: passed.
- `PYTHONPATH=src python3` focused smoke script: passed.
- `pytest`: unavailable in environment (`pytest` command/module missing).
- `ruff`: unavailable in environment (`ruff` command missing; `uv` cache access blocked).

## Notes

- Existing concurrent changes, including the Wave A plan update, were preserved.
