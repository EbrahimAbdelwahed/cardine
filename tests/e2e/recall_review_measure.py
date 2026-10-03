"""Optional Chromium measurement against real HTTP and a synthetic recall ledger.

Run from the repository root with PYTHONPATH=src:. and Playwright on NODE_PATH.
No user data, provider calls, or production endpoint is involved.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from threading import Thread
from time import perf_counter, sleep
from typing import cast

from cardine.cli.repository import LocalRepository
from cardine.demo.browser import BrowserSurface, _BrowserServer, create_server
from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.artifacts import HumanAuthoredArtifactProvenance, HybridFlashcardContent
from study_agent.domain._validation import JsonObject
from tests.integration.demo.TUT08.test_repository_recall_flow import (
    COURSE,
    SESSION,
    _accept,
    _command,
    _context,
    _opener,
    _Scheduler,
    _seed,
)


class _MeasuredApplication(RepositoryUiApplication):
    delay_ms = 1000
    read_delay_ms = 500
    rated = False
    canonical_ms = 0.0
    post_ms = 0.0

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        start = perf_counter()
        if path.endswith("/reviews"):
            sleep(self.delay_ms / 1000)
        work = perf_counter()
        result = super().post(path, command)
        if path.endswith("/reviews"):
            self.canonical_ms = (perf_counter() - work) * 1000
            self.post_ms = (perf_counter() - start) * 1000
            self.rated = True
        return result

    def get(self, path: str) -> JsonObject:
        if self.rated and path in {"/api/v1/bootstrap", "/api/v1/recall/due"}:
            sleep(self.read_delay_ms / 1000)
        return super().get(path)


class _BaselineSurface(BrowserSurface):
    javascript = b""

    def asset(self, name: str) -> bytes:
        return self.javascript if name == "browser.js" else super().asset(name)



def seed_application(root: Path) -> _MeasuredApplication:
    revision = _seed(root)
    revisions = [revision]
    with LocalRepository.open(root) as repository:
        original = repository.artifacts.get(COURSE).revision(revision)
        assert isinstance(original.content.content, HybridFlashcardContent)
        assert isinstance(original.provenance, HumanAuthoredArtifactProvenance)
        for n in (2, 3):
            content = replace(original.content, content=replace(
                original.content.content, prompt=f"Synthetic question {n}"
            ))
            snapshot = repository.artifact_service.record_human_revision(
                content, original.provenance, None, _context(f"proposal-{n}"),
                repository.events.read(COURSE)[-1].course_sequence,
            )
            revisions.append(snapshot.pending()[-1].id)
    application = _MeasuredApplication(
        root, COURSE, SESSION, repository_opener=_opener(_Scheduler())
    )
    for n, target in enumerate(revisions):
        receipt = _accept(application, target, f"accept-{n}")
        application.post(f"/api/v1/recall/{target}/enrollments", _command(
            f"enroll-{n}", cast(int, receipt["high_water_sequence"])
        ))
    return application

def measure(delay_ms: int, read_delay_ms: int, baseline: bytes, mobile: bool) -> dict[str, object]:
    with TemporaryDirectory(prefix="cardine-recall-measure-") as directory:
        application = seed_application(Path(directory).resolve() / "repository")
        application.delay_ms = delay_ms
        application.read_delay_ms = read_delay_ms
        server = create_server("127.0.0.1", 0, ui_application=application)
        if baseline:
            surface = _BaselineSurface(application)
            surface.javascript = baseline
            cast(_BrowserServer, server).surface = surface
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = cast(tuple[str, int], server.server_address)
        try:
            result = subprocess.run([
                "node", str(Path(__file__).with_suffix(".cjs")),
                f"http://{host}:{port}/", "mobile" if mobile else "desktop",
            ], capture_output=True, text=True, timeout=20, check=True)
            measurement = cast(dict[str, object], json.loads(result.stdout))
            measurement.update(canonical_ms=application.canonical_ms, post_ms=application.post_ms)
            return measurement
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--delay-ms", type=int, default=1000)
    parser.add_argument("--read-delay-ms", type=int, default=500)
    parser.add_argument("--baseline-ref", default="")
    parser.add_argument("--mobile", action="store_true")
    args = parser.parse_args()
    baseline = subprocess.check_output([
        "git", "show", f"{args.baseline_ref}:src/cardine/demo/browser.js"
    ]) if args.baseline_ref else b""
    measurements = [measure(args.delay_ms, args.read_delay_ms, baseline, args.mobile)
                    for _ in range(args.samples)]
    times = [float(cast(float, item["clickToPaintAndInteractiveMs"])) for item in measurements]
    print(json.dumps({
        "samples": measurements,
        "median_ms": median(times), "maximum_ms": max(times),
        "viewport": "mobile" if args.mobile else "desktop",
        "baseline_ref": args.baseline_ref or None,
    }, indent=2))
    if not baseline and max(times) > 200:
        raise SystemExit("next-card interaction exceeded 200 ms")


if __name__ == "__main__":
    main()
