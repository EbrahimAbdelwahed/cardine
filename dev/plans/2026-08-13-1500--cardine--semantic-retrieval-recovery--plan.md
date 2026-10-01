# Plan: semantic retrieval recovery

Date: 2026-08-13 15:00 CEST
Area: Cardine / tutor retrieval

## Goal

Make an unpinned explanation recover from one empty lexical search without a
classifier or a general agent loop.

## Scope

- In scope:
  - one Luna structured-output request after the initial FTS query returns no evidence;
  - at most three bounded alternative lexical queries;
  - reuse of the first alternative that returns canonical evidence;
  - regression coverage through `LocalRepository.tutor_conversation`.
- Out of scope:
  - retries for mutations, flashcards, explicit lesson pins, or successful searches;
  - a general ReAct loop;
  - Tool Chips and other browser work.

## Approach

1. Preflight the trusted capability query against the same source policy used by
   `source.search`.
2. On `insufficient` only, give Luna the learner target, failed query, and a
   bounded vocabulary of canonical source titles and headings.
3. Validate one structured response containing one to three distinct queries,
   try them in order, and execute the capability with the first query that hits.
4. If recovery fails or is invalid, preserve the existing insufficient-evidence path.

## Risks

- Extra model latency occurs only after a genuinely empty search.
- Source vocabulary is bounded and contains navigation labels only, never evidence text.
- Explicit lesson attachments and deterministic lesson aliases must retain precedence.

## Verification

- Focused semantic recovery integration tests, including the no-retry success path.
- Existing lesson routing, attached-pin, repository chat, and browser suites.
- Ruff, mypy, JavaScript syntax, and `git diff --check`.
