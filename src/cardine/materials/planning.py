"""Deterministic transcript units and strict segment-boundary contracts."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from itertools import pairwise
from typing import Any, cast

from study_agent.domain._validation import JsonObject, JsonValue, freeze_object, require_text
from study_agent.state import canonical_json_bytes

from .generation_contracts import MAX_BOUNDARY_BYTES, MAX_SEGMENTS, MAX_UNITS


@dataclass(frozen=True, slots=True)
class TranscriptUnit:
    ordinal: int
    start: int
    end: int
    text: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("unit ordinal must be non-negative")
        if (
            type(self.start) is not int
            or type(self.end) is not int
            or self.start < 0
            or self.end <= self.start
        ):
            raise ValueError("unit span must be a non-empty forward range")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("unit text must be non-empty")
        if "\x00" in self.text:
            raise ValueError("unit text cannot contain NUL")
        if self.end - self.start != len(self.text):
            raise ValueError("unit span must match text length")

    def to_json(self) -> JsonObject:
        return {"ordinal": self.ordinal, "start": self.start, "end": self.end, "text": self.text}

    @classmethod
    def from_json(cls, raw: Mapping[str, JsonValue]) -> TranscriptUnit:
        _exact(raw, {"ordinal", "start", "end", "text"}, "transcript unit")
        return cls(
            _integer(raw, "ordinal"),
            _integer(raw, "start"),
            _integer(raw, "end"),
            _unit_text(raw, "text"),
        )


@dataclass(frozen=True, slots=True)
class UnitManifest:
    text_fingerprint: str
    character_length: int
    units: tuple[TranscriptUnit, ...]

    def __post_init__(self) -> None:
        _sha(self.text_fingerprint, "text_fingerprint")
        if type(self.character_length) is not int or self.character_length < 1:
            raise ValueError("manifest character_length must be positive")
        units = tuple(self.units)
        if not 1 <= len(units) <= MAX_UNITS:
            raise ValueError("manifest unit count is outside its bound")
        if tuple(item.ordinal for item in units) != tuple(range(len(units))):
            raise ValueError("manifest unit ordinals must be contiguous")
        if units[0].start != 0 or units[-1].end != self.character_length:
            raise ValueError("manifest units do not cover the exact transcript")
        for previous, current in pairwise(units):
            if previous.end != current.start:
                raise ValueError("manifest units contain a coverage gap")
        object.__setattr__(self, "units", units)

    @property
    def fingerprint(self) -> str:
        return sha256(b"unit-manifest@1\0" + self.to_bytes()).hexdigest()

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "schema_version": 1,
                "text_fingerprint": self.text_fingerprint,
                "character_length": self.character_length,
                "units": tuple(item.to_json() for item in self.units),
            }
        )

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())

    @classmethod
    def from_json(cls, raw: Mapping[str, JsonValue]) -> UnitManifest:
        _exact(
            raw,
            {"schema_version", "text_fingerprint", "character_length", "units"},
            "unit manifest",
        )
        if _integer(raw, "schema_version") != 1:
            raise ValueError("unit manifest schema version is unsupported")
        items = raw.get("units")
        if not isinstance(items, tuple):
            raise ValueError("unit manifest units must be an array")
        return cls(
            _text(raw, "text_fingerprint"),
            _integer(raw, "character_length"),
            tuple(TranscriptUnit.from_json(_mapping(item, "unit")) for item in items),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> UnitManifest:
        try:
            raw: Any = json.loads(data)
            if not isinstance(raw, dict):
                raise ValueError("unit manifest must be an object")
            result = cls.from_json(freeze_object(cast(Mapping[str, JsonValue], raw)))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("unit manifest bytes are invalid") from error
        if result.to_bytes() != data:
            raise ValueError("unit manifest bytes are not canonical")
        return result


@dataclass(frozen=True, slots=True)
class SegmentBoundary:
    start_unit: int
    end_unit: int
    title: str

    def __post_init__(self) -> None:
        if (
            type(self.start_unit) is not int
            or type(self.end_unit) is not int
            or self.start_unit < 0
            or self.end_unit <= self.start_unit
        ):
            raise ValueError("segment boundary must be a non-empty forward range")
        require_text(self.title, "segment title")

    def to_json(self) -> JsonObject:
        return {"start_unit": self.start_unit, "end_unit": self.end_unit, "title": self.title}


@dataclass(frozen=True, slots=True)
class SegmentBoundaries:
    manifest_fingerprint: str
    segments: tuple[SegmentBoundary, ...]

    def __post_init__(self) -> None:
        _sha(self.manifest_fingerprint, "manifest_fingerprint")
        segments = tuple(self.segments)
        if not 1 <= len(segments) <= MAX_SEGMENTS:
            raise ValueError("segment count is outside its bound")
        for previous, current in pairwise(segments):
            if previous.end_unit != current.start_unit:
                raise ValueError("segment boundaries contain a gap or overlap")
        object.__setattr__(self, "segments", segments)

    def validate_against(self, manifest: UnitManifest) -> None:
        if self.manifest_fingerprint != manifest.fingerprint:
            raise ValueError("segment boundaries do not identify this unit manifest")
        if (
            not self.segments
            or self.segments[0].start_unit != 0
            or self.segments[-1].end_unit != len(manifest.units)
        ):
            raise ValueError("segment boundaries do not cover all transcript units")
        if any(item.end_unit > len(manifest.units) for item in self.segments):
            raise ValueError("segment boundary exceeds the unit manifest")

    @property
    def fingerprint(self) -> str:
        return sha256(b"segment-boundaries@1\0" + self.to_bytes()).hexdigest()

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "schema_version": 1,
                "manifest_fingerprint": self.manifest_fingerprint,
                "segments": tuple(item.to_json() for item in self.segments),
            }
        )

    def to_bytes(self) -> bytes:
        data = canonical_json_bytes(self.to_json())
        if len(data) > MAX_BOUNDARY_BYTES:
            raise ValueError("segment boundaries exceed their bound")
        return data

    @classmethod
    def from_json(cls, raw: Mapping[str, JsonValue]) -> SegmentBoundaries:
        _exact(raw, {"schema_version", "manifest_fingerprint", "segments"}, "segment boundaries")
        if _integer(raw, "schema_version") != 1:
            raise ValueError("segment boundary schema version is unsupported")
        values = raw.get("segments")
        if not isinstance(values, tuple):
            raise ValueError("segments must be an array")
        return cls(
            _text(raw, "manifest_fingerprint"),
            tuple(_boundary(_mapping(item, "boundary")) for item in values),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> SegmentBoundaries:
        if len(data) > MAX_BOUNDARY_BYTES:
            raise ValueError("segment boundaries exceed their bound")
        try:
            raw: Any = json.loads(data)
            if not isinstance(raw, dict):
                raise ValueError("segment boundaries must be an object")
            result = cls.from_json(freeze_object(cast(Mapping[str, JsonValue], raw)))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("segment boundary bytes are invalid") from error
        if result.to_bytes() != data:
            raise ValueError("segment boundary bytes are not canonical")
        return result


def build_unit_manifest(text: str, *, max_unit_characters: int = 4_000) -> UnitManifest:
    """Split an NFC transcript deterministically while preserving every character."""

    if not isinstance(text, str) or not text:
        raise ValueError("transcript text must be non-empty")
    normalized = unicodedata.normalize("NFC", text)
    if normalized != text:
        raise ValueError("transcript text must already be NFC-normalized")
    if type(max_unit_characters) is not int or not 128 <= max_unit_characters <= 16_000:
        raise ValueError("max_unit_characters is outside its bound")

    spans: list[tuple[int, int]] = []
    start = 0
    # Paragraph boundaries are preferred; oversized paragraphs are sliced at a
    # deterministic character ceiling. Whitespace remains part of a unit.
    for paragraph in text.splitlines(keepends=True):
        end = start + len(paragraph)
        if end - start <= max_unit_characters:
            spans.append((start, end))
        else:
            cursor = start
            while cursor < end:
                boundary = min(end, cursor + max_unit_characters)
                spans.append((cursor, boundary))
                cursor = boundary
        start = end
    if not spans:
        spans = [(0, len(text))]
    units = tuple(
        TranscriptUnit(index, start, end, text[start:end])
        for index, (start, end) in enumerate(spans)
    )
    return UnitManifest(sha256(text.encode("utf-8")).hexdigest(), len(text), units)


def parse_segment_boundaries(
    output: Mapping[str, JsonValue] | tuple[JsonValue, ...],
    manifest: UnitManifest,
) -> SegmentBoundaries:
    """Decode Luna's strict boundary object and mechanically prove coverage."""

    if isinstance(output, Mapping):
        result = SegmentBoundaries.from_json(output)
    elif isinstance(output, tuple):
        result = SegmentBoundaries(
            manifest.fingerprint,
            tuple(_boundary(_mapping(item, "boundary")) for item in output),
        )
    else:
        raise ValueError("boundary response must be an object")
    result.validate_against(manifest)
    return result


