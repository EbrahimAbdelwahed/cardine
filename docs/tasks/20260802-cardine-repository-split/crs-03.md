# Task Bead: crs-03 Normalize Cardine repository identity and ownership

Status: Open
Priority: P1
Type: task
Depends On: cardine-ui-fix-crs-02-u2l
Run ID: `20260802-cardine-repository-split`
Spec: `docs/specs/separate-cardine-from-study-agent-harness.md`

## Outcome

Cardine is an internally coherent private product repository with correct metadata, commands, licensing boundaries, design-source custody, CI, and no false OSS Harness identity.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- Cardine metadata, commands, URLs, ownership, license notices, CI, and documentation no longer present it as the OSS Harness.

## Grilling Evidence

- docs/flywheel-runs/20260802-cardine-repository-split/grill-with-docs.md
- Decision state: approved
- ADR-0020 owns the boundary; CONTEXT.md owns terminology

## Worker Profile

none needed

Rationale:

No reusable specialization selected yet.

## Context

The copied tree still identifies itself as study-agent-harness and its root Apache license would make product ownership ambiguous.

## What To Do

- Rebrand README, pyproject metadata, URLs, scripts, Docker/compose, security, and contribution docs.
- Preserve study_agent imports while renaming the distribution and product commands.
- Add explicit copied-core and third-party license/notice boundaries plus private product ownership notice.
- Import the scanned design ZIP in documented private custody.
- Update CI for Cardine commands and package-data.

## Likely Files / Packages

- README.md, pyproject.toml, uv.lock: product/package identity
- LICENSE*, NOTICE*, SECURITY.md, CONTRIBUTING.md: ownership and policy
- .github/workflows/ci.yml: Cardine gates
- Dockerfile*, compose.production.yaml, docker/: product deployment
- docs/: ADR, design source, repository guidance

## Acceptance Criteria

- [ ] No stale study-agent-harness repository URLs or release claims remain in Cardine-owned metadata.
- [ ] Internal study_agent imports still work.
- [ ] License scanner can distinguish copied core, third-party assets, and private product code.
- [ ] CI invokes real Cardine entry points and preserves offline defaults.

## Verification

- `rg -n 'EbrahimAbdelwahed/study-agent-harness|Study Agent Harness' README.md pyproject.toml SECURITY.md CONTRIBUTING.md Dockerfile* compose.production.yaml`: expected to pass or produce documented output
- `python -m build`: expected to pass or produce documented output
- `git diff --check`: expected to pass or produce documented output

## Out Of Scope

- Renaming the study_agent namespace or changing product behavior.

## Notes / Handoff

- Worker must report files changed, behavior implemented, verification results, unresolved questions, and follow-up beads.
