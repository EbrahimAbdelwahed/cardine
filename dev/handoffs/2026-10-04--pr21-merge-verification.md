# PR 21 merge verification and local rollout

The owner requested merging all open PRs without further review cycles and
updating port 8765. PR 20 is merged at 2912344; PR 18, the original dependency,
and PR 19 are also merged. PR 21 now targets main and integrates that exact base.

Both development-memory additions and the independent progress/tutor-context
custody overlays are preserved. The progress overlay binds the automatically
combined browser code. Historical manifest digest constants now match the
already-integrated main manifests from PRs 18/19; those manifest files are not
rewritten by this continuation. Ruff, strict mypy (675 files), the 322-row audit
and diff checks pass. Integrated browser/product regressions and current-head
CI must pass before merging.

No new semantic review is requested. Latest automatic P2 comments 4177855163
and 4177855172 on bcfb8ae remain unresolved: historical transcript validation is
repeated on every status poll, and retryable audio wrapper failures can retain
the child's active-stage label. These are retained and reported to the owner,
along with the two PR 20 findings recorded in its merge handoff. The explicit
owner merge instruction authorizes proceeding without another review round.

The local instance uses the dedicated merge-open-prs-8901/cardine checkout on
main, the existing cardine-wave-a-live repository, course-wave-a and session-live.
After current-head CI and merge, update that clean checkout by fast-forward,
sync frozen dependencies and use its existing merged-main-server/launch.py.
Before stopping, verify the recorded PID/command and no active material jobs;
retain verified SQLite backups plus configuration, student journal and launcher
metadata under the existing preview run directory. Verify health, configuration
identity and unchanged canonical event contents after restart. Runtime OpenAI
credentials must be re-entered through Settings; inherited OpenRouter/Groq
bindings are available in the launcher environment without disclosure.

The runtime version and backup path are recorded by the existing launcher and
reported to the owner after activation. No paid provider test or canonical
study-data write is part of the rollout verification.
