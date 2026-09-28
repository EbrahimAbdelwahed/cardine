# Failed-turn observability

The owner requested operational trace improvements after a saved learner turn
returned `503 tutor_unavailable` with no diagnostic trace. The old store filtered
out turns without a validated decision. The regression test reproduced that
loss before the change.

`TurnTraceStore` remains the single diagnostic store. Schema 5 retains every
captured turn, including failures before routing, and keeps the existing typed
decision trajectory. It adds bounded operations for the application turn, host
decision attempts, model decisions, provider HTTP transport, and capability
start/resume. Exact request retries retain one correlation with distinct attempt
numbers. The store is process-local: 24 turns, four decisions and 32 operations
per turn, with an explicit omitted-operation count.

The Luna transport delegates to the existing transport and adapter. It observes
HTTP status and safe exception categories before the portable model error loses
transport detail; it does not implement another provider or error mapper.
Operational spans also carry elapsed time, a closed outcome/error vocabulary,
and an owned module/function/line for local errors. No exception messages,
stack text, local variables, URLs, headers, keys, prompts, learner/model text,
source data, or provider payloads enter the trace. Browser diagnostics render
the same records. No canonical study writes or provider spend are introduced.

This owner-requested extension supersedes ADR-0001's narrower restriction on
operational phases in diagnostic traces. UI activity and its titles/progress
remain in `TurnActivityStore`; they are not duplicated into diagnostics.

The live preview requires a process restart to load Python changes. Its API key
is process-local and must be re-entered afterward; historical failed traces
cannot be reconstructed after a restart.

The approved recovery custody overlay still freezes the previous exact bytes
of six modified Cardine files. This change does not edit or bypass that gate.
The patch needs a separately approved custody update before CI can be green.

Verification:
- The pre-fix regression failed because a failed decisionless turn disappeared.
- Full offline pytest before committing: 2474 passed, 13 skipped. Clean-archive
  custody tests in that run inspected the previous committed HEAD; this is not
  evidence that the changed six bytes pass the custody gate.
- Final affected diagnostic/adapter/chat/browser tests: 80 passed, including
  TLS/DNS/timeout classification, HTTP status, privacy, retention, concurrent
  stores, original exception preservation, semantic failure, and retry rendering.
- Ruff passes; mypy passes on 620 files. Wheel/sdist build and both artifact
  verifications pass.
- Live preview restarted with schema 5. Canonical session remained at sequence
  252. No provider generation or artifact decision was performed by this patch.

The owner's subsequent live retry selected `propose_flashcards` after a successful
HTTP 200 decision. Three generation HTTP calls returned 400 and the capability
terminated after about 107 seconds (129 seconds for the whole turn). This confirms
the failure moved beyond routing and is a rejected generation request, not an
unavailable provider. The trace does not contain its raw provider response.
Follow-up instrumentation keeps the existing portable error code on every Luna
generation call, and reports `terminated` outcomes as failed rather than completed.
Final follow-up verification: 82 affected tests passed; Ruff, mypy and rebuilt
wheel/sdist artifact verification passed. The sanitized live trace was preserved
outside the repository before restarting the preview with this follow-up.
