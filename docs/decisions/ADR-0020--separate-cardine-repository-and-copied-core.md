# ADR-0020: Separate Cardine repository with a copied Harness core

Date: 2026-08-02
Status: Accepted

Cardine becomes a private autonomous repository whose `main` preserves the
history of `codex/cardine-ui-enterprise` at `707d852`, including its exact OSS
baseline at `study-agent-harness/main@e18f670`. Cardine copies the Harness core
instead of depending on or synchronizing with the public repository, because
the product already spans core and product contracts and a manual partial
extraction would risk silent omissions. The internal Python namespace remains
`study_agent` during migration to avoid an unrelated cross-cutting rename.

The public Harness retains its generic shell UI, reference browser, TUT-08,
developer CLI, and provider-neutral contracts. Only Cardine-specific refs,
worktrees, plans, assets, names, and product-only contracts are removed from
the Harness workspace. The Cardine checkout at `/private/tmp/cardine-ui-fix`
must be converted from a linked worktree into an independent clone without
changing the Harness `origin`.

The copied Apache-2.0 core and third-party assets retain their licenses and
attributions. Private Cardine product code has an explicit separate ownership
notice; the split does not rewrite public Harness history or imply future
automatic upstream synchronization.
