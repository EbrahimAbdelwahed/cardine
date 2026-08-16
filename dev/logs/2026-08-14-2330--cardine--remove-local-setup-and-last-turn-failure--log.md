# Log: Remove local setup and diagnose the last live turn

Date: 2026-08-14 23:30
Area: cardine

## Summary

Replaced the supervised local-preview job with the same live Cardine command minus
`--local-owner-setup`. The server now exposes the loopback-only local repository UI
without a local password/setup gate.

The last learner turn at course sequence 191 did not fail during intent routing.
The host durably selected `propose_flashcards@1` with the requested Italian scope and
entered the lesson flashcard worker. The worker stopped while resolving its first
bundle, and the UI recorded the safe failure category `tutor_unavailable`, which is
the model adapter classification for a provider 5xx or transport failure.

## Files Changed

- `dev/logs/2026-08-14-2330--cardine--remove-local-setup-and-last-turn-failure--log.md`: records the live operational change and evidence-backed diagnosis.

## Verification

- `launchctl print gui/501/com.cardine.local-preview`: running PID 79054; arguments do not include `--local-owner-setup`.
- `curl -s -i http://127.0.0.1:8765/health`: HTTP 200 with `mode: local_repository`.
- SQLite event/run inspection: sequence 191 is the learner interaction; the associated schema-v3 handoff selected `propose_flashcards@1` and became stale after the lesson worker stopped at bundle 0 with the UI category `tutor_unavailable`.

## Notes

- The submitted launchd job does not contain `OPENAI_API_KEY`, and the current shell environment does not provide it. Removing setup also removes the browser path used to supply a runtime-only key. Provider-backed turns therefore require the job to receive `OPENAI_API_KEY` from a trusted environment source; no secret was copied or persisted during this change.
