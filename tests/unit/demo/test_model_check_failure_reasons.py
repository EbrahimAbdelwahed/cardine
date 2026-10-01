from cardine.demo.ui_application import _model_check_message, _model_check_reason
from study_agent.ports.model import ModelErrorCode


def test_readiness_distinguishes_permission_and_schema_errors() -> None:
    assert _model_check_reason(ModelErrorCode.AUTHORIZATION.value) == "permission_denied"
    assert "permessi" in _model_check_message("permission_denied")
    assert _model_check_reason(ModelErrorCode.SCHEMA_INCOMPATIBLE.value) == "schema_incompatible"
    assert "schema" in _model_check_message("schema_incompatible")
