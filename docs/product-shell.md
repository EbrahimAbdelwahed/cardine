# Cardine product shell

The Cardine product shell is a conversation-first consumer of the copied
Harness tutor contracts. It accepts a free-form learner entry before the
context is complete, then renders the conversation, material snapshot, learner
evidence sequence, context conflicts, capability discovery, and optional due
review.

The default journey is deterministic and offline:

```bash
cardine-shell "I have ten minutes. Help me understand heart valves."
```

Use `--json` to inspect the same trace in scripts. The shell command reuses the
existing anatomy host trace; it does not create another tutor loop or write
canonical state. The optional TUT-07 due-review view is omitted safely when no
recall composition is installed.

For a repository-backed browser surface, run:

```bash
cardine-shell-web \
  --repository ./my-study-repository \
  --course-id course-anatomy \
  --session-id session-2026-08-01
```

Open <http://127.0.0.1:8765/>. The standard-library server binds to localhost,
serves the packaged accessible HTML page, and delegates every response to the
repository application seam. Browser code never imports SQLite or a provider
adapter and never receives credentials over the wire. Use `--port 0` only for
an embedding host or integration test.

The private access mode is explicit:

```bash
cardine-shell-web \
  --repository ./my-study-repository \
  --course-id course-anatomy \
  --session-id session-2026-08-01 \
  --private \
  --production
```

The page includes conversation, material, evidence, context-conflict, and
optional due-review panels. A clear conflict and unavailable recall capability
are explicit empty states, not invented evidence. A configured GPT-5.6 adapter
remains an opt-in host composition; the offline route never silently selects a
provider or claims API-key availability.

## States shown to learners

`working` is emitted as soon as free-form text is accepted. Host suspension and
learner questions are visible as `suspended` or `needs_learner_input`.
Snapshot divergences are shown as `conflicted_context`; due items as
`needs_review`; stale, provider/interruption failure, and a successful refresh
as `stale`, `degraded`, and `recovered` respectively.

## Three-minute sample/eval script

1. (0:00–0:20) Enter the learner request with no onboarding form.
2. (0:20–0:55) Show the bundled material and evidence sequence.
3. (0:55–1:30) Show the host trace: complete, clarification suspension,
   evidence refresh, and recovered completion.
4. (1:30–2:05) Run `cardine-shell --json` and inspect capability discovery,
   parity, and the safe optional recall message.
5. (2:05–2:40) Run the focused tests, Ruff, and source mypy gate.
6. (2:40–2:55) Explain that SQLite and model/provider imports remain outside
   the shell; GPT-5.6 Luna is opt-in and never used by the offline path.

## Container images

The default `Dockerfile` builds a small Cardine CLI image for package and
entry-point verification. It is not a public Cardine release:

```bash
docker build -t cardine-cli .
docker run --rm cardine-cli
```

The authenticated browser deployment uses `Dockerfile.production` and
`compose.production.yaml`; see [private production](private-production.md).
`cardine-shell-web` accepts `--host`, `--port`, `--repository`, `--course-id`,
`--session-id`, `--private`, `--local-owner-setup`, and `--production`.
`--local-owner-setup` is restricted to a loopback bind and keeps the verifier
and runtime credentials in memory. There is no `--public-demo` command-line
flag.
