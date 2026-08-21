# Log: minimal study memory

Date: 2026-08-14 16:49 CEST
Area: Cardine / tutor memory

## Summary

Implemented a minimal durable archive for topics covered and attributable
learner signals. The archive reuses canonical session notes with a strict
`study-memory@1` codec, course-wide bounded search, actor/principal validation,
and host-derived origin sequences.

Luna now receives two private tools:

- `study_memory.record` records one current-turn learner observation;
- `study_memory.search` retrieves at most eight prior course observations.

Completed capabilities queue a `topic_covered` entry and settle it only after
the tutor presentation is canonical. Failed capabilities record nothing. The
structured notes are hidden from the ordinary tutor prompt and browser chat;
they enter model context only through explicit memory search.

The session view now accepts a verified continuation summary as a canonical
history prefix. This preserves the compact prior summary while a later learner
turn is being appended, and prevents structured notes from making the next
turn unreadable.

## Files Changed

- `src/cardine/application/study_memory.py`: strict codec, writer, bounded reader,
  origin lookup, and spoof protection.
- `src/cardine/application/tool_surface.py`: private record/search manifests,
  authority, receipts, filters, and idempotency conflict mapping.
- `src/cardine/cli/repository.py`: composition, stable agent-write identity,
  activity labels, completed-topic queue, and post-presentation settlement.
- `src/cardine/hosts/context.py`: removes structured memory from the ordinary
  provider snapshot.
- `src/cardine/application/grounding_ask.py`: removes only validated private
  memory notes from the public grounded-question continuation context.
- `src/cardine/hosts/source_grounding.py`: preserves explicit memory tool calls.
- `src/cardine/hosts/runner.py`: strips memory prose from durable handoff values.
- `src/study_agent/prompts/tutor_decision_v1.py`: prompt `1.6.0` guidance with
  positive/negative examples and explicit anti-mastery constraints.
- `src/cardine/demo/ui_application.py`: settles completed memory and hides its
  internal notes from chat.
- `src/study_agent/domain/session.py`: permits a verified continuation summary
  to describe a strict prefix of newer canonical interaction history.
- focused unit and integration tests under `tests/`.

## Verification

- `PYTHONPATH=.:src .venv/bin/pytest -q tests/unit/sessions tests/unit/application/test_study_memory.py tests/unit/application/test_harness_tool_surface.py tests/unit/hosts/test_source_grounding.py tests/unit/adapters/model/test_tutor_decision.py tests/unit/hosts/test_tutor_host_runner.py tests/integration/test_tutor_host_runner.py tests/integration/demo/TUT08/test_study_memory_tools.py tests/integration/demo/TUT08/test_conversation_memory_tools.py tests/architecture/test_flashcard_capability_boundaries.py::test_exact_seven_public_study_tools_remain_unchanged`: 132 passed.
- targeted next-turn flashcard, failed-capability, prompt-privacy, and memory tests:
  6 passed.
- final focused regression set (sessions, memory, harness tools, grounding,
  tutor host, conversation memory, deictic flashcards, public-tool boundary):
  154 passed.
- `.venv/bin/ruff check` on changed production/tests: passed.
- `git diff --check`: passed.
- full `PYTHONPATH=.:src .venv/bin/pytest -q`: 2359 passed, 31 skipped,
  27 failed. Failures are outside this slice and reproduce current dirty-tree or
  sandbox issues: socket binding, AnyDoc worker protocol, stale CLI/golden/UI
  contract tests, one pre-existing activity retry receipt mismatch, and a
  pre-existing host-context expectation superseded by conversation-window work.
- project mypy invocation remains blocked by duplicate module discovery in the
  current checkout; explicit-package checking reports two pre-existing errors
  in `diagnostics/turn_activity.py` and no remaining study-memory type error.
- independent Terra semantic review: no HIGH/MEDIUM findings remained after
  origin validation, validated-ID redaction, grounded-context redaction, and
  bounded long-topic normalization were added.
- live bootstrap: fixed two syntax/import defects already present in the
  concurrent generated-material work, then started the local-owner server;
  `GET http://127.0.0.1:8765/health` returned HTTP 200.

## Notes

- The seven public StudyTools are unchanged; both memory tools remain in the
  private Cardine harness surface.
- No mastery, readiness, percentage, confidence score, or learner biography is
  inferred or stored.
- The shared worktree contains unrelated concurrent edits; this change was not
  committed as a bundle.
- The live server is running from this worktree at `http://127.0.0.1:8765/`.
