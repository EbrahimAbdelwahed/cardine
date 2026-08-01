# ADR-0018: Use GPT-5.6 Luna as Cardine's base model

Date: 2026-07-31
Status: Accepted

## Context

Cardine previously selected DeepSeek through the generic
`openai-compatible-http` adapter. The owner selected GPT-5.6 Luna as the new
base model. OpenAI's current model catalog documents `gpt-5.6-luna` as an
explicit API model supporting Chat Completions, Responses, and Structured
Outputs. The unsuffixed `gpt-5.6` alias routes to Sol and therefore cannot be
used as a Luna shorthand.

The existing Cardine model path is a bounded, stateless Chat Completions call
behind `ModelPort`; conversation state, tools, retries, continuation, and
canonical persistence remain owned by the harness.

## Decision

Add the technical adapter ID `openai-gpt-5.6-luna`. It fixes:

- endpoint `https://api.openai.com/v1/chat/completions`;
- model `gpt-5.6-luna`;
- effective reasoning `none`;
- strict JSON Schema structured output;
- `max_completion_tokens` for output bounds.

Repository configuration may set only a bounded timeout and reference
`OPENAI_API_KEY`. It cannot override endpoint, model, reasoning effort, or
adapter provenance. The generic provider-neutral adapter remains available
only to trusted hosts that explicitly opt into configurable network
destinations; it is removed from the repository's default registry.

## Consequences

- Cardine gains an explicit, cost-oriented base model without changing domain,
  prompt, UI, persistence, or replay contracts.
- Existing repositories configured with `openai-compatible-http` require an
  explicit trusted-host registry or migration to the Luna adapter. This
  intentional compatibility break prevents an opened repository from choosing
  an arbitrary network destination and environment-secret name by default.
- The baseline preserves the prior non-reasoning behavior and avoids GPT-5.6's
  implicit medium-reasoning default.
- A live smoke and activation require an `OPENAI_API_KEY`; Codex/ChatGPT login
  credentials are not reused as API credentials.
- Responses API features, persisted reasoning, hosted tools, and Pro mode are
  not adopted by this migration.

## Alternatives Considered

- Use the `gpt-5.6` alias: rejected because it routes to Sol.
- Invoke `codex exec`: rejected because Luna is a documented public API model
  and a coding-agent subprocess would add tool isolation, latency, and
  lifecycle risk.
- Reuse arbitrary `openai-compatible-http` settings: rejected because the base
  product must not silently point its Luna identity at another endpoint/model.
- Migrate to Responses in the same change: deferred because the existing
  single-request closed-decision and playbook contracts do not require
  provider-owned conversation state or hosted tools.
