# Note di studio da fonti, PDF e audio

Owner approved the plan in this chat on 2026-09-30. Implementation belongs to
`codex/source-study-notes` in the `ee3d/cardine` worktree. It depends on PR #5,
`codex/cardine-wave-a-recovery`, base commit `cfd7934`. No deployment or merge is
part of this authorization.

## Outcome and ownership

Three inputs converge on the existing paired material pipeline:

- an exact current text/Markdown revision;
- PDF text already admitted by AnyDoc, split into lessons using canonical page
  spans and HUMAN-confirmed, editable page ranges;
- an uploaded recording transcribed through Groq `whisper-large-v3-turbo`.

Each lesson follows segmentation → segment elaboration → complete merge →
study variant. Existing versioned Luna prompts/adapters own note generation;
Audio to Sbobina supplies the reference behavior, not live imports or provider
clients. The original source remains immutable. Both outputs are proposals.

The existing material coordinator owns generation checkpoints and CAS leases.
The artifact service alone owns HUMAN decisions. `GeneratedSourceMaterializer`
alone publishes accepted outputs; complete may publish alone, while study needs
accepted complete lineage. Existing indexing and recall owners remain unchanged.

`cardine.materials.product` binds product entrypoints to those services. Its
session-scoped registry lists jobs; it cannot authorize canonical writes. Workers
capture course/session context, limit concurrent jobs to two, reopen repositories
between lease waits, and recover durable jobs after process restart. Reads never
publish or infer acceptance. Configuration failures expose safe resumable errors. Publication sequence
conflicts retry with exact-sequence root/parent validation, then persist pending
publication and schedule a worker retry without repeating HUMAN approval.

## Extraction contract

`TextExtractionProvenance` extends the operational copied core with generic
extraction lineage, without a Harness package migration. It records immutable
input/text digests, a verified content-addressed manifest and its byte length,
adapter, media type and limitations. Historical PDF provenance remains unchanged.
Original, extracted and generated origins remain distinct.

PDF lesson manifests bind exact parent source/revision, page and offset ranges.
All page ranges must cover the PDF exactly once, in order. Parent replacement or
retirement blocks unfinished generation and publication. Each resulting lesson
is independent, with its own immutable extraction identity and note pair.

Audio manifests carry global millisecond spans and the exact model. Successful
chunk transcripts are content-addressed and checkpointed before continuing.
No partial recording enters the canonical source catalog. Retries reuse completed
chunks. Provider consent and active session are rechecked before each chunk and
before admission. Credentials and provider requests stay on the server.

## Product surface

Sources offers “Genera note di studio” and an informational disclosure. PDF input
opens editable lesson ranges before starting. Sources also accepts audio uploads;
“trascrivi e genera note” explicitly starts transcription and note generation.
The review panel on its own grid row shows truthful stages, lazy safe Markdown previews,
limitations, independent approval/rejection and links to published sources.
Polling retains disclosure state and does not duplicate event listeners.

Only the source-page entrypoint is in this approved plan. Conversational study
and flashcard generation use the resulting sources after approval through their
existing paths; a separate chat generation tool is not added.

## Runtime and bounds

- Audio server requires `ffmpeg`, `ffprobe`, `GROQ_API_KEY`, provider consent and
  the configured Luna adapter/OpenAI credential for subsequent notes.
- Audio formats: mp3, wav, m4a, mp4, ogg, webm, flac, aac; at most 128 MiB and
  eight hours. Preparation isolates temporary files, converts to mono 16 kHz
  FLAC in ten-minute chunks, and bounds duration, runtime and upload size.
  Demuxer/protocol allowlists reject playlists and external input references.
- Raw audio transport requires same-origin requests, bounded streaming and the
  existing private authentication/CSRF boundary. No arbitrary server path is
  accepted from HTTP clients.
- Existing material limits remain: at most 512,000 transcript characters per
  lesson and 16 thematic segments. Binary originals may be at most 256 MiB.
  Audio stops before canonical admission if the transcript exceeds those limits;
  the terminal receipt asks the user to upload shorter recordings.
- Existing PDF admission policy applies. Scans without text still require OCR;
  this feature does not add an OCR provider, slides, image generation or exports.
- No provider/model fallback or automatic acceptance. Network model tests and
  deployment require separate authorization.

## Verification and handoff

Implemented: source-scoped generation, PDF lesson extraction, Groq audio adapter,
checkpointed audio ingress, background API, review/publication and offline tests.
Offline integration and browser journeys pass, including explicit approval,
publication, reload, PDF coverage/staleness and audio checkpoint recovery.
Groq wire/model/timestamp and raw-upload same-origin tests use fixtures.
Desktop/mobile screenshots received a fresh visual critique; singular labels,
Italian proposal status, heading hierarchy and reading width were corrected.
Full offline suite on the corrected implementation: 2509 passed, 14 skips
(optional platform/dependency checks and disabled live model tests), including
clean-archive audit and browser tests. Both automatic-review findings have
regression coverage. Ruff, mypy, custody audit, sdist/wheel build and package
verification pass. No live provider call made. Publication and submitted-commit
CI/review evidence are recorded in the repository development log.

Run the prescribed pytest/Ruff/mypy/build gates. Focused tests are
`tests/integration/test_material_product.py` and
`tests/e2e/test_material_notes_journey.py`. The optional
`CARDINE_NOTES_VISUAL_DIR` variable captures desktop/mobile screenshots from
synthetic fixtures. Run screenshot-critique on those captures before accepting
the visual slice. Preserve the historic custody overlays; the feature overlay
pins only changed/new source paths for this approved scope and does not assert
that pending GitHub review is complete.

