# Three-second page-load budget

Status: owner-requested implementation, 2026-10-04; verification 2026-10-05.

Cardine page loads have an offline regression budget of three seconds. The
browser measures navigation to usable content and a paint, not a heading or
loading skeleton. Fonti includes the material-generation list. Initial startup,
Oggi, Chat, Fonti, Proposte, Verifiche, Percorso, Ripasso, Piano and Settings are
checked on desktop and mobile, with repeat navigation. APIs have the same budget.

The reproducible fixture has a 194 MiB synthetic original, 559 page spans, more
than 3,900 canonical chunks, ready PageIndex structure, a pending proposal and
an accepted/enrolled card scheduled by real FSRS. Model responses are offline;
page loads must neither invoke a provider nor change canonical events. The
fixture represents an admitted extraction; it does not benchmark PDF conversion,
provider latency, arbitrary hardware, or every possible course size.

Bootstrap shares one verified PageIndex summary with its indexing-status DTO.
PageIndex load shares its freshly computed fingerprint across validation and
freshness checks. The source-event decoder verifies original and normalized bytes
once, then uses those proven digests for provenance and canonical/historical
identity checks. The snapshot reader already checks course and session existence;
the application avoids repeating those checks before invoking that reader.

These are response-local reductions in repeated work. No validation result is
cached between requests. Each source read still verifies actual canonical bytes,
and PageIndex reads still validate stored indexes and exact navigation bounds.
Canonical writes, source/chunk identities, consent and human decisions are intact.
There is no data migration or new backward-compatibility path.

Three deterministic work guards catch the original duplicated work independently
of runner speed. Existing corruption, provenance, historical identity, cancellation
and read-coherence tests remain required. A dedicated CI job requires Chromium
and runs the browser budgets; missing Chromium must fail that job rather than
turn the gate into a skip. Thresholds are fixed at three seconds, not calibrated
from a slower baseline.

The three changed runtime paths are bound by
`tests/parity/page-load-latency-overlay.json`. Historical custody manifests remain
unchanged; installed-Harness parity/removal and live deployment remain separate.
