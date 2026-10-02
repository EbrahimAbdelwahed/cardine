# Source structure dropdown and scoped notes

Owner-requested independent task, 2026-10-02. Checkout:
`/Users/ebrahimabdelwahed/.codex/worktrees/lesson-structure-picker/cardine`;
branch `codex/lesson-structure-picker`; fetched default base `1f49efd`.
Origin is `EbrahimAbdelwahed/cardine`. The shared dirty checkout and other
chat worktrees were preserved. No merge, deployment or paid provider call.

## Diagnosis and implementation

Main already included selective PDF notes (PR #14), but preparing notes exposed
only a mechanically inferred full PDF page partition. Users had to confirm the
manual boundary editor before selecting lessons. Ready PageIndex navigation
was not exposed by this notes API; non-PDF input immediately generated the
whole source.

Preparation now returns exact ready structure spans and truthful availability
status. The note action opens an initially unselected dropdown for Markdown,
text and PDF with nested sections. The explicit selection confirms its exact
boundaries. PDF spans never widen to pages, including two lessons on one page.
The manual full-page editor and multiple-lesson selection remain accessible.
Sources uploads stay on Sources; first-source study setup on Today is preserved.

The new mutually exclusive `structure_lesson` command revalidates current
source/revision, canonical digest, ready candidate equality, exact bounds,
consent and capacity. A canonical extracted slice records parent revision,
parent digest and Unicode offsets, then uses the existing complete/study
pipeline, SourceChunk evidence checks, HUMAN decisions and publication gates.
Index IDs/summaries are absent from the input, extraction manifest and citations.
A selection-specific fingerprint prevents request ID reuse with different
boundaries; unchanged retries preserve jobs. Parent retirement/replacement
continues to block unfinished generation/publication.

No CSS, source-grid or overall layout change. Parallel layout integration must
preserve `[data-generate-notes]` source/revision JSON, `#material-jobs`, and
`[data-notes-lessons]` polling guard. Focused runtime changes are in notes
functions in browser.js, `_material_command`, and MaterialProduct.
The independent `structure-lesson-notes-overlay.json` pins these three runtime
paths without rewriting historic custody overlays; audit scope/drift tests
cover the new overlay.

## Verification and delivery

Existing selective PDF/material/browser tests: 38 passed.
New structure integration/browser cases cover precise nested spans, shared PDF
pages, unknown/altered selections, missing/disabled/queued/degraded/failed
structure, restart, lost responses, stable retry identity, API dispatch and
mixed-mode rejection, no premature publication, parent retirement and consent.
Full pytest/Ruff/mypy/build verification and current-head GitHub CI/automatic
review are pending at this implementation checkpoint. No local reviewer was
launched. Automatic review is limited to two rounds per PR.
