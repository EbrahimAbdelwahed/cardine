# ADR-0023: Minimal append-only student journal

Accepted by the owner on 2026-10-01.

Cardine needs to remember the student's covered topics and encountered difficulties,
without maintaining unused assessment-evidence estimates and context conflicts.
Persist attributable observations in `state/student-state.json` through one small
`StudentStateService`, shared by the tutor and UI. The file contains JSON Lines;
updates append events, never overwrite the student's past.

Do not derive mastery or readiness yet. Keep recall scheduling/history separate.
The tutor receives recent course-wide journal entries and can search older entries.
Preserve old SQLite histories and import their relevant facts idempotently, with
source references. Retain archival context decoding/replay, but retire the product
context writer and conflict-resolution routes. Source grounding and citations retain
all existing integrity boundaries.

The journal has its own sequence; it never advances or impersonates the SQLite
course sequence. Back up the entire repository, as historical export-v1 only exports
SQLite events. This decision supersedes learner-evidence projection and student
memory storage requirements in the adaptive-tutor slices for Cardine. Detailed
contracts and verification live in `specs/student-journal/README.md`.
