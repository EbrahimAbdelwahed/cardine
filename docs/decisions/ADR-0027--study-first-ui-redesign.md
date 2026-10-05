# Study-first UI redesign

Status: owner-requested implementation, 2026-10-05.

The owner asked for Cardine to feel like "NotebookLM, but open and hackable":
calm, ordered and organised around the student's workflow. The five-phase
plan is `specs/ui-ux-redesign/README.md`.

The browser presents study work, not service internals. Navigation has two
always-visible groups (Studio, Revisione); every content page shares one frame
with a single reading column; architecture explanations, inline projection
labels and opaque identifiers leave the page and stay available in the details
sheet. Route keys, element identifiers, endpoints and canonical commands are
unchanged by the presentation work, and HUMAN decisions remain explicit.

Later phases extend the same decision: the exam date and study rhythm become a
canonical course event, the lesson outline and day-by-day schedule are derived
on read from verified structure and the student journal, accepted notes render
to PDF through Typst, documents and the tutor share one study workspace, and
the agent can perform the same actions except handling credentials, provider
consent and accepting proposals.

The changed runtime paths are bound by `tests/parity/ui-redesign-overlay.json`
and grow with each phase. Historical custody manifests remain unchanged;
installed-Harness parity/removal and live deployment remain separate.
