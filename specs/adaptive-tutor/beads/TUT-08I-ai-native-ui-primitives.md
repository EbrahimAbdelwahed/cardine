# Task Bead: TUT-08I AI-native UI primitives

Status: Complete
Priority: P0
Type: product polish and interaction completeness
Depends On: TUT-08G

## Outcome

Cardine exposes the complete useful AI-chat component vocabulary from the
selected Beautiful UI reference through real study flows, while retaining the
existing chat-centric shell and canonical repository boundaries.

## Acceptance Criteria

- [x] All 17 reference patterns have an explicit Cardine mapping.
- [x] No reference branding, marketing shell, demo data, React dependency, or
  hotlinked asset enters production.
- [x] Every primary interaction is keyboard accessible and truthful about
  whether it mutates canonical state or only prepares a local follow-up.
- [x] Desktop, compact rail, mobile drawer, reduced motion, loading, empty,
  working, success, and error states remain usable.
- [x] Existing route/API/domain contracts and public-demo isolation remain
  unchanged.
- [x] Focused/full tests, lint, package, E2E, and design QA pass with no open
  P0/P1/P2 findings.

## Evidence

- Source captures and pattern inventory:
  `dev/plans/assets/beautiful-ui-pattern-audit/`
- Plan:
  `dev/plans/2026-07-31-0330--product-shell--ai-native-primitives--plan.md`
- Design QA: `design-qa.md`
- Completion log:
  `dev/logs/2026-07-31-0415--product-shell--ai-native-primitives--log.md`
