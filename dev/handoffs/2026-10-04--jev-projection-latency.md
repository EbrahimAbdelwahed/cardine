# Jev canonical projection preparation

Owner-authorized task 3 from the 2026-10-04 Jev/retrieval findings. Dedicated
checkout: `/Users/ebrahimabdelwahed/.codex/worktrees/jev-projection-latency/cardine`;
branch `codex/jev-projection-latency`, base `origin/main` at `6e9c904` (PR #22).
The untested two-file draft in worktree `051b` was read and transferred without
changing that checkout. Main/shared product checkout and other owners' work
remain outside this task.

## Behavior and integrity

- `_text_binding` prepares the SHA-256 digest, UTF-8 length and Unicode line
  offsets once per exact immutable text. Its process-local LRU holds four texts.
- `verified_node_spans(index, context)` validates the current index fingerprint
  and complete source/revision/substrate/text binding on every invocation. A
  four-entry immutable preparation cache resolves each node once, checks the
  complete root and ordered, disjoint, parent-contained children. The public
  dictionary is a copy; callers cannot edit cached spans.
- `candidates_for_canonical_chunks(..., selected_chunks=None)` remains backwards
  compatible. Always supply the full canonical revision as `chunks`, and optionally
  supply exact canonical `SourceChunk` values as `selected_chunks`. The complete
  canonical catalog is checked for IDs, ordinals, hashes, offsets, non-whitespace
  gaps and full coverage before projecting only the selected chunks. A chunk
  crossing nodes still belongs whole to the deepest containing ancestor.
- Analyzer preparation has a four-entry per-instance LRU keyed by complete index
  fingerprint, verified normalized digest, normalization, exact page map/count,
  all canonical chunk fields/metadata, ordered scope, policy and provider identity.
  `cache_key_for`, `analyze` and `validate_cached` share it. Every hit first checks
  the current canonical context/index. Altered unselected chunks change the key
  and undergo full canonical validation. Failed preparation is never cached.
- Production preprocessing checks selected chunk membership, then uses one scoped
  analyzer preparation. Structural-only mode uses the same scoped unitizer API.
  Generation planning checks verified spans without partitioning the full document
  again. Existing persisted semantic cache keys/candidate fingerprints are preserved.

Caches remain derived and bounded. They cannot write canonical state, create
ChunkIds, change consent or provider credentials, accept proposals or enroll recall.
OFF and SHADOW retain their existing behavior. No retrieval policy changes are
included: PR #22 published an audit/experiment, not an adjacent-chunk runtime fix.

## Integration with the other tasks

The public unitizer signature adds only keyword-only `selected_chunks`; calls
without it still return all whole canonical chunks. Linguistic Jev scope changes
should keep `_scope_candidates` and full-chunk boundary checks, and include new
input-affecting fields in the preparation key. Post-generation grounding should
continue to use canonical evidence resolution and HUMAN proposal acceptance;
these process-local caches provide no independent authorization. Any further
changes to the three copied-core files require refreshing their exact hashes in
`scripts/audit_harness_ownership.py` in the same integration commit.

## Offline evidence

`tests/unit/knowledge/test_projection_preparation.py` verifies one line-map
preparation, 701 cold/zero warm node resolutions, identical selected/global
projections, unselected-chunk tampering, counterfeit selected metadata, full-root
coverage even for an empty selection, one canonical validation/projection across
cache key/analyze/cached validation/planning, normalization invalidation and bounds.
Existing PDF, Unicode, Markdown, cache receipt, replay and OFF/SHADOW tests remain.

Reproducible source-free comparison (development environment installed in this
checkout; no study stores or source files):

```sh
.venv/bin/python scripts/profile_jev_projection.py --baseline-ref 6e9c904
.venv/bin/python scripts/profile_jev_projection.py
```

The script compares the base's global projection followed by selection with the
new selected projection on generated Unicode Markdown: 1,404,232 characters,
701 nodes, 3,676 canonical chunks, eight selected chunks. It reports setup and
cold/warm projection separately, locator calls and a selected-output digest.
Retrieval, Jev transport, generation and post-generation verification are not
exercised; provider calls are zero. This is local projection evidence, not live
latency evidence or a provider/model benchmark.

Measured locally with Python 3.13 on 2026-10-04 (same generated input, 1,407,908
UTF-8 bytes, within the 2 MiB admission bound):

| Phase/count | Base `6e9c904` | This implementation |
| --- | ---: | ---: |
| Fixture/canonical setup | 0.0702 s | 0.1204 s |
| Cold projection | 80.4884 s | 0.0470 s |
| Repeated projection | 72.9408 s | 0.0422 s |
| Cold locator resolutions | 1,402 | 701 |
| Repeated locator resolutions | 1,402 | 0 |
| Whole canonical chunks verified per projection | 3,676 | 3,676 |
| Canonical candidates constructed per projection | 3,676 | 8 |

Selected-output digest (orders, offsets and owning nodes) matches exactly:
`2b2505e3cbe8bd2be3460fa7cbed6ec20baf9aa80fb5dfd5a9b2ac64af5fb99c`.
Text preparation now occurs in setup; cold projection still verifies the entire
canonical catalog. Semantic cache reuse is separately checked by the behavioral
regression: one selected projection and one canonical catalog validation across
cache key, analyze, cached validation and planning. A reopened analyzer prepares
and verifies its selected scope once rather than trusting the persisted cache.

## Delivery and verification

Implementation commit: `579db63`. Published branch and
[PR #24](https://github.com/EbrahimAbdelwahed/cardine/pull/24) include this handoff
and source-free evidence; PR is attached to the owning Codex chat. The current
submitted commit is the branch head, including final documentation.

Dedicated `uv sync --frozen --extra dev --extra openai --extra jev --extra pdf`
environment: **3,003 offline tests passed, four expected opt-in/platform skips**
(189.87 s). Additional mocked transport/streaming/PDF group: 74 passed, one
platform containment skip. Ruff, strict mypy (681 files), wheel/sdist build and
archive verification, clean provider-free wheel imports/CLI help, and exact-byte
ownership audit (322 historical rows) pass. The three modified copied-core
commitments were updated in the existing audit, without changing classifications.

At publication, GitHub CI and the automatic Codex review of the final submitted
head are external pending checks; current results and actionable findings belong
in PR #24. Marking ready starts automatic review round 1; maximum two rounds,
no local reviewer. No merge, deployment, local rollout, study-store migration or
paid provider call was performed. No tracked local implementation work remains.

