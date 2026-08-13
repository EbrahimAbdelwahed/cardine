from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

import pytest

from cardine.adapters.pageindex import PageIndexWorker, PageIndexWorkerError
from cardine.adapters.pageindex.worker_child import QUALIFIED_FUNCTION_AST_SHA256


def test_worker_executes_only_qualified_subset() -> None:
    tree = PageIndexWorker().run("# Lezione 1\nA\n## Dettaglio\nB\n")

    assert tree[0]["title"] == "Lezione 1"
    assert tree[0]["nodes"][0]["title"] == "Dettaglio"
    assert set(tree[0]) == {"title", "node_id", "text", "line_num", "nodes"}


def test_worker_rejects_oversized_input_before_process() -> None:
    with pytest.raises(PageIndexWorkerError, match="pageindex_input_limit"):
        PageIndexWorker().run("x" * (2 * 1024 * 1024))


def test_worker_rejects_heading_bomb_before_upstream_tree_build() -> None:
    markdown = "\n".join(f"# Heading {index}" for index in range(257))

    with pytest.raises(PageIndexWorkerError, match="pageindex_limit"):
        PageIndexWorker().run(markdown)


def test_worker_rejects_line_bomb_before_upstream_split() -> None:
    markdown = "x\n" * 16_384

    with pytest.raises(PageIndexWorkerError, match="pageindex_limit"):
        PageIndexWorker().run(markdown)


def test_qualified_data_source_is_hash_bound() -> None:
    path = Path(__file__).parents[5] / "src/cardine/adapters/pageindex/page_index_md.py.data"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "0a8831a5cf39e60d9e7e21ff95644a770e49f0b7e613be5c784619dd0c60bcbd"
    )


def test_qualified_function_ast_digests_match_on_qualification_python() -> None:
    if sys.version_info[:2] != (3, 13):
        pytest.skip("qualification digests are authored against CPython 3.13 AST")
    path = Path(__file__).parents[5] / "src/cardine/adapters/pageindex/page_index_md.py.data"
    source = path.read_text(encoding="utf-8")
    functions = {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in QUALIFIED_FUNCTION_AST_SHA256
    }
    assert {
        name: hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        for name, node in functions.items()
    } == QUALIFIED_FUNCTION_AST_SHA256
