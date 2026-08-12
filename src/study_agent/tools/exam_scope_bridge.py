"""Request-bound private bridge for complete exam-sample evidence."""

from study_agent.domain import ExecutionContext
from study_agent.domain._validation import JsonObject
from cardine.exams.contracts import ExamAnalysisRequest, ExamPromptEvidenceProjection
from study_agent.ports.exam import ExamSampleScopePreparationPort
from study_agent.skills import SemanticVersion

VERSION = SemanticVersion.parse("1.0.0")


class BoundExamSampleScopeExecutor:
    name = "source.prepare_exam_sample_scope@1"
    behavior_version = VERSION

    def __init__(
        self,
        request: ExamAnalysisRequest,
        context: ExecutionContext,
        preparation: ExamSampleScopePreparationPort,
    ) -> None:
        self._request = request
        self._context = context
        self._preparation = preparation

    async def invoke(self, arguments: JsonObject) -> JsonObject:
        if arguments != {
            "sample_revision_ids": tuple(str(item) for item in self._request.sample_revision_ids)
        }:
            raise ValueError("exam scope arguments differ from the trusted request")
        scope = self._preparation.prepare(self._request, self._context)
        projection = ExamPromptEvidenceProjection.from_scope(scope)
        projection.verify_scope(scope)
        return {
            "prepared_scope": scope.to_json(),
            "prompt_projection": projection.to_json(),
        }


__all__ = ["VERSION", "BoundExamSampleScopeExecutor"]
