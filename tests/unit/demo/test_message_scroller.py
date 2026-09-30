"""Execute the shipped scroller against browser geometry and event seams."""

import subprocess
from pathlib import Path


def test_message_scroller_keeps_reader_control_and_navigates_rendered_messages() -> None:
    root = Path(__file__).parents[3]
    result = subprocess.run(
        ["node", str(root / "tests/e2e/fixtures/message_scroller_ui.cjs")],
        check=True,
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert "PASS: message navigation" in result.stdout
