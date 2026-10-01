# Plan: sbobina material-generation workflow

Date: 2026-08-14 17:00 CEST
Area: Cardine / materials
Status: Slice 02 implemented — awaiting user review

## Goal

Port the historical sbobina-to-complete-to-study workflow into Cardine while
preserving exact source lineage, GPT-5.6 Luna isolation, restart safety, HUMAN
review and canonical publication rules.

## Scope

- In scope: one imported text/Markdown transcript, complete and study outputs,
  reviewable paired proposals, explicit decisions, generated-source
  materialization, existing indexing, source-button and exact-attached chat
  triggers.
- Out of scope: audio/transcription, PDF input, auto-run, auto-accept, Anki,
  images/chemistry rendering, extra providers/dependencies and unrelated
  refactors.

## Approach

The canonical multi-slice specification is
`specs/material-generation-workflow/README.md` with four independently
verifiable slices. The user authorized and implementation completed Slices 01
and 02; Slices 03–04 remain outside the current authorization.

## Risks

- Misclassifying generated text as original source content.
- Long multi-call generation without durable checkpoints.
- Publishing a study derivative whose complete parent was rejected.
- Losing concurrent dirty-worktree changes in shared Cardine UI/host files.

## Verification

- This planning turn: inspect all new spec files, run Markdown/link sanity and
  `git diff --check`; do not run application tests because no application code
  is changed.
