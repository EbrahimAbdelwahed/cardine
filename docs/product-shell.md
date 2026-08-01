# Build Week product shell

The product shell is a small conversation-first consumer of the public tutor
contracts. It accepts a free-form learner entry before the context is complete,
then renders the current conversation, material snapshot, learner evidence
sequence, context conflicts, capability discovery, and optional due review.

The default journey is deterministic and offline:

```bash
study-agent-shell "I have ten minutes. Help me understand heart valves."
```

Use `--json` to inspect the same trace in scripts. The shell command reuses the
existing anatomy host trace; it does not create another tutor loop or write
canonical state. The optional TUT-07 due-review view is omitted safely when no
recall composition is installed.

For a browser-visible reference surface, run:

```bash
study-agent-shell-web
```

Open <http://127.0.0.1:8765/>. The standard-library server binds to localhost,
serves a packaged accessible HTML page, and exposes `GET /api/state` plus
`POST /api/entry` for the same bounded free-form journey. The browser owns only
the latest presentation input in memory; every response is delegated to the
existing `run_offline_shell_demo` / `ProductShell` seam. It never imports
SQLite or a provider adapter and never sends credentials over the wire. Use
`--port 0` only for an embedding host or an integration test.

The page includes conversation, material, evidence, context-conflict, and
optional due-review panels. A clear conflict and unavailable recall capability
are explicit empty states, not invented evidence. The offline route is the only
browser mode in this package. A configured GPT-5.6 adapter remains an opt-in
host composition through the existing host port; this local reference server
does not silently select a provider or claim API-key availability.

## States shown to learners

`working` is emitted as soon as free-form text is accepted. Host suspension and
learner questions are visible as `suspended` or `needs_learner_input`.
Snapshot divergences are shown as `conflicted_context`; due items as
`needs_review`; stale, provider/interruption failure, and a successful refresh
as `stale`, `degraded`, and `recovered` respectively.

## Three-minute sample/eval/video script

1. (0:00–0:20) Enter the learner request with no onboarding form.
2. (0:20–0:55) Show the bundled material and evidence sequence.
3. (0:55–1:30) Show the host trace: complete, clarification suspension,
   evidence refresh, and recovered completion.
4. (1:30–2:05) Run `study-agent-shell --json` and point to capability
   discovery, parity, and the safe optional recall message.
5. (2:05–2:40) Run the focused tests, Ruff, and source mypy gate.
6. (2:40–2:55) Explain that SQLite and model/provider imports remain outside
   the shell; an API-key GPT-5.6 adapter is opt-in and never used by the
offline path.

## Public demo container

The repository includes a production container for the sanitized, stateless
demo surface:

```bash
docker build -t cardine-demo .
docker run --read-only --tmpfs /tmp --cap-drop ALL \
  -p 127.0.0.1:8080:8080 cardine-demo
```

The container runs as a non-root user and starts
`study-agent-shell-web --public-demo`. Public-demo mode is the only mode that
may bind `0.0.0.0`; it serves the packaged UI, health check, and `/api/v1/*`
fixture routes while returning `404` for the legacy mutable `/api/state` and
`/api/entry` routes. It does not mount a repository, accept credentials, call a
provider, or persist learner text. Deploy it behind an ingress that supplies
TLS, request-rate limits, body/time limits, and access logs that omit request
bodies. The built-in server also caps concurrent request workers and applies a
socket timeout, but it is not a replacement for a production ingress.

The implementation uses only the Python standard library and a packaged page:
it provides an accessible, deterministic proof surface without a web framework
or a second UI state owner. The terminal and browser surfaces share the same
immutable view projection and offline anatomy journey.

The complete repository-backed product has a separate private production
topology. See [private production](private-production.md); do not use the
stateless public-demo image for the authenticated product.
