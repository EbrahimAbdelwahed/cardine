"""Run a private needle manifest against verified, read-only repository snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

from cardine.evaluation.retrieval import GoldSpan, NeedleCase, SearchPlan, measure_case
from cardine.evaluation.snapshot import audit_snapshot
from study_agent.adapters.sqlite.fts_retrieval import (
    RETRIEVAL_STRATEGY_ID,
    RETRIEVAL_STRATEGY_VERSION,
    SQLiteFtsRetrieval,
)
from study_agent.domain import CourseId, RevisionId, SourceId
from study_agent.ports.retrieval import (
    IndexReceipt,
    RetrievalDocument,
    RetrievalEvidenceSet,
    RetrievalQuery,
)


class SnapshotSearch:
    """Reuse identical lexical probes only within one verified immutable snapshot."""

    def __init__(self, retrieval: SQLiteFtsRetrieval) -> None:
        self._retrieval = retrieval
        self._cache: dict[RetrievalQuery, RetrievalEvidenceSet] = {}

    def search(self, query: RetrievalQuery) -> RetrievalEvidenceSet:
        if query not in self._cache:
            self._cache[query] = self._retrieval.search(query)
        return self._cache[query]

    def index(self, documents: Sequence[RetrievalDocument]) -> IndexReceipt:
        raise PermissionError("benchmark search is read-only")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fail-on-miss", action="store_true")
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    manifest = json.loads(raw)
    if manifest["schema_version"] != 1:
        raise ValueError("unsupported needle manifest version")
    plans = tuple(SearchPlan(**item) for item in manifest["plans"])
    if not plans or len({plan.name for plan in plans}) != len(plans):
        raise ValueError("plans must have distinct names")
    rows = []
    print("Validating canonical snapshot and retrieval index...", file=sys.stderr)
    with audit_snapshot(args.repository, CourseId(manifest["course_id"])) as snapshot:
        catalog, retrieval, profile, metadata = snapshot
        search = SnapshotSearch(retrieval)
        cases = tuple(
            NeedleCase(
                case_id=item["id"],
                query=RetrievalQuery(
                    profile.id, item["query"], limit=item.get("search_limit", 8),
                    revision_ids=tuple(RevisionId(value) for value in item.get("revisions", [])),
                    minimum_trust_level=profile.source_policy.minimum_trust_level,
                    source_roles=profile.source_policy.allowed_roles,
                ),
                facets={
                    name: tuple(GoldSpan(
                        SourceId(span["source_id"]), RevisionId(span["revision_id"]),
                        span["start"], span["end"], span["checksum"],
                    ) for span in spans)
                    for name, spans in item.get("facets", {}).items()
                },
                alternatives=tuple(item.get("alternatives", [])),
                expected_empty=item.get("expected_empty", False),
            ) for item in manifest["cases"]
        )
        if not cases or len({case.case_id for case in cases}) != len(cases):
            raise ValueError("cases must have distinct ids")
        for case in cases:
            for plan in plans:
                rows.append(measure_case(case, plan, search, catalog))
            print(f"Measured {case.case_id}", file=sys.stderr)
    summaries = {}
    for plan in plans:
        results = [row for row in rows if row["plan"] == plan.name]
        positives = [row for row in results if not row["expected_empty"]]
        negatives = [row for row in results if row["expected_empty"]]
        summaries[plan.name] = {
            "positive_cases": len(positives),
            "complete_positive_cases": sum(bool(row["complete"]) for row in positives),
            "negative_cases": len(negatives),
            "correct_empty_cases": sum(bool(row["complete"]) for row in negatives),
            "false_sufficient_cases": sum(bool(row["false_sufficient"]) for row in results),
            "total_context_chunks": sum(row["context_chunks"] for row in results),
            "total_context_characters": sum(row["context_characters"] for row in results),
            "total_queries": sum(row["query_count"] for row in results),
        }
    report = {
        "schema_version": 1,
        "manifest_sha256": sha256(raw).hexdigest(),
        "retrieval_strategy": {
            "id": RETRIEVAL_STRATEGY_ID, "version": RETRIEVAL_STRATEGY_VERSION,
        },
        "snapshot": metadata,
        "summaries": summaries,
        "results": rows,
    }
    # Reports deliberately contain identities/bounds/counts, never source text.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return int(args.fail_on_miss and any(not row["complete"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
