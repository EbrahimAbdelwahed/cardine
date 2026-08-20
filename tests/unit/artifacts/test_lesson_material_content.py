from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import cast

import pytest

from study_agent.artifacts import LessonMaterialContent, StudyArtifactEnvelope
from study_agent.domain import BlobId, BlobRef, LessonMaterialVariant, StudyArtifactKind
from study_agent.domain._validation import JsonObject, JsonValue

MARKDOWN = b"# Heart valves\n\nThe aortic valve has three cusps.\n"
DIGEST = sha256(MARKDOWN).hexdigest()
ROOT_DIGEST = "b" * 64


def _blob() -> BlobRef:
    return BlobRef(BlobId(f"sha256:{DIGEST}"), DIGEST, len(MARKDOWN))


def _envelope(
    variant: LessonMaterialVariant = LessonMaterialVariant.COMPLETE,
) -> StudyArtifactEnvelope:
    return StudyArtifactEnvelope(
        StudyArtifactKind.LESSON_MATERIAL,
        LessonMaterialContent(
            variant=variant,
            title="Heart valves",
            markdown_blob=_blob(),
            markdown_character_length=len(MARKDOWN.decode()),
            direct_parent_blob_sha256=ROOT_DIGEST,
            limitations=("Generated from the supplied lesson source.",),
        ),
    )


def _plain(value: JsonValue) -> object:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def test_lesson_material_round_trips_as_exact_canonical_blob_addressed_content() -> None:
    envelope = _envelope()

    assert StudyArtifactEnvelope.from_json(envelope.to_json()) == envelope
    assert StudyArtifactEnvelope.from_bytes(envelope.to_bytes()) == envelope
    assert StudyArtifactEnvelope.from_bytes(envelope.to_bytes()).to_bytes() == envelope.to_bytes()
    assert b"The aortic valve" not in envelope.to_bytes()


def test_lesson_material_rejects_unknown_fields_and_reserved_product_state() -> None:
    payload = cast(dict[str, object], _plain(_envelope().to_json()))
    content = cast(dict[str, object], payload["content"])

    for field, value in (("unknown", True), ("status", "accepted"), ("provider", "vendor")):
        mutated = {**payload, "content": {**content, field: value}}
        with pytest.raises((TypeError, ValueError)):
            StudyArtifactEnvelope.from_json(cast(JsonObject, mutated))


def test_lesson_material_requires_checksum_bound_blob_and_positive_lengths() -> None:
    valid = cast(LessonMaterialContent, _envelope().content)
    mismatched_blob = BlobRef(BlobId("sha256:" + "c" * 64), DIGEST, len(MARKDOWN))
    with pytest.raises(ValueError, match="id"):
        replace(valid, markdown_blob=mismatched_blob)

    with pytest.raises(ValueError):
        replace(valid, direct_parent_blob_sha256="not-a-sha256")

    with pytest.raises(ValueError):
        replace(valid, markdown_character_length=0)
    with pytest.raises(ValueError):
        replace(valid, markdown_blob=BlobRef(valid.markdown_blob.id, DIGEST, 0))


def test_complete_and_study_variants_are_distinct_and_preserve_direct_parent_digest() -> None:
    complete = cast(LessonMaterialContent, _envelope(LessonMaterialVariant.COMPLETE).content)
    study = cast(LessonMaterialContent, _envelope(LessonMaterialVariant.STUDY).content)

    assert complete.variant is LessonMaterialVariant.COMPLETE
    assert study.variant is LessonMaterialVariant.STUDY
    assert complete.direct_parent_blob_sha256 == ROOT_DIGEST
    assert study.direct_parent_blob_sha256 == ROOT_DIGEST
    assert complete != study
