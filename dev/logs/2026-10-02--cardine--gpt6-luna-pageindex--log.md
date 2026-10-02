# GPT-6 Luna and large-document PageIndex

Owner request, 2026-10-02 CEST: explain query/target, switch the local tutor
to GPT-6 Luna, activate PageIndex; reuse the existing OpenAI credential.

The inspected instance runs `main-preview` at `18f941a` against the existing
local study store. `query` is lexical search input; `target` is the task the
explanation must address. These are not two independent search requests.
The observed clarification failure loses the previous subject in both fields;
this change does not claim to repair that separate conversation contract.

Implementation uses `codex/gpt6-luna-pageindex`, based on fetched main
`9465dae`, in the 3551 managed worktree. GPT-6 Luna has a separate adapter
and invocation identity, keeps strict Chat Completions with effort none,
and leaves the historical GPT-5.6 adapter available. Settings and composer
labels derive from the configured adapter. New material jobs bind the selected
model; recovery of existing jobs selects their original model pins.

The actual admitted document has 700 headings and fails the old 256-node
PageIndex bound. The worker and normalizer now admit 1,024 structural nodes
plus the synthetic root, while retaining byte, line, depth and timeout bounds.
The configuration identity changes to invalidate old limited indexes. The
qualification source and its approved function ASTs are unchanged.

The owner request authorizes these scoped product changes. Existing custody
rows are refreshed only for the changed owned sources; original baseline
classification and approval checkpoints remain preserved. No credentials or
study data are committed. The current runtime OpenAI credential is memory-only
and must be re-entered by the owner after restarting the instance.

Verification: Ruff and mypy pass (662 source files). Isolated wheel/sdist build
and `verify_cardine_wheel.py` pass. Ownership audit passes all 322 rows. The
offline suite outside the Codex sandbox passed 2,856 tests with four intentional
provider/PDF-extra skips; one historical browser assertion expected the fixed
5.6 label for a fixture model. It now checks the truthful generic Tutor label
and its focused browser test passes. The final committed suite is rerun before
making the PR ready. No paid model smoke test was performed.

Local activation is complete: verified SQLite backups of events, runs and
retrieval, the previous configuration and launcher are retained under the
existing preview directory in `backup-gpt6-pageindex-20261002-221832`.
The live configuration selects `openai-gpt-6-luna` and document index ON;
the other feature selections are preserved. PageIndex built 701 nodes from
the exact admitted PDF substrate. The existing lexical index audited 3,676
chunks. The derived repository coordinator was settled to READY only after
that PageIndex build succeeded. The canonical event database digest is unchanged.

The loopback server now runs this task's source directory, not main-preview;
settings show GPT-6 Luna and `/api/v1/indexing/status` reports READY, one ready
revision, 3,676 chunks and no error. The inherited OpenRouter credential was
preserved in process memory without persistence or disclosure. The owner must
re-enter the existing OpenAI key in Settings; the UI confirms it is absent after
restart. The old launcher and checkout remain available for rollback. Restoring
the old configuration is sufficient to select 5.6/OFF; database backups must not
overwrite new study activity blindly.

Delivery is one PR to main; CI and automatic GitHub Codex review are checked
after publishing. No merge is authorized. The clarification context defect
remains separate: ordinary capability retrieval still uses FTS, while PageIndex
provides source structure to the document-index consumers.
