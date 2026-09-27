# Recovery CI alignment and pending acceptance feedback

The live Cardine checkout is Desktop/Dev/cardine on codex/cardine-wave-a-recovery.
Main 1163005 is included through c54cc5a; recovery and the earlier Luna provider
schema/routing fixes remain intact. PR #5 contains the full unmerged Wave A
lineage and stays draft while its ownership gate is unresolved.

Browser decision feedback was committed in ebdb65f. Accepted/rejected cards leave
the pending list, remain available in collapsed decision history, and produce
an explicit success message after the refreshed route. The Node fixture invokes
the production rendering and command functions; 13 browser contract tests pass.

The restart receipt tests in db7291a now compare all durable payload fields while
excluding only process-local activity diagnostics. Material lineage tests select
by variant rather than revision sort order. Capability decisions and accessible
iframe naming are asserted against their current contracts.

This change aligns recovery typing with immutable JSON/protocol contracts,
retaining canonical assertions and all mypy checks. Native AnyDoc's mixed-PDF
case now has the same qualified-worker guard as its siblings; the dedicated
macOS CI job still executes it. Existing owner files remain outside the commit.

Verification:
- Ruff passes. Full mypy passes on 618 source files.
- Wheel and sdist build successfully; verify_cardine_wheel passes.
- Complete pytest before the final Mapping correction: 2456 passed, 13 skipped,
  with the frozen ownership audit and that corrected test failing.
- The corrected harness-tool-surface file passes all 3 tests.
- A subsequent complete run excluding only the blocked audit: 2454 passed,
  13 skipped, one intermittent SQLite descriptor identity failure in native PDF
  restart. That exact case passes in isolation; 56 affected host/diagnostic/import
  contracts also pass. The instability remains visible, not hidden by retries.

The frozen CA-01/CA-02 audit rejects 46 committed recovery paths. A read-only
exact-byte overlay proposal is available in the coordinating chat's outputs.
Automatic approval review rejected writing that wider baseline; it has not been
applied. Keep the audit and historical ledgers unchanged until specific owner
approval. CI is not claimed green before that decision and current-head results.

The local runtime had two confirmed setup blockers: its stale console script
imported the removed study_agent.demo.browser path, and the OpenAI SDK was absent.
Reinstalling the current checkout editable with its declared openai extra repaired
both. cardine-shell-web --help and cardine --json describe now work; SDK 2.54.0 is
installed. The 49 Luna/router/runtime-settings offline tests pass.

The documented durable store remains at Desktop/Med/Lezioni/Audio_to_Sbobina/
cardine-wave-a-live, configured for openai-gpt-5.6-luna. The updated checkout serves
it on http://127.0.0.1:8765 with course-wave-a/session-live. Health and Settings
return HTTP 200 in local_repository mode. credential_configured is false; the
owner must enter the key through browser Settings before a live provider-backed
generation can be verified. No key, model call or human artifact decision was
made by these setup checks. The unrelated node preview on port 3100 belongs to
sbobby-web and was left untouched.
