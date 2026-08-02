# Cardine

Cardine is a private, repository-backed study workspace for grounded medical
learning. It combines a conversation-first product shell with a durable,
inspectable runtime: canonical events, trusted source snapshots,
suspend/resume, deterministic replay, and explicit provider boundaries.

![Status: private product](https://img.shields.io/badge/Status-private%20product-5b4b8a.svg)

Cardine is not an open-source release. Its runtime includes a copied snapshot
of the provider-neutral Harness core; the ownership and licensing boundary is
documented in [`NOTICE.md`](NOTICE.md) and [`LICENSE-CARDINE.md`](LICENSE-CARDINE.md).
The internal Python namespace remains `study_agent` so copied-core imports and
protocol identifiers stay stable.

## Run the offline proof

The default workflows are credential-free and make no network requests:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/cardine --help
.venv/bin/cardine-demo
.venv/bin/cardine-shell --json
```

For the full local quality gates:

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
.venv/bin/python -m build
```

The `cardine` command is the product-facing entry point. `study-agent` and the
other `study-agent-*` commands remain compatibility aliases for copied Harness
hosts; they do not change Cardine's product identity.

## Product shell

The terminal shell runs the deterministic conversation-first trace:

```bash
cardine-shell "I have ten minutes. Help me understand heart valves."
```

The repository-backed browser surface is the private product entry point:

```bash
cardine-shell-web \
  --repository ./my-study-repository \
  --course-id course-anatomy \
  --session-id session-2026-08-01 \
  --private
```

Production deployment uses the pinned container and Compose topology in
[`docs/private-production.md`](docs/private-production.md). The browser owns
presentation state only; canonical course, source, and session state remains
under the runtime services and append-only event stream.

## Runtime boundary

```text
┌─────────────────────────────────────────────────────────┐
│ Cardine product shell (UI, HTTP, auth, settings)         │
├─────────────────────────────────────────────────────────┤
│ Copied Harness core (events, sources, sessions, tools)   │
├──────────────────────────┬──────────────────────────────┤
│ Canonical event stream   │ Technical model adapters      │
│ Source snapshots         │ (offline by default)          │
│ Read-only projections    │                              │
└──────────────────────────┴──────────────────────────────┘
```

- **Events are canonical.** Course, source, and session projections are
  rebuildable read models. SQLite checkpoints and retrieval indexes are
  operational state, never an independent study authority.
- **The product shell is a host.** Browser and HTTP code do not write event or
  SQLite state directly; server-side composition owns credentials and
  authority.
- **Adapters are technical boundaries.** The fixed GPT-5.6 Luna adapter is an
  explicit opt-in composition. Offline tests and demos use scripted or
  recorded decisions and never need an API key.
- **The CLI is another host.** `cardine` exposes the copied-core command
  contract without changing its internal IDs or import namespace.

The copied-core contract and rationale remain available in the inherited
specifications under [`docs/specs/`](docs/specs/). Cardine-specific decisions
are recorded under [`docs/decisions/`](docs/decisions/), including the
repository boundary in
[`ADR-0020`](docs/decisions/ADR-0020--separate-cardine-repository-and-copied-core.md).

## First repository workflow

```bash
cardine init ./my-cardine-repository
cardine --repository ./my-cardine-repository \
  course create \
  --title "Example course" \
  --learning-goal "Explain the core concepts"
cardine --repository ./my-cardine-repository source add COURSE_ID notes.md
```

Continue with `source list`, `ask`, session commands, `export`, or `doctor`.
The default repository remains fully offline; only an explicit model adapter
configuration enables provider-backed `ask` or Cardine chat behavior.

For Cardine's fixed GPT-5.6 Luna baseline:

```bash
export OPENAI_API_KEY="..."
cardine init ./my-cardine-repository \
  --model-adapter openai-gpt-5.6-luna \
  --model-setting timeout_seconds=60 \
  --credential-env OPENAI_API_KEY
```

Only the environment-variable name is stored. Credentials never enter
repository configuration, transcripts, exports, or design evidence.

## Ownership and contribution policy

This is a private product repository, not a public contribution project. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the boundary between private Cardine
work and the copied core, and [`SECURITY.md`](SECURITY.md) for private
vulnerability reporting. Third-party font and icon notices remain next to
their assets. The preserved scanned design archive and its custody record are
documented under [`docs/design-source/`](docs/design-source/).
