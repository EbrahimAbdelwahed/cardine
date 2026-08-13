# Plan: Cardine rapid usability fixes 1-4

Date: 2026-08-13 12:38 CEST
Area: cardine retrieval, background indexing, tutor routing, flashcards UI

## Goal

Make Cardine usable after four targeted fixes: ordinary chat uses structural
lesson scope, source upload returns after canonical save with visible derived-
index progress, natural study requests reliably execute the advertised
capability instead of promising future work, and flashcard generation is visibly
in progress and ends with persisted proposals discoverable in the UI.

## Scope

- In scope:
  - Automatically resolve one unambiguous lesson reference in ordinary chat to
    a complete canonical source pin and expose every complete canonical chunk in
    that bounded section to grounding.
  - Preserve PageIndex as navigation-only and preserve canonical chunks as the
    sole evidence/citation authority.
  - Decouple canonical source admission from discardable FTS/PageIndex work.
  - Persist and expose `queued`, `indexing`, `ready`, `degraded`, and `failed`
    derived-index state, with restart reconciliation and browser polling.
  - Harden versioned capability/tool routing with action intent, positive and
    negative examples, output expectations, and no-future-promise behavior.
  - Show flashcard work in progress and make completed proposal batches visible
    from Proposte.
- Out of scope:
  - Chat copy/edit controls and message-history mutation.
  - New dependencies, distributed workers, generic job framework, embeddings,
    vector retrieval, PageIndex-authored evidence, or unrelated UI redesign.

## Public Test Seams

1. Repository-backed chat: `Lezione 1` selects a unique structural range and
   grounding reads only all complete canonical chunks inside it.
2. Repository browser API: upload response reports canonical save plus queued
   derived work before a rebuild completes; status survives reopening and moves
   through terminal state via an explicit bounded reconcile command.
3. Tutor decision port and repository chat: natural `cards`/`flashcards`
   requests start and persist `propose_flashcards`; meta-questions do not.
4. Browser contract: indexing and flashcard statuses are visible, polling is
   bounded, and completion exposes a direct route to Proposte.

## Approach

1. Add one red behavior test, then the minimal routing fix.
2. Add a red repository chat test for automatic structural lesson scope, then
   bind the existing lesson-selection and pinned canonical retrieval seams into
   the explain gateway.
3. Add a small Cardine-owned derived-index operation record over the existing
   namespaced run store; admission queues it and returns immediately. A bounded
   reconcile endpoint performs atomic FTS rebuild plus existing PageIndex work.
4. Add browser progress polling/status presentation and flashcard completion
   affordance without changing artifact/recall semantics.
5. Run focused tests after every vertical slice, then the Wave A journey,
   browser contract, Ruff, mypy, and diff review.

## Risks

- The recovered checkout contains user-owned uncommitted Wave A fixes; edits
  must merge with them and never reset or overwrite them.
- A selected lesson may exceed the retrieval contract's 100-item ceiling;
  fail truthfully rather than silently truncate canonical evidence.
- Background reconciliation must not hold the UI mutation lock while doing the
  expensive derived rebuild; state transitions remain durable and idempotent.
- Ambiguous lesson matches must remain explicit and must never be auto-selected.
- Generated flashcards remain proposals; acceptance and recall enrollment stay
  HUMAN-only and unchanged.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/hosts/test_flashcard_routing_natural_language.py`
- Focused repository structural-chat and indexing API tests added by this plan.
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/integration/demo/TUT08/test_repository_backed_chat.py tests/integration/demo/TUT08/test_flashcard_proposals.py tests/integration/demo/TUT08/test_wave_a_study_journey.py`
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/e2e/test_cardine_browser_contract.py`
- `.venv/bin/ruff check` on changed source and test paths.
- Focused strict mypy on changed Python paths.
- `node --check src/cardine/demo/browser.js`
- `git diff --check`
