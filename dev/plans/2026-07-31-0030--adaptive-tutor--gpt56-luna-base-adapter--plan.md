# Plan: GPT-5.6 Luna base adapter

Date: 2026-07-31 00:30
Area: adaptive-tutor

## Goal

Replace Cardine's active DeepSeek model role with a dedicated, auditable
`gpt-5.6-luna` adapter while preserving the provider-neutral `ModelPort`,
closed tutor-decision schema, canonical persistence, and browser behavior.

## Scope

- In scope:
  - A fixed GPT-5.6 Luna adapter preset over the existing bounded HTTP model
    transport.
  - Explicit Chat Completions `reasoning_effort: none`, preserving the current
    non-reasoning chat baseline.
  - Registry/config composition through `OPENAI_API_KEY`.
  - Product policy/spec updates and deterministic adapter/composition tests.
- Out of scope:
  - Prompt rewrites, Responses state persistence, hosted tools, model picker,
    fallback routing, pricing UI, authentication, and remote deployment.
  - Rewriting historical logs or fixtures that document earlier providers.

## Test Seams

The existing public seams are retained:

1. `ModelPort.generate()` proves request translation, structured output,
   provenance, usage, and safe error behavior.
2. `ModelAdapterRegistry.create()` proves explicit configuration, environment
   credential resolution, and fixed adapter selection.
3. `RepositoryUiApplication`/conversation integration proves the unchanged
   closed tutor-decision lifecycle with an injected recorded transport.

These seams are already the repository's approved model and composition
contracts; tests will not inspect private methods.

## Approach

1. Add failing public-seam tests for the Luna request contract and registry.
2. Add the smallest dedicated Luna adapter over the generic transport.
3. Register the adapter without removing the provider-neutral adapter used by
   historical or external configurations.
4. Update active product policy/specs to name Luna and supersede DeepSeek.
5. Run focused tests, full tests, Ruff, mypy when available, wheel packaging,
   and an opt-in live smoke only when `OPENAI_API_KEY` is configured.

## Risks

- GPT-5.6 defaults to medium reasoning; omission would change cost/latency from
  the current non-reasoning baseline.
- Treating the family alias `gpt-5.6` as Luna would silently route to Sol.
- A dedicated adapter must not permit repository configuration to override the
  fixed endpoint or model ID.
- The current environment has no `OPENAI_API_KEY`, so deterministic transport
  tests can close implementation but a billed live API smoke may remain
  blocked until the owner configures the credential.

## Verification

- `python -m pytest -q tests/unit/adapters/model/test_openai_luna.py`
- `python -m pytest -q tests/unit/cli/test_repository.py`
- `python -m pytest -q`
- `python -m ruff check .`
- `python -m mypy` when installed
- `python -m pip wheel . --no-deps --no-build-isolation`
- `git diff --check`
