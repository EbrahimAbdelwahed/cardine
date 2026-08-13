# Cardine Repository Boundary

This context names the ownership boundary between the private Cardine product
and the public Study Agent Harness. It distinguishes the current copied-core
state from the approved installed-package target.

## Language

**Cardine repository**:
The private, autonomous product repository whose canonical local checkout is
`/Users/ebrahimabdelwahed/Desktop/Med/Lezioni/Audio_to_Sbobina/cardine-wave-a-recovery`.
_Avoid_: Harness product branch, OSS Cardine branch

**Harness OSS repository**:
The public `study-agent-harness` repository that owns the reusable runtime,
developer CLI, generic shell UI, reference browser, and TUT-08.
_Avoid_: Cardine upstream, shared product repository

**Copied Harness core**:
The source snapshot inherited by Cardine and currently evolved independently
inside `src/study_agent`. This remains the operational implementation until the
package-adoption gates pass.
_Avoid_: live dependency, synchronized submodule, shared checkout

**Installed Harness target**:
The exact, versioned `study-agent-harness` distribution consumed through a
Cardine-owned anti-corruption layer. Harness `0.3.0` is available as the local
adoption artifact, but Cardine does not yet declare or use it.
_Avoid_: sibling-checkout import, `PYTHONPATH` integration, partial source copy

**Cardine product shell**:
Cardine's private UI, application composition, authentication, settings,
deployment, and product-only presentation behavior.
_Avoid_: generic shell, reference browser

**Generic shell UI**:
The provider-neutral shell UI, reference browser, and TUT-08 retained and
maintained by the Harness OSS repository.
_Avoid_: Cardine shell

**Canonical Cardine checkout**:
The independent Git clone at
`/Users/ebrahimabdelwahed/Desktop/Med/Lezioni/Audio_to_Sbobina/cardine-wave-a-recovery`;
it does not share a Git directory or remotes with the Harness repository.
_Avoid_: linked worktree

## Current versus target

- **Current:** Cardine contains its own `src/study_agent` tree and has no
  `study-agent-harness` dependency in `pyproject.toml`.
- **Target:** Cardine product code consumes the released Harness package only
  through `cardine.integrations.study_agent`, while Cardine DTOs remain the UI
  and API boundary.
- **CA-08 prerequisite:** prove behavioral parity against the installed
  distribution, including replay, identity, citations, session recovery,
  artifacts, assessments, recall, and fail-closed behavior.
- **CA-10 prerequisite:** remove the copied core only after parity and import
  gates pass. Until then, `src/study_agent` is current truth and must not be
  deleted or described as already migrated.
