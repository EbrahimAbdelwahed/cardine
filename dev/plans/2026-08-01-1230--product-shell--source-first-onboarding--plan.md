# Plan: source-first onboarding and responsive Cardine chat

Date: 2026-08-01 12:30
Area: product shell

## Goal

Make a known course start with material collection and a small guided setup,
then use the collected material to offer a first study topic. Repair the
source-upload gap and the interaction polish reported during product testing.

## Scope

- In scope: repository-backed text/Markdown upload, source-first UI, optimistic
  chat send, sidebar action placement/alignment, and the continuation dock.
- Out of scope: document/PDF extraction, a new course-profile mutation contract,
  and changes to existing canonical study behaviour.

## Approach

1. Add a bounded private source-ingestion command that uses the existing
   canonical ingestion service and reports unsupported files clearly.
2. Replace the generic empty-course home state with guided source, objective,
   and source-derived-topic steps; the objective is handed to the real tutor
   conversation after source collection.
3. Make chat sends optimistic and form-local while retaining idempotent command
   semantics, then refine the persistent dock and sidebar affordances.
4. Add focused unit/transport coverage and run the relevant browser journey.

## Risks

- The existing ingestion contract only supports UTF-8 `.txt` and `.md`; PDF and
  binary formats must be explicitly unavailable rather than silently accepted.
- Existing course metadata is immutable, so setup intent is recorded through
  the normal tutor conversation instead of inventing a new profile mutation.

## Verification

- pytest -q tests/unit/demo/test_ui_application.py tests/unit/demo/test_browser.py
- pytest -q tests/e2e/test_cardine_repository_browser_journey.py
