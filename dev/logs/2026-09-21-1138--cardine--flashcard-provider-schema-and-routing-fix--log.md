# Log: Flashcard provider schema and routing fix

Date: 2026-09-21 11:38 CEST
Area: Cardine / flashcard generation

## Summary

Fixed the two independent failures reproduced by the latest live turns.

- The GPT-5.6 Luna preset now projects provider-unsupported `uniqueItems`
  keywords out of its copied strict Structured Outputs schema. The canonical
  capability schema is unchanged, so the local integrity validator continues
  to enforce uniqueness after generation.
- The closed Italian flashcard router now recognizes `generiamo` as an explicit
  generation action and classifies it as Italian.

The generic OpenAI-compatible adapter retains its existing schema translation;
the provider projection is isolated to the fixed Luna preset.

## Files Changed

- `src/study_agent/adapters/model/openai_compatible.py`: add a narrow schema
  translation hook used by provider presets.
- `src/cardine/adapters/model/openai_luna.py`: recursively remove only the
  unsupported `uniqueItems` keyword from the provider-bound schema copy.
- `src/cardine/hosts/flashcard_routing.py`: recognize `generiamo`.
- `tests/unit/adapters/model/test_openai_luna.py`: exercise the real hybrid
  flashcard schema and prove the local contract remains unchanged.
- `tests/unit/hosts/test_flashcard_routing_natural_language.py`: pin the exact
  live learner phrase.

## Verification

- Red phase: the two focused regressions failed with `uniqueItems` still in the
  provider payload and an `AssistantMessageDecision` for `generiamo`.
- Focused adapter and router suite: `53 passed`.
- Broader flashcard and artifact suite: `194 passed`.
- Focused mypy with explicit package bases: no issues in 5 files.
- Ruff: passed.
- `git diff --check`: passed.

## Operational Note

The currently running local preview must be restarted to load this code. Its
process-local API credential will need to be entered again after restart.
