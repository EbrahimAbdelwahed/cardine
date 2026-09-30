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
frozen. `tests/parity/source-study-notes-overlay.json` binds only the exact fifteen
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
- Raw audio HTTP test verifies exact-origin enforcement and decoded filename/title;
  private transport also requires valid session CSRF. Native audio preparation
  verifies real WAV→FLAC conversion and rejects a disguised HLS playlist before
  any provider call, through container/protocol allowlists.
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

## Automatic review corrections

Codex reviewed `79e2aa5` and identified two P2 findings. Both are corrected in
this PR. Publication now distinguishes typed sequence/projection conflicts from
blocked dependencies, revalidates root/parent at each bounded retry against an
exact canonical sequence, and records exhausted retries durably. The decision
handler schedules the publication worker; a request arriving during worker exit
is coalesced into another run instead of being lost. The UI offers truthful
publication retry status without repeating the HUMAN decision or model calls.

Audio transcript size is checked after chunk checkpoints and before extraction
admission. Oversized input is terminal with an actionable request to upload
shorter recordings. It creates no canonical transcript or note proposals and
cannot loop through the same completed chunks on resume.

Regression tests exercise actual concurrent course events (one conflict and
exhausted retries across repository reopening), retained HUMAN decisions,
publication dispatch, worker completion races and oversized audio rejection.

## Closeout

Initial implementation: `8f60ec5` (2026-09-30). Final audio containment and
immutable dispatch-scope corrections are included in the same feature PR.

Full offline pytest on `9af0059`: **2509 passed, 14 skipped** in 97.57 seconds,
including the clean-archive audit, browser and review regressions. The focused
product/materializer set passed **19 tests** before that final run. Ruff and mypy
(628 source files) pass. Ownership audit passes (322 historical rows). Both sdist
and wheel build and `verify_cardine_wheel.py` pass. Optional recall/CPython 3.13
qualification checks account for the local skips; CI owns their additional lanes.

Published PR: [#9](https://github.com/EbrahimAbdelwahed/cardine/pull/9),
based on PR #5. Automatic Codex semantic review and GitHub CI must still be
inspected for the final submitted commit. All eight CI lanes passed on the
previous head `79e2aa5`; that evidence is superseded by the corrected head and
cannot authorize a merge. The two Codex findings are covered by the corrections
and regression tests above. No merge or deployment has occurred.
Continue on this branch/PR for actionable automatic Codex review or CI findings.
Do not merge or deploy without owner authorization.

Merge preparation fixes the current review: background material workers use SERVICE provenance; page-mapped PDF generation requires confirmed lesson ranges at the product boundary. Regressions reject whole-PDF jobs without canonical writes and verify actual dispatched worker context.

Final review repairs prevalidate normalized bounds for every selected PDF lesson before admission, and handle retryable audio ingestion conflicts with a released lease and durable chunks so the dispatched worker retries. Product regressions verify both triggers. Recovery integration and exact feature custody are updated together.
