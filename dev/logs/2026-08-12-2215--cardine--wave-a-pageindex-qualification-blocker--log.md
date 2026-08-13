# Log: Wave A PageIndex projection checkpoint

Date: 2026-08-12 22:15 CEST
Area: cardine / Wave A / WA-C

## Summary

The qualification gate was resolved with the signed upstream commit and exact
archive evidence. WA-C now has a bounded subprocess adapter, canonical text
mapping, and a restart-safe derived projection backed by the existing namespaced
SQLite run store.

## Qualification

- Upstream commit: `9470b639609e3113e66a58e23f36bd6b0221fd85`.
- Archive SHA-256:
  `c46682f3a9259087fefc8df79f4ed6b1d901c5177a4f25fa9ceced430fbb98bd`.
- Qualified source SHA-256:
  `0a8831a5cf39e60d9e7e21ff95644a770e49f0b7e613be5c784619dd0c60bcbd`.
- The four approved function AST digests are bound in the isolated child; the
  source is carried as non-importable `page_index_md.py.data`.

## Behavior

- The child executes only the four approved structural functions, with strict
  JSON, input/output, node, and depth bounds.
- PageIndex output is navigation-only. Nodes are retained only when exact text
  maps uniquely to canonical revision offsets; ambiguous/unmappable nodes are
  discarded.
- Projection states are `queued`, `indexing`, `ready`, `degraded`, `failed`,
  and `disabled`, with bounded leases/retries, CAS transitions, rebuild,
  enable/disable, and explicit active-revision reconciliation.

## Verification

- Focused tests: 8 passed, 1 qualification-digest test skipped under Python
  3.12 (the supplied AST digests are authored against CPython 3.13; a direct
  CPython 3.13 verification passed).
- Ruff: passed.
- mypy (`cardine.adapters.pageindex`, `cardine.knowledge`): passed.
- `git diff --check`: passed.

## Constraints

No provider/network imports, canonical Harness files, CLI/UI files, PDF files,
new database schema, or dependencies were changed.
