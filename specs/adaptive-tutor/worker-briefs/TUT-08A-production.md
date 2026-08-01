# Worker Brief: TUT-08A

## Assignment

Implement `TUT-08A` from
`specs/adaptive-tutor/cardine-full-product.md`.

## Read First

- `specs/adaptive-tutor/cardine-full-product.md`
- `specs/adaptive-tutor/beads/TUT-08A-durable-adaptive-conversation.md`
- `specs/adaptive-tutor/worker-profiles/cardine-product-slice.md`
- `docs/decisions/ADR-0015--persist-validated-host-presentations.md`
- `src/study_agent/hosts/runner.py`
- `src/study_agent/sessions/turn_service.py`
- `src/study_agent/cli/repository.py`

## Scope

You may change:

- `src/study_agent/application/conversation_turn.py`
- `src/study_agent/application/__init__.py`
- `src/study_agent/adapters/sqlite/tutor_continuations.py`
- `src/study_agent/adapters/sqlite/__init__.py`
- narrowly necessary host/session contracts fixed by ADR-0015
- `src/study_agent/cli/repository.py`
- focused unit/integration/architecture tests for this bead

Do not change:

- `src/study_agent/demo/**`
- provider/model adapters or prompts
- artifacts, assessments, recall, study-context, sources, or plan
- public snapshot/event semantics outside ADR-0015

## Requirements

- Implement every acceptance criterion in TUT-08A.
- Preserve old repository replay and grounding-only composition.
- Do not introduce a dependency.
- Use exact retry before stale checks and CAS-bound canonical commits.
- Store opaque continuation bytes separately from canonical presentation
  records; expose only safe descriptors.

## Verification

Run:

```bash
PYTHONPATH=src:. python -m pytest -q tests/unit/application tests/integration/test_conversation_turn_application.py tests/integration/test_tutor_host_runner.py tests/unit/sessions
PYTHONPATH=src:. python -m ruff check src/study_agent/application src/study_agent/adapters/sqlite src/study_agent/cli/repository.py tests/unit/application tests/integration/test_conversation_turn_application.py
git diff --check
```

## Report Back

Return:

- files changed;
- behavior implemented;
- verification results;
- unresolved questions;
- follow-up beads needed.
