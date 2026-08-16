# Plan: minimal study memory

Date: 2026-08-14 14:55 CEST
Area: Cardine / tutor memory

## Goal

Give the bounded tutor a durable, searchable archive of topics actually
covered and attributable learner-knowledge signals, without implementing
mastery/readiness or treating model-authored observations as medical evidence.

## Scope

- In scope:
  - versioned structured memory entries stored through the existing canonical
    `session.record_note` owner;
  - private `study_memory.record` and `study_memory.search` tutor tools;
  - course-wide, cross-session, high-water-bounded lexical reads;
  - automatic `topic_covered` entry only after a capability completes;
  - prompt guidance and host routing preservation for the new write tool;
  - removal of structured memory payloads from the ordinary recent-conversation
    snapshot so memory enters Luna context only through the search tool.
- Out of scope:
  - mastery, readiness, percentages, curriculum graphs, embeddings, UI, model
    biography, cross-course aggregation, or changes to the seven public
    StudyTools.

## Confirmed seams

1. `HarnessToolSurface.invoke("study_memory.record", ...)` records exactly one
   learner-signal observation for the current host turn.
2. `HarnessToolSurface.invoke("study_memory.search", ...)` returns at most eight
   structured entries from the current course through the captured tutor
   snapshot sequence.
3. `POST /api/v1/session/turns` can execute record -> capability in one bounded
   agent turn, and a completed explanation becomes searchable as
   `topic_covered`.

## Private DTO

- Record input: exact `topic` (1..120), `summary` (1..300), `signal`
  (`self_reported_difficulty|incorrect|partial|correct|unknown`), and
  `assistance` (`none|hint|explanation|unknown`). Origin identity and sequence
  are host-derived.
- Search input: nullable query up to 120 characters, kind filter
  (`any|topic_covered|learner_signal`), signal filter (`any` plus the signal
  vocabulary), and limit 1..8.
- Search results expose bounded observation text plus opaque memory identity,
  origin/recorded sequence, and `recorded_by`; they never expose authority,
  prompts, or provider data.

## Approach

1. Add one public-seam RED test for record/search and implement the strict
   codec/reader/writer around SERVICE-authored `study-memory@1` notes.
2. Add one RED agent-loop test and wire both private tools with host-derived
   authority, stable idempotency, activity labels, and prompt guidance.
3. Add one RED completion test and record `topic_covered` best-effort only after
   `CompletedCapabilityOutcome`.
4. Filter structured memory notes from ordinary tutor snapshot timeline, notes,
   and continuation-summary prose; use `study_memory.search` for retrieval.
5. Run focused tests, broader tutor/chat regressions, Ruff, diff check, and one
   independent semantic review.

## Invariants

- A normal HUMAN note or malformed/prefix-spoofed note is never returned as
  study memory.
- The model cannot author course/session/interaction IDs, event sequence,
  `recorded_by`, mastery, readiness, score, or confidence.
- One exact retry is idempotent; a changed second payload under the same host
  turn conflicts safely.
- Failed, suspended, or terminated capabilities do not produce
  `topic_covered`.
- Memory may guide tutoring scope but is never canonical source evidence for a
  medical claim.
- Existing dirty shared-worktree changes remain untouched.

## Risks

- Existing session notes feed continuation summaries; structured memory must be
  filtered before provider context to avoid duplicated/unbounded payloads.
- Source-grounding host routing currently rewrites arbitrary decisions for
  explanation requests; it must preserve the explicit memory-record tool.
- Automatic note persistence is secondary to the learner response: a final
  storage conflict must not convert a completed explanation into a failed turn.

## Verification

- Focused study-memory unit/contract and repository-backed chat tests.
- Existing conversation-memory, bounded-loop, source-grounding, flashcard, and
  tutor-decision suites.
- `.venv/bin/ruff check` on changed files.
- `git diff --check`.