Next agent: check the current branch/PR before changing files, inspect this
handoff and `dev/index.md`, fix actionable CI/review findings in this same PR,
then update verification evidence here. Do not merge or deploy without permission.

## Selective lesson generation — owner-approved continuation, 2026-10-02

The source-page PDF flow now separates editing the complete lesson partition
from selecting lessons to generate. The editor still confirms ordered,
non-overlapping full PDF coverage; the next screen starts with no lessons
selected, offers select-all and returns to the preserved boundary editor.
Only checked lessons enter extraction and generation. Each keeps its existing
independent complete/study proposal pair, HUMAN review and publication gates.

The generation command accepts `selected_lessons` as an alternative to the
legacy `lessons` payload. Selected ranges may skip pages and arrive in any
order; the server sorts them and validates exact current source/revision,
integer page bounds, titles, disjointness, size, consent and capacity before
admission. Empty, overlapping, duplicated, malformed or stale selections fail.
The existing `lessons` contract continues to require full PDF coverage.
Both payloads together, or explicit null payloads, are rejected.

Selected request identity binds parent source/revision, title and page range
rather than position in the submitted array. Reordering a retry reuses the
same jobs; reusing a request ID with another selection fails. The browser
retains its request ID across failed submissions while selection is unchanged.
Existing batch reservations, extraction manifests, restart checkpoints and
parent lifetime validation remain authoritative. Unselected lessons produce
no extraction or model job. Chat-triggered note generation remains deferred.

## PageIndex lesson dropdown — owner-approved continuation, 2026-10-02

The source note action opens a dropdown over the existing ready PageIndex
navigation for the exact current source/revision. It supports Markdown/text
and PDF, preserves nested sections, starts unselected, and submits only the
explicitly chosen lesson. The browser confirms the scope before starting.
An explicit whole-source choice remains available for non-PDF input.

Preparation returns `structure_status` and `structure`, whose entries carry
only title, exact Unicode offsets and the canonical text SHA-256. The new
`structure_lesson` generation payload is mutually exclusive with `lessons`
and `selected_lessons`. The server revalidates the exact ready projection,
source/revision, digest, bounds, selected candidate, consent and capacity before
admission. PDF selections retain exact character boundaries even when lessons
share a page; they never expand to whole pages. Node IDs and summaries do not
cross this boundary or authorize evidence/citations.

A selected canonical slice is admitted with immutable extraction lineage
(parent source/revision/text hash and exact offsets), then enters the existing
paired-material pipeline. The canonical extraction and its SourceChunks own
citations and evidence. HUMAN proposal decisions and dependency-aware
publication stay unchanged. Request identity binds the exact selection;
unchanged retries reuse the job, changed selections require a new request.
A retired/replaced parent blocks generation and publication as before.

Loading, queued/indexing, missing/disabled/degraded structure and failed reads
are explicit. Refresh retries preparation without model work. Absent structure
cannot start structural generation. The existing PDF boundary editor and
multiple-lesson selection remain an explicit alternative. Uploads made from
Sources stay on Sources; first-source study setup remains available on Today.
Source admission still completes independently of derived indexing, and note
preparation does not run extraction or initiate model calls.

Integration points for the parallel Sources layout task: retain
`[data-generate-notes]` with its exact source/revision JSON, `#material-jobs`,
and the `[data-notes-lessons]` polling guard. No CSS or source-grid layout change
is part of this continuation. Its new custody overlay binds only the three
changed runtime paths, preserving all historical custody overlays.

Offline coverage: `test_structure_lesson_notes.py`,
`test_structure_lesson_notes_journey.py`, existing PDF selection and material
journeys, plus exact custody drift checks. Run pytest, Ruff, mypy, ownership
audit and package build before delivery. No paid provider tests are authorized.

Delivery verification (2026-10-03 CEST): PR #18 includes published main `0990dbf`;
2,910 offline tests passed with four expected optional/live skips. Ruff, strict
mypy (670 files), ownership audit, JS syntax, wheel/sdist and package verification
passed. GitHub CI and automatic Codex review remain pending at publication.
See [the focused handoff](../../dev/logs/2026-10-02--cardine--lesson-structure-picker--log.md).
No merge or deployment was performed.

## Visible lesson progress — owner-requested continuation, 2026-10-04

Based on PR #18's automatic PageIndex lesson picker. Successful submission
focuses the generated lesson panel. Each job reports its active checkpoint lease:
segmenting the named lesson, generating the named segment, merging complete notes,
preparing study notes and preparing the proposal. Queued work explicitly waits
for a worker slot. No queue position, completion time or percentage is invented.

Segment totals and titles are read from hash-verified boundary and unit checkpoints
and validated against their manifest and request bounds. Missing or inconsistent
progress details produce a bounded presentation warning without writes or changes
to generation authority. Active stages require a non-expired lease; failures and
terminal states never appear as active. Restart retains completed counts.

The accessible native progress bar counts completed segments only; accompanying
text distinguishes the current segment from completed work. Resume appears only
for generation/publication retry states. HUMAN approval, PageIndex scope checks,
paired outputs, source lifetime and canonical publication remain unchanged.

The three runtime paths have a separate `notes-progress-overlay.json`; historical
custody records remain intact. Offline browser coverage holds the model at the
segmentation and generation seams before completion, checks mobile overflow,
completion and approval controls. Product tests cover restart, manifest mismatch
and read-only status behavior. Visual critique covers desktop/mobile captures.
