# Minimal student journal

Status: owner-approved on 2026-10-01. This replaces Cardine's assessment-evidence
projection, session-note memory writer, and product context-conflict workflow.
It does not adopt an installed Harness package or remove the operational copied core.

## Contract

- One local study repository belongs to one student. `state/student-state.json`
  is an append-only JSON Lines journal: one complete JSON object per line, despite
  the requested `.json` filename. Every event carries its course and session.
- `StudentStateService` is the only reader/writer. There are no projections,
  derived scores, indexes, conflict resolution or scheduling in this service.
- Records contain schema version, stable event identity, journal sequence,
  occurrence time, kind, topic, optional observation/signal/assistance, writer,
  originating course-event sequence and reference. UI self-reports have origin
  sequence zero and a UI request reference; agent observations reference a real
  HUMAN turn in the selected session. Model arguments never choose scope or writer.
- Native observations are `topic_covered` or `learner_signal`. Covered topics are
  appended only after a completed capability has a canonical tutor presentation.
  Historical context and assessment activity can be copied as factual entries;
  no learner mastery, readiness, coverage or confidence estimate is calculated.
- Writes take an exclusive file lock, validate existing history, append and fsync.
  Reads take a shared lock and never create the file. File identity uses no-follow
  opens and the repository's retained state descriptor when available. Symbolic
  links, hard links, malformed/truncated records and duplicate identities fail
  closed. Corrupt bytes are preserved for explicit recovery, never truncated.
- Idempotency identity includes course, session, trusted writer, request key and
  event kind. An exact retry returns the committed event; changed content under
  the same identity fails without modifying history.
- `get` exposes the course journal, `search` retrieves bounded matching history.
  UI history pages use journal sequence cursors. Recent tutor context includes
  24 entries with omission metadata, with search available for earlier history.
  These observations are untrusted conversational data, never source citations.
- `student_state.record` and `student_state.search` replace the private memory
  tools. `evidence.get`, `context.get`, `/api/v1/evidence` and the context-conflict
  read/write endpoints are removed. `GET/POST /api/v1/student-state` use the same
  service; the UI's Percorso page can append covered topics and self-reported
  difficulties. Journal sequence must never replace course sequence in browser
  commands, assessment commands or recall scheduling.
- Explicit `POST /api/v1/student-state/import` copies validated historical memory
  notes, assessment activity and old context statements. The Percorso page offers
  this import. Copies keep stable source-event references and are retry-safe.
  Assessment mutations and completed tutor turns reconcile committed assessment
  history, including activity left unmirrored by an interrupted prior request.
- Existing SQLite events, source bytes, artifact decisions, assessment attempts/
  grades and recall history remain intact. Historical context codecs and replay
  are retained solely for old archives; Cardine does not compose the old writer,
  context-conflict views or evidence estimator. Recall history stays separate.
- Back up the complete local repository, including this file. Export-v1 remains
  the historical SQLite event export; it is not a full student-journal backup.
  In multi-student hosted storage, one repository per authenticated student is
  required; this change does not introduce a shared multi-tenant journal.

## Verification

Offline tests cover source/session isolation, trusted origins, append-only bytes,
restart, exact/changed retries, concurrent writers, pagination, corrupt tails,
symlinks/hardlinks, historical note spoofing, UI/agent parity, assessment lifecycle
facts and retired routes. Run the full CONTRIBUTING.md verification contract.
