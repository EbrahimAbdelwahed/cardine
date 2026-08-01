# Log: Cardine chat greeting diagnosis

Date: 2026-07-31 00:58 CEST
Area: adaptive tutor / product shell

## Summary

Diagnosed the live Cardine chat at `127.0.0.1:8765` after a learner reported
that greetings do not receive a real model-generated reply.

A real `POST /api/v1/session/turns` with content `ciao` committed successfully
(course sequence 34 to 37), but returned the exact deterministic text:

`The supplied sources do not contain enough evidence to answer.`

The persisted run checkpoint proves the final answer followed the
`grounded_answer_flow` only through `load_context`, `search_sources`, and
`check_evidence`. Retrieval returned no evidence and
`EvidenceSufficiencyValidator` terminated the flow before any model step.
The displayed assistant text therefore comes from `INSUFFICIENT_NOTE`, not
from a model completion.

The live server was started from this worktree with
`/private/tmp/cardine-live-repository`. Its `study-agent.json` is still
configured with `openai-compatible-http`, `deepseek-chat`, and
`DEEPSEEK_API_KEY`; it is not using the later `openai-gpt-5.6-luna` preset.

## Files Changed

- `dev/logs/2026-07-31-0058--adaptive-tutor--cardine-chat-greeting-diagnosis--log.md`: records diagnosis evidence only.

## Verification

- `curl -X POST http://127.0.0.1:8765/api/v1/session/turns ... content=ciao`: 200; canonical learner turn and deterministic insufficient-evidence answer persisted.
- `sqlite3 state/runs.sqlite3 ... run-sha256:46b... | jq`: checkpoint contains empty evidence, `evidence_sufficiency` termination, and no model trace.
- `study-agent.json` inspection: active adapter is `openai-compatible-http` / `deepseek-chat`.

## Notes

- The model-backed tutor decision may still precede capability dispatch, but it
  does not own the final speech after the selected grounded capability returns
  insufficient evidence.
- A greeting needs an explicit conversational path that selects/preserves
  `assistant_message` instead of dispatching to source-grounded answering.
- Switching this live repository to the Luna preset additionally requires an
  `OPENAI_API_KEY` and a server restart; it alone does not prove that greeting
  routing has been corrected.
