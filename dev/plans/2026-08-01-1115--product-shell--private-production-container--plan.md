# Plan: private production container

Date: 2026-08-01 11:15
Area: product-shell

## Goal

Make the complete repository-backed Cardine product runnable in a production
container. The target is the authenticated single-owner surface, not the
sanitized public demo.

## Scope

- In scope:
  - production-only container image and entrypoint;
  - persistent repository volume and explicit course/session configuration;
  - `--private --production` binding behind a TLS ingress;
  - health, secret, and single-replica operational requirements;
  - focused tests and deployment documentation.
- Out of scope:
  - selecting or provisioning a hosting vendor;
  - publishing a public URL;
  - multi-user accounts, OAuth, horizontal scaling, or session replication.

## Approach

1. Keep the existing `Dockerfile` as the stateless public-demo fixture.
2. Add a production image/entrypoint that requires repository, course, and
   session settings and never defaults to public-demo mode.
3. Permit `0.0.0.0` only for explicit private production mode; retain the
   localhost-only guard for development/private non-production mode.
4. Document TLS termination, exact canonical origin, persistent volume,
   `OPENAI_API_KEY`, owner password hash, one replica, and ingress limits.

## Risks

- Private sessions and runtime credential overrides are process-local, so
  multiple replicas are not supported.
- SQLite/event repository writes require a single writer and a persistent
  volume; ephemeral container storage is not acceptable.
- The in-process HTTP server still requires a real TLS/rate-limiting ingress.

## Verification

- Unit coverage for bind-mode gating and CLI production requirements.
- Focused private browser/API tests.
- Ruff, Node syntax, and package/build configuration checks.
