# Log: private production container

Date: 2026-08-01 11:45
Area: product-shell

## Summary

Prepared the complete repository-backed Cardine product for a private
production deployment. The existing `Dockerfile` remains the sanitized demo;
the new production image always starts `--private --production`, mounts a
persistent repository, and requires explicit owner/origin/course/session
configuration.

## Files Changed

- `Dockerfile.production`: pinned non-root production image with healthcheck.
- `docker/production-entrypoint.sh`: fail-closed production command wrapper.
- `compose.production.yaml`: single-replica reference topology with read-only
  filesystem, dropped capabilities, and persistent repository volume.
- `src/study_agent/demo/browser.py`: allow `0.0.0.0` only for explicit private
  production mode; local private mode remains localhost-only.
- `pyproject.toml`: add the password-hash generation command.
- `docs/private-production.md`: runtime contract and release gates.
- `tests/unit/demo/test_private_production_packaging.py`: packaging guardrails.

## Verification

- `python -m pytest -q tests/unit/demo tests/integration/demo/TUT08 tests/e2e/test_cardine_private_product_journey.py tests/e2e/test_cardine_repository_browser_journey.py::test_public_demo_browser_is_stateless_read_only_and_redacted`: 139 passed.
- `python -m ruff check src/study_agent/demo tests/unit/demo`: passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `sh -n docker/production-entrypoint.sh`: passed.
- `git diff --check`: passed.
- Docker image build was not run because Docker is not installed in this
  environment.

## Deployment Boundary

No external deployment was performed. The remaining release inputs are a
hosting target, HTTPS domain, persistent repository volume, owner password
hash, and deployment-managed `OPENAI_API_KEY`. The production process must
remain single-replica and sit behind a TLS/rate-limiting ingress.
