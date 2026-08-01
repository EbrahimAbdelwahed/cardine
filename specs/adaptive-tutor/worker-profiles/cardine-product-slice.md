# Worker Profile: cardine-product-slice

## Reuse Trigger

Use this worker when one existing Study Agent canonical owner must be exposed as
an independently verifiable Cardine repository-backed UI flow.

## Mandate

Deliver one thin end-to-end Cardine slice from application composition through
the existing versioned API and dependency-free UI, while preserving canonical
ownership, retries, sequence fencing, and public-demo isolation.

## Scope

In scope:

- Compose existing repository views/services behind a transport-independent
  Cardine application boundary.
- Add bounded JSON DTOs and command validation.
- Connect the matching existing UI control and states.
- Add real-repository integration and browser-contract tests.

Out of scope:

- Redesigning domain ownership, event schemas, provider policy, or the selected
  Claude-inspired visual system.
- Direct provider, SQLite, event-store, or filesystem calls from UI code.
- Adding dependencies without orchestrator approval.
- Hosting, authentication, or tenancy.

## Required Context

Read first:

- `AGENTS.md`
- `specs/adaptive-tutor/cardine-full-product.md`
- the assigned TUT-08 child bead
- `docs/decisions/ADR-0015--persist-validated-host-presentations.md`
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js`
- the assigned canonical service/view package

Current-doc research:

- Not needed for dependency-free application/UI slices. Provider- or
  scheduler-specific beads must read their official adapter documentation.

## Allowed Files

May edit:

- `src/study_agent/demo/**`
- the assigned application/composition package named by the bead
- focused tests named by the bead
- assigned spec/bead/brief/log files

May inspect:

- all `src/study_agent/**`, `tests/**`, `docs/decisions/**`, and
  `specs/adaptive-tutor/**`

Do not edit:

- unrelated domain packages
- existing event schemas outside the assigned owner
- public demo fixtures unless the bead explicitly requires parity
- deployment/hosting configuration

## Forbidden Decisions

Stop and report back before deciding:

- a new canonical owner or event schema;
- a new provider, dependency, or credential path;
- any weakening of idempotency, sequence fencing, authority, provenance, or
  source grounding;
- any invented mastery, coverage, retention, time, or plan metric;
- a visual redesign unrelated to the assigned flow.

## Quality Gates

- Existing owner remains the only canonical writer.
- Exact retry resolves before stale-sequence rejection.
- A new stale command performs no model/host work.
- DTOs are bounded, redacted, schema-versioned, and course/session scoped.
- Empty/unavailable/error states are distinct and accessible.
- Public demo cannot reach repository mutations.
- Focused unit, real-repository integration, and browser-contract tests pass.

## Verification

Run:

```bash
PYTHONPATH=src:. python -m pytest -q <focused tests>
PYTHONPATH=src:. python -m ruff check <changed Python and tests>
git diff --check
```

If verification cannot run, report the reason and the narrowest manual check
completed.

## Report Format

Return:

- files changed;
- behavior implemented;
- verification results;
- profile constraints followed;
- unresolved questions;
- recommended next worker or review step.
