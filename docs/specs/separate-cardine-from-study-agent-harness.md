# Feature Spec: Separate Cardine from Study Agent Harness

Status: Approved
Owner: Orchestrator / user approved
Date: 2026-08-02
Run ID: `20260802-cardine-repository-split`

## Grilling Evidence

- Session/artifact: `docs/flywheel-runs/20260802-cardine-repository-split/grill-with-docs.md`
- Decision state: approved
- ADR/glossary changes: `CONTEXT.md` and
  `docs/decisions/ADR-0020--separate-cardine-repository-and-copied-core.md`

## Goal

Convert `/private/tmp/cardine-ui-fix` on
`codex/cardine-ui-enterprise@707d852` into the canonical independent checkout
of the private `EbrahimAbdelwahed/cardine` repository, including an autonomous
copy of the Harness core from `e18f670`, then remove only Cardine-specific
residues from Study Agent Harness while preserving its generic shell UI,
reference browser, and TUT-08.

## Problem

Cardine currently lives in local branches and linked worktrees owned by the
public Harness Git directory. Product code, untracked design evidence, and
Cardine-specific contracts can be lost during cleanup or pushed accidentally
to the OSS remote, while the canonical Cardine path cannot have independent
remotes. A verified split must preserve every Cardine delta, establish an
autonomous private repository, and remove only product-specific residues
without damaging the generic Harness shell that the user chose to retain.

## Source Inputs

- Intake: `docs/flywheel-runs/20260802-cardine-repository-split/intake.md`
- Context pack: `docs/flywheel-runs/20260802-cardine-repository-split/context-pack.md`
- Approved detailed plan: external path recorded in grilling evidence.

## In Scope

- Bundle, checksum, manifest, dirty-worktree comparison, and secret scan.
- Private GitHub repository creation and history-preserving push.
- Independent-clone verification and safe in-place path conversion.
- Cardine repository metadata, packaging, ownership, licensing, and CI.
- Cardine-focused and full Python/build/browser/container verification.
- Removal of Cardine-specific branch/worktree/memory/archive residues.
- Full Harness regression and residual scan after cleanup.

## Out of Scope

- Removing or reducing the generic Harness shell UI, reference browser, or
  TUT-08.
- Renaming the `study_agent` Python namespace.
- Redesigning Cardine or changing prompt/RAG/pedagogical behavior.
- Automatic core synchronization or a third shared package.
- Public Cardine or PyPI publication.
- Rewriting public Harness history or pruning unrelated recovery objects.

## Domain and Interface Boundaries

- Cardine owns its private UI, HTTP application, auth/settings, deployment,
  product tool surface, Luna composition, and product-only presentation state.
- Harness owns the public provider-neutral runtime, developer CLI, generic
  shell UI, reference browser, TUT-08, and existing public contracts.
- Course event streams remain canonical; browser/HTTP do not write event or
  SQLite state directly; authority and credentials remain server-side.
- The Cardine checkout must have its own Git directory and `origin`; changing
  the linked worktree's shared `origin` is forbidden.

## Acceptance Criteria

- [ ] All Cardine refs, dirty deltas, and the prototype ZIP are covered by a
  verified bundle/manifest/checksum before deletion.
- [ ] Private `EbrahimAbdelwahed/cardine` exists and `main` preserves
  `707d852` with merge-base `e18f670`.
- [ ] `/private/tmp/cardine-ui-fix` is an independent clone of that remote.
- [ ] Cardine metadata, commands, URLs, ownership, license notices, CI, and
  documentation no longer present it as the OSS Harness.
- [ ] Cardine passes focused/full tests, Ruff, strict mypy, build, clean-wheel
  install, browser checks, container checks, secret scan, semantic review, and
  security review.
- [ ] Study Agent Harness has no reachable Cardine-specific branch, worktree,
  file, plan, archive, name, or product-only contract.
- [ ] Harness generic shell UI/reference browser/TUT-08 remain present and
  pass their focused and full regression gates.
- [ ] No public history rewrite or unrelated dirty-work loss occurs.

## Verification

1. Preservation manifest and bundle verification.
2. Remote/clone SHA and tree equality.
3. Focused Cardine tests, then full local gates and package/container builds.
4. Independent semantic and security reviews; approved fixes; rerun gates.
5. Harness residual/import scan, focused shell tests, full local gates.
6. Remote audits, clean-clone checks, migration tag, logs, and handoff.

## Task Beads

- `CRS-01`: preserve and prove every Cardine source and ref.
- `CRS-02`: create and verify the independent private repository.
- `CRS-03`: normalize Cardine identity, ownership, packaging, and CI.
- `CRS-04`: verify and review Cardine from a clean clone.
- `CRS-05`: remove Cardine-specific Harness residues while retaining shell UI.
- `CRS-06`: close both repositories with remote audit and durable logs.
