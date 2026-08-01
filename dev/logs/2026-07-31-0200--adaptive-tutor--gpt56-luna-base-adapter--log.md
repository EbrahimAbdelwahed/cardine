# Log: GPT-5.6 Luna base adapter

Date: 2026-07-31 02:00
Area: adaptive tutor

## Summary

Implemented the fixed `gpt-5.6-luna` Cardine model adapter over the existing
provider-neutral model port. The preset pins OpenAI Chat Completions, Luna,
`reasoning_effort: none`, strict structured output, and
`max_completion_tokens`. It resolves only `OPENAI_API_KEY`, owns its
provenance identity, rejects non-boolean/non-strict schema requests, and does
not follow authenticated redirects.

The generic configurable endpoint adapter remains available only through an
explicit trusted-host opt-in. This is an intentional compatibility boundary
recorded in ADR-0018.

## Files Changed

- `src/study_agent/adapters/model/openai_luna.py`: fixed Luna preset.
- `src/study_agent/adapters/model/openai_compatible.py`: bounded reasoning/token
  configuration, implementation-owned provenance, and redirect denial.
- `src/study_agent/cli/repository.py`: Luna registry composition and exact
  credential/config validation.
- `src/study_agent/ports/model.py`: boolean strictness validation.
- `src/study_agent/grounding/draft.py`: provider-valid nullable note schema.
- `src/study_agent/skills/builtin/grounded_answer.py`: matching public output
  contract.
- `src/study_agent/playbooks/engine.py`: JSON Schema union-type validation.
- `tests/`: focused contract, registry, schema, and opt-in live coverage.
- `README.md`, `docs/decisions/ADR-0018--gpt56-luna-base-model.md`, and
  `specs/adaptive-tutor/beads/TUT-08H-gpt56-luna-base-adapter.md`: activation
  and product contract.

## Verification

- `python -m pytest -q <focused Luna/model/repository/playbook tests>`:
  107 passed, 1 live smoke skipped because it is opt-in.
- `python -m ruff check <changed Python files>`: passed.
- `python -m pytest -q` with local socket permission: 2057 passed, 13 skipped,
  1 expected golden-fingerprint mismatch.
- `python -m pip wheel . --no-deps --no-build-isolation`: passed.
- Clean virtualenv wheel install/import: passed.
- Wheel SHA-256:
  `1139ba4ecaf72b23a288f07757fa7fcad8e872fd1a3dda8b2a08964fff3848f4`.
- Independent semantic re-review: no remaining P0/P1/P2 findings.
- Independent security re-review: no remaining P0/P1/P2 findings.

## Notes

- The grounded-answer schema correction intentionally changes the canonical
  playbook fingerprint from
  `5c11a23dd4a553e18e4dd42a3df4a4e356a7aa5d505408f97d72e2e0525268a0`
  to
  `558fc944ed6c36f4fc951db7e5119d288ef3f466f368c74d4133998636041dae`.
  Updating that golden remains pending explicit owner approval.
- The live provider smoke remains skipped because `OPENAI_API_KEY` is not
  present. The currently running local Cardine process was not restarted or
  migrated, so its existing DeepSeek-backed session remains available.
