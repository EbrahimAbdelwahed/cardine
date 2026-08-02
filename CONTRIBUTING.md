# Contributing to Cardine

Cardine is a private product repository. Contributions are coordinated with
the owner; this is not an invitation to public contributions or a promise of
an open-source Cardine release. Read [`NOTICE.md`](NOTICE.md) before changing
files that originated in the copied Harness core or third-party assets.

The copied core remains under the Apache License 2.0 in [`LICENSE`](LICENSE).
Cardine product work is separately owned and is not licensed for reuse; see
[`LICENSE-CARDINE.md`](LICENSE-CARDINE.md). Do not assume that a pull request,
patch, or issue grants a license to private product code.

## Development setup

Python 3.12 or newer is required.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Before submitting a change, run:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
.venv/bin/python -m build
```

Use the Cardine entry points in examples and deployment scripts:
`cardine`, `cardine-demo`, `cardine-shell`, and `cardine-shell-web`. The
`study-agent-*` names are retained only as copied-core compatibility aliases.

Keep changes small and preserve these architectural boundaries:

- canonical study state comes from the append-only domain event stream;
- projections, indexes, and run checkpoints do not become authorities;
- skills and playbooks own study behaviour;
- model/provider adapters translate technical protocols only;
- trusted execution context is separate from model-proposed tool arguments;
- tests are offline by default and never require credentials.

Add behaviour-focused tests for contract changes. Document durable
architectural decisions under `docs/decisions/` and update affected specs.
Never commit local study repositories, source material, exports, credentials,
raw provider payloads, or raw Flywheel execution artifacts. Do not add design
source outside the custody path documented in
[`docs/design-source/README.md`](docs/design-source/README.md).
