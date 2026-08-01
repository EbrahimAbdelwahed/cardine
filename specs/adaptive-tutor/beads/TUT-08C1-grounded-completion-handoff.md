# Task Bead: TUT-08C1 grounded completion handoff

Status: Done
Priority: P0
Type: tracer-bullet
Depends On: TUT-08B, ADR-0016

## Outcome

A verified `explain_concept@1` completion is represented by a closed reference,
recovered through its owner, and committed as a canonical tutor presentation.
Unknown, tampered, unrecoverable, and terminated outcomes create no fabricated
conversation content.

## Acceptance Criteria

- [x] Runner emits no completion reference until authority/gateway verification.
- [x] Reference binds capability identity, manifest, run, output, and retry receipt.
- [x] Private registry recovers only the matching owner output.
- [x] Grounded explanation becomes a bounded canonical presentation and survives reload.
- [x] Exact retry does not repeat provider work or presentation events.
- [x] Unknown/tampered/unrecoverable and insufficient-evidence outcomes are status-only.
- [x] Raw generic completion output is never copied into tutor speech.

## Verification

- 101-test privileged integrated C1 matrix passed.
- Worker sandbox matrix: 106 passed, 2 loopback skips.
- Ruff and `git diff --check` passed.

## Notes

- The repository recovery adapter's use of gateway engine/binding internals is
  under independent semantic review before C2 proceeds.
