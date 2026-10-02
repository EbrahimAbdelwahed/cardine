# Local read budgets and live drafts

Status: owner-requested implementation, 2026-10-02.

The owner authorized profiling and reducing local page/turn latency, regression
tests and actual provider output during generation. This supersedes the older
client word-reveal delivery described in the compact-thinking log.

SQLite caches parsed immutable projections and event envelopes within one
repository lease. Every read still observes the guarded database and compares
the actual bytes; sequence equality alone cannot authorize reuse. Source
catalogs are shared within that lease and reuse validated manifests only after
reading the current canonical events and rechecking every referenced blob.
New source/selection events, generated-source projection freshness, retirement,
missing blobs and same-length tampering retain their conservative behavior.
Failures are never cached. These caches cannot append or authorize domain events.

Filesystem reads retain at most 32 previously hashed blobs within a 256 MiB
budget. Each access still opens the descriptor-anchored regular file and reads
all its bytes. A hash may be reused only when those bytes exactly equal the
previously verified content for the same complete BlobRef. Same-length edits,
missing files and symlinks cannot be hidden by inode or timestamp reuse.

Deep JSON freezing reuses only the runtime's owned immutable mapping type.
Caller dictionaries and arbitrary mapping proxies still get an owned deep copy;
mapping equality and finite-number validation remain part of the contract.

UI reads defer the startup index queue and compute source, indexing and recall
payloads only for the routes which consume them. Explicit admission/reconciliation
continues to own index work. Lesson navigation reuses validated records and avoids
resolving a known lesson again for query recovery or capability construction.

The Cardine Luna adapter uses native provider SSE for the grounded-answer schema
when a live turn has an output observer. Decision/tool/flashcard schemas retain
their existing path. Partial JSON parsing exposes only segment text, never raw
wire JSON, evidence keys, action arguments, prompts or reasoning. The same final
model-response parser and worker validators still run before publication.

A separate process-local draft store holds at most 24 drafts of 16,000 characters.
Its read endpoint has the normal browser authentication boundary. The UI labels
the draft as being generated and unverified, inserts it as text rather than HTML,
and grants it no citations, actions or study-state authority. Completion, failure
or cancellation erase the draft. The final validated receipt replaces it without
an artificial reveal animation. Drafts never enter diagnostics, event history,
exports or persistent storage. Stale polling cannot update another turn or route.
The obsolete word-reveal implementation, its CSS, timers, callbacks and browser
state are removed. Custody row checks share one exact-scope validator instead of
adding another duplicate validation loop.

Offline tests exercise output before provider completion, rejection of invalid
evidence, draft cleanup and deterministic work budgets for large-source reads
and explanations. Provider latency and quality are not certified by those tests.

The exact implementation bytes are bound by `tests/parity/local-latency-overlay.json`;
historical recovery/journal ledgers are preserved. Copied-core reconciliation
still belongs to the pending installed-Harness parity/removal work.
