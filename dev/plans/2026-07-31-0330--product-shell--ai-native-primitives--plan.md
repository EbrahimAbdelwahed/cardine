# Plan: AI-native primitives for Cardine

Date: 2026-07-31 03:30
Area: product shell

## Goal

Adapt the 17 component patterns demonstrated by `beautiful-ui-five.vercel.app`
to Cardine's existing repository-backed study flows. Preserve the Claude-like
chat workspace, canonical data boundaries, accessibility, and public-demo
isolation.

## Scope

- In scope:
  - Loading state, thinking trace, staged answer reveal, approval card, tool
    activity, task rows, chat panel, recommendation card, context cards, diff
    table, records table, filter table, sidebar navigation, command search,
    insight cards, code/excerpt block, and response fine-tuning controls.
  - Dedicated dependency-free CSS/JavaScript primitive modules.
  - Mapping every primitive to real Cardine DTOs/actions or an honest
    unavailable/local-presentation state.
  - Desktop and 390 × 844 responsive states, keyboard, focus, reduced motion,
    and live browser verification.
- Out of scope:
  - Copying the reference site's shell, brand, marketing page, demo content,
    source assets, React/Tailwind runtime, or proprietary identity.
  - Inventing canonical data, provider actions, persistence, or backend
    capabilities in the browser.
  - Hosting and authentication.

## Pattern Mapping

1. Loading State → route/model work indicator with pixel-grid and elapsed time.
2. Thinking → expandable bounded tutor activity trace.
3. Streaming Text → staged reveal of a completed response, citations, actions,
   and follow-up prompts.
4. Approval Card → canonical continuation and assessment choices.
5. Tool Chips → compact capability/retrieval/provenance activity.
6. Task Rows → Today/open-work and plan status rows.
7. Chat → existing primary Cardine conversation and composer.
8. Recommendation Card → next-study action from attributable readiness facts.
9. Context Cards → materials and bounded source excerpts.
10. Diff Table → proposed artifact revision/decision comparison.
11. Records Table → source/artifact records with metadata.
12. Filter Table → live UI filtering of canonical rows.
13. Sidebar Nav → existing compact/expanded/mobile rail plus quick search.
14. Search → command palette over routes, sources, and suggested prompts.
15. Insight Cards → paged readiness/evidence facts without a fake mastery score.
16. Code Block → bounded code/source excerpt presentation with copy affordance.
17. Fine-tune Card → local response-style follow-up controls that populate the
    composer rather than mutating hidden model settings.

## Approach

1. Capture desktop/mobile reference evidence, interaction states, computed
   tokens, and the code exposed by every `View code` control.
2. Add failing asset/behavior contracts for all 17 primitives.
3. Introduce `ai-primitives.css` and `ai-primitives.js`; allowlist, serve, and
   package them alongside the existing shell.
4. Integrate primitives into current render functions without changing API
   routes or domain ownership.
5. Add dynamic behavior for disclosure, search, filters, follow-ups,
   fine-tuning, copy, and motion-safe response reveal.
6. Run focused tests, real-browser E2E, full suite, lint, package/install, and
   desktop/mobile design QA. Iterate until no P0/P1/P2 finding remains.

## Risks

- A visual pattern may imply backend state Cardine does not own; those controls
  must remain local presentation helpers or honest unavailable states.
- Simulated streaming must not be described as provider streaming.
- Additional disclosure/search controls must preserve keyboard and focus
  contracts.
- Existing large browser assets make integration-sensitive changes easy to
  regress; new primitives stay in separate modules.

## Verification

- `node --check` for both JavaScript assets.
- Focused unit, integration, and real-browser E2E tests.
- Full pytest and Ruff.
- Wheel asset inspection plus clean install.
- Same-state desktop/mobile reference-to-implementation comparison recorded in
  `design-qa.md`.
