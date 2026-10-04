"""Product settlement for a safely validated source-bound non-answer."""

from study_agent.capabilities.builtin_validators import ExplainConceptIntegrityValidator
from study_agent.domain._validation import JsonObject
from study_agent.playbooks import ValidationOutcome, ValidatorDisposition


class SourceBoundedExplanationValidator(ExplainConceptIntegrityValidator):
    """Keep integrity checks, but settle genuine evidence limits truthfully."""

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        outcome = await self._delegate.validate(inputs)
        if set(inputs) == {"output"} or not outcome.passed:
            return outcome
        if outcome.result.get("status") == "insufficient_evidence":
            # The grounding validator has already prohibited supported claims
            # and verified every reference. This does not publish a successful
            # explanation or weaken the capability's answered-only manifest.
            return ValidationOutcome(
                True, ValidatorDisposition.TERMINATE, outcome.result,
                "the retrieved source excerpts do not answer the requested detail",
            )
        if outcome.result.get("status") == "answered":
            return outcome
        return ValidationOutcome(
            False,
            ValidatorDisposition.TERMINATE,
            {"status": "failed", "code": "capability_validation_failed"},
            "explanation must be an answered grounded result",
        )
