"""Offline synthetic projection comparison; emits counts/times, never source text.

Run with the project's installed development Python. --baseline-ref loads only
that commit's document_index/unitizer implementations in this isolated process.
No store, retrieval policy, network, provider or generation is exercised.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from unittest.mock import patch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as private:
        if args.baseline_ref:
            for name in ("document_index", "unitizer"):
                module_name = f"study_agent.knowledge.{name}"
                code = subprocess.run(
                    ["git", "show", f"{args.baseline_ref}:src/study_agent/knowledge/{name}.py"],
                    check=True,
                    capture_output=True,
                ).stdout
                path = Path(private) / f"{name}.py"
                path.write_bytes(code)
                spec = importlib.util.spec_from_file_location(module_name, path)
                assert spec is not None and spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
        measure(baseline=bool(args.baseline_ref))


def measure(*, baseline: bool) -> None:
    from datetime import UTC, datetime
    from hashlib import sha256

    from study_agent.domain import BlobId, RevisionId, SourceId
    from study_agent.domain.document_index import (
        DocumentIndex,
        DocumentNode,
        LocatorKind,
        SourceLocator,
    )
    from study_agent.domain.identifiers import substrate_id_for
    from study_agent.domain.provenance import StructureOrigin
    from study_agent.domain.source import BlobRef, SourceDocument, SourceKind
    from study_agent.domain.substrate import Substrate
    from study_agent.ingestion import chunk_text
    from study_agent.knowledge import document_index as locators
    from study_agent.knowledge import unitizer
    from study_agent.knowledge.unitizer import candidates_for_canonical_chunks

    text = "".join(f"Café synthetic paragraph {n:04d} " + "x" * 350 + "\n\n" for n in range(3676))
    assert len(text.encode()) <= 2 * 1024 * 1024
    start = perf_counter()
    digest = sha256(text.encode()).hexdigest()
    blob = BlobRef(BlobId(f"sha256:{digest}"), digest, len(text.encode()))
    source = SourceDocument(
        SourceId("synthetic"),
        RevisionId("synthetic"),
        SourceKind.MARKDOWN,
        "Synthetic",
        "text/markdown",
        digest,
        blob.byte_length,
        datetime(2026, 1, 1, tzinfo=UTC),
        100,
        "primary",
        blob,
        blob,
        "normalization@1",
        len(text),
        StructureOrigin.SOURCE_AUTHORED,
        "synthetic",
    )
    binding = locators.DocumentIndexContext(
        source,
        Substrate(substrate_id_for(text.encode()), blob, len(text), "normalization@1"),
        text,
    )
    chunks = chunk_text(
        text,
        source_id=binding.source.source_id,
        revision_id=binding.source.revision_id,
        kind=binding.source.kind,
    )

    def lines(first: int, last: int) -> SourceLocator:
        return SourceLocator(LocatorKind.MARKDOWN_LINE_RANGE, start_line=first, end_line=last)

    nodes = (
        DocumentNode(
            "root",
            None,
            "Synthetic",
            None,
            lines(1, 7352),
            0,
            tuple(f"n{n}" for n in range(700)),
        ),
        *(
            DocumentNode(
                f"n{n}",
                "root",
                f"Section {n}",
                None,
                lines((n * 3676 // 700) * 2 + 1, ((n + 1) * 3676 // 700) * 2),
                n + 1,
            )
            for n in range(700)
        ),
    )
    derived = DocumentIndex(
        source.source_id,
        source.revision_id,
        binding.substrate.substrate_id,
        "1",
        "synthetic",
        "1",
        "config",
        nodes,
    )
    setup = perf_counter() - start
    selected = chunks[100:108]
    times = []
    counts = []
    outputs = []
    for _ in range(2):
        with ExitStack() as stack:
            resolve = stack.enter_context(
                patch.object(locators, "resolve_locator", wraps=locators.resolve_locator)
            )
            if baseline:
                stack.enter_context(
                    patch.object(unitizer, "resolve_locator", wraps=locators.resolve_locator)
                )
            start = perf_counter()
            if baseline:
                all_candidates = candidates_for_canonical_chunks(derived, binding, chunks)
                output = tuple(item for item in all_candidates if item.order in range(100, 108))
            else:
                output = candidates_for_canonical_chunks(
                    derived,
                    binding,
                    chunks,
                    selected_chunks=selected,
                )
            times.append(round(perf_counter() - start, 4))
            counts.append(resolve.call_count)
            outputs.append(
                tuple(
                    (item.order, item.span.start_offset, item.span.end_offset, item.node_key)
                    for item in output
                )
            )
    assert outputs[0] == outputs[1] and len(outputs[0]) == 8 and len(chunks) == 3676
    print(
        json.dumps(
            {
                "baseline": baseline,
                "characters": len(text),
                "utf8_bytes": len(text.encode()),
                "nodes": len(nodes),
                "chunks": len(chunks),
                "selected": len(selected),
                "setup_seconds": round(setup, 4),
                "projection_seconds_cold_warm": times,
                "locator_calls_cold_warm": counts,
                "selected_projection_digest": sha256(repr(outputs[0]).encode()).hexdigest(),
                "providers_called": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
