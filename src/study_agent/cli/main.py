"""Stdlib argparse entry point for the reference CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Never, cast

from study_agent.application import GroundingAskError
from study_agent.domain._validation import JsonObject
from study_agent.lifecycle import RetryableLifecycleConflictError, StaleLifecyclePlanError
from study_agent.ports import CourseNotFoundError, SessionNotFoundError
from study_agent.repository_config import (
    LocalConfigError,
    LocalRepositoryConfig,
    ModelAdapterConfig,
)
from study_agent.sessions import RetryableSessionConflictError

from .commands import SourceIndexError, _DeferredSigint, execute, execute_without_repository
from .lifecycle import LifecyclePlanExpectationError
from .output import CommandOutcome, emit_error, emit_success
from .registry import CommandRequest, RepositoryRequirement, configure_parser, registration_for
from .repository import (
    LocalRepositoryError,
    ModelAdapterConfigurationError,
    ModelAdapterRegistry,
)


class CliArgumentError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise CliArgumentError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="cardine", description="Local event-sourced Cardine study repository")
    parser.add_argument("--repository", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--json", action="store_true", help="emit one JSON document")
    configure_parser(parser)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    model_adapters: ModelAdapterRegistry | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    raw = tuple(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in raw
    try:
        parse_arguments = tuple(item for item in raw if item != "--json")
        if json_mode and any(item in {"-h", "--help"} for item in parse_arguments):
            help_stream = StringIO()
            try:
                with redirect_stdout(help_stream):
                    build_parser().parse_args(parse_arguments)
            except SystemExit as error:
                if error.code != 0:
                    raise
            emit_success(
                CommandOutcome("help", {"text": help_stream.getvalue()}),
                json_mode=True,
            )
            return 0
        namespace = build_parser().parse_args(parse_arguments)
        namespace.json = json_mode
        request = _request(namespace)
        registration = registration_for(request.name)
        # Auto-session creation and its run live in separate durable stores. Treat the
        # complete authoritative host operation, including its success emission, as one
        # narrow SIGINT-deferred region: success wins once canonical work has committed.
        with _DeferredSigint(enabled=_automatic_session_ask(request)):
            if registration.repository is RepositoryRequirement.NONE:
                outcome = execute_without_repository(request)
            elif model_adapters is None and environment is None:
                outcome = asyncio.run(execute(request))
            else:
                outcome = asyncio.run(
                    execute(
                        request,
                        model_adapters=model_adapters,
                        environment=environment,
                    )
                )
            emit_success(outcome, json_mode=json_mode)
    except KeyboardInterrupt:
        emit_error(
            "interrupted",
            "operation interrupted; no cancellation transition was fabricated",
            json_mode=json_mode,
        )
        return 130
    except SourceIndexError as error:
        emit_error(
            "index_rebuild_failed",
            str(error),
            json_mode=json_mode,
            details={"canonical_source": error.committed},
        )
        return 4
    except ModelAdapterConfigurationError:
        emit_error(
            "model_unavailable",
            "configured model adapter is unavailable",
            json_mode=json_mode,
        )
        return 4
    except (LocalConfigError, LocalRepositoryError):
        emit_error(
            "repository_error",
            "local repository is absent or incompatible",
            json_mode=json_mode,
        )
        return 4
    except (CliArgumentError, ValueError, TypeError) as error:
        emit_error("invalid_request", _safe_message(error), json_mode=json_mode)
        return 2
    except (CourseNotFoundError, SessionNotFoundError, FileNotFoundError) as error:
        del error
        emit_error(
            "not_found",
            "requested canonical or local resource was not found",
            json_mode=json_mode,
        )
        return 3
    except GroundingAskError as error:
        emit_error(error.code.value, str(error), json_mode=json_mode)
        return 4
    except RetryableSessionConflictError:
        emit_error(
            "retryable_conflict",
            "session state changed concurrently; retry with the same host-supplied identity",
            json_mode=json_mode,
        )
        return 4
    except LifecyclePlanExpectationError as error:
        emit_error(
            "lifecycle_plan_mismatch",
            "authorized lifecycle plan is stale; replan and retry",
            json_mode=json_mode,
            details={
                "expected_plan": error.expected,
                "observed_plan": error.observed.fingerprint,
            },
        )
        return 4
    except StaleLifecyclePlanError as error:
        emit_error(
            "lifecycle_plan_stale",
            "lifecycle inputs changed during apply; replan and retry",
            json_mode=json_mode,
            details={
                "expected_plan": error.expected_plan.fingerprint,
                "observed_plan": error.observed_plan.fingerprint,
            },
        )
        return 4
    except RetryableLifecycleConflictError as error:
        emit_error(
            "lifecycle_retryable_conflict",
            "canonical state changed during apply; replan and retry",
            json_mode=json_mode,
            details={"receipt": error.receipt.to_json()},
        )
        return 4
    except (OSError, RuntimeError):
        emit_error(
            "operational_failure",
            "operation could not be completed safely",
            json_mode=json_mode,
        )
        return 4
    return 0


def _request(namespace: argparse.Namespace) -> CommandRequest:
    values = vars(namespace).copy()
    repository = Path(values.pop("repository"))
    values.pop("json", None)
    values.pop("group", None)
    values.pop("action", None)
    name = str(values.pop("command_name"))
    if name == "init":
        repository = Path(values.pop("directory"))
        values["config"] = _init_config(values)
    return CommandRequest(repository, name, values)


def _init_config(values: dict[str, object]) -> LocalRepositoryConfig:
    adapter = values.pop("model_adapter")
    raw_settings = values.pop("model_setting")
    credential_env = values.pop("credential_env")
    if adapter is None:
        if raw_settings or credential_env is not None:
            raise CliArgumentError("model settings require --model-adapter")
        return LocalRepositoryConfig()
    if not isinstance(adapter, str) or not isinstance(raw_settings, list):
        raise CliArgumentError("model adapter configuration is invalid")
    settings: dict[str, object] = {}
    for assignment in raw_settings:
        if not isinstance(assignment, str) or "=" not in assignment:
            raise CliArgumentError("--model-setting must use NAME=JSON")
        key, payload = assignment.split("=", 1)
        if not key or key in settings:
            raise CliArgumentError("model setting names must be non-empty and unique")
        try:
            settings[key] = json.loads(payload)
        except json.JSONDecodeError as error:
            raise CliArgumentError("model setting values must be JSON") from error
    return LocalRepositoryConfig(
        ModelAdapterConfig(
            adapter,
            cast(JsonObject, settings),
            None if credential_env is None else str(credential_env),
        )
    )


def _safe_message(error: BaseException) -> str:
    message = str(error).strip()
    return message if message and "api" not in message.lower() else "request is invalid"


def _automatic_session_ask(request: CommandRequest) -> bool:
    return request.name == "ask" and request.values.get("session_id") is None


def color_enabled() -> bool:
    """CLI semantics never depend on color; this exposes the conventional policy."""
    return "NO_COLOR" not in os.environ and sys.stdout.isatty()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "color_enabled", "main"]
