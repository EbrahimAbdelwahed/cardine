"""Strict, non-secret configuration shared by repository composition and lifecycle."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.domain.features import FeatureMode
from study_agent.state import canonical_json_bytes

CONFIG_SCHEMA_VERSION = 2
CONFIG_FILENAME = "study-agent.json"
MAX_CONFIG_BYTES = 64 * 1024
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SECRET_FIELD_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authentication",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "key",
        "passphrase",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


class LocalConfigError(ValueError):
    """The local configuration is absent, malformed, or contains secret material."""


@dataclass(frozen=True, slots=True)
class ModelAdapterConfig:
    """Provider-neutral operational selection for one technical model adapter."""

    adapter_id: str
    settings: JsonObject = field(default_factory=dict, repr=False)
    credential_env: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_id, str):
            raise LocalConfigError("model.adapter_id must be text")
        _trimmed(self.adapter_id, "model.adapter_id")
        if not isinstance(self.settings, Mapping):
            raise LocalConfigError("model.settings must be an object")
        try:
            settings = freeze_object(self.settings)
        except (RecursionError, TypeError, ValueError) as error:
            raise LocalConfigError("model.settings must contain strict JSON values") from error
        _reject_secret_fields(settings)
        object.__setattr__(self, "settings", settings)
        if self.credential_env is not None and (
            not isinstance(self.credential_env, str)
            or _ENVIRONMENT_NAME.fullmatch(self.credential_env) is None
        ):
            raise LocalConfigError(
                "model.credential_env must be an uppercase environment-variable name"
            )


@dataclass(frozen=True, slots=True)
class JudgementAdapterConfig:
    """Operational OpenRouter selection; policy belongs to the consumers."""

    model_id: str = "typesafe/jev-1.13"
    resolved_model_id: str = "typesafe/jev-1.13-20260917"
    credential_env: str = "OPENROUTER_API_KEY"
    timeout_seconds: float = 10.0
    max_retries: int = 2
    concurrency: int = 64

    def __post_init__(self) -> None:
        _trimmed(self.model_id, "judgement.model_id")
        _trimmed(self.resolved_model_id, "judgement.resolved_model_id")
        if (
            not isinstance(self.credential_env, str)
            or _ENVIRONMENT_NAME.fullmatch(self.credential_env) is None
        ):
            raise LocalConfigError("judgement.credential_env must name an environment variable")
        if (
            type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 120
        ):
            raise LocalConfigError("judgement timeout must be between zero and 120 seconds")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 3:
            raise LocalConfigError("judgement retries must be between zero and three")
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= 256:
            raise LocalConfigError("judgement concurrency must be between one and 256")

    def to_json(self) -> JsonObject:
        return {
            "model_id": self.model_id,
            "resolved_model_id": self.resolved_model_id,
            "credential_env": self.credential_env,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "concurrency": self.concurrency,
        }


@dataclass(frozen=True, slots=True)
class DocumentIndexAdapterConfig:
    """Qualified PageIndex worker configuration, without provider credentials."""

    timeout_seconds: float = 3.0
    max_input_bytes: int = 2 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 30
        ):
            raise LocalConfigError("document index timeout must be between zero and 30 seconds")
        if (
            type(self.max_input_bytes) is not int
            or not 1 <= self.max_input_bytes <= 2 * 1024 * 1024
        ):
            raise LocalConfigError("document index input exceeds the worker bound")

    def to_json(self) -> JsonObject:
        return {"timeout_seconds": self.timeout_seconds, "max_input_bytes": self.max_input_bytes}


@dataclass(frozen=True, slots=True)
class SemanticFeaturesConfig:
    """Versioned consumer policy. OFF preserves historical repository behavior."""

    document_index_mode: FeatureMode = FeatureMode.OFF
    flashcard_semantic_mode: FeatureMode = FeatureMode.OFF
    tutor_routing_mode: FeatureMode = FeatureMode.OFF
    flashcard_grounding_mode: FeatureMode = FeatureMode.OFF
    grounding_probability: float = 0.9
    grounding_margin: float = 0.2
    policy_version: str = "conservative-v1"
    anchor_probability: float = 0.8
    context_probability: float = 0.95
    exclusion_probability: float = 0.99
    semantic_margin: float = 0.2
    route_probability: float = 0.8
    route_margin: float = 0.2
    capability_probability: float = 0.85
    capability_margin: float = 0.2
    boolean_probability: float = 0.9
    boolean_margin: float = 0.2
    enum_probability: float = 0.9
    enum_margin: float = 0.2
    emergency_fallback: bool = True

    def __post_init__(self) -> None:
        _trimmed(self.policy_version, "features.policy_version")
        for name in (
            "document_index_mode",
            "flashcard_semantic_mode",
            "tutor_routing_mode",
            "flashcard_grounding_mode",
        ):
            if not isinstance(getattr(self, name), FeatureMode):
                raise LocalConfigError(f"features.{name} must be off, shadow or on")
        for name in (
            "grounding_probability",
            "grounding_margin",
            "anchor_probability",
            "context_probability",
            "exclusion_probability",
            "semantic_margin",
            "route_probability",
            "route_margin",
            "capability_probability",
            "capability_margin",
            "boolean_probability",
            "boolean_margin",
            "enum_probability",
            "enum_margin",
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise LocalConfigError(f"features.{name} must be a finite probability")
        if self.grounding_probability <= 0 or self.grounding_margin <= 0:
            raise LocalConfigError("grounding thresholds must be positive")
        if self.exclusion_probability < self.context_probability:
            raise LocalConfigError("exclusion threshold cannot be weaker than context threshold")
        if type(self.emergency_fallback) is not bool:
            raise LocalConfigError("features.emergency_fallback must be boolean")
        if self.flashcard_semantic_mode is not FeatureMode.OFF and (
            self.document_index_mode is FeatureMode.OFF
        ):
            raise LocalConfigError("flashcard semantics requires document indexing")
        if self.flashcard_semantic_mode is FeatureMode.ON and (
            self.document_index_mode is not FeatureMode.ON
        ):
            raise LocalConfigError("primary flashcard semantics requires primary document indexing")

    def to_json(self) -> JsonObject:
        from dataclasses import fields

        return {
            item.name: (value.value if isinstance(value, FeatureMode) else value)
            for item in fields(self)
            if (value := getattr(self, item.name)) is not None
        }


@dataclass(frozen=True, slots=True)
class LocalRepositoryConfig:
    """Versioned repository configuration; credentials are references, never values."""

    model: ModelAdapterConfig | None = None
    schema_version: int = CONFIG_SCHEMA_VERSION
    judgement: JudgementAdapterConfig | None = None
    document_index: DocumentIndexAdapterConfig = field(default_factory=DocumentIndexAdapterConfig)
    features: SemanticFeaturesConfig = field(default_factory=SemanticFeaturesConfig)

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != CONFIG_SCHEMA_VERSION:
            raise LocalConfigError(f"schema_version must be exactly {CONFIG_SCHEMA_VERSION}")
        if self.model is not None and not isinstance(self.model, ModelAdapterConfig):
            raise LocalConfigError("model must be a ModelAdapterConfig or null")
        if self.judgement is not None and not isinstance(self.judgement, JudgementAdapterConfig):
            raise LocalConfigError("judgement configuration is incompatible")
        if not isinstance(self.document_index, DocumentIndexAdapterConfig) or not isinstance(
            self.features, SemanticFeaturesConfig
        ):
            raise LocalConfigError("semantic configuration is incompatible")
        if self.judgement is None and any(
            mode is not FeatureMode.OFF
            for mode in (
                self.features.flashcard_semantic_mode,
                self.features.tutor_routing_mode,
                self.features.flashcard_grounding_mode,
            )
        ):
            raise LocalConfigError("enabled semantic features require a judgement adapter")

    def to_bytes(self) -> bytes:
        model: JsonValue
        if self.model is None:
            model = None
        else:
            model = {
                "adapter_id": self.model.adapter_id,
                "credential_env": self.model.credential_env,
                "settings": self.model.settings,
            }
        payload = (
            canonical_json_bytes(
                {
                    "schema_version": self.schema_version,
                    "model": model,
                    "judgement": None if self.judgement is None else self.judgement.to_json(),
                    "document_index": self.document_index.to_json(),
                    "features": self.features.to_json(),
                }
            )
            + b"\n"
        )
        if len(payload) > MAX_CONFIG_BYTES:
            raise LocalConfigError("serialized configuration exceeds the 64 KiB bound")
        return payload

    @classmethod
    def from_bytes(cls, payload: bytes) -> LocalRepositoryConfig:
        if type(payload) is not bytes or not payload or len(payload) > MAX_CONFIG_BYTES:
            raise LocalConfigError("configuration must be non-empty bounded UTF-8 JSON")
        try:
            raw: Any = json.loads(
                payload.decode("utf-8", errors="strict"),
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_invalid_json_constant,
            )
        except (OverflowError, RecursionError, UnicodeError, ValueError) as error:
            raise LocalConfigError("configuration must be valid UTF-8 JSON") from error
        if not isinstance(raw, dict):
            raise LocalConfigError("configuration fields are incompatible")
        schema_version = raw.get("schema_version")
        if type(schema_version) is not int:
            raise LocalConfigError("schema_version must be an integer")
        if schema_version == 1 and set(raw) == {"schema_version", "model"}:
            # Explicit migration: preserve OFF behavior, emit current schema on next write.
            return cls(model=None if raw["model"] is None else _decode_model(raw["model"]))
        if schema_version != CONFIG_SCHEMA_VERSION or set(raw) != {
            "schema_version",
            "model",
            "judgement",
            "document_index",
            "features",
        }:
            raise LocalConfigError("configuration fields are incompatible")
        model_raw = raw["model"]
        model = None if model_raw is None else _decode_model(model_raw)
        try:
            return cls(
                model=model,
                schema_version=schema_version,
                judgement=None
                if raw["judgement"] is None
                else _decode_dataclass(JudgementAdapterConfig, raw["judgement"]),
                document_index=_decode_dataclass(DocumentIndexAdapterConfig, raw["document_index"]),
                features=_decode_features(raw["features"]),
            )
        except (TypeError, ValueError) as error:
            raise LocalConfigError("semantic configuration fields are incompatible") from error

    @classmethod
    def load(cls, path: Path) -> LocalRepositoryConfig:
        if path.is_symlink() or not path.is_file():
            raise LocalConfigError("configuration must be a regular non-symlink file")
        try:
            return cls.from_bytes(path.read_bytes())
        except OSError as error:
            raise LocalConfigError("configuration could not be read") from error


def _decode_model(raw: object) -> ModelAdapterConfig:
    if not isinstance(raw, dict) or set(raw) != {
        "adapter_id",
        "credential_env",
        "settings",
    }:
        raise LocalConfigError("model configuration fields are incompatible")
    adapter_id = raw["adapter_id"]
    if not isinstance(adapter_id, str):
        raise LocalConfigError("model.adapter_id must be text")
    credential_env = raw["credential_env"]
    if credential_env is not None and not isinstance(credential_env, str):
        raise LocalConfigError("model.credential_env must be text or null")
    settings = raw["settings"]
    if not isinstance(settings, dict):
        raise LocalConfigError("model.settings must be an object")
    return ModelAdapterConfig(
        adapter_id=adapter_id,
        credential_env=credential_env,
        settings=cast(JsonObject, settings),
    )


def _decode_dataclass(cls: Any, raw: object) -> Any:
    from dataclasses import fields

    if not isinstance(raw, dict) or set(raw) != {item.name for item in fields(cls)}:
        raise LocalConfigError("adapter configuration fields are incompatible")
    return cls(**raw)


def _decode_features(raw: object) -> SemanticFeaturesConfig:
    if not isinstance(raw, dict):
        raise LocalConfigError("features must be an object")
    values = dict(raw)
    values.setdefault("flashcard_grounding_mode", "off")
    values.setdefault("grounding_probability", 0.9)
    values.setdefault("grounding_margin", 0.2)
    for name in (
        "document_index_mode",
        "flashcard_semantic_mode",
        "tutor_routing_mode",
        "flashcard_grounding_mode",
    ):
        value = values.get(name)
        if not isinstance(value, str):
            raise LocalConfigError("feature mode must be text")
        values[name] = FeatureMode(value)
    return cast(SemanticFeaturesConfig, _decode_dataclass(SemanticFeaturesConfig, values))


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocalConfigError("configuration cannot contain duplicate object keys")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise LocalConfigError(f"invalid JSON number: {value}")


def _trimmed(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LocalConfigError(f"{name} must be non-empty trimmed text")


def _reject_secret_fields(value: JsonValue) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            camel_split = _CAMEL_BOUNDARY.sub("_", key).lower()
            normalized = _NON_ALPHANUMERIC.sub("_", camel_split).strip("_")
            parts = tuple(part for part in normalized.split("_") if part)
            if (
                normalized in _SECRET_FIELD_PARTS
                or any(normalized.endswith(f"_{part}") for part in _SECRET_FIELD_PARTS)
                or any(part in _SECRET_FIELD_PARTS for part in parts)
            ):
                raise LocalConfigError("model.settings cannot contain credential fields")
            _reject_secret_fields(item)
    elif isinstance(value, tuple):
        for item in value:
            _reject_secret_fields(item)


EMPTY_CONFIG = LocalRepositoryConfig()

__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_SCHEMA_VERSION",
    "EMPTY_CONFIG",
    "MAX_CONFIG_BYTES",
    "DocumentIndexAdapterConfig",
    "JudgementAdapterConfig",
    "LocalConfigError",
    "LocalRepositoryConfig",
    "ModelAdapterConfig",
    "SemanticFeaturesConfig",
]
