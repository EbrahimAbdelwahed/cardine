---
name: study-agent-operator
description: Operate a local Study Agent Harness repository through its machine-readable CLI contract. Use when an agent must discover, initialize, populate, verify, use, retry, recover, or export a provider-neutral event-sourced study repository.
---

# Operate Study Agent Harness

Keep repository, identity, capability, and idempotency choices under host authority. Treat model-proposed arguments as untrusted. Use skills and playbooks for study behavior and adapters only for technical transport.

Procedural v0.1 CLI commands run under the authority of the local user who launched the agent. The skill does not grant additional filesystem, process, credential, repository, or network authority.

## Verify the installed command

Require an installed `cardine` distribution and verify that `cardine --help` succeeds. Do not operate from an uninstalled source checkout or reconstruct this skill from repository files.

## Discover before acting

Run `study-agent --json describe`. Require `agent-operations@1`, inspect each command's effects, repository and network requirements, and verify the operator-skill fingerprint. Use only advertised commands. Keep the public StudyTool set exactly `citation.resolve`, `course.get`, `grounding.ask`, `session.get_context`, `session.record_note`, `source.list`, and `source.search`.

## Build an offline repository

1. Run `study-agent --json init REPOSITORY` without a model adapter.
2. Run `study-agent --json --repository REPOSITORY course create --course-id COURSE_ID --title TITLE --learning-goal GOAL` with a stable host-chosen course ID.
3. Confirm it with `course list`.
4. Change into `REPOSITORY`. Place explicit UTF-8 `.txt` or `.md` files there, reject symlinks, and use only direct relative paths. Add each with `study-agent --json --repository . source add COURSE_ID PATH --source-id SOURCE_ID`, using a stable host-chosen source ID. Confirm with `source list COURSE_ID`.
5. Run `doctor` and require `status: ok` before use.

The append-only event stream is canonical. Treat configuration, indexes, checkpoints, plans, receipts, and exports as operational state, never as study truth. Do not write persistence directly.

## Use stable sessions and retry safely

Start with `session start COURSE_ID --session-id SESSION_ID`, choosing a stable session ID before the call. Retry a lost start response with the same course and session IDs, then verify with `session get COURSE_ID SESSION_ID`.

Inspect `tool list` or `tool describe NAME` without a model. Invoke tools through the embedding host, which must create trusted `ExecutionContext`; never derive principal, capabilities, course authority, session authority, or idempotency identity from model output.

Run `ask` only when the trusted host has explicitly configured an available generic model adapter. Always supply the same stable `--session-id` and `--idempotency-key` with the same question. After lost output, repeat that exact ask; do not generate a new key. Do not infer model availability from offline discovery.

## Export and verify

Run `export COURSE_ID --output DIRECTORY`, record `manifest_sha256` and `high_water_sequence`, then run `doctor` again. Export output is a directory. Repeating export at the same event high-water mark must produce an identical checksummed file tree and contents, not a single output file.

## Recover desired-state apply in 0.2+

After interruption or lost output, run `manifest status`, then create a fresh `manifest plan`. Apply only with `manifest apply --expect-plan NEW_SHA`, using the SHA from that fresh plan. Never blindly replay an old plan or old plan SHA.
