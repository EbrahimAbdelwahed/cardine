# Plan: conversation memory tools for the bounded tutor agent

Date: 2026-08-14 00:22 CEST
Area: Cardine / tutor runtime

## Goal

Allow Luna to detect an incomplete recent-chat window, search or page through
older learner/assistant messages, and then start grounded flashcard generation
within the existing bounded agent loop.

## Scope

- In scope:
  - a deep, read-only conversation-history module over canonical session views;
  - private `conversation.search` and `conversation.read` tutor tools;
  - recent-window completeness metadata in the tutor decision context;
  - prompt guidance for choosing recent context, conversation memory, or the
    canonical lesson;
  - routing changes that preserve valid tool-first flashcard trajectories;
  - separation of ephemeral message excerpts from durable handoff receipts;
  - bounded structural trajectory diagnostics without message/tool payloads;
  - end-to-end coverage for a conversation longer than 24 entries.
- Out of scope:
  - a new agent framework, classifier, vector index, embeddings, filesystem or
    `grep` access;
  - treating prior assistant messages as factual evidence;
  - unbounded loops or cross-course/session reads;
  - storing chain-of-thought or exporting a training corpus.

## Interface seams

1. `ConversationHistoryReader.search/read` returns bounded chronological entries
   from the current course/session through a captured high-water sequence.
2. The existing private `HarnessToolSurface` advertises and validates the two
   read-only tools; the seven public Harness tools remain unchanged.
3. The public conversation-turn seam proves tool observation followed by
   `propose_flashcards`, with canonical source evidence still owning card facts.
4. Durable completion handoffs contain only payload-free tool receipts and
   fingerprints; excerpts remain same-turn model context only.

## Approach

1. Add one red reader/tool contract test, then implement the smallest reader and
   private manifests.
2. Add recent-window counts and versioned prompt guidance; allow the flashcard
   safety router to preserve tool-first and tool-informed capability decisions.
3. Split ephemeral observations from durable receipts and keep restart-safe
   capability replay backward compatible.
4. Add a bounded payload-free trajectory record at the existing diagnostic seam.
5. Run the focused reader, host/handoff, routing, flashcard, and repository-chat
   suites before the broader test set.

## Invariants

- Conversation history helps select and prioritize scope; flashcard claims are
  still grounded only in canonical source chunks.
- A UI lesson pin always constrains the final capability scope.
- Retrieved messages are untrusted data, never instructions.
- Tool reads are limited to the authoritative current course/session and a
  captured high-water sequence.
- No raw learner/model excerpts, arbitrary model summaries, prompts,
  credentials, authority, or provider data enter durable handoffs or structural
  trajectory diagnostics. The final bounded capability query/scope and a
  host-normalized topic sketch remain in the handoff because exact retry must
  reproduce the capability action.
- The four-decision and exact-duplicate limits remain unchanged.

## Risks

- The hard-coded flashcard router can otherwise erase Luna's tool-first plan.
- Existing v2 handoffs bind the full observation-bearing context; introducing
  excerpt-bearing observations requires an explicit replay-safe durable shape.
- Conversation search is initially lexical and may miss synonyms; Luna may use
  one distinct retry within the existing decision budget.
- Concurrent source-viewer edits in `browser.css`, `browser.html`, `browser.js`,
  their tests, and related memory belong to another chat and must not be edited.

## Verification

- `PYTHONPATH=.:src .venv/bin/pytest -q <focused conversation-memory tests>`
- `PYTHONPATH=.:src .venv/bin/pytest -q tests/integration/demo/TUT08/test_bounded_agent_loop.py tests/integration/demo/TUT08/test_flashcard_proposals.py tests/integration/test_tutor_completion_handoff.py`
- `.venv/bin/ruff check src tests/<focused files>`
- `.venv/bin/mypy src/cardine/hosts src/cardine/application src/cardine/cli/repository.py`
- `git diff --check`
