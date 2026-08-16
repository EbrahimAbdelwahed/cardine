# Plan: Lesson routing usability

Date: 2026-08-13 14:05 CEST
Area: Cardine / tutor routing / structural retrieval

## Goal

Make the first lesson-study turn produce a grounded answer without introducing
a classifier or a general agent loop.

## Scope

- In scope:
  - canonical structural evidence construction;
  - deterministic equivalence between natural lesson references and converted
    headings such as `L01_04/03/2025`;
  - immediate grounding for an explicit lesson-study question;
  - one bounded host correction when the decision model returns a promise
    instead of the advertised capability.
- Out of scope:
  - semantic search retries and a general ReAct loop;
  - Tool Chips or any browser/UI work;
  - changes to PageIndex ownership or indexing limits.

## Approach

1. Pin the live structural and chat journeys at their existing public seams.
2. Repair structural citations and add narrow lesson-reference normalization.
3. Make an explicit lesson-study question fail closed into `explain_concept`;
   preserve genuine social conversation.
4. Let Luna decide first, then replace a mismatched capability promise once at
   the closed host boundary. Do not add a second provider call without a
   versioned decision-feedback contract.
5. Run focused routing/retrieval tests, then the repository-backed chat suite.

## Risks

- Broad language regexes would recreate the original routing brittleness.
  Structural-reference rules must stay separate from general intent routing.
- A decision correction must never duplicate a repository mutation or exceed
  the existing attempt budget.
- Concurrent lesson-attachment UI work touches the repository composition;
  this fix must not overwrite or redesign that work.

## Verification

- Exact live wording returns grounded content in one public chat turn.
- `Lezione 1` resolves an `L01`-style converted heading and excludes the next
  lesson.
- Structural evidence satisfies canonical citation validation.
- Social conversation mentioning a lesson remains conversational.
- Focused pytest, Ruff, JavaScript untouched, and diff checks pass.
