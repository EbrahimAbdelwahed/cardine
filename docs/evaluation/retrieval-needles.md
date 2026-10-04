# Offline retrieval needle benchmark

The owner requested a reliability audit and keyword-climbing experiments on
2026-10-04. This explicitly supersedes the earlier exclusion of benchmarks from
the Jev/PageIndex delivery. It does not change production retrieval policy.

Run the benchmark from an installed Cardine checkout:

```sh
python scripts/benchmark_retrieval.py \
  --repository /path/to/private-study-repository \
  --manifest /path/to/private-needles.json \
  --output /path/to/private-results.json
```

Add `--fail-on-miss` to return exit code 1 when any case/plan misses its expected
evidence. Default execution records misses without treating a diagnostic
baseline as a command failure. An invalid gold commitment, corrupt index or
inconsistent snapshot raises an error and produces no new report.

## What is measured

A positive case has named facets, each containing one or more exact canonical
spans. Every span in a facet must be covered by the returned, capped context;
every facet must pass for the case to be complete. A matching definition does
not satisfy a separate mechanism facet. Coverage uses source and revision IDs
and exact character intervals; overlapping chunks cannot inflate coverage.

An explicit negative case expects empty results. `false_sufficient` records an
initial lexical `sufficient` result when the plan's final context fails the gold
expectation. This is an audit label, not a change to the adapter contract:
`sufficient` currently means that lexical candidates exist. The benchmark does
not claim that this flag proves semantic entailment or that a model would
hallucinate when it receives an unrelated candidate.

Each case retains the configured course's trust/role policy and can restrict
revision IDs. Every gold span must lie inside that query scope. Superseded and
retired sources cannot supply gold for the default current-source query.

## Private manifest, schema version 1

Top-level fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `course_id` | Existing canonical course ID |
| `plans` | Nonempty list of distinct search plans |
| `cases` | Nonempty list of distinct needle cases |

Plan fields are `name`, optional `climb` (boolean, default false),
`neighbor_radius` (integer 0–3, default 0) and `context_limit` (integer 1–100,
default 8). A case contains `id`, `query`, optional `search_limit` (default 8),
optional `revisions`, and either positive `facets` or `expected_empty: true`.
Optional `alternatives` supplies at most three distinct additional lexical
queries. A facet maps its name to a nonempty list of span objects containing
`source_id`, `revision_id`, `start`, `end`, and `checksum` (SHA-256 of the exact
UTF-8 normalized text slice). Gold text is resolved and hashed before scoring.
Never compute authoritative gold exclusively from an unchecked derived index.

Climbing runs the original query and all supplied alternatives, then takes a
deduplicated union in query/result order. Original hits retain precedence.
Expansion adds ordinal neighbors at increasing distance, within the same exact
source, revision and section path, subject to the original trust/role filters.
Existing hits keep precedence over neighbors. The final context limit applies
before scoring; when original results fill it, alternatives/expansion can be
starved. This deliberately explicit merge policy is an experiment, not a
production ranking recommendation. Neighbor evidence is resolved canonically.

## Snapshot and privacy boundaries

The loader opens the live event/index databases with SQLite `mode=ro` and makes
SQLite backups into an automatically deleted temporary directory. This includes
committed WAL frames; `immutable=1` is used only for the completed copies. The
two database backups are not an atomic transaction: a full catalog/index
integrity check rejects an inconsistent pair. Canonical source records are
decoded and verified against read-only, content-addressed blobs. The audit
catalog then freezes those verified records and validates every citation's
ownership, bounds and optional quote against their exact bytes.

Repeated identical searches are reused only inside that immutable snapshot.
`query_count` counts each plan's logical probes, including cached probes. These
results do not measure live latency. No provider is called; no canonical event,
source, index, configuration or credential is written or repaired.

The report contains case IDs, facet outcomes, evidence identities/bounds, context
characters/chunks, query counts, source-policy metadata, sequence, manifest hash
and index/catalog fingerprints. It contains no source excerpts or query text.
Keep manifests, reports and study data outside version control, for example in
the already ignored `.cardine-ui-preview/` directory. Reports still describe a
private study corpus and should not be published without checking their scope.

## Limits and verification

This measures exact gold coverage, not answer correctness, semantic precision,
calibrated confidence or agent-level success. Another supporting passage may
answer a question while missing the selected exact gold. Manually chosen
keywords informed by the source measure recoverability, not generalization.
Raw natural-language probes bypass tutor query distillation. Replay actual
recorded queries separately, and reserve unseen cases before tuning production.

Offline regressions cover definition-only false sufficiency, keyword/neighbor
recovery, capped contexts, stale hashes, query scope, section/trust boundaries,
negative cases and multi-chunk coverage. The snapshot integration test retains
committed WAL frames, verifies unchanged live database/blob state, and checks
that a corrupt index fails without being repaired.
