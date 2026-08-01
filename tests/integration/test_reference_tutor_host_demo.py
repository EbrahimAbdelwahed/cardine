from __future__ import annotations

from typing import cast

from examples.reference_tutor_host import run_reference_demo


def test_reference_demo_runs_the_same_offline_host_trace_for_both_adapters() -> None:
    result = run_reference_demo()

    assert result["learner_entry"] == "I have ten minutes. Help me understand heart valves."
    assert result["discovered_capabilities"] == ("explain_concept",)
    assert result["scripted_statuses"] == ("completed", "suspended", "completed")
    assert result["recorded_statuses"] == result["scripted_statuses"]
    assert result["parity"] is True
    assert result["recorded_request_count"] == 3
    assert result["evidence_refresh_sequence"] == 2
    assert result["gateway_trace"] == (
        "start:completed",
        "start:suspended",
        "resume:completed",
    )
    assert result["timeline"] == (
        {"step": 1, "status": "completed", "detail": "Initial grounded explanation"},
        {"step": 2, "status": "suspended", "detail": "Which valve should we focus on?"},
        {"step": 3, "status": "completed", "detail": "Resumed with refreshed evidence"},
    )
    assert result["context_state"] == {
        "initial_sequence": 1,
        "refreshed_sequence": 2,
        "selected_focus": "aortic valve",
    }


def test_reference_demo_captures_only_a_safe_markdown_descriptor() -> None:
    result = run_reference_demo()
    descriptor = result["captured_file"]

    assert isinstance(descriptor, dict)
    assert descriptor["media_type"] == "text/markdown"
    assert descriptor["display_name"] == "Lesson notes"
    assert "/" not in descriptor["id"]
    assert "original_filename" not in descriptor
    assert "content" not in descriptor

    source = cast(dict[str, object], result["source_state"])
    assert source["fixture"] == "heart-valves.md"
    assert source["title"] == "Valvole cardiache — materiale di esempio"
    assert source["checksum_sha256"] == descriptor["checksum_sha256"]
    assert source["byte_size"] == descriptor["byte_size"]
    assert source["evidence"] == (
        "The aortic valve sits between the left ventricle and the aorta.",
        "The pulmonary valve sits between the right ventricle and the pulmonary trunk.",
    )
