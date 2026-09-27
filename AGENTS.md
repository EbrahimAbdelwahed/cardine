# Cardine — Agent Instructions

## Repository boundary and source of truth

- Work in the Cardine repository. Read `CONTEXT.md`, the relevant approved spec
  under `specs/`, and `CONTRIBUTING.md` before implementation. Start current
  development context from `dev/index.md` when present; historical sibling
  workspace logs do not determine this repository's branch or delivery state.
- Cardine owns product UI, application composition, authentication, settings,
  and deployment. The reusable Harness is a separate repository. Verify Git
  identity instead of inferring ownership from a copied package name.
- The copied `src/study_agent` core remains operational until installed-package
  parity and removal gates pass. Do not silently replace it with sibling imports,
  `PYTHONPATH`, partial copies, or an unapproved package migration.

## Git, worktrees, and delivery

- At task start, inspect the repository root, remotes, branch, status, upstream,
  and existing PR. Confirm which checkout owns the task before editing.
  Read-only research and review do not need a new branch or worktree.
- Give each independent implementation task one owner and one `codex/<topic>`
  branch. Do not develop on `main`, switch another active chat's branch, or
  mix unrelated tasks into a long-lived product branch.
- Prefer a suitable free Codex-managed worktree. Inspect attached worktrees
  first; use the app's worktree tools for creation, archival, and recovery when
  available. Reuse only after accounting for prior work and preparing the base.
  Use a durable checkout when app tools are unavailable; temporary directories
  must never hold the only copy of unpublished work.
- Start independent changes from the fetched GitHub default branch. A task
  continuing an existing branch must keep that branch and its PR. If a change
  depends on unmerged work, name that dependency and base explicitly in the PR;
  do not accidentally submit the whole product lineage as a small fix.
- Preserve other owners' dirty, staged, untracked, and ignored files. Stage
  only the assigned files or hunks; inspect the staged diff before committing.
  Never use blanket staging, destructive reset/clean, automatic stash, or
  force-push to make a checkout look clean. Preserve and verify recoverable
  backups before any authorized migration or cleanup.
- For owner-requested implementation, normal delivery includes scoped commits,
  pushing the task branch to the existing GitHub repository, and creating or
  updating its PR, unless the user requests local-only work or another limit.
  This does not authorize new repositories, releases, deployments, credential
  changes, direct pushes to `main`, or merging without an explicit user request
  or an already-approved merge policy.
- Keep one PR per independently verifiable outcome. Include its tests, necessary
  documentation, and review fixes in that PR. Do not open branches or PRs for
  individual reviewers, review passes, or each progress note. Read-only reviewers
  inspect the implementation branch; assigned fixes go back to its owner.
- Use draft PRs for unfinished or blocked work. Make a PR ready when its scoped
  implementation and prescribed verification are complete. Attach created PRs
  to the current Codex chat and keep title, description, base, and validation
  accurate as scope changes. Routine documentation-only changes need diff and
  link inspection, not invented runtime tests; applicable CI still runs.
- Use only automatic Codex GitHub review for semantic code review. Do not
  launch a local reviewer or specialist reviewer chain. Put repository-specific
  correctness, security, migration, and data-loss rules in the automatic review
  instructions and cover them with executable CI. Keep human product decisions
  and existing acceptance criteria explicit.
- Before an authorized merge, require applicable CI and review evidence for the
  current submitted commit, resolve actionable findings, and check dependencies.
  A missing review, absent check, failed run, or old green commit is not approval.
  After a fix, push to the same PR and reassess the updated commit.
- At handoff, report checkout, branch, commit, PR URL, verification, outstanding
  review/CI, and any remaining local work. Distinguish implemented, published,
  reviewed, and merged; do not call pending work complete.
- At task transitions, reuse free worktrees or archive retired managed worktrees
  with the app tool after checking that no chat or process needs them. Preserve
  needed ignored files separately. Keep backup refs and unpublished commits.
  Close a superseded PR only after verifying where its changes are retained.
  Prune missing Git worktree registrations only after inspecting paths and
  saving their metadata and referenced commits. Never close an active PR merely
  to clean up a checkout or attachment.

## Code Review Rules

- Prioritize reproducible correctness, security, data-loss, and public-contract
  regressions caused by the change. Give a concrete trigger and affected code;
  avoid speculative warnings, formatting preferences, and duplicate findings.
- Inspect the actual PR base and dependency context. Report unrelated backlog
  or missing integration separately rather than treating it as this patch's bug.
- Apply the repository's domain rules below and in its canonical specifications.
  Review findings are evidence, not authorization to merge or publish user data.

## Domain and verification boundaries

- Canonical source bytes, append-only events, citations, artifact decisions,
  and recall history remain authoritative. Indexes, projections, and run
  checkpoints are derived state and cannot authorize canonical writes.
- Generated study objects remain proposals until the required explicit human
  decisions. Keep trusted execution context separate from model tool arguments.
- Keep provider calls behind adapters and server-owned credential boundaries;
  browser presentation and transport do not own canonical study state.
- Never commit credentials, local study stores, source material, exports, raw
  provider payloads, or raw execution artifacts. Preserve licensing boundaries.
- Use behavior-focused tests for changed contracts and the verification defined
  by `CONTRIBUTING.md`, the approved spec, and applicable CI. Tests remain offline
  by default; network tests and model spend require their existing authorization.
- Keep task memory beside the code in this repository's `dev/` tree. Commit
  necessary handoffs with the implementation rather than creating separate PRs
  for orchestration reports. Record durable decisions in their existing home.
