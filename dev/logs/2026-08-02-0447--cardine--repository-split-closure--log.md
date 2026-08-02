# Log: Cardine repository split closure

Date: 2026-08-02 04:47 CEST
Area: repository boundary

## Summary

Cardine is now an autonomous private repository with a copied Harness core,
private product identity, release gates, and an independently verified default
branch. Cardine-specific residues were removed from Study Agent Harness while
its generic shell UI and TUT-08 remained intact.

## Verification

- `.venv/bin/python -m pytest -q`: 2,140 passed, 13 optional skips
- `.venv/bin/python -m ruff check .`: passed
- `.venv/bin/python -m mypy`: passed on 525 source files
- clean Cardine clone: same full test, Ruff, and mypy outcomes
- clean Harness `main` clone: 1,869 passed, 12 optional skips; Ruff and mypy passed
- GitHub CI runs `30729124449` (canonical branch) and `30729435967`
  (default `main`): all 6 jobs passed on the tagged implementation commit
- preservation bundle checksums and Git bundle: passed
- Harness residual scans across maintained files, names, refs, and worktrees: empty

## Notes

- Annotated tag `cardine-repository-split-20260802` targets `98456e7`.
- Local Docker verification was unavailable because Docker is not installed;
  package, artifact, clean-install, optional-extra, and remote CI gates passed.
