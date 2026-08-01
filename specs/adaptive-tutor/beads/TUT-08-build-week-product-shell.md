# Task Bead: TUT-08 Build Week product shell

Status: Product complete — submission assets pending
Priority: P1
Type: product tracer-bullet
Depends On: TUT-06

## Worker Profile

create a product-shell profile only after a visual direction is selected; use
independent design QA and accessibility review

## Outcome

A thin conversation-first consumer demonstrates the adaptive tutor without
duplicating behavior or persistence.

## Production Composition Decomposition

- [TUT-08A — durable adaptive conversation](TUT-08A-durable-adaptive-conversation.md)
- [TUT-08B — DeepSeek live tutor chat](TUT-08B-deepseek-live-tutor-chat.md)
- [TUT-08C — reference runtime, materials, artifacts, and context](TUT-08C-reference-runtime-artifacts-context.md)
- [TUT-08D — assessments and learner evidence](TUT-08D-assessments-and-evidence.md)
- [TUT-08E — recall queue and review flow](TUT-08E-recall-review-flow.md)
- [TUT-08F — Today aggregation and exam readiness](TUT-08F-today-and-exam-readiness.md)
- [TUT-08G — full-product E2E and release closure](TUT-08G-full-product-e2e-closure.md)
- [TUT-08H — GPT-5.6 Luna base adapter](TUT-08H-gpt56-luna-base-adapter.md)

The complete product acceptance contract is
[`cardine-full-product.md`](../cardine-full-product.md). TUT-08H supersedes
Cardine's earlier DeepSeek runtime choice with the explicit
`gpt-5.6-luna` adapter; provider-neutral host contracts remain unchanged.

## Acceptance Criteria

- [x] Free-form learner entry can act before context is complete.
- [x] Conversation, material, evidence, and conflict states are coherent; due
  review is exposed when the optional TUT-07 capability is installed.
- [x] UI uses the product shell/public contracts and never SQLite/model providers directly.
- [x] One-command sample journey works offline; configured GPT-5.6 Luna remains an
  explicit host-owned composition rather than an implicit browser mode.
- [ ] README, sample data, eval report, and sub-three-minute video script satisfy submission requirements.

## Verification

- Browser journey, accessibility, visual diff/critique, deterministic demo,
  packaging, and full core gates. The deterministic HTTP journey and static
  accessibility markers are covered in TUT-08 tests. Fresh localhost browser
  screenshots, an independent critique, the resulting overflow fix, and a
  mobile no-overflow DOM check are recorded; the configured GPT host journey
  and final submission package remain release evidence to collect.
