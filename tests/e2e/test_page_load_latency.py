"""Offline page-load budgets over a large canonical course, not an empty shell."""

from __future__ import annotations

import json
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import cast

import pytest

from cardine.cli.repository import LocalRepository, ModelAdapterRegistry
from cardine.demo.ui_application import RepositoryUiApplication
from cardine.knowledge import PageIndexStatus
from study_agent.adapters.scheduling.py_fsrs import PyFsrsSchedulingPolicy
from study_agent.artifacts import HumanAuthoredArtifactProvenance
from study_agent.domain import ContentOrigin, SourceId
from study_agent.domain.features import FeatureMode
from study_agent.domain.provenance import DocumentConversionProvenance, DocumentPageSpan
from study_agent.ingestion import ChunkingConfig, TextIngestionService
from study_agent.repository_config import (
    CONFIG_FILENAME,
    LocalRepositoryConfig,
    ModelAdapterConfig,
    SemanticFeaturesConfig,
)
from tests.e2e.test_cardine_repository_browser_journey import (
    API_PATHS,
    _BrowserModel,
    _real_browser,
    _serve,
)
from tests.integration.demo.TUT08.test_repository_recall_flow import (
    COURSE,
    SESSION,
    _accept,
    _command,
    _context,
    _seed,
)

# Browser ceilings come from the slowest final desktop/mobile samples on the
# representative copied course, recorded in the page-load verification log.
# Keep startup width-specific; navigation uses the worst observed value per page.
API_LOAD_BUDGET_SECONDS = 3.0
STARTUP_BUDGET_MS = {1440: 2_764, 390: 2_590}
PAGE_BUDGET_MS = {
    "oggi": 2_538,
    "sessione": 1_450,
    "fonti": 2_913,
    "proposte": 316,
    "verifiche": 316,
    "percorso": 332,
    "ripasso": 365,
    "piano": 282,
    "impostazioni": 34,
}
# Recorded samples are rounded and browser scheduling adds small run-to-run
# variation. Allow 15% (at least 300 ms) above each measured baseline.
REGRESSION_ALLOWANCE_MS = 300


