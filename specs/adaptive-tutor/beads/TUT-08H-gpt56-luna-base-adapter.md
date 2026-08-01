# Task Bead: TUT-08H GPT-5.6 Luna base adapter

Status: Implemented; golden approval and live credential pending
Priority: P0
Type: compatibility migration
Depends On: TUT-08G

## Outcome

Cardine uses the explicit `gpt-5.6-luna` API model through a dedicated
provider adapter, preserving all closed model, tutor, persistence, retry, and
UI contracts.

## Contract

- Adapter ID: `openai-gpt-5.6-luna`
- Model ID: `gpt-5.6-luna` (fixed; never the `gpt-5.6` Sol alias)
- Endpoint: OpenAI Chat Completions (fixed)
- Effective reasoning: `none`, preserving the previous non-reasoning baseline
- Credential: `OPENAI_API_KEY` by environment reference only
- Structured output: strict JSON Schema, validated again locally

## Acceptance Criteria

- [x] Repository configuration cannot override the Luna endpoint or model.
- [x] Requests explicitly send `reasoning_effort: none`.
- [x] Invocation provenance records the Luna adapter and model identity.
- [x] Structured tutor decisions retain the existing closed schema.
- [x] Credential values never enter config, request bodies, errors, or reprs.
- [x] The provider-neutral adapter remains explicitly available to trusted
  hosts, but is not exposed by the repository's safe default registry.
- [ ] Focused, full-suite, lint, package, and review gates pass. The full suite
  is green except for the intentionally changed schema fingerprint awaiting
  owner approval.
- [ ] Live smoke is run when `OPENAI_API_KEY` is available, or recorded as an
  explicit credential blocker.

## Out of Scope

- Model routing, fallback providers, hosted tools, prompt rewrites, auth, and
  deployment.
