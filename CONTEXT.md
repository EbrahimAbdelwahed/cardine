# Cardine Repository Boundary

This context names the ownership boundary between the private Cardine product
and the public Study Agent Harness from which its runtime core is copied.

## Language

**Cardine repository**:
The private, autonomous product repository whose canonical local checkout is
`/private/tmp/cardine-ui-fix`.
_Avoid_: Harness product branch, OSS Cardine branch

**Harness OSS repository**:
The public `study-agent-harness` repository that owns the reusable runtime,
developer CLI, generic shell UI, reference browser, and TUT-08.
_Avoid_: Cardine upstream, shared product repository

**Copied Harness core**:
The source snapshot inherited by Cardine at upstream commit `e18f670`, owned
and evolved independently inside the Cardine repository after the split.
_Avoid_: live dependency, synchronized submodule, shared checkout

**Cardine product shell**:
Cardine's private UI, application composition, authentication, settings,
deployment, and product-only presentation behavior.
_Avoid_: generic shell, reference browser

**Generic shell UI**:
The provider-neutral shell UI, reference browser, and TUT-08 retained and
maintained by the Harness OSS repository.
_Avoid_: Cardine shell

**Canonical Cardine checkout**:
The independent Git clone at `/private/tmp/cardine-ui-fix` after migration; it
must no longer share a Git directory or remotes with the Harness repository.
_Avoid_: linked worktree
