# Design QA: Cardine Referto

Date: 2026-07-29 15:20
Visual target: `study-agent-ui/Study Agent per Medicina.zip` →
`Cardine Referto.dc.html`
Result: PASS for the selected desktop visual target

## Final comparison

- Source: `source-referto-desktop.png` — 1280 × 947.
- Implementation: `implementation-desktop-approved.png` — 1440 × 1050.
- Side-by-side normalized comparison: `comparison-desktop-approved.png` —
  2560 × 947.
- Core-flow state: `implementation-session-desktop.png`.

The implementation preserves the source's warm-paper palette, editorial serif
hierarchy, dark study-entry hero, fixed numbered rail, dense dotted rows, and
outlined status cards. The final independent visual gate reported no P1 or P2
issues.

## Iterations

1. Rebuilt the exported Referto as packaged HTML, CSS, and JavaScript without
   the mockup runtime.
2. Connected navigation and the primary study form to the versioned demo API.
3. Reduced hero height and title scale, strengthened active navigation,
   anchored rail status, improved contrast, and separated learner/tutor turns.
4. Removed internal implementation language and action buttons for unavailable
   features. The public demo uses deliberate empty states instead of invented
   metrics.
5. Added a visible input boundary and attached submit action, then corrected
   the final Italian status copy.

## Interaction checks

- Bootstrap renders course, status, counts, and feature availability.
- Every rail destination opens its corresponding interface or an explicit
  unavailable state.
- Submitting a study question reaches `POST /api/v1/session/turns` and renders
  the returned demo trace immediately.
- The demo response is labelled as non-persistent and does not claim a
  canonical save.
- Provenance and trust drawers open and close.
- Browser DOM inspection found no remaining request-ID, fixture, or canonical
  owner language on the primary public screen.
- Browser console inspection performed during the main flow showed no errors.

## Responsive note

Responsive breakpoints, mobile rail behavior, and single-column layouts are
covered by the static asset contract. The in-app browser's viewport override
did not change its native desktop viewport in this session, so no mobile
visual screenshot is claimed as verified.
