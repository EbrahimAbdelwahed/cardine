from __future__ import annotations

from cardine.demo.product_shell import run_offline_shell_demo


def test_one_command_offline_shell_trace_is_recovered_and_inspectable() -> None:
    result = run_offline_shell_demo()

    assert result["status"] == "recovered"
    assert result["evidence_sequence"] == 2
    assert result["capabilities"] == ("explain_concept",)
    assert result["optional_due_review"] == "unavailable (TUT-07 is optional)"
    assert result["parity"] is True
