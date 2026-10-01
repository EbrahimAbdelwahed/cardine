# Slice 04: product entrypoints and review

Status: proposed; depends on accepted Slice 03.

## Contract unlocked

The same material-generation workflow is usable from a source-page button and
from chat with an exact full-source attachment. The learner can observe progress,
read both outputs and provenance, decide them explicitly and follow published
materials into the existing Sources/Materials experience.

## API seam and ownership

Suggested endpoints:

```text
POST /api/v1/material-generations
GET  /api/v1/material-generations
GET  /api/v1/material-generations/{job_id}
GET  /api/v1/material-generations/{job_id}/outputs/{complete|study}
POST /api/v1/material-generations/{job_id}/decisions
```

- The source button and chat tool `materials.generate_pair` both submit the
  same exact pin/request to `MaterialGenerationService.request_pair`.
- Chat requires an attached full-revision `SourcePin`; it never resolves
  “latest”, a title, a search result or an excerpt into generation authority.
- The API starts/reconciles background work and exposes truthful status. It does
  not keep a long provider request open.
- The browser renders bounded Markdown through the existing safe renderer and
  reads canonical provenance through authenticated endpoints.

Likely integration areas:

- `src/cardine/demo/ui_application.py`
- `src/cardine/demo/browser.py`
- `src/cardine/demo/browser.js`, `browser.css`, `browser.html`
- tutor tool/capability registry and host routing modules
- focused unit, integration and browser journey tests

These files currently contain unrelated dirty work. Implementation must reserve
exact hunks/paths, preserve concurrent changes and stop if it cannot integrate
safely.

## Playable review surface

Browser journey:

1. open an imported transcript in Sources;
2. choose **Genera materiali**;
3. observe restart-safe stage status;
4. compare complete and study output with lineage/limitations;
5. approve/reject independently;
6. see the study-only blocked-parent state when applicable;
7. open published derived sources and use one in chat/flashcards;
8. repeat through chat with the same attached source and observe the same job.

## Verification

- Both entrypoints return the same job for the same request identity.
- No/ambiguous/foreign/stale/section-only chat attachment cannot start work and
  makes zero provider calls.
- Page reload and server restart retain status and review content.
- Only direct HUMAN controls submit decisions; navigation, polling and timeout
  do not.
- Study-only approval clearly explains why publication is blocked.
- Rejected/pending outputs never appear as canonical sources.
- Auth, CSRF/request identity, body bounds, content escaping and CSP remain
  intact.
- Focused browser/API tests, full repository journey, JavaScript syntax, Ruff,
  focused strict mypy and `git diff --check`.
- Run the unprimed `screenshot-critique` skill on desktop and mobile review
  screenshots as the final visual gate. If a reference or prior comparison is
  added later, also run `compare-screenshots` on the named crop/variable.

## Non-blocking human review checkpoint

Use `preview-shots` to open the source action, progress state, paired review,
blocked-parent state and published-source screens. Give the user about five
minutes to comment. If there is no response, decide from the screenshot critique
and accessibility evidence, record the rationale here, close Preview through
the skill and proceed. This checkpoint applies only after the overall plan and
Slices 01–03 have already been approved and completed.

## Human feedback that changes this slice

- Preferred placement or wording of the source action.
- Side-by-side versus sequential review on small screens.
- Whether chat should only link to the job rather than also start it.
