# Plan: chat-attached lesson pin

Date: 2026-08-13 13:52 CEST
Area: Cardine / Wave A / browser + tutor conversation

## Goal

The home screen lets a learner search a lesson and pin it, but the pin only fed
two dedicated forms (`/api/v1/lessons/ask`, `/api/v1/lessons/flashcards`). Those
forms were removed in the UI slice that precedes this plan: a pinned lesson is
now presented as a source attached to the chat composer, and questions and
flashcard requests are typed in the chat like everything else.

The pin must therefore become a real turn input. A chat turn sent with an
attached lesson has to retrieve evidence only from that lesson, exactly as the
removed `/api/v1/lessons/ask` path did, instead of relying on the tutor
inferring "lezione 1" from the learner's wording.

## Scope

- In scope:
  - `src/cardine/demo/browser.js`: send the attached pin with
    `/api/v1/session/turns` and with continuation responses.
  - `src/cardine/demo/ui_application.py`: accept an optional `lesson_pin` in the
    turn payload, decode it through the existing pin decoder, and pass it to the
    tutor conversation.
  - `src/cardine/cli/repository.py`: `tutor_conversation(..., lesson_pin=...)`
    and explicit-pin precedence inside `_RepositoryTutorGateway._gateway`.
  - Regression tests for explicit-pin precedence and cross-course rejection.
- Out of scope:
  - Restoring the removed per-lesson question/flashcard forms.
  - Any change to `/api/v1/lessons/search|select|ask|flashcards` semantics.
  - Persisting the attachment across reloads; the pin stays browser-local
    session state, as it already was.
  - PageIndex, FTS, artifact decision, or recall behavior.

## Approach

1. `_RepositoryTutorGateway` takes an optional explicit `lesson_pin`. In
   `_gateway`, an explicit pin wins over `resolve_lesson_scope(course_id, query)`;
   the pin is validated for course ownership before use, mirroring the checks
   already in `grounding_service`. The existing `_StructuralRangeRetrieval`
   scoping and `retrieval_limit = 100` are reused unchanged.
2. `LocalRepository.tutor_conversation` grows a keyword-only `lesson_pin` and
   forwards it to the gateway. An injected `self.conversation` (test seam) keeps
   precedence and ignores the pin, as it already ignores the session id.
3. `_command` in `ui_application.py` grows an optional payload-key set so the
   strict shape check can admit `lesson_pin` next to `content`/`response`
   without loosening any other command.
4. The turn handler decodes the pin with `_lesson_pin_payload_from_json` and
   passes it to `tutor_conversation`. A malformed pin is a 400 through the
   existing `UiRequestError` path; a pin from another course is rejected by the
   repository check and surfaces as a 400.
5. The browser adds `lesson_pin` to the turn payload only when a pin is
   attached, so an unpinned chat sends the exact payload it sends today.

## Risks

- The turn command shape is a public browser/server contract. Mitigation: the
  new key is optional and additive, and the strict key check stays strict.
- Explicit precedence changes behavior for a learner who pins lesson 1 and then
  writes "lezione 2". The pin wins, which is the point of an explicit
  attachment, and the attachment is visible and removable in the UI.
- A stale pin (source superseded) must fail loudly rather than silently answer
  from the whole course. `validate_lesson_pin` already raises for that.
- The `_gateway` path runs per capability start; the added validation is a
  repository read, no provider call.

## Verification

- `python -m pytest tests/unit/cli/test_automatic_lesson_grounding.py tests/unit/cli/test_pinned_lesson_grounding.py tests/unit/cli/test_selected_lesson_flashcards.py`
- `python -m pytest tests/integration/demo/TUT08/test_wave_a_study_journey.py`
- `node --check src/cardine/demo/browser.js`
- `python -m ruff check src/cardine`
- Manual: pin a lesson on the live server at `http://127.0.0.1:8765/`, ask in
  chat, confirm the answer cites only the pinned revision.
