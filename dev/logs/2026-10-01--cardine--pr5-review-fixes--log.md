# Log: PR #5 automatic review fixes

Date: 2026-10-01
Area: Cardine / flashcard routing and material validation

Applied three current automatic review findings at PR #5 head
`8a4334350fcef0da4cf43027f2288256cd52b016`.

- Memory-scoped flashcard capability arguments now use a host-derived topic
  sketch from successful conversation search/read observations whose result is
  bound to the tutor snapshot high-water sequence and whose entries match the
  conversation-history schema and bounds. Missing or invalid history asks for
  an explicit topic. Conversation text remains scope context only; canonical
  source chunks still supply flashcard facts.
- Emphasis validation now checks set inclusion so every source marker must
  remain in each output.
- Uncertainty and limitation validation recognizes common Italian forms
  `incertezza`, `ambiguo/a`, and `limitazione` in generated content and
  limitation statements.
- Added behavior tests and refreshed the flashcard router's hash in the
  recovery custody overlay. Historical ownership records and audit rules were
  unchanged.

Verification: 34 focused unit and conversation-memory integration tests passed;
Ruff and strict focused mypy passed; ownership audit passed with 322 rows;
`git diff --check` passed. No network or model-backed test ran.

The changes are committed locally as a review-fix commit from an isolated,
managed worktree. The existing PR branch is checked out elsewhere, so the
coordinator must integrate this commit into `codex/cardine-wave-a-recovery`
before the single authorized publish/review step.

Further current-head review repairs preserve explicit topics when history is auxiliary, reject history-only starts without validated observations, persist terminal failures for unreadable ancestry checkpoints, and reject generated admission from canonically retired roots. Forty-six focused regressions, full mypy and exact custody pass; current submitted CI/review remain required.
