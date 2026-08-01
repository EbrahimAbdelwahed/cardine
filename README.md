 ciao 

# Study Agent Harness

**A durable, inspectable execution layer for AI tutoring agents — provider-neutral, event-sourced, and verifiable offline.**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Status: Alpha](https://img.shields.io/badge/Status-v0.1.0%20alpha-orange.svg)](https://github.com/EbrahimAbdelwahed/study-agent-harness/releases)

Models are great at *proposing* what to do next. They are terrible custodians of
learner state, source truth, and long-running execution. Every team building a
tutoring agent ends up rebuilding the same missing layer: durable state,
grounded sources, suspend/resume, and a way to prove what actually happened.

**Study Agent Harness is that layer, as an open-source core.** A model may
decide; it never owns. The harness keeps canonical state in an append-only
event stream, snapshots trusted sources, runs versioned skills through
playbooks, and replays the same session deterministically — with any provider
behind a thin technical adapter, or no provider at all.

## Why this is a dev tool, not another chatbot

| The model proposes | The harness owns |
|---|---|
| Which skill to invoke, with what arguments | Canonical learner state (append-only events) |
| When to ask the learner a clarifying question | Source truth (immutable snapshots) |
| How to phrase an explanation | Execution, suspension, and resumption |
| — | Deterministic replay and verification |

This boundary is the product. Everything else — UI, domain packs, providers —
is replaceable by design.

## See it run in 60 seconds (no API key)

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/study-agent --help
```

The Build Week demo starts where a real student starts: *"I have ten minutes.
Help me understand heart valves."* The recorded offline trace:

1. **Snapshots** a sanitized anatomy source
2. **Completes** a grounded study action
3. **Suspends** to ask which valve deserves focus
4. **Refreshes** evidence after the learner picks the aortic valve
5. **Resumes** the exact continuation — `completed → suspended → completed`

Scripted and recorded-provider decision adapters reproduce the full trace
**without a single network request**. What you see in the demo video is what
`pytest` verifies in CI.

```bash
# Full offline quality gates — no API key, no provider SDK, no hosted service
python3.12 -m pip install -e '.[dev]'
python3.12 -m pytest
python3.12 -m ruff check .
python3.12 -m mypy
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Hosts (CLI, embedding host, your product)              │
├─────────────────────────────────────────────────────────┤
│  Skills + Playbooks   (versioned, portable behavior)    │
├─────────────────────────────────────────────────────────┤
│  Application services (suspend/resume, fail-closed)     │
├──────────────────────────┬──────────────────────────────┤
│  Canonical event stream  │  Provider adapters           │
│  Source snapshots        │  (technical transport only:  │
│  Projections (read-only) │   scripted, recorded, HTTP)  │
└──────────────────────────┴──────────────────────────────┘
```

- **Events are canonical.** Course, source, and session projections are read
  models: rebuildable from events, never independently authoritative. SQLite
  checkpoints and the lexical index are operational state — they aid recovery
  and performance but never redefine the study record.
- **Skills and playbooks are the behavior layer.** Versioned skills describe
  study capabilities; playbooks compose them. They travel across providers
  and hosts unchanged.
- **Adapters are boundaries, not brains.** Model adapters translate transport.
  They own no prompts, no policy, no authority, no domain state. Any
  OpenAI-compatible endpoint works; none is privileged. See
  [`docs/examples/external_agent.py`](docs/examples/external_agent.py) for the
  trusted-context boundary without coupling to any agent SDK.
- **The CLI is just another host** over the same application services — not a
  second behavior layer.

The approved v0.1 contract lives in
[`docs/specs/oss-study-agent-harness-v0-1.md`](docs/specs/oss-study-agent-harness-v0-1.md);
the reference CLI and export boundary in
[`docs/specs/oss-harness-v0-1-reference-cli-and-export.md`](docs/specs/oss-harness-v0-1-reference-cli-and-export.md);
architecture rationale in [`docs/decisions/`](docs/decisions).

## Reliability guarantees

**Deterministic replay.** The same event stream replays to the same state.
`doctor` verifies event replay and retrieval rebuildability without contacting
a provider.

**Honest interruption semantics.** The current OpenAI-compatible call has no
in-flight cancellation primitive, so the harness refuses to pretend otherwise:
a pre-run interruption produces no mutation; once a durable operation starts,
SIGINT is deferred until the authoritative outcome is emitted. The harness
never invents a canonical `cancelled` transition for work it could not
actually cancel.

**Commit-then-index recovery.** Source ingestion commits the canonical source
revision *before* rebuilding the discardable retrieval index. If indexing
fails, that is reported as a recoverable operational failure — never rolled
back, never concealed.

**Deterministic, credential-free export.** Repeated exports at the same event
high-water mark are byte-identical. The allowlisted bundle excludes
credentials, credential-variable names, endpoints, provider bodies,
checkpoints, host paths, and source bytes. The checksummed manifest is the
integrity boundary; the event stream remains the recovery boundary.

**Credentials never touch disk.** Adapter configuration stores the *name* of a
credential environment variable; the value is read from the environment at
open time and is never written to configuration, transcripts, or exports.

## First workflow

```bash
study-agent init ./my-study-repository
study-agent --repository ./my-study-repository course create \
  --title "Example course" --learning-goal "Explain the core concepts"
study-agent --repository ./my-study-repository source add COURSE_ID notes.md
```

Continue with `source list`, `ask`, the session commands, `export`, or
`doctor`. Use `--json` for one machine-clean success or safe-error document on
stdout. The default repository is fully offline; only `ask` requires an
explicitly configured model adapter.

For a new Cardine repository using the fixed GPT-5.6 Luna baseline:

```bash
export OPENAI_API_KEY="..."
study-agent init ./my-cardine-repository \
  --model-adapter openai-gpt-5.6-luna \
  --model-setting timeout_seconds=60 \
  --credential-env OPENAI_API_KEY
```

The configuration stores only the environment-variable name. The adapter pins
`gpt-5.6-luna`, the OpenAI endpoint, strict structured output, and
`reasoning_effort: none`; repository configuration cannot redirect that
identity to another model or endpoint. The unsuffixed `gpt-5.6` alias is not
used because it routes to Sol.

## Built at OpenAI Build Week

This project began as a medical student's frustration: a year of disconnected
tools for sources, study-material generation, exam questions, fact-checking,
and correction. The missing piece was never another chatbot — it was a durable
execution layer that lets a tutor meet a student where they are without
forgetting what happened before.

For Build Week we deliberately built the **reusable core** instead of a single
rigid study app. Codex and GPT-5.6 were used through an adapted Agent Flywheel:
approved specs decomposed into dependency-aware beads, implemented in bounded
slices, closed with focused tests, architecture/semantic review, and durable
handoffs — making the workflow itself inspectable, without Codex ever owning
architecture approval or canonical learner state. The demo UI in the
submission video is a demonstrative visualization; the behavior and trace it
shows are the real offline harness.

## Roadmap

1. **Harden the core** — stable contributor contracts for hosts, skills,
   playbooks, persistence, and replay.
2. **Self-improvement proposal loop** — when an agent hits a capability
   boundary (e.g., an unsupported material type), it records a structured
   proposal instead of silently inventing behavior. Proposals pass through
   explicit human review, validation, scoped implementation, tests, and
   replay checks before entering the harness.
3. **Vertical products on the same core** — biomedical, medical, legal, and
   other learning domains own their UI and subject skills while reusing the
   same durable execution and trust boundary.

The goal: a free, community-maintained core that students, teachers, and
builders **embed** instead of each rebuilding their own tutor runtime.

## Status and contributing

v0.1.0 is an alpha release; the public API is not yet stable. Release
acceptance requires deterministic replay/export checks, the credential-free
end-to-end CLI fixture, a clean-wheel install and CLI smoke test, and
independent semantic review. Network smoke tests are strictly opt-in.

- Contributor guide: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Vulnerability reporting: [`SECURITY.md`](SECURITY.md)
- License: [Apache-2.0](LICENSE)
- Platform: Python 3.12/3.13 · stdlib-only runtime · CI on Ubuntu · Build Week
  verification on macOS arm64

