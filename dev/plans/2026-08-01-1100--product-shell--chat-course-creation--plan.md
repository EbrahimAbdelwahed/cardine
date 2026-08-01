# Plan: chat-native course creation

Date: 2026-08-01 11:00
Area: product shell and repository UI

## Goal

Let the private owner create and immediately enter a new study course from the
chat workspace, without giving the model general repository write authority.

## Scope

- In scope:
  - A chat-native entry point and an Italian intent shortcut for creating a
    course.
  - A structured draft/confirmation card for course title, language, learning
    goal, stable course id, and initial session id.
  - One private, versioned command that creates the immutable course, starts
    its first session, and selects it with idempotent service calls.
  - Focused backend, browser-contract, and E2E coverage.
- Out of scope:
  - Parsing arbitrary course metadata through an LLM.
  - Model or public-demo authority to create courses.
  - Editing an immutable course profile after confirmation.

## Accepted seams

The user approved the chat-native creation flow. The seams under test are the
private `POST /api/v1/chat/course-creation` command and the browser composer
flow that opens/validates its confirmation card.

## Approach

1. Add a failing repository UI integration test for create + start + select.
2. Add a bounded command handler under the existing repository mutation lock.
3. Add the private chat draft and confirmation card, plus a typed Italian
   shortcut and command-search entry point.
4. Add browser assertions and run focused plus private E2E checks.

## Risks

- Course creation changes durable study state, so the UI must require a
  deliberate confirmation and preserve idempotency.
- A partial failure after course creation must be safely repeatable; the same
  profile/session command must reconcile through the canonical services.

## Verification

- Focused repository integration and browser asset tests.
- Private product E2E with local sockets enabled.
- Ruff, JavaScript syntax, and diff checks.
