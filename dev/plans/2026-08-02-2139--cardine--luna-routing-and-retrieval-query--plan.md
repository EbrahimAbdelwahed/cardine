# Plan: Luna routing and retrieval query contract

Date: 2026-08-02 21:39 CEST
Area: Cardine tutor routing and grounded retrieval

## Goal

Let Luna reliably choose the correct conversational decision, capability, or
repository tool from an explicit dynamic catalog, and let grounded
capabilities search course material with a concise relevance query instead of
requiring every learner-message token to occur in one chunk.

## Scope

- In scope:
  - version the Cardine tutor-decision prompt with explicit routing and query rules;
  - advertise purpose and usage guidance for available capabilities and tools;
  - keep the catalog derived from trusted manifests rather than a drifting static list;
  - replace literal-all-token retrieval behavior with a safe relevance-oriented query contract;
  - add routing and retrieval eval/regression fixtures, including conversational turns;
  - determine which generic contract/retrieval changes must also land in Harness.
- Out of scope:
  - training or deploying a separate intent classifier;
  - vector retrieval or new dependencies;
  - weakening grounding, citation resolution, authority, or structured-output validation.

## Approach

1. Pin the current failure with eval cases for greetings, generic study guidance,
   explicit source explanation, assessment, and repository mutation requests.
2. Extend trusted advertised descriptors with concise `purpose`, `when_to_use`,
   `when_not_to_use`, and input-field guidance where necessary.
3. Compose those descriptors into the decision prompt context and version the
   prompt; tell Luna to emit short content-keyword retrieval queries rather than
   copied learner utterances.
4. Change lexical retrieval so safe token quoting remains, while normal natural
   language does not become an impossible all-token conjunction.
5. Add focused end-to-end tests proving conversational turns do not start a
   grounded capability and source requests retrieve existing material.
6. Apply Harness-owned descriptor/retrieval contract changes to the independent
   Harness repository after Cardine behavior is approved.

## Risks

- Descriptor changes affect serialized host-context fingerprints and replay
  contracts and therefore require explicit schema/version handling.
- Broad OR retrieval can return irrelevant chunks; ranking and a bounded match
  policy must avoid turning any weak token match into “sufficient” evidence.
- Prompt-only routing remains probabilistic; the eval dataset must establish an
  acceptable threshold before deciding whether a classifier is warranted.
- Cardine and Harness are independent repositories; shared changes must be
  applied and verified separately.

## Verification

- Focused tutor-decision adapter and host-context contract tests.
- Retrieval unit tests for concise, verbose, stop-word-heavy, title, and no-match queries.
- Repository-backed chat tests for `Tutto bene?` and a real indexed-source explanation.
- Routing eval confusion matrix over the new labeled fixture set.
- Ruff, mypy on changed files, full pytest where practical, and `git diff --check`.
