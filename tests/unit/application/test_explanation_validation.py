from __future__ import annotations

import asyncio

import pytest

from cardine.application.explanation_validation import SourceBoundedExplanationValidator
from study_agent.grounding import EvidenceEnvelope
from study_agent.playbooks import ValidatorDisposition
from tests.unit.grounding.test_grounded_answer_validators import Content, answer, evidence_set


@pytest.mark.parametrize("status", ("answered", "insufficient_evidence"))
def test_explanation_settlement_keeps_evidence_validation(status: str) -> None:
    retrieval = evidence_set()
    envelope = EvidenceEnvelope.from_retrieval(retrieval)
    canonical = retrieval.evidence[0]
    validator = SourceBoundedExplanationValidator(Content(canonical.citation, canonical.text))
    handles = (envelope.items[0].handle, "ev_unknown")
    outcomes = tuple(
        asyncio.run(validator.validate({
            "answer": answer(handle, status=status), "evidence": envelope.to_json(),
        }))
        for handle in handles
    )
    assert outcomes[0].passed is (status == "answered")
    assert not outcomes[1].passed
    assert outcomes[1].disposition is ValidatorDisposition.TERMINATE
