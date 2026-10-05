# UI/UX redesign — phase 1 (visual system, navigation, copy)

Date: 2026-10-05
Branch: `claude/cardine-ui-ux-redesign-bbc0d6`
Spec: [`specs/ui-ux-redesign/README.md`](../../specs/ui-ux-redesign/README.md)

## Request

The owner wants Cardine to feel like "NotebookLM, but open and hackable":
clean, elegant and ordered, freeing cognitive space for studying. The full
workflow (exam date → plan, background source processing, selective study
notes, Typst PDFs, document + agent linked, agent parity) is planned in five
phases in the spec. This log covers phase 1, frontend only, no API change.

## Changes

- Rail: two always-visible groups ("Studio", "Revisione") replace the
  collapsible "Spazio di studio" disclosure; the rail starts expanded.
  Labels: Nuova chat, Libreria, Da approvare, Progressi. Route keys, element
  IDs and endpoints are unchanged.
- One `page()` frame for content routes: title, optional lede/actions, a
  single reading column. All explanatory asides about service invariants are
  removed from Piano, Da approvare, Verifiche, Ripasso, Progressi, loading,
  error and unavailable states.
- Captions are sentence-case sans; status pills and the rail status are no
  longer monospaced capitals.
- Home: composer first, then a quiet "Da fare oggi" list (exam countdown when
  configured, due cards, pending decisions) replacing "Panoramica di studio".
  The lesson picker no longer shows revision identifiers.
- Libreria: compact hairline source rows (Apri · Genera note · Dettagli),
  the reader as the second pane, study notes as a first-class section; the
  extract/revision registers and the in-page search duplicate are removed.
- Piano: exam countdown or an honest "Data d'esame non impostata", stats that
  link to the relevant page, goals/constraints as a plain list. Inline
  provenance labels ("fonte: scheda del corso") are removed; dates are only
  formatted, never computed, in the browser.
- Da approvare: proposals render as the flashcard itself with Accetta/Rifiuta;
  revision/session identifiers move to the "Dettagli" sheet; bulk selection
  appears only when there is more than one reviewable card.
- Dead helpers and CSS removed (`sourceRef*`, `spec*`, `sideItem`,
  `PROJECTION_LABELS`, unused primitive adapters, `section-grid`, `side-card`,
  `today-strip`, `rail-tools`, …).

## Bug found and fixed

`test_material_generation_progress[True]` became flaky (~50%) on this branch.
When background indexing finished, `pollIndexing` re-rendered the library and
the morph replaced the live notes-job nodes with the "Caricamento…"
placeholder, so the progress element the reader (and the test) followed was
detached. The race also exists on `main` with different timing. `renderFonti`
now re-attaches the same job nodes after a re-render: no flicker, no lost
progress. 6/6 isolated runs and the full suite pass.

## Verification

- `pytest`: 3000 passed, 64 skipped (optional httpx/pydantic/live tests),
  including the real-browser e2e journeys.
- `ruff check .`, `mypy` (684 files), `python -m build`: pass.
- Visual review on a synthetic seeded repository (no real study data):
  desktop 1440×900 light and dark, 800px, 320px and mobile 375×812 with the
  drawer; source reader, proposal decisions, plan, progress and chat.

## Not done (later phases)

Exam-date persistence and onboarding progress (phase 2), Typst PDF notes
(phase 3), document + chat split view with selection actions (phase 4),
agent parity tools (phase 5). Login, settings and workspace screens keep
their previous layout apart from the shared caption style.
