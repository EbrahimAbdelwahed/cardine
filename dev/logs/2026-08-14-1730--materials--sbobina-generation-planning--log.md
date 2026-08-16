# Log: sbobina material-generation planning

Date: 2026-08-14 17:30 CEST
Area: Cardine / materials

## Summary

Created a proposed, four-slice specification for porting the historical
sbobina-to-complete-to-study workflow into Cardine. The plan is explicitly
blocked on user approval; no application code, prompt, schema, test or UI was
changed.

The plan synthesizes three independent Codex drafts with fewest-slices,
risk-first and seam-quality biases. The required Claude/Fable planning attempt
could not run because the local Claude CLI was not authenticated.

## Files Changed

- `specs/material-generation-workflow/README.md`: canonical proposed contract,
  ownership, state model and review map.
- `specs/material-generation-workflow/slices/*.md`: four independently
  verifiable implementation slices.
- `dev/plans/2026-08-14-1700--materials--sbobina-generation-workflow--plan.md`:
  repository-memory pointer and scope summary.
- `dev/logs/2026-08-14-1730--materials--sbobina-generation-planning--log.md`:
  this planning record.

## Verification

- `git diff --check -- specs/material-generation-workflow dev/plans/...`: passed.
- Manual `refactor-clean` ownership audit: removed duplicated decision state
  from the material-generation job; the artifact service remains the sole
  decision owner.
- Application tests: not run because this turn changed planning documents only.

## Notes

- Approval initially authorizes Slice 01 only.
- Existing dirty Cardine source/UI/host changes were preserved untouched.
