"""Closed, capability-enforcing registry for the seven public v0.1 tools."""

from __future__ import annotations

from typing import cast

from cardine.application.grounding_ask import (
    GroundingAskError,
    GroundingAskErrorCode,
    GroundingAskService,
)
from cardine.courses import (
    CourseCommandError,
    CourseConflictError,
    RetryableCourseConflictError,
)
from study_agent.domain import ExecutionContext, PrincipalKind
from study_agent.domain._validation import JsonObject
from study_agent.ingestion import TextIngestionError
from study_agent.ports import (
    CourseNotFoundError,
    CourseViewPort,
    RetrievalPort,
    SourceContentPort,
    StudyTool,
)
from study_agent.ports.retrieval import RetrievalCatalogPort
from study_agent.retrieval import SourceContentError
from study_agent.sessions import (
    IdempotencyConflictError,
    RetryableSessionConflictError,
    SessionCommandError,
    SessionService,
)

from .builtin import GroundingAskServiceProvider, builtin_tools
from .contracts import IdempotencyMode, ToolError, ToolErrorCode, ToolManifest, ToolResult
from .schema import SchemaValidationError, validate_json, validate_schema_definition


class StudyToolRegistry:
    """A closed composition-root registry; callers cannot register generated tools."""

    def __init__(
        self,
        *,
        courses: CourseViewPort,
        catalog: RetrievalCatalogPort,
        retrieval: RetrievalPort,
        content: SourceContentPort,
        sessions: SessionService,
        grounding: GroundingAskService | GroundingAskServiceProvider,
    ) -> None:
        tools = cast(
            tuple[StudyTool, ...],
            builtin_tools(
                courses=courses,
                catalog=catalog,
                retrieval=retrieval,
                content=content,
                sessions=sessions,
                grounding=grounding,
            ),
        )
        self._tools = {tool.manifest.name: tool for tool in tools}
        if len(tools) != 7 or len(self._tools) != 7:
            raise RuntimeError("the public v0.1 registry must contain exactly seven unique tools")
        for tool in tools:
            validate_schema_definition(tool.manifest.input_schema)
            validate_schema_definition(tool.manifest.output_schema)

    @property
    def manifests(self) -> tuple[ToolManifest, ...]:
        return tuple(self._tools[name].manifest for name in sorted(self._tools))

    async def invoke(
        self, name: str, arguments: JsonObject, context: ExecutionContext
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return _failure(ToolErrorCode.INVALID_ARGUMENTS, "unknown study tool")
        manifest = tool.manifest
        try:
            validate_json(arguments, manifest.input_schema)
        except (SchemaValidationError, ValueError, TypeError):
            return _failure(ToolErrorCode.INVALID_ARGUMENTS, "tool arguments are invalid")
        if not set(manifest.required_capabilities) <= context.requested_capabilities:
            return _failure(ToolErrorCode.UNAUTHORIZED, "required capability was not granted")
        if not isinstance(context.principal_kind, PrincipalKind):
            return _failure(ToolErrorCode.UNAUTHORIZED, "tool context principal is not trusted")
        if manifest.idempotency is IdempotencyMode.REQUIRED and context.idempotency_key is None:
            return _failure(ToolErrorCode.INVALID_ARGUMENTS, "tool invocation requires idempotency")
        try:
            result = await tool.invoke(arguments, context)
            if result.error is not None:
                if result.error.code not in manifest.error_codes:
                    return _failure(
                        ToolErrorCode.INCOMPATIBLE_RUNTIME, "tool returned an undeclared error"
                    )
                return result
            assert result.value is not None
            try:
                validate_json(result.value, manifest.output_schema)
            except (SchemaValidationError, ValueError, TypeError):
                return _failure(
                    ToolErrorCode.INCOMPATIBLE_RUNTIME,
                    "tool output violated its declared contract",
                )
            declared = set(manifest.emitted_event_kinds)
            if any(event.kind.value not in declared for event in result.events):
                return _failure(
                    ToolErrorCode.INCOMPATIBLE_RUNTIME, "tool emitted an undeclared event"
                )
            return result
        except GroundingAskError as error:
            return _grounding_error(error)
        except (CourseNotFoundError, LookupError):
            return _failure(ToolErrorCode.NOT_FOUND, "requested study state was not found")
        except SourceContentError as error:
            if error.code.value == "not_found":
                return _failure(ToolErrorCode.NOT_FOUND, "requested source state was not found")
            return _failure(ToolErrorCode.CONFLICT, "canonical source state could not be verified")
        except (IdempotencyConflictError, CourseConflictError):
            return _failure(ToolErrorCode.CONFLICT, "request conflicts with canonical state")
        except (RetryableSessionConflictError, RetryableCourseConflictError):
            return _failure(
                ToolErrorCode.RETRYABLE_CONFLICT,
                "canonical state advanced; retry safely",
                retryable=True,
            )
        except (SessionCommandError, CourseCommandError, TextIngestionError, ValueError, TypeError):
            return _failure(ToolErrorCode.INVALID_ARGUMENTS, "request violates the study contract")
        except Exception:
            return _failure(ToolErrorCode.EXECUTION_FAILED, "study tool execution failed safely")


def _failure(code: ToolErrorCode, message: str, *, retryable: bool = False) -> ToolResult:
    return ToolResult.failure(ToolError(code, message, retryable))


def _grounding_error(error: GroundingAskError) -> ToolResult:
    code = {
        GroundingAskErrorCode.INVALID_REQUEST: ToolErrorCode.INVALID_ARGUMENTS,
        GroundingAskErrorCode.UNAUTHORIZED: ToolErrorCode.UNAUTHORIZED,
        GroundingAskErrorCode.NOT_FOUND: ToolErrorCode.NOT_FOUND,
        GroundingAskErrorCode.CONFLICT: ToolErrorCode.CONFLICT,
        GroundingAskErrorCode.RETRYABLE_CONFLICT: ToolErrorCode.RETRYABLE_CONFLICT,
        GroundingAskErrorCode.RUNNING: ToolErrorCode.CONFLICT,
        GroundingAskErrorCode.SUSPENDED: ToolErrorCode.CONFLICT,
        GroundingAskErrorCode.FAILED: ToolErrorCode.EXECUTION_FAILED,
        GroundingAskErrorCode.INCOMPATIBLE_RUNTIME: ToolErrorCode.INCOMPATIBLE_RUNTIME,
        GroundingAskErrorCode.EXECUTION_FAILED: ToolErrorCode.EXECUTION_FAILED,
    }[error.code]
    return _failure(
        code,
        "grounded question could not be completed",
        retryable=code is ToolErrorCode.RETRYABLE_CONFLICT,
    )
