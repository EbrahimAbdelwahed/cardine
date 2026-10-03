"""Offline regression for the packaged browser's review dispatch and queue."""

import subprocess
from pathlib import Path

import pytest


def test_review_advances_before_http_and_preserves_retry_and_order() -> None:
    root = Path(__file__).parents[2]
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("recall_review_runtime.cjs")),
         str(root / "src/cardine/demo/browser.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_browser_queues_ratings_and_retries_lost_commit_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled real POST cannot stall the next card or duplicate its ledger."""
    from collections.abc import Mapping
    from threading import Event
    from typing import cast

    from cardine.cli.repository import LocalRepository
    from cardine.demo.ui_application import UiRequestError
    from study_agent.domain._validation import JsonObject
    from tests.e2e.recall_review_measure import seed_application
    from tests.e2e.test_cardine_repository_browser_journey import _real_browser, _serve
    from tests.integration.demo.TUT08.test_repository_recall_flow import COURSE

    application = seed_application(tmp_path / "repository")
    application.delay_ms = 0
    application.read_delay_ms = 0
    original_post = application.post
    commands: list[Mapping[str, object]] = []
    release = Event()

    def post(path: str, command: Mapping[str, object]) -> JsonObject:
        if not path.endswith("/reviews"):
            return original_post(path, command)
        commands.append(command)
        assert release.wait(timeout=10), "test did not release simulated latency"
        receipt = original_post(path, command)
        if len(commands) == 1:
            raise UiRequestError("response lost after commit", status_code=503)
        return receipt

    monkeypatch.setattr(application, "post", post)
    try:
        with _serve(application=application) as url, _real_browser(url) as browser:
            browser.wait_for(
                "document.querySelector('#rail-course').textContent.includes('Recall')"
            )
            browser.evaluate("document.querySelector('[data-route=ripasso]').click()")
            browser.wait_for("Boolean(document.querySelector('[data-reveal-review]'))")
            result = cast(dict[str, object], browser.evaluate("""(() => {
              document.querySelector('[data-reveal-review]').click();
              const start = performance.now();
              const first = document.querySelector('[data-command=review][data-rating=good]');
              const firstId = first.dataset.revisionId;
              first.click(); first.click();
              const next = document.querySelector('[data-reveal-review]');
              if (!next || next.disabled || next.dataset.revealReview === firstId)
                throw Error('next card not interactive before POST');
              const ms = performance.now() - start;
              next.click();
              const second = document.querySelector('[data-command=review][data-rating=hard]');
              const secondId = second.dataset.revisionId;
              second.click(); second.click();
              return {ms, firstId, secondId};
            })()"""))
            assert cast(float, result["ms"]) < 200
            browser.wait_for("Boolean(document.querySelector('[data-review-pending]'))")
            assert len(commands) == 1, "the second rating must wait for the first receipt"
            release.set()
            browser.wait_for("Boolean(document.querySelector('[data-review-error]'))")
            assert len(commands) == 1
            browser.evaluate("document.querySelector('[data-review-retry]').click()")
            browser.wait_for("!document.querySelector('[data-review-pending]')")
            assert len(commands) == 3
            assert commands[0]["request_id"] == commands[1]["request_id"]
            assert commands[0]["payload"] == commands[1]["payload"] == {"rating": "good"}
            assert commands[2]["payload"] == {"rating": "hard"}
            assert commands[2]["expected_sequence"] == cast(int, commands[1]["expected_sequence"])
    finally:
        release.set()

    with LocalRepository.open(application.repository) as repository:
        events = repository.events.read(COURSE)
        reviews = [event for event in events if event.event_type == "recall.review_recorded"]
        schedules = [event for event in events if event.event_type == "recall.schedule_applied"]
        assert len(reviews) == 2
        assert [event.payload["rating"] for event in reviews] == ["good", "hard"]
        assert [event.payload["revision_id"] for event in reviews] == [
            result["firstId"], result["secondId"]
        ]
        assert len(schedules) == 5  # Three enrollments and two review decisions.


@pytest.mark.parametrize(("failure_type", "status"), [
    (ValueError, 400), (OSError, 503),
])
def test_recall_response_failure_after_commit_keeps_retry_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_type: type[Exception], status: int,
) -> None:
    from typing import cast

    from cardine.cli.repository import LocalRepository
    from cardine.demo.ui_application import UiRequestError
    from study_agent.domain import TutorSnapshotV1
    from study_agent.state import Projection
    from tests.e2e.recall_review_measure import seed_application
    from tests.integration.demo.TUT08.test_repository_recall_flow import COURSE, _command

    application = seed_application(tmp_path / "repository")
    application.delay_ms = 0
    application.read_delay_ms = 0
    due = application.get("/api/v1/recall/due")
    row = cast(tuple[dict[str, object], ...], due["items"])[0]
    endpoint = f"/api/v1/recall/{row['revision_id']}/reviews"
    command = _command(
        "response-failure", cast(int, due["high_water_sequence"]), {"rating": "good"}
    )

    def failed_capture(repository: LocalRepository) -> tuple[Projection, TutorSnapshotV1]:
        raise failure_type("failed after canonical append")

    with monkeypatch.context() as patch:
        patch.setattr(application, "_captured_state", failed_capture)
        with pytest.raises(UiRequestError) as raised:
            application.post(endpoint, command)
    assert raised.value.status_code == status
    assert raised.value.command_committed
    assert raised.value.request_id == "response-failure"
    assert application.post(endpoint, command)["status"] == "committed"
    with LocalRepository.open(application.repository) as repository:
        reviews = [event for event in repository.events.read(COURSE)
                   if event.event_type == "recall.review_recorded"]
        assert len(reviews) == 1


def test_recall_response_conflict_after_commit_is_not_a_rejected_rating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import cast

    from cardine.cli.repository import LocalRepository
    from cardine.demo.ui_application import UiRequestError
    from study_agent.domain import TutorSnapshotV1
    from study_agent.state import Projection
    from tests.e2e.recall_review_measure import seed_application
    from tests.integration.demo.TUT08.test_repository_recall_flow import _command

    application = seed_application(tmp_path / "repository")
    application.delay_ms = 0
    application.read_delay_ms = 0
    due = application.get("/api/v1/recall/due")
    row = cast(tuple[dict[str, object], ...], due["items"])[0]
    command = _command(
        "response-conflict", cast(int, due["high_water_sequence"]), {"rating": "good"}
    )

    def failed_capture(repository: LocalRepository) -> tuple[Projection, TutorSnapshotV1]:
        raise UiRequestError("capture changed during read", status_code=409)

    monkeypatch.setattr(application, "_captured_state", failed_capture)
    with pytest.raises(UiRequestError) as raised:
        application.post(f"/api/v1/recall/{row['revision_id']}/reviews", command)
    assert raised.value.status_code == 409
    assert raised.value.command_committed
    assert raised.value.request_id == "response-conflict"