def units_for_boundary(manifest: UnitManifest, boundary: SegmentBoundary) -> str:
    if boundary.end_unit > len(manifest.units):
        raise ValueError("boundary exceeds manifest")
    return "".join(item.text for item in manifest.units[boundary.start_unit : boundary.end_unit])


def _boundary(raw: Mapping[str, JsonValue]) -> SegmentBoundary:
    _exact(raw, {"start_unit", "end_unit", "title"}, "segment boundary")
    return SegmentBoundary(
        _integer(raw, "start_unit"), _integer(raw, "end_unit"), _text(raw, "title")
    )


def _exact(value: Mapping[str, JsonValue], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields are not canonical")


def _mapping(value: object, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: Mapping[str, JsonValue], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str):
        raise ValueError(f"{name} must be text")
    require_text(item, name)
    return item


def _unit_text(value: Mapping[str, JsonValue], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item or "\x00" in item:
        raise ValueError(f"{name} must be non-empty text without NUL")
    return item


def _integer(value: Mapping[str, JsonValue], name: str) -> int:
    item = value.get(name)
    if type(item) is not int:
        raise ValueError(f"{name} must be an integer")
    return item


def _sha(value: str, name: str) -> None:
    require_text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")


__all__ = [
    "SegmentBoundaries",
    "SegmentBoundary",
    "TranscriptUnit",
    "UnitManifest",
    "build_unit_manifest",
    "parse_segment_boundaries",
    "units_for_boundary",
]
