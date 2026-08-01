# ADR-0019: Private product-shell access and runtime secrets

Date: 2026-07-31
Status: Accepted

## Context

Cardine is becoming a personal, private first product in addition to remaining
a reusable OSS study-agent harness. The owner requires login, account/settings
UI, and the ability to provide the fixed GPT-5.6 Luna API credential without
placing a provider secret in browser storage, repository configuration, domain
events, exports, source-controlled files, or logs.

The core harness remains provider-neutral and does not own users, sessions,
tenancy, or credentials. The existing repository composition already accepts
an injected `environment: Mapping` and resolves the Luna adapter exclusively
through `OPENAI_API_KEY`.

## Decision

Add an explicit private, single-owner mode to `study_agent.demo`, not to the
domain/core packages.

The private product shell owns three bounded modules:

1. A `PrivateAccessController` verifies a versioned scrypt password hash from
   deployment configuration, rate-limits login, and owns bounded opaque
   sessions plus CSRF tokens in process memory.
2. A thread-safe runtime credential store overlays only `OPENAI_API_KEY` on the
   existing environment mapping. It can replace or remove a runtime override
   but never mutates `os.environ`.
3. A private settings application returns safe account/data/model/privacy
   metadata and accepts write-only credential commands.

All private API reads except the session probe require a valid session. Every
private mutation except login requires an exact canonical Origin and an
`X-CSRF-Token` bound to that session. Production cookies use
`__Host-cardine_session`, `HttpOnly`, `Secure`, `SameSite=Strict`, and
`Path=/`. Local loopback development may use a separate non-Secure cookie.

Sessions, CSRF state, login limits, and runtime credentials are intentionally
process-local and disappear on restart. Persistent provider credentials remain
the deployment secret store's responsibility. The UI never receives a key,
password hash, session token readable by JavaScript, repository path, SQLite
handle, or provider error payload.

Public-demo mode remains stateless and cannot construct or reach private
access, settings, or credential state.

## Consequences

- Cardine gains a functional personal login and settings experience without
  adding account/session tables, browser secret storage, or a cryptographic
  dependency.
- Restart logs out every session and removes UI-provided keys. The Settings UI
  must communicate that limitation.
- The private mode is single-owner and single-instance. Multi-user tenancy,
  social/OAuth login, password recovery, billing, and cross-instance sessions
  remain out of scope.
- Remote private binding requires an HTTPS canonical origin and ingress that
  preserves Host while excluding cookies and request bodies from logs.
- XSS can still act as the signed-in owner; existing CSP, escaping, bounded
  DTOs, and absence of browser-stored secrets remain material defenses.
- ADR-0001 continues to govern the OSS harness. This ADR creates a narrow
  exception only for the optional `study_agent.demo` product shell.

## Alternatives Considered

- OAuth or a full web framework: rejected as disproportionate for the current
  personal single-owner stage.
- HTTP Basic: rejected because browser credential caching, logout, expiry, and
  CSRF behavior are insufficient.
- Stateless signed cookies: rejected because logout and revocation still need
  server state.
- SQLite account/session storage: rejected because restart invalidation is
  acceptable and no multi-user state is required.
- Browser, repository, `.env`-file, or event storage for the API key: rejected
  because each crosses a secret or domain boundary.
- Custom encryption in the repository: rejected because standard-library AEAD
  is unavailable and it merely moves the master-key problem.
- Mutating `os.environ`: rejected because the existing injected environment
  seam provides narrower process authority.
