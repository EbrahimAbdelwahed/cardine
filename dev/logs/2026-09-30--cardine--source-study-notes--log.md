# Source, PDF and audio study notes

Owner approved implementation on 2026-09-30 after the plan in this chat.
Checkout: `/Users/ebrahimabdelwahed/.codex/worktrees/ee3d/cardine`.
Branch: `codex/source-study-notes`, based on `cfd7934` from
`codex/cardine-wave-a-recovery` / PR #5. Main does not contain that foundation;
this outcome must be reviewed against PR #5's branch, with that dependency explicit.

## Delivered scope

The Sources page offers **Genera note di studio** on current original/extracted
sources. Text enters the existing restart-safe paired coordinator. PDF page spans
propose editable lesson ranges; all pages must be covered once. Audio upload uses
a server-owned Groq Turbo adapter with local ffmpeg preparation and checkpointed
transcription chunks, then enters that same coordinator. The recording and
extraction manifest remain immutable; partial audio is not admitted.

The panel shows generation stages, lazy safe previews and independent HUMAN
accept/reject actions. Accepted complete output can publish alone. Study output
requires accepted complete lineage. Existing materializer, indexing, consent,
canonical artifact decisions and exact source pins remain the authorities.
Browser polling and run registries do not authorize canonical state changes.

Workers capture the original course/session, bound concurrency to two, recover
audio/material checkpoints on restart, and expose safe errors. Runtime credential
failure remains resumable instead of falsely marking the source stale. Browser
responses from an obsolete rendered context are discarded before decisions.

The operational copied core gained optional generic extraction provenance;
historical PDF decoding remains compatible. No installed Harness migration or
copied-core removal occurred. The original custody/classification files remain
frozen. `tests/parity/source-study-notes-overlay.json` binds only the exact fourteen
new/changed source paths in the approved feature scope; it records implementation
custody, not semantic review or package parity approval.

## Verification

- Offline product integration: idempotent job identity, separate decisions,
  dependency-aware publication, replay, session isolation, PDF coverage and parent
  staleness, audio checkpoint recovery, configuration retry.
- Offline native browser: generate, read, approve, publish and reload; generated
  outputs have no recursive generation button; proposals remain noncanonical
  during previews/polling; mobile has no horizontal overflow.
- Groq wire fixtures verify Turbo model, segment timestamps and safe HTTP errors.
  Preparation fixture verifies skipped completed chunks and global time offsets.
- Raw audio HTTP test verifies exact-origin enforcement and decoded filename/title.
- Fresh screenshot-critique identified singular/plural copy, an English status,
  excessive desktop width and heading ambiguity. All were corrected and final
  desktop/mobile captures inspected. Captures live in `/tmp`, not this repository.
- Ruff, mypy and ownership audit pass. Final committed-tree pytest/package evidence
  and submitted-commit GitHub CI/review status are recorded at closeout below.

No live provider spend, deployment, merge, credentials or study data were part of
this change. Runtime audio requires `GROQ_API_KEY`, `ffmpeg`, `ffprobe`, provider
consent and the configured Luna/OpenAI credential for subsequent generation.
A separate conversational generation tool, OCR, diarization and exports remain
outside this approved continuation.

## Closeout

Implementation is ready for final committed-tree verification and publication.
Continue on this branch/PR for actionable automatic Codex review or CI findings.
Do not merge or deploy without owner authorization.
