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