def _regression_ceiling(baseline_ms: int) -> int:
    return min(3000, baseline_ms + max(REGRESSION_ALLOWANCE_MS, baseline_ms * 15 // 100) + 1)


# Each selector belongs to the real renderer, so a heading in the loading
# skeleton or a quickly displayed error/unavailable surface cannot pass.
PAGE_CONTENT = {
    "oggi": "#hero-entry textarea",
    "sessione": "#session-entry textarea",
    "fonti": ".source-row [data-source-viewer]",
    "proposte": "[data-command=artifact]",
    "verifiche": "#assessment-heading",
    "percorso": "#student-state-heading",
    "ripasso": "[data-reveal-review]",
    "piano": "#plan-heading",
    "impostazioni": "#settings-heading",
}


def _large_course(directory: Path) -> tuple[RepositoryUiApplication, _BrowserModel]:
    root = directory / "repository"
    revision = _seed(root)
    model = _BrowserModel()
    adapters = ModelAdapterRegistry(
        {"browser-fixture": lambda _config, _credential: model},
        versions={"browser-fixture": "1.0.0"},
    )
    (root / CONFIG_FILENAME).write_bytes(LocalRepositoryConfig(
        ModelAdapterConfig("browser-fixture", {}, None),
        features=SemanticFeaturesConfig(document_index_mode=FeatureMode.ON),
    ).to_bytes())
    # Representative original-blob size, page count and canonical chunks. This
    # is an admitted extraction fixture: PDF conversion itself is not measured.
    pages = [
        f"# Lesson {index}\n\n" + "\n\n".join(
            f"Section {part}. " + "The aortic valve has three cusps. " * 14
            for part in range(7)
        ) + "\n\n"
        for index in range(559)
    ]
    content = "".join(pages).encode()
    original = b"%PDF-synthetic\n" + b"0" * (194 * 1024 * 1024)
    spans = []
    offset = 0
    for index, page in enumerate(pages, 1):
        spans.append(DocumentPageSpan(index, offset, offset + len(page)))
        offset += len(page)
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        result = TextIngestionService(
            blobs=repository.blobs, events=repository.events, clock=repository.clock,
            courses=repository.courses, chunking=ChunkingConfig(max_characters=600),
        ).ingest(
            filename="textbook.md", content=content, original_content=original,
            source_id=SourceId("textbook"), title="Synthetic textbook",
            trust_level=90, source_role="primary", content_origin=ContentOrigin.EXTRACTED,
            conversion_provenance=DocumentConversionProvenance(
                sha256(original).hexdigest(), sha256(content).hexdigest(),
                "offline-fixture", "1", sha256(b"fixture-manifest").hexdigest(),
                "fixture-normalizer@1", ("Synthetic bytes; no PDF conversion tested",),
                page_count=len(pages), page_spans=tuple(spans),
            ),
            context=_context("textbook"),
        )
        assert len(result.chunks) >= 3_900
        statuses = repository.reconcile_pageindex(COURSE, budget=8)
        assert statuses and all(item.status is PageIndexStatus.READY for item in statuses)
        # Keep Proposte populated as well as the enrolled card in Ripasso.
        first = repository.artifacts.get(COURSE).revision(revision)
        assert isinstance(first.provenance, HumanAuthoredArtifactProvenance)
        repository.artifact_service.record_human_revision(
            first.content, first.provenance, None, _context("pending-card"),
            repository.events.read(COURSE)[-1].course_sequence,
        )
    app = RepositoryUiApplication(
        root, COURSE, SESSION, model_adapters=adapters,
        recall_scheduler_factory=PyFsrsSchedulingPolicy,
    )
    receipt = _accept(app, revision, "accept-budget-card")
    app.post(f"/api/v1/recall/{revision}/enrollments", _command(
        "enroll-budget-card", cast(int, receipt["high_water_sequence"]),
    ))
    return app, model


@pytest.fixture(scope="module")
def large_course(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RepositoryUiApplication]:
    app, model = _large_course(tmp_path_factory.mktemp("page-load"))
    with LocalRepository.open(app.repository) as repository:
        before = repository.events.read(COURSE)
    yield app
    assert model.requests == [], "page loads must never invoke a provider"
    with LocalRepository.open(app.repository) as repository:
        assert repository.events.read(COURSE) == before, "page loads changed canonical events"


def test_large_course_api_loads_within_three_seconds(
    large_course: RepositoryUiApplication,
) -> None:
    failures = []
    for iteration in range(2):
        for path in API_PATHS:
            start = perf_counter()
            payload = large_course.get(path)
            elapsed = perf_counter() - start
            print(f"API pass={iteration} {path}: {elapsed:.3f}s")
            assert payload["schema_version"] == 1
            if elapsed > API_LOAD_BUDGET_SECONDS:
                failures.append(f"{path}: {elapsed:.3f}s")
    assert not failures, "Page load budget exceeded: " + ", ".join(failures)


@pytest.mark.parametrize("width", [1440, 390], ids=["desktop", "mobile"])
def test_browser_page_loads_within_three_seconds(
    large_course: RepositoryUiApplication, width: int,
) -> None:
    with _serve(application=large_course) as url, _real_browser("about:blank") as browser:
        browser.call("Emulation.setDeviceMetricsOverride", width=width, height=844,
                     deviceScaleFactor=1, mobile=width < 500)
        browser.navigate(url)
        browser.wait_for("Boolean(document.querySelector('#hero-entry textarea'))")
        initial_ms = cast(float, browser.evaluate("performance.now()"))
        print(f"Browser width={width} initial: {initial_ms:.0f}ms")
        startup_limit_ms = _regression_ceiling(STARTUP_BUDGET_MS[width])
        assert initial_ms <= startup_limit_ms, (
            f"startup: {initial_ms:.0f}ms > {startup_limit_ms}ms"
        )
        for iteration in range(2):
            for route, content in PAGE_CONTENT.items():
                # Explicitly include material-job loading, not just Fonti's first
                # paint. Two animation frames give the populated DOM a paint.
                expression = """(async () => {
                  const route = ROUTE;
                  const start = performance.now();
                  document.querySelector('.nav-item[data-route="' + route + '"]').click();
                  while (performance.now() - start < 4000) {
                    const root = document.querySelector('#view-root');
                    const active = document.querySelector('.nav-item[data-route="' + route +
                      '"][aria-current="page"]');
                    if (active && root.querySelector(CONTENT) &&
                        !root.hasAttribute('aria-busy') &&
                        !root.querySelector('.loading-state, .error-state, .unavailable-state') &&
                        (route !== 'fonti' || !root.querySelector('[data-material-jobs-status]'))) {
                      await new Promise(resolve => requestAnimationFrame(() =>
                        requestAnimationFrame(resolve)));
                      return performance.now() - start;
                    }
                    await new Promise(resolve => setTimeout(resolve, 10));
                  }
                  throw Error('Page did not become usable: ' + route);
                })()""".replace("ROUTE", json.dumps(route)).replace("CONTENT", json.dumps(content))
                elapsed = cast(float, browser.evaluate(expression, await_promise=True))
                print(f"Browser width={width} pass={iteration} {route}: {elapsed:.0f}ms")
                baseline_ms = PAGE_BUDGET_MS[route]
                limit_ms = _regression_ceiling(baseline_ms)
                assert elapsed <= limit_ms, f"{route}: {elapsed:.0f}ms > {limit_ms}ms"
        assert browser.evaluate("window.__cardineErrors") == []


def test_browser_regression_ceiling_preserves_absolute_budget() -> None:
    assert _regression_ceiling(2913) == 3000
    assert _regression_ceiling(316) >= 616
