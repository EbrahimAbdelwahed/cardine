# Plan: CA-02 Cardine namespace recovery

Date: 2026-08-12 16:15
Area: Cardine namespace transition

## Goal

Reconstruct the reviewed CA-02 namespace transition in a durable checkout so
Cardine-owned runtime and host paths use the `cardine` namespace while copied
Harness core remains under `study_agent`, with the existing behavior preserved.

## Scope

- In scope: the 86 Cardine-owned targets, the 33 mechanically forced copied-core
  import points, the reviewed transition overlay/seam and its tests, the five
  Cardine entry points/package data, the two authorized example imports, and
  `scripts/audit_harness_ownership.py` plus `scripts/verify_cardine_wheel.py`.
- Out of scope: Wave A/PageIndex behavior, Harness behavior/schema changes,
  dependencies/network, unrelated refactors, and user files outside this
  namespace-transition contract.

## Invariants

- Exactly 86 Cardine-owned targets; exactly 33 copied-core import points.
- The transition overlay remains 119 targets, with 43 grandfathered rows.
- The transition seam exports exactly seven identity-preserving names and has
  exactly two consumers.
- The distribution exposes exactly five `cardine*` commands targeting
  `cardine.*`; no Cardine-owned `study-agent*` aliases are published.
- Moved old paths are absent; copied-core imports remain under `study_agent`.

## Approach

1. Reproduce import collection and inspect the reviewed ownership evidence.
2. Restore the frozen Cardine-owned source set and mechanically retarget only
   the copied-core consumers and authorized test/resource literals.
3. Harden the ownership and wheel verifiers around the transition invariants.
4. Run focused architecture/import/collision/operator tests, behavior tests,
   ownership audit, Ruff, mypy, wheel/sdist verification, and diff checks.

## Verification

- `.venv/bin/python -m pytest tests/architecture tests/contract/cli tests/integration/test_operator_skill_release.py tests/parity`
- `.venv/bin/python -m pytest tests/unit tests/integration tests/e2e`
- `.venv/bin/python scripts/audit_harness_ownership.py --check`
- `.venv/bin/ruff check .`
- `.venv/bin/mypy`
- `.venv/bin/python -m build`
- `.venv/bin/python scripts/verify_cardine_wheel.py <wheel> <sdist>`
- `git diff --check`
