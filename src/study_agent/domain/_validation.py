from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | tuple[JsonValue, ...] | Mapping[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True, eq=False)
class _FrozenObject(Mapping[str, JsonValue]):
    """An owned, deeply frozen object, never an arbitrary mapping proxy."""

    _data: Mapping[str, JsonValue]

    def __getitem__(self, key: str) -> JsonValue:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def require_text(value: str, field_name: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and have no surrounding whitespace")


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, _FrozenObject):
        return value
    if isinstance(value, Mapping):
        frozen = {key: freeze_json(item) for key, item in value.items()}
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return _FrozenObject(MappingProxyType(frozen))
    if isinstance(value, Sequence) and not isinstance(value, str):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError("JSON numbers must be finite")
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError(f"unsupported JSON value: {type(value).__name__}")
    return value


def freeze_object(value: Mapping[str, JsonValue]) -> JsonObject:
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - narrowed by the input type
        raise TypeError("expected an object")
    return frozen
