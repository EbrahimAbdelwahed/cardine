# Log: Minimum flashcard action recovery

Date: 2026-08-14 19:46 CEST
Area: cardine tutor routing

## Summary

Recovered the four explicit Italian flashcard-generation requests observed in the
live Cardine session. The host now converts Luna's conversational promise into the
existing flashcard capability start without changing the tutor decision contract.

The matcher recognizes the observed `generi`, `fai`, and `falshcards` forms, applies
negation per clause across every action match, preserves genuine negative requests,
and rejects conditional/meta questions. Italian language detection covers the new
action forms.

The repository-backed integration test proves that the recovered request produces
reviewable proposed flashcards scoped to Lezione 1 and does not persist the model's
promise as the terminal tutor response.

## Files Changed

- `src/cardine/hosts/flashcard_routing.py`: recover explicit live flashcard actions with clause-local safety guards.
- `tests/unit/hosts/test_flashcard_routing_natural_language.py`: cover the exact four live messages, language, negation, mixed clauses, and meta questions.
- `tests/integration/demo/TUT08/test_flashcard_proposals.py`: prove repository-backed proposal generation remains scoped to Lezione 1.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/hosts/test_flashcard_routing_natural_language.py tests/integration/demo/TUT08/test_flashcard_proposals.py`: 25 passed in 8.69s.
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/adapters/model/test_tutor_decision.py tests/integration/test_tutor_host_runner.py tests/integration/test_tutor_completion_handoff.py`: 73 passed in 0.26s.
- `.venv/bin/ruff check src/cardine/hosts/flashcard_routing.py tests/unit/hosts/test_flashcard_routing_natural_language.py tests/integration/demo/TUT08/test_flashcard_proposals.py`: passed.
- `git diff --check -- src/cardine/hosts/flashcard_routing.py tests/unit/hosts/test_flashcard_routing_natural_language.py tests/integration/demo/TUT08/test_flashcard_proposals.py`: passed.
- Independent semantic review found three routing edge cases; all were fixed and covered before restart.
- Restarted the supervised live server; PID changed from `2986` to `6881`.
- `curl -sS --max-time 3 -i http://127.0.0.1:8765/health`: HTTP 200, runtime `cardine-local-source-grounding-v2`, mode `setup`.

## Notes

- Local-owner password and the runtime OpenAI credential are intentionally held in memory and must be configured again after restart.
- A provider-backed live replay was not run because the restart cleared the runtime credential; the repository-backed end-to-end fixture verifies the recovered execution path without handling user secrets.
