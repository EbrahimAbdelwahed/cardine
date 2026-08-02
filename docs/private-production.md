# Cardine private production

This is the deployment path for the complete Cardine product: repository-backed
study state, the chat UI, login, settings, and the GPT-5.6 Luna adapter. It is
the only supported container topology for the authenticated browser surface.

## Runtime contract

Run exactly one Cardine replica against a persistent repository directory. The
private access controller, login sessions, CSRF state, and UI-entered runtime
credential override are process-local by design; a restart logs the owner out
and clears the UI-entered override. Configure `OPENAI_API_KEY` in the host
secret store for a stable production credential.

The application must sit behind a TLS ingress that preserves the canonical
`Host` header and forwards only bounded requests. The ingress should enforce
TLS, request/body/time limits, per-client login throttling, and access logs
that omit request bodies. Cardine's built-in HTTP server is not a TLS
terminator.

## Container

The production image is separate from the stateless public-demo image:

```bash
docker build -f Dockerfile.production -t cardine:production .
```

The image entrypoint refuses to start without the repository, course, session,
owner password hash, and HTTPS public origin. It starts the product with
`--private --production`; it never falls back to public-demo mode.

For a host-managed reverse proxy, the included Compose file is a reference
topology:

```bash
docker compose -f compose.production.yaml up -d --build
```

Set these values in the deployment environment or secret store before starting:

```text
CARDINE_OWNER_PASSWORD_HASH=scrypt$v1$...
CARDINE_PUBLIC_ORIGIN=https://study.example.com
CARDINE_COURSE_ID=course-anatomy
CARDINE_SESSION_ID=session-2026-08-01
CARDINE_REPOSITORY_PATH=/srv/cardine/repository
OPENAI_API_KEY=...
```

Generate the owner hash without placing the password in shell history:

```bash
uv run cardine-private-password-hash --confirm
```

The repository volume must be backed up and writable by the container's
non-root user (`uid 10001`). The container exposes port `8080` to a local
reverse proxy; it should not be published directly to the Internet.

## Release gates

Before switching the ingress to the production origin, verify:

- `/health` returns `{"status":"ok","mode":"private"}`;
- login succeeds only through the HTTPS canonical origin;
- the `__Host-cardine_session` cookie is `Secure`, `HttpOnly`, `SameSite=Strict`,
  and `Path=/`;
- the repository survives a container restart and the process remains a
  single replica;
- the browser console is clean and the full private browser journey passes;
- ingress logs contain no password, API key, or learner message bodies.
