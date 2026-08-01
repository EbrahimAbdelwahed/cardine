# Worker Brief: TUT-08B

## Assignment

Implement `TUT-08B` from
`specs/adaptive-tutor/cardine-full-product.md`.

## Read First

- `specs/adaptive-tutor/cardine-full-product.md`
- `specs/adaptive-tutor/beads/TUT-08B-deepseek-live-tutor-chat.md`
- `specs/adaptive-tutor/worker-profiles/cardine-product-slice.md`
- `docs/decisions/ADR-0004--adaptive-tutor-host-boundary.md`
- `docs/decisions/ADR-0015--persist-validated-host-presentations.md`
- completed TUT-08A log/diff
- `src/study_agent/hosts/runner.py`
- `src/study_agent/adapters/model/openai_compatible.py`
- `src/study_agent/demo/ui_application.py`

## Scope

You may change:

- one provider-neutral tutor-decision adapter under
  `src/study_agent/adapters/model/`
- versioned prompt/schema files required by that adapter
- `src/study_agent/cli/repository.py`
- `src/study_agent/demo/ui_application.py`
- narrowly necessary `src/study_agent/demo/browser.py` and `browser.js`
- focused unit/integration/eval/Cardine tests for this bead

Do not change:

- artifact, assessment, recall, study-context, source, or readiness owners
- existing capability/event schemas
- `TutorSnapshotV1`
- public-demo persistence behavior
- hosting/deployment configuration

## Requirements

- Implement every TUT-08B acceptance criterion.
- Use DeepSeek through the configured environment-owned adapter.
- Validate a closed `TutorDecision` locally; reject unknown fields/kinds.
- Use the TUT-08A application instead of direct grounded ask for repository
  chat.
- Keep grounded ask available through the host's existing tool/capability
  boundary.
- Preserve same-origin, idempotency, sequence fences, restart, and redaction.

## Verification

Run:

```bash
PYTHONPATH=src:. python -m pytest -q <focused adapter/conversation/demo/eval tests>
PYTHONPATH=src:. python -m ruff check <changed Python/tests>
git diff --check
```

Run one opt-in live DeepSeek browser smoke only after recorded fixtures pass.

## Report Back

Return:

- files changed;
- behavior implemented;
- recorded and live verification results;
- secrets/redaction checks;
- unresolved questions;
- follow-up beads needed.
