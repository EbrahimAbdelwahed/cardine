"""Closed command registrations and static automation discovery for the CLI."""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from study_agent import __version__
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.operator_skill import skill_metadata
from study_agent.repository_config import CONFIG_SCHEMA_VERSION
from study_agent.tools.builtin import public_study_tool_manifests
from study_agent.tools.contracts import ToolManifest

if TYPE_CHECKING:
    from .output import CommandOutcome
    from .repository import LocalRepository

_MAX_COMMANDS = 64
_MAX_ARGUMENTS = 64
_OUTPUT_CONTRACT = "cli-envelope@1"


class OperationEffect(StrEnum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    CANONICAL_WRITE = "canonical_write"
    OPERATIONAL_WRITE = "operational_write"
    EXTERNAL_MODEL = "external_model"


class RepositoryRequirement(StrEnum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class NetworkRequirement(StrEnum):
    NEVER = "never"
    MODEL_ONLY = "model_only"


class ArgumentKind(StrEnum):
    POSITIONAL = "positional"
    OPTION = "option"


class ArgumentValueType(StrEnum):
    STRING = "string"
    PATH = "path"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class CommandRequest:
    repository: Path
    name: str
    values: dict[str, object]


class CommandHandler(Protocol):
    def __call__(
        self, request: CommandRequest, repository: LocalRepository | None
    ) -> CommandOutcome | Awaitable[CommandOutcome]: ...


@dataclass(frozen=True, slots=True)
class ArgumentDescriptor:
    name: str
    kind: ArgumentKind
    value_type: ArgumentValueType
    required: bool
    repeated: bool = False
    default_json: JsonValue = None
    secret: bool = False

    def to_json(self) -> JsonObject:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "value_type": self.value_type.value,
            "required": self.required,
            "repeated": self.repeated,
            "default_json": self.default_json,
            "secret": self.secret,
        }


class _ParserTopology:
    """The fixed argparse groups used by this CLI, created in registration order."""

    def __init__(self, root: argparse.ArgumentParser) -> None:
        self.root = root.add_subparsers(dest="group", required=True)
        self._groups: dict[str, argparse._SubParsersAction[argparse.ArgumentParser]] = {}

    def group(
        self, name: str, help_text: str
    ) -> argparse._SubParsersAction[argparse.ArgumentParser]:
        existing = self._groups.get(name)
        if existing is not None:
            return existing
        parser = self.root.add_parser(name, help=help_text)
        actions = parser.add_subparsers(dest="action", required=True)
        self._groups[name] = actions
        return actions


ParserCallback = Callable[[_ParserTopology], None]


@dataclass(frozen=True, slots=True)
class CommandRegistration:
    name: str
    version: str
    summary: str
    effect: OperationEffect
    repository: RepositoryRequirement
    network: NetworkRequirement
    idempotency: str
    retry: str
    arguments: tuple[ArgumentDescriptor, ...]
    output_contract: str
    verification: str
    parser: ParserCallback
    handler: CommandHandler

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise ValueError("command name must be non-empty trimmed text")
        if len(self.arguments) > _MAX_ARGUMENTS:
            raise ValueError("command argument declaration exceeds contract bound")
        if len({item.name for item in self.arguments}) != len(self.arguments):
            raise ValueError("command argument names must be unique")

    def to_json(self) -> JsonObject:
        return {
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "effect": self.effect.value,
            "repository": self.repository.value,
            "network": self.network.value,
            "idempotency": self.idempotency,
            "retry": self.retry,
            "arguments": tuple(item.to_json() for item in self.arguments),
            "output_contract": self.output_contract,
            "verification": self.verification,
        }


def configure_parser(parser: argparse.ArgumentParser) -> None:
    topology = _ParserTopology(parser)
    for registration in command_registrations():
        registration.parser(topology)


@lru_cache(maxsize=1)
def command_registrations() -> tuple[CommandRegistration, ...]:
    """Return the closed, process-static registration set in CLI display order."""
    from . import commands

    registrations = (
        _registration(
            "init",
            "Initialize an offline local repository.",
            OperationEffect.LOCAL_WRITE,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "convergent for a new empty target",
            "inspect the target before retrying after lost output",
            (
                _argument("directory", ArgumentKind.POSITIONAL, ArgumentValueType.PATH, True),
                _argument("model_adapter", ArgumentKind.OPTION, ArgumentValueType.STRING, False),
                _argument(
                    "model_setting",
                    ArgumentKind.OPTION,
                    ArgumentValueType.STRING,
                    False,
                    repeated=True,
                    default_json=(),
                ),
                _argument("credential_env", ArgumentKind.OPTION, ArgumentValueType.STRING, False),
            ),
            "cardine --json init REPOSITORY",
            _add_init,
            commands.handle_init,
        ),
        _registration(
            "course.create",
            "Create an immutable course profile.",
            OperationEffect.CANONICAL_WRITE,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "derived or host-supplied course identity",
            "retry with the same arguments and course identity",
            (
                _argument("course_id", ArgumentKind.OPTION, ArgumentValueType.STRING, False),
                _argument("title", ArgumentKind.OPTION, ArgumentValueType.STRING, True),
                _argument(
                    "language",
                    ArgumentKind.OPTION,
                    ArgumentValueType.STRING,
                    False,
                    default_json="en",
                ),
                _argument("exam_date", ArgumentKind.OPTION, ArgumentValueType.STRING, False),
                _argument(
                    "learning_goal",
                    ArgumentKind.OPTION,
                    ArgumentValueType.STRING,
                    True,
                    repeated=True,
                ),
                _argument(
                    "assessment_style",
                    ArgumentKind.OPTION,
                    ArgumentValueType.STRING,
                    False,
                    repeated=True,
                    default_json=(),
                ),
            ),
            "cardine --json --repository REPOSITORY course create --help",
            _add_course_create,
            commands.handle_course_create,
        ),
        _registration(
            "course.list",
            "List canonical course profiles in deterministic order.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "not applicable",
            "safe to retry",
            (),
            "cardine --json --repository REPOSITORY course list",
            _add_course_list,
            commands.handle_course_list,
        ),
        _registration(
            "source.add",
            "Ingest a text or Markdown source.",
            OperationEffect.CANONICAL_WRITE,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "derived or host-supplied source identity with immutable revisions",
            "inspect source list before retrying after lost output",
            (
                _argument("course_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
                _argument("path", ArgumentKind.POSITIONAL, ArgumentValueType.PATH, True),
                _argument("source_id", ArgumentKind.OPTION, ArgumentValueType.STRING, False),
                _argument("title", ArgumentKind.OPTION, ArgumentValueType.STRING, False),
                _argument(
                    "trust_level",
                    ArgumentKind.OPTION,
                    ArgumentValueType.INTEGER,
                    False,
                    default_json=50,
                ),
                _argument(
                    "source_role",
                    ArgumentKind.OPTION,
                    ArgumentValueType.STRING,
                    False,
                    default_json="course_material",
                ),
            ),
            "cardine --json --repository REPOSITORY source list COURSE_ID",
            _add_source_add,
            commands.handle_source_add,
        ),
        _registration(
            "source.list",
            "List canonical source revisions.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "not applicable",
            "safe to retry",
            (_argument("course_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),),
            "cardine --json --repository REPOSITORY source list COURSE_ID",
            _add_source_list,
            commands.handle_source_list,
        ),
        _registration(
            "ask",
            "Ask a grounded question; automatic identities are convenience-only.",
            OperationEffect.EXTERNAL_MODEL,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.MODEL_ONLY,
            "agent-safe only with host-supplied session and idempotency identities",
            "automatic identities are not crash-retry-safe; retry only with the same explicit "
            "session-id, idempotency-key, and question",
            (
                _argument("course_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
                _argument("question", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
                _argument("session_id", ArgumentKind.OPTION, ArgumentValueType.STRING, False),
                _argument("idempotency_key", ArgumentKind.OPTION, ArgumentValueType.STRING, False),
            ),
            "cardine --json --repository REPOSITORY ask COURSE_ID QUESTION --help",
            _add_ask,
            commands.handle_ask,
        ),
        _registration(
            "session.list",
            "List sessions for a course.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "not applicable",
            "safe to retry",
            (_argument("course_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),),
            "cardine --json --repository REPOSITORY session list COURSE_ID",
            _add_session_list,
            commands.handle_session_list,
        ),
        _registration(
            "session.start",
            "Start an explicitly identified session idempotently.",
            OperationEffect.CANONICAL_WRITE,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "stable host-supplied course and session identity",
            "safe to retry with the same course and session identities",
            (
                _argument("course_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
                _argument("session_id", ArgumentKind.OPTION, ArgumentValueType.STRING, True),
            ),
            "cardine --json --repository REPOSITORY session get COURSE_ID SESSION_ID",
            _add_session_start,
            commands.handle_session_start,
        ),
        _registration(
            "session.get",
            "Read one explicitly identified session receipt.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "not applicable",
            "safe to retry",
            (
                _argument("course_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
                _argument("session_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
            ),
            "cardine --json --repository REPOSITORY session get COURSE_ID SESSION_ID",
            _add_session_get,
            commands.handle_session_get,
        ),
        _registration(
            "session.resume",
            "Resume an explicitly identified session.",
            OperationEffect.CANONICAL_WRITE,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "stable host-supplied session identity",
            "retry with the same course and session identities",
            (
                _argument("course_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
                _argument("session_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
            ),
            "cardine --json --repository REPOSITORY session resume COURSE_ID SESSION_ID",
            _add_session_resume,
            commands.handle_session_resume,
        ),
        _registration(
            "export",
            "Write deterministic export v1, v2, or v3.",
            OperationEffect.LOCAL_WRITE,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "deterministic for the same event high-water mark",
            "safe to retry to the same destination",
            (
                _argument("course_id", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),
                _argument("output", ArgumentKind.OPTION, ArgumentValueType.PATH, True),
                _argument(
                    "version",
                    ArgumentKind.OPTION,
                    ArgumentValueType.STRING,
                    False,
                    default_json="1",
                ),
            ),
            "cardine --json --repository REPOSITORY export COURSE_ID --output PATH --version 3",
            _add_export,
            commands.handle_export,
        ),
        _registration(
            "doctor",
            "Run offline integrity diagnostics.",
            OperationEffect.OPERATIONAL_WRITE,
            RepositoryRequirement.REQUIRED,
            NetworkRequirement.NEVER,
            "rebuilds discardable operational state",
            "safe to retry",
            (),
            "cardine --json --repository REPOSITORY doctor",
            _add_doctor,
            commands.handle_doctor,
        ),
        _registration(
            "operator.skill",
            "Extract the versioned operator skill from the installed distribution.",
            OperationEffect.LOCAL_WRITE,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "byte-identical for the same installed distribution",
            "safe to retry to an absent or byte-identical destination",
            (_argument("output", ArgumentKind.OPTION, ArgumentValueType.PATH, True),),
            "verify the receipt fingerprint against the extracted bytes",
            _add_operator_skill,
            commands.handle_operator_skill,
        ),
        _registration(
            "manifest.schema",
            "Describe the closed lifecycle manifest v1 schema without file access.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "not applicable",
            "safe to retry",
            (),
            "cardine --json manifest schema",
            _add_manifest_schema,
            commands.handle_manifest_schema,
        ),
        _registration(
            "manifest.validate",
            "Validate and fingerprint one bounded lifecycle manifest.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "content-addressed by the canonical manifest fingerprint",
            "safe to retry while the selected manifest is unchanged",
            (
                _argument(
                    "path",
                    ArgumentKind.POSITIONAL,
                    ArgumentValueType.PATH,
                    False,
                    default_json="./study-agent.manifest.json",
                ),
            ),
            "compare the reported fingerprint and counts",
            _add_manifest_validate,
            commands.handle_manifest_validate,
        ),
        _registration(
            "manifest.plan",
            "Plan lifecycle reconciliation from verified desired and observed state.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "content-addressed by the lifecycle plan fingerprint",
            "safe to retry while manifest, sources, and repository state are unchanged",
            (
                _argument(
                    "path",
                    ArgumentKind.POSITIONAL,
                    ArgumentValueType.PATH,
                    False,
                    default_json="./study-agent.manifest.json",
                ),
            ),
            "compare the reported plan fingerprint before apply",
            _add_manifest_plan,
            commands.handle_manifest_plan,
        ),
        _registration(
            "manifest.status",
            "Report lifecycle convergence and drift without mutation.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "content-addressed by the lifecycle plan fingerprint",
            "safe to retry while manifest, sources, and repository state are unchanged",
            (
                _argument(
                    "path",
                    ArgumentKind.POSITIONAL,
                    ArgumentValueType.PATH,
                    False,
                    default_json="./study-agent.manifest.json",
                ),
            ),
            "inspect status kind, plan fingerprint, conflicts, and warnings",
            _add_manifest_status,
            commands.handle_manifest_status,
        ),
        _registration(
            "manifest.apply",
            "Apply one explicitly authorized lifecycle plan with per-action revalidation.",
            OperationEffect.CANONICAL_WRITE,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "plan-derived per-action identities and canonical event idempotency",
            "replan and retry with the newly reported plan fingerprint",
            (
                _argument(
                    "path",
                    ArgumentKind.POSITIONAL,
                    ArgumentValueType.PATH,
                    False,
                    default_json="./study-agent.manifest.json",
                ),
                _argument(
                    "expect_plan",
                    ArgumentKind.OPTION,
                    ArgumentValueType.STRING,
                    True,
                ),
            ),
            "run manifest status and verify convergence or operational degradation",
            _add_manifest_apply,
            commands.handle_manifest_apply,
        ),
        _registration(
            "describe",
            "Describe the agent-operable harness contract.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "not applicable",
            "safe to retry",
            (),
            "cardine --json describe",
            _add_describe,
            commands.handle_describe,
        ),
        _registration(
            "tool.list",
            "List static public StudyTool manifests.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "not applicable",
            "safe to retry",
            (),
            "cardine --json tool list",
            _add_tool_list,
            commands.handle_tool_list,
        ),
        _registration(
            "tool.describe",
            "Describe one static public StudyTool manifest.",
            OperationEffect.READ_ONLY,
            RepositoryRequirement.NONE,
            NetworkRequirement.NEVER,
            "not applicable",
            "safe to retry",
            (_argument("name", ArgumentKind.POSITIONAL, ArgumentValueType.STRING, True),),
            "cardine --json tool describe grounding.ask",
            _add_tool_describe,
            commands.handle_tool_describe,
        ),
    )
    if len(registrations) > _MAX_COMMANDS:
        raise RuntimeError("command registration exceeds contract bound")
    names = tuple(item.name for item in registrations)
    if len(set(names)) != len(names):
        raise RuntimeError("command registrations must have unique identities")
    return registrations


def registration_for(name: str) -> CommandRegistration:
    try:
        return next(item for item in command_registrations() if item.name == name)
    except StopIteration as error:
        raise ValueError("unknown command") from error


def agent_operations_manifest() -> JsonObject:
    commands = tuple(
        item.to_json() for item in sorted(command_registrations(), key=lambda item: item.name)
    )
    tools = tuple(_tool_entry(item) for item in public_study_tool_manifests())
    return {
        "contract_version": "agent-operations@1",
        "harness_version": __version__,
        "repository_schema_versions": (CONFIG_SCHEMA_VERSION,),
        "offline_default": True,
        "commands": commands,
        "study_tools": tools,
        "operator_skill": skill_metadata(),
    }


def public_study_tool_entries() -> tuple[JsonObject, ...]:
    return tuple(_tool_entry(item) for item in public_study_tool_manifests())


def _tool_entry(manifest: ToolManifest) -> JsonObject:
    return {"manifest": _plain_object(manifest.to_json()), "fingerprint": manifest.fingerprint}


def _plain_object(value: JsonObject) -> JsonObject:
    def plain(item: JsonValue) -> JsonValue:
        if isinstance(item, Mapping):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return tuple(plain(child) for child in item)
        return item

    return {key: plain(item) for key, item in value.items()}


def _registration(
    name: str,
    summary: str,
    effect: OperationEffect,
    repository: RepositoryRequirement,
    network: NetworkRequirement,
    idempotency: str,
    retry: str,
    arguments: tuple[ArgumentDescriptor, ...],
    verification: str,
    parser: ParserCallback,
    handler: CommandHandler,
) -> CommandRegistration:
    return CommandRegistration(
        name,
        "1.0.0",
        summary,
        effect,
        repository,
        network,
        idempotency,
        retry,
        arguments,
        _OUTPUT_CONTRACT,
        verification,
        parser,
        handler,
    )


def _argument(
    name: str,
    kind: ArgumentKind,
    value_type: ArgumentValueType,
    required: bool,
    *,
    repeated: bool = False,
    default_json: JsonValue = None,
) -> ArgumentDescriptor:
    return ArgumentDescriptor(name, kind, value_type, required, repeated, default_json)


def _leaf(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    parser.set_defaults(command_name=name)
    return parser


def _add_init(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.root.add_parser("init", help="initialize an offline local repository"), "init"
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument("--model-adapter")
    parser.add_argument(
        "--model-setting",
        action="append",
        default=[],
        metavar="NAME=JSON",
        help="technical adapter setting; repeat for multiple values",
    )
    parser.add_argument("--credential-env", help="environment-variable name, never its value")


def _add_course_create(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("course", "course commands").add_parser(
            "create", help="create an immutable course profile"
        ),
        "course.create",
    )
    parser.add_argument("--course-id")
    parser.add_argument("--title", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--exam-date")
    parser.add_argument("--learning-goal", dest="learning_goals", action="append", required=True)
    parser.add_argument("--assessment-style", dest="assessment_styles", action="append", default=[])


def _add_course_list(topology: _ParserTopology) -> None:
    _leaf(
        topology.group("course", "course commands").add_parser(
            "list", help="list canonical course profiles"
        ),
        "course.list",
    )


def _add_source_add(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("source", "source commands").add_parser(
            "add", help="ingest a text or Markdown source"
        ),
        "source.add",
    )
    parser.add_argument("course_id")
    parser.add_argument("path")
    parser.add_argument("--source-id")
    parser.add_argument("--title")
    parser.add_argument("--trust-level", type=int, default=50)
    parser.add_argument("--source-role", default="course_material")


def _add_source_list(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("source", "source commands").add_parser(
            "list", help="list canonical source revisions"
        ),
        "source.list",
    )
    parser.add_argument("course_id")


def _add_ask(topology: _ParserTopology) -> None:
    parser = _leaf(topology.root.add_parser("ask", help="ask a grounded question"), "ask")
    parser.add_argument("course_id")
    parser.add_argument("question")
    parser.add_argument("--session-id")
    parser.add_argument("--idempotency-key")


def _add_session_list(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("session", "session commands").add_parser(
            "list", help="list sessions for a course"
        ),
        "session.list",
    )
    parser.add_argument("course_id")


def _add_session_start(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("session", "session commands").add_parser(
            "start", help="start an explicitly identified session"
        ),
        "session.start",
    )
    parser.add_argument("course_id")
    parser.add_argument("--session-id", required=True)


def _add_session_get(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("session", "session commands").add_parser(
            "get", help="read one explicitly identified session"
        ),
        "session.get",
    )
    parser.add_argument("course_id")
    parser.add_argument("session_id")


def _add_session_resume(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("session", "session commands").add_parser(
            "resume", help="resume an explicitly identified session"
        ),
        "session.resume",
    )
    parser.add_argument("course_id")
    parser.add_argument("session_id")


def _add_export(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.root.add_parser("export", help="write deterministic export v1, v2, or v3"),
        "export",
    )
    parser.add_argument("course_id")
    parser.add_argument("--output", required=True)
    parser.add_argument("--version", choices=("1", "2", "3"), default="1")


def _add_doctor(topology: _ParserTopology) -> None:
    _leaf(topology.root.add_parser("doctor", help="run offline integrity diagnostics"), "doctor")


def _add_operator_skill(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("operator", "agent operator resources").add_parser(
            "skill", help="extract the packaged operator skill"
        ),
        "operator.skill",
    )
    parser.add_argument("--output", required=True)


def _add_manifest_schema(topology: _ParserTopology) -> None:
    _leaf(
        topology.group("manifest", "lifecycle manifest operations").add_parser(
            "schema", help="describe the closed lifecycle manifest v1 schema"
        ),
        "manifest.schema",
    )


def _add_manifest_validate(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("manifest", "lifecycle manifest operations").add_parser(
            "validate", help="validate and fingerprint a lifecycle manifest"
        ),
        "manifest.validate",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("./study-agent.manifest.json"),
    )


def _add_manifest_plan(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("manifest", "lifecycle manifest operations").add_parser(
            "plan", help="plan lifecycle reconciliation without mutation"
        ),
        "manifest.plan",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("./study-agent.manifest.json"),
    )


def _add_manifest_status(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("manifest", "lifecycle manifest operations").add_parser(
            "status", help="report lifecycle convergence and drift"
        ),
        "manifest.status",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("./study-agent.manifest.json"),
    )


def _add_manifest_apply(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("manifest", "lifecycle manifest operations").add_parser(
            "apply", help="apply one explicitly authorized lifecycle plan"
        ),
        "manifest.apply",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("./study-agent.manifest.json"),
    )
    parser.add_argument("--expect-plan", required=True)


def _add_describe(topology: _ParserTopology) -> None:
    _leaf(
        topology.root.add_parser("describe", help="describe agent operations and study tools"),
        "describe",
    )


def _add_tool_list(topology: _ParserTopology) -> None:
    _leaf(
        topology.group("tool", "static StudyTool discovery").add_parser(
            "list", help="list public StudyTool manifests"
        ),
        "tool.list",
    )


def _add_tool_describe(topology: _ParserTopology) -> None:
    parser = _leaf(
        topology.group("tool", "static StudyTool discovery").add_parser(
            "describe", help="describe one public StudyTool manifest"
        ),
        "tool.describe",
    )
    parser.add_argument("name")


__all__ = [
    "ArgumentDescriptor",
    "ArgumentKind",
    "ArgumentValueType",
    "CommandRegistration",
    "CommandRequest",
    "NetworkRequirement",
    "OperationEffect",
    "RepositoryRequirement",
    "agent_operations_manifest",
    "command_registrations",
    "configure_parser",
    "public_study_tool_entries",
    "registration_for",
]
