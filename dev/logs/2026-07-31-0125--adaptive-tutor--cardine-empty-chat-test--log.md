# Log: Cardine empty-chat greeting test

Date: 2026-07-31 01:25 CEST
Area: adaptive tutor / product shell

## Summary

Created an isolated local repository with the same two Cardine source files,
the same `openai-compatible-http`/`deepseek-chat` configuration, one course,
and an `empty-chat` session containing zero interactions.

The exact repository UI command for `ciao` appended the learner interaction
(sequence 4 to 5) but did not produce a validated tutor presentation. The
result was a safe 503 (`repository runtime is unavailable`). A direct
eight-token `deepseek-chat` adapter smoke from the same isolated execution
environment returned `ModelError(code=unavailable, "model endpoint is
unavailable")`.

This is not evidence of either greeting classification branch. The active
server on port 8765 has a provider-capable process environment that is not
available to this isolated execution. The empty-session classification test
must be run from that same provider-capable host environment, or after a new
clean server is launched from a terminal that supplies the DeepSeek credential
and network access.

## Files Changed

- `dev/logs/2026-07-31-0125--adaptive-tutor--cardine-empty-chat-test--log.md`: partial test evidence only.

## Verification

- Clean session bootstrap: sequence 4, zero timeline entries.
- Direct `RepositoryUiApplication.post(... content=ciao)`: safe failure; the
  temporary session timeline contains only the learner message at sequence 5.
- Direct adapter smoke: `ModelError` with code `unavailable`; no assistant
  output or grounded-answer run was persisted.

## Notes

- The temporary repository is retained at
  `/private/tmp/cardine-empty-session.6S7MuE` for a provider-capable rerun.
- No user repository state was changed by this isolated test.
