# Task Bead: crs-05 Run Cardine full verification and independent reviews

Status: Open
Priority: P0
Type: task
Depends On: cardine-ui-fix-crs-04-nmm
Run ID: `20260802-cardine-repository-split`
Spec: `docs/specs/separate-cardine-from-study-agent-harness.md`

## Outcome

A clean Cardine clone passes the complete local/CI/package/container story and independent semantic and security review.

## Slice Strategy

contract

Fresh Context Fit: yes

## Spec Coverage

- Cardine passes all full gates and reviews with zero secrets.

## Grilling Evidence

- docs/flywheel-runs/20260802-cardine-repository-split/grill-with-docs.md
- Decision state: approved
- No ADR/glossary change: review gate

## Worker Profile

none needed

Rationale:

No reusable specialization selected yet.

## Context

Cleanup of the source repository is forbidden until Cardine is independently recoverable and green.

## What To Do

- Run Python 3.12/3.13 tests, Ruff, mypy, build, clean install, browser and Docker gates.
- Run secret scan.
- Obtain semantic and security reviews and apply only approved findings.
- Push verified commits and require green remote CI.

## Likely Files / Packages

- docs/reviews/: review evidence
- dev/logs/: exact verification record
- approved finding files only

## Acceptance Criteria

- [ ] All local gates pass from a clean clone.
- [ ] Remote CI is green.
- [ ] Semantic and security findings are closed or explicitly rejected with evidence.
- [ ] No credential value, private source material, or unsafe path appears in commit history selected for publication.

## Verification

- `python -m pytest`: expected to pass or produce documented output
- `python -m ruff check .`: expected to pass or produce documented output
- `python -m mypy`: expected to pass or produce documented output
- `python -m build`: expected to pass or produce documented output
- `docker build`: expected to pass or produce documented output
- `gh run list --repo EbrahimAbdelwahed/cardine`: expected to pass or produce documented output

## Out Of Scope

- Harness cleanup before this bead is approved.

## Notes / Handoff

- Worker must report files changed, behavior implemented, verification results, unresolved questions, and follow-up beads.
