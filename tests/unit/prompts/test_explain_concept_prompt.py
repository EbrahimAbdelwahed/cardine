from __future__ import annotations

from study_agent.prompts import EXPLAIN_CONCEPT_LAYERS
from study_agent.skills import PromptLayerKind


def test_explain_concept_prompt_matches_grounding_segment_contract() -> None:
    task = next(
        layer
        for layer in EXPLAIN_CONCEPT_LAYERS
        if layer.kind is PromptLayerKind.TASK_INSTRUCTION
    )

    assert "answered" in task.template
    assert "supported_claim" in task.template
    assert "synthesis" in task.template
    assert "study_guidance" in task.template
    assert "must have no evidence_ids" in task.template
