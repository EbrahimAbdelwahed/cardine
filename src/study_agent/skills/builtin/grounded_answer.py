from __future__ import annotations

from study_agent.grounding import GROUNDED_ANSWER_DRAFT_SCHEMA
from study_agent.prompts import GROUNDED_ANSWER_LAYERS
from study_agent.skills import (
    ArtifactReference,
    CapabilityFallback,
    CapabilityRequirement,
    EvalFixture,
    GroundingPolicy,
    JsonSchema,
    SemanticVersion,
    SkillPackage,
    StateWritePolicy,
    ToolRequirement,
    ValidatorDefinition,
    VersionRange,
)

VERSION = SemanticVersion.parse("1.0.0")
ENGINE_V2 = SemanticVersion.parse("2.0.0")

GROUNDED_ANSWER_OUTPUT_SCHEMA = JsonSchema(
    {
        "type": "object",
        "required": ("status", "segments", "unsupported_information_note"),
        "additionalProperties": False,
        "properties": {
            "status": {
                "type": "string",
                "enum": (
                    "answered",
                    "insufficient_evidence",
                    "conflicting_evidence",
                    "failed",
                ),
            },
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ("kind", "text", "citations"),
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"type": "string"},
                        "text": {"type": "string"},
                        "citations": {"type": "array"},
                    },
                },
            },
            "unsupported_information_note": {"type": ("string", "null")},
        },
    }
)

GROUNDED_ANSWER_SKILL = SkillPackage(
    id="grounded_answer",
    version=VERSION,
    purpose="Answer study questions only from supplied, canonically resolvable evidence.",
    engine_compatibility=VersionRange(VERSION, ENGINE_V2),
    input_schema=JsonSchema(
        {
            "type": "object",
            "required": ("course_id", "session_id", "question"),
            "additionalProperties": False,
            "properties": {
                "course_id": {"type": "string"},
                "session_id": {"type": "string"},
                "question": {"type": "string"},
            },
        }
    ),
    output_schema=GROUNDED_ANSWER_OUTPUT_SCHEMA,
    prompt_layers=GROUNDED_ANSWER_LAYERS,
    course_profile_fields=(
        "language",
        "assessment_styles",
        "learning_goals",
        "source_policy",
        "terminology_policy",
    ),
    grounding_policy=GroundingPolicy(True, "insufficient_evidence", True),
    state_write_policy=StateWritePolicy(
        (
            "session.interaction_recorded",
            "session.answer_recorded",
            "session.continuation_summary_updated",
        )
    ),
    required_capabilities=(CapabilityRequirement("structured_output"),),
    required_tools=(
        ToolRequirement("session.get_context", VERSION),
        ToolRequirement("source.search", VERSION),
    ),
    playbook=ArtifactReference("grounded_answer_flow", VERSION),
    fallbacks=(
        CapabilityFallback(
            "structured_output",
            "parse_json_then_validate",
            validator_ids=("grounded_answer_integrity",),
        ),
    ),
    validators=(
        ValidatorDefinition(
            "evidence_sufficiency",
            VERSION,
            "Terminate deterministically when retrieval supplies no evidence.",
        ),
        ValidatorDefinition(
            "grounded_answer_integrity",
            VERSION,
            "Reconstruct and re-resolve citations from trusted evidence handles.",
        ),
    ),
    known_failure_modes=(
        "insufficient source evidence",
        "conflicting trusted evidence",
        "unknown or stale evidence handle",
        "canonical citation resolution failure",
    ),
    eval_fixtures=(
        EvalFixture(
            "supported_answer",
            {"question": "What does the source support?"},
            {"status": "answered"},
        ),
        EvalFixture(
            "insufficient_answer",
            {"question": "What is absent from the sources?"},
            {"status": "insufficient_evidence"},
        ),
        EvalFixture(
            "conflicting_answer",
            {"question": "How do the supplied sources disagree?"},
            {"status": "conflicting_evidence"},
        ),
    ),
)

# The model step consumes the draft schema; the skill output is the validated schema above.
GROUNDED_ANSWER_MODEL_SCHEMA = GROUNDED_ANSWER_DRAFT_SCHEMA
