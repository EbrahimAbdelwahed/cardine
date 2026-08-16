"""Small fail-closed JSON Schema subset used by public tool manifests."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue


class SchemaValidationError(ValueError):
    pass


_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
)
_COMMON = frozenset({"type", "enum"})
_BY_TYPE = {
    "object": _COMMON | {"properties", "required", "additionalProperties"},
    "array": _COMMON | {"items", "minItems", "maxItems"},
    "string": _COMMON | {"minLength", "maxLength"},
    "integer": _COMMON | {"minimum", "maximum"},
    "number": _COMMON | {"minimum", "maximum"},
    "boolean": _COMMON,
    "null": _COMMON,
}


def validate_schema_definition(schema: JsonObject, path: str = "$") -> None:
    unknown = set(schema) - _KEYWORDS
    if unknown:
        raise ValueError(f"{path}: unsupported schema keywords: {sorted(unknown)}")
    raw_kind = schema.get("type")
    supported = {"object", "array", "string", "integer", "number", "boolean", "null"}
    if isinstance(raw_kind, tuple):
        if (
            len(raw_kind) < 2
            or len(set(raw_kind)) != len(raw_kind)
            or not all(isinstance(item, str) and item in supported for item in raw_kind)
            or any(item in {"object", "array"} for item in raw_kind)
        ):
            raise ValueError(f"{path}: type unions must contain unique scalar JSON types")
        kinds = cast(tuple[str, ...], raw_kind)
        kind = raw_kind[0]
        allowed: frozenset[str] = frozenset.intersection(*(_BY_TYPE[item] for item in kinds))
    else:
        kind = raw_kind
        if kind not in supported:
            raise ValueError(f"{path}: unsupported or missing schema type")
        kinds = (str(kind),)
        allowed = _BY_TYPE[str(kind)]
    misplaced = set(schema) - allowed
    if misplaced:
        raise ValueError(f"{path}: keywords do not apply to {kind}: {sorted(misplaced)}")
    if kind == "object":
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError(f"{path}: object schemas require properties")
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"{path}: object schemas require additionalProperties false")
        required = schema.get("required")
        if not isinstance(required, tuple) or not all(isinstance(item, str) for item in required):
            raise ValueError(f"{path}: object schemas require a required tuple")
        if len(set(required)) != len(required) or not set(required) <= set(properties):
            raise ValueError(f"{path}: required fields must be unique declared properties")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, Mapping):
                raise ValueError(f"{path}: invalid property schema")
            validate_schema_definition(child, f"{path}.{name}")
    if kind == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ValueError(f"{path}: array schemas require items")
        validate_schema_definition(items, f"{path}[]")
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, tuple) or not enum):
        raise ValueError(f"{path}: enum must be a non-empty tuple")
    if isinstance(enum, tuple):
        if len({repr(item) for item in enum}) != len(enum):
            raise ValueError(f"{path}: enum values must be unique")
        for item in enum:
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError(f"{path}: enum numbers must be finite")
    for keyword in (
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    ):
        value = schema.get(keyword)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{path}: {keyword} must be finite numeric data")
        if (
            keyword in {"minLength", "maxLength", "minItems", "maxItems"}
            and value is not None
            and (type(value) is not int or value < 0)
        ):
            raise ValueError(f"{path}: {keyword} must be a non-negative integer")
    low = schema.get("minimum")
    high = schema.get("maximum")
    if low is not None and high is not None and low > high:  # type: ignore[operator]
        raise ValueError(f"{path}: minimum cannot exceed maximum")
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    if min_items is not None and max_items is not None and min_items > max_items:  # type: ignore[operator]
        raise ValueError(f"{path}: minItems cannot exceed maxItems")
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if min_length is not None and max_length is not None and min_length > max_length:  # type: ignore[operator]
        raise ValueError(f"{path}: minLength cannot exceed maxLength")


def validate_json(value: JsonValue, schema: JsonObject, path: str = "$") -> None:
    validate_schema_definition(schema, path="schema")
    raw_kind = schema["type"]
    kinds = (
        cast(tuple[str, ...], raw_kind) if isinstance(raw_kind, tuple) else (cast(str, raw_kind),)
    )
    matches = {
        "object": lambda: isinstance(value, Mapping),
        "array": lambda: isinstance(value, tuple),
        "string": lambda: isinstance(value, str),
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }
    kind = next((item for item in kinds if matches[item]()), None)
    if kind is None:
        raise SchemaValidationError(f"{path}: expected {' or '.join(kinds)}")
    if isinstance(value, float) and not math.isfinite(value):
        raise SchemaValidationError(f"{path}: number must be finite")
    if "enum" in schema and value not in schema["enum"]:  # type: ignore[operator]
        raise SchemaValidationError(f"{path}: value is not declared in enum")
    if kind == "object":
        assert isinstance(value, Mapping)
        properties = schema["properties"]
        assert isinstance(properties, Mapping)
        unknown = {str(item) for item in value} - {str(item) for item in properties}
        if unknown:
            raise SchemaValidationError(f"{path}: unknown fields: {sorted(unknown)}")
        required = cast(tuple[str, ...], schema["required"])
        missing = set(required) - {str(item) for item in value}
        if missing:
            raise SchemaValidationError(f"{path}: missing fields: {sorted(missing)}")
        for name, item in value.items():
            validate_json(item, properties[name], f"{path}.{name}")  # type: ignore[arg-type]
    elif kind == "array":
        assert isinstance(value, tuple)
        for index, item in enumerate(value):
            validate_json(item, schema["items"], f"{path}[{index}]")  # type: ignore[arg-type]
        _bounds(len(value), schema, path, "minItems", "maxItems")
    elif kind == "string":
        assert isinstance(value, str)
        minimum = schema.get("minLength")
        if minimum is not None and len(value) < minimum:  # type: ignore[operator]
            raise SchemaValidationError(f"{path}: string is too short")
        if minimum and (not value or value != value.strip()):
            raise SchemaValidationError(f"{path}: string must be non-blank trimmed text")
        maximum = schema.get("maxLength")
        if maximum is not None and len(value) > maximum:  # type: ignore[operator]
            raise SchemaValidationError(f"{path}: string is too long")
    elif kind in {"integer", "number"}:
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        _bounds(value, schema, path, "minimum", "maximum")


def _bounds(value: int | float, schema: JsonObject, path: str, low: str, high: str) -> None:
    if low in schema and value < schema[low]:  # type: ignore[operator]
        raise SchemaValidationError(f"{path}: value is below {low}")
    if high in schema and value > schema[high]:  # type: ignore[operator]
        raise SchemaValidationError(f"{path}: value is above {high}")
