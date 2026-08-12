"""Private child for the qualified, navigation-only PageIndex subset.

The qualified upstream source is carried as ``page_index_md.py.data``.  It is
never imported as a module: this child verifies its immutable bytes, extracts
only the four approved functions, and executes them in a tiny private
namespace.  All bounds and the JSON protocol are owned here rather than by
the upstream project.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_NODES = 256
MAX_DEPTH = 32
MAX_LINES = 16_384

QUALIFIED_UPSTREAM_COMMIT = "9470b639609e3113e66a58e23f36bd6b0221fd85"
QUALIFIED_ARCHIVE_SHA256 = (
    "c46682f3a9259087fefc8df79f4ed6b1d901c5177a4f25fa9ceced430fbb98bd"
)

QUALIFIED_SOURCE_SHA256 = (
    "0a8831a5cf39e60d9e7e21ff95644a770e49f0b7e613be5c784619dd0c60bcbd"
)
QUALIFIED_FUNCTION_AST_SHA256 = {
    "extract_nodes_from_markdown": (
        "7cbb34070cd3f1ad50b1a98cdc7f5f76775345e95c64500b69fa93b172325f1b"
    ),
    "extract_node_text_content": (
        "aa0be3ad156093d6d84edef07976867c5f76501d3fa66ede631e2c72448e71cb"
    ),
    "build_tree_from_nodes": (
        "f8dbd3951b09185e21e68a2fde7d909a2697aa23ee1fcfcde775388e4b1856f8"
    ),
    "clean_tree_for_output": (
        "1995c5f28298f798282dce0ff51d08af11d8a23b1468ca5602da76a39f005bd2"
    ),
}

# CPython 3.12 serializes a few AST fields differently from the qualified
# CPython 3.13 result.  These values are compatibility checks over the same
# source bytes; they do not qualify a different implementation.
_PY312_FUNCTION_AST_SHA256 = {
    "extract_nodes_from_markdown": (
        "d89ea3685e13b2985d8713d2979d3662016e28102f1b8756296e88f8215ac819"
    ),
    "extract_node_text_content": (
        "f21c7ad454201441bab40f552c28a5e76d571ed41fba60bbce08bcdb5f83366c"
    ),
    "build_tree_from_nodes": (
        "df35f02c126104a62cb7cb73b858dc09acd134887fbf32d934fc0e021db2fa1f"
    ),
    "clean_tree_for_output": (
        "c479e9c1da7be4731b63503767b12f3646ef4eadda464db0d87a741692697928"
    ),
}
_FUNCTION_NAMES = tuple(QUALIFIED_FUNCTION_AST_SHA256)


def _qualified_functions(source_path: Path) -> dict[str, Any]:
    source = source_path.read_bytes()
    if hashlib.sha256(source).hexdigest() != QUALIFIED_SOURCE_SHA256:
        raise ValueError("pageindex_source_digest")
    tree = ast.parse(source.decode("utf-8"), filename=str(source_path))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in _FUNCTION_NAMES
    }
    if tuple(functions) != _FUNCTION_NAMES or len(functions) != len(_FUNCTION_NAMES):
        raise ValueError("pageindex_function_set")
    expected = (
        QUALIFIED_FUNCTION_AST_SHA256
        if sys.version_info[:2] == (3, 13)
        else _PY312_FUNCTION_AST_SHA256
        if sys.version_info[:2] == (3, 12)
        else None
    )
    if expected is None:
        raise ValueError("pageindex_python_version")
    for name, function in functions.items():
        digest = hashlib.sha256(
            ast.dump(function, include_attributes=False).encode("utf-8")
        ).hexdigest()
        if digest != expected[name]:
            raise ValueError("pageindex_function_digest")
    namespace: dict[str, Any] = {"__builtins__": __builtins__, "re": re}
    selected = ast.Module(body=[functions[name] for name in _FUNCTION_NAMES], type_ignores=[])
    ast.fix_missing_locations(selected)
    exec(compile(selected, str(source_path), "exec"), namespace, namespace)
    return {name: namespace[name] for name in _FUNCTION_NAMES}


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.buffer.write(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _validate_tree(tree: object) -> None:
    if not isinstance(tree, list):
        raise ValueError("pageindex_tree")
    count = 0
    stack: list[tuple[object, int]] = [(node, 1) for node in reversed(tree)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, dict):
            raise ValueError("pageindex_tree")
        if set(node) - {"title", "node_id", "text", "line_num", "nodes"}:
            raise ValueError("pageindex_tree")
        if any(
            type(node.get(key)) is not str or not node[key]
            for key in ("title", "node_id", "text")
        ):
            raise ValueError("pageindex_tree")
        if type(node.get("line_num")) is not int or node["line_num"] < 1:
            raise ValueError("pageindex_tree")
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("pageindex_limit")
        children = node.get("nodes", [])
        if not isinstance(children, list):
            raise ValueError("pageindex_tree")
        stack.extend((child, depth + 1) for child in reversed(children))


def _preflight_markdown(markdown: str) -> None:
    if markdown.count("\n") + 1 > MAX_LINES:
        raise ValueError("pageindex_limit")
    in_code_block = False
    possible_nodes = 0
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block and stripped.startswith("#"):
            possible_nodes += 1
            if possible_nodes > MAX_NODES:
                raise ValueError("pageindex_limit")


def main() -> int:
    if len(sys.argv) != 2:
        _emit({"v": 1, "ok": False, "code": "pageindex_worker_protocol"})
        return 2
    try:
        functions = _qualified_functions(Path(sys.argv[1]))
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("pageindex_input_limit")
        request = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
        if type(request) is not dict or set(request) != {"markdown"}:
            raise ValueError("pageindex_worker_protocol")
        markdown = request["markdown"]
        if (
            type(markdown) is not str
            or not markdown
            or len(markdown.encode("utf-8")) > MAX_INPUT_BYTES
        ):
            raise ValueError("pageindex_input_limit")
        _preflight_markdown(markdown)
        raw_nodes, lines = functions["extract_nodes_from_markdown"](markdown)
        if not isinstance(raw_nodes, list) or len(raw_nodes) > MAX_NODES:
            raise ValueError("pageindex_limit")
        nodes = functions["extract_node_text_content"](raw_nodes, lines)
        tree = functions["clean_tree_for_output"](
            functions["build_tree_from_nodes"](nodes)
        )
        _validate_tree(tree)
        output = json.dumps(
            {"tree": tree}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if len(output) > MAX_OUTPUT_BYTES:
            raise ValueError("pageindex_output_limit")
        _emit({"v": 1, "ok": True, "tree": tree})
        return 0
    except (
        OSError,
        UnicodeError,
        SyntaxError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        code = str(error) if str(error).startswith("pageindex_") else "pageindex_worker_protocol"
        _emit({"v": 1, "ok": False, "code": code})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
