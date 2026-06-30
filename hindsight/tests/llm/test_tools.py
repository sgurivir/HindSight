"""Tests for hindsight.llm.tools — registry, schemas, and per-tool handlers."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from hindsight.llm import ToolCall
from hindsight.llm.tools import (
    TOOL_DEFINITIONS,
    ToolContext,
    ToolRegistry,
    build_default_registry,
    check_file_size_tool,
    get_file_content_by_lines_tool,
    get_implementation_tool,
    list_files_tool,
    normalize_parameters,
    read_file_tool,
    run_terminal_cmd_tool,
    validate_tool_parameters,
)


# ----------------------------------------------------------------------
# schemas
# ----------------------------------------------------------------------


def test_schemas_define_every_tool():
    assert set(TOOL_DEFINITIONS.keys()) == {
        "readFile",
        "getFileContentByLines",
        "checkFileSize",
        "runTerminalCmd",
        "list_files",
        "inspectDirectoryHierarchy",
        "getImplementation",
        "getSummaryOfFile",
    }


def test_normalize_parameters_renames_aliases():
    assert normalize_parameters("readFile", {"file_path": "x"}) == {"path": "x"}
    assert normalize_parameters("getFileContentByLines", {"start_line": 1, "end_line": 5}) == {
        "startLine": 1,
        "endLine": 5,
    }
    assert normalize_parameters("getImplementation", {"class_name": "Foo"}) == {"name": "Foo"}


def test_validate_tool_parameters_required_and_types():
    ok, _ = validate_tool_parameters("readFile", {"path": "x"})
    assert ok
    ok, err = validate_tool_parameters("readFile", {})
    assert not ok and "Missing required parameter: path" in err
    ok, err = validate_tool_parameters("readFile", {"path": 123})
    assert not ok and "must be a string" in err
    ok, err = validate_tool_parameters("getFileContentByLines", {"path": "x", "startLine": "1", "endLine": 5})
    assert not ok and "must be an integer" in err


# ----------------------------------------------------------------------
# registry — allowed-set + error handling
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_rejects_disallowed_tool(tmp_path):
    ctx = ToolContext(repo_path=str(tmp_path))
    reg = build_default_registry(ctx)
    out = await reg.execute(ToolCall(name="readFile", args={"path": "x"}), allowed=frozenset({"runTerminalCmd"}))
    assert "is not available in this context" in out


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool(tmp_path):
    ctx = ToolContext(repo_path=str(tmp_path))
    reg = build_default_registry(ctx)
    out = await reg.execute(ToolCall(name="bogusTool", args={}), allowed=frozenset({"bogusTool"}))
    assert "Unknown tool" in out


@pytest.mark.asyncio
async def test_registry_validates_missing_required_param(tmp_path):
    ctx = ToolContext(repo_path=str(tmp_path))
    reg = build_default_registry(ctx)
    out = await reg.execute(ToolCall(name="readFile", args={}), allowed=frozenset({"readFile"}))
    assert "Missing required parameter: path" in out


@pytest.mark.asyncio
async def test_registry_special_error_for_query_alias(tmp_path):
    """LLMs sometimes pass {tool: getImplementation, query: Foo}. Preserve the
    legacy helpful error message that calls out the right parameter name."""
    ctx = ToolContext(repo_path=str(tmp_path))
    reg = build_default_registry(ctx)
    out = await reg.execute(
        ToolCall(name="getImplementation", args={"query": "Foo"}),
        allowed=frozenset({"getImplementation"}),
    )
    assert "requires 'name' parameter" in out


# ----------------------------------------------------------------------
# fs tools
# ----------------------------------------------------------------------


@pytest.fixture
def small_repo(tmp_path):
    # `.swift` because ALL_SUPPORTED_EXTENSIONS is the analyzer's source-language
    # set; `getImplementation`'s repo-walk fallback skips other extensions.
    (tmp_path / "a.swift").write_text("func foo() {\n    return 1\n}\n", encoding="utf-8")
    (tmp_path / "b.swift").write_text("func bar() {\n    return 2\n}\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.swift").write_text("func baz() {\n    return 3\n}\n", encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_read_file_tool_returns_content(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await read_file_tool({"path": "a.swift"}, ctx)
    assert "func foo" in out
    # CodeContextPruner adds line numbers; just check content is present.
    assert ctx.stats["readFile"].count == 1


@pytest.mark.asyncio
async def test_read_file_tool_missing_file(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await read_file_tool({"path": "nope.swift"}, ctx)
    assert "cannot be found" in out


@pytest.mark.asyncio
async def test_check_file_size_tool_returns_json_with_line_count(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await check_file_size_tool({"path": "a.swift"}, ctx)
    payload = json.loads(out)
    assert payload["file_available"] is True
    # File has "func foo() {\n    return 1\n}\n" → 3 lines + a trailing blank from splitlines.
    assert payload["line_count"] == 3
    assert payload["size_bytes"] > 0
    assert payload["within_size_limit"] is True


@pytest.mark.asyncio
async def test_check_file_size_tool_missing_file_returns_json(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await check_file_size_tool({"path": "nope.swift"}, ctx)
    payload = json.loads(out)
    assert payload["file_available"] is False
    assert "not found" in payload["error"]


@pytest.mark.asyncio
async def test_get_file_content_by_lines_basic(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await get_file_content_by_lines_tool(
        {"path": "a.swift", "startLine": 1, "endLine": 1}, ctx
    )
    # split('\n') on content ending in newline yields 4 entries: 3 lines + empty.
    assert "lines 1-1 of 4 total" in out
    assert "func foo" in out


@pytest.mark.asyncio
async def test_get_file_content_by_lines_end_of_file_signal(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await get_file_content_by_lines_tool(
        {"path": "a.swift", "startLine": 100, "endLine": 200}, ctx
    )
    payload = json.loads(out)
    assert payload["end_of_file"] is True
    assert payload["total_lines"] == 4


@pytest.mark.asyncio
async def test_get_file_content_by_lines_validates_args(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await get_file_content_by_lines_tool(
        {"path": "a.swift", "startLine": 5, "endLine": 2}, ctx
    )
    assert "cannot be greater than" in out


# ----------------------------------------------------------------------
# shell tool — async subprocess + validation + timeout
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_terminal_cmd_runs_safe_command(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await run_terminal_cmd_tool({"command": "ls"}, ctx)
    assert "a.swift" in out
    assert "b.swift" in out
    assert "Command: ls" in out


@pytest.mark.asyncio
async def test_run_terminal_cmd_blocks_dangerous_command(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await run_terminal_cmd_tool({"command": "rm -rf /"}, ctx)
    # CommandValidator should reject `rm`.
    assert "blocked" in out.lower() or "not allowed" in out.lower() or "Command" not in out


@pytest.mark.asyncio
async def test_run_terminal_cmd_returns_nonzero_exit(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await run_terminal_cmd_tool({"command": "ls /nonexistent_path_xyz"}, ctx)
    assert "Exit code:" in out


@pytest.mark.asyncio
async def test_run_terminal_cmd_empty_string_rejected(small_repo):
    ctx = ToolContext(repo_path=str(small_repo))
    out = await run_terminal_cmd_tool({"command": "   "}, ctx)
    assert "must be a non-empty string" in out


# ----------------------------------------------------------------------
# Concurrent tool dispatch (validates async value)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_dispatch_via_registry(small_repo):
    """Three tool calls dispatched concurrently against a real registry."""
    ctx = ToolContext(repo_path=str(small_repo))
    reg = build_default_registry(ctx)
    allowed = frozenset({"readFile", "checkFileSize"})

    calls = [
        ToolCall(name="readFile", args={"path": "a.swift"}),
        ToolCall(name="readFile", args={"path": "b.swift"}),
        ToolCall(name="checkFileSize", args={"path": "sub/c.swift"}),
    ]
    results = await asyncio.gather(*[reg.execute(c, allowed=allowed) for c in calls])
    assert "func foo" in results[0]
    assert "func bar" in results[1]
    assert json.loads(results[2])["file_available"] is True
    # Stats accumulated for all three.
    assert ctx.stats["readFile"].count == 2
    assert ctx.stats["checkFileSize"].count == 1


# ----------------------------------------------------------------------
# symbols tool — registry-driven lookup with a minimal merged_functions.json
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_implementation_uses_function_registry(small_repo, tmp_path):
    """With a merged_functions.json registry, getImplementation returns the
    numbered slice at the registered line range.
    """
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    registry_path = artifacts / "merged_functions.json"
    # Build a minimal registry pointing at our fixture function.
    registry_path.write_text(
        json.dumps(
            {
                "foo": {
                    "code": [
                        {"file_name": "a.swift", "start": 1, "end": 3},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    ctx = ToolContext(repo_path=str(small_repo), artifacts_dir=str(artifacts))
    out = await get_implementation_tool({"name": "foo"}, ctx)
    assert "Function Implementation: foo" in out
    assert "func foo" in out
    assert "Start_line: 1" in out


@pytest.mark.asyncio
async def test_get_implementation_falls_back_to_repo_search(small_repo, tmp_path):
    """No registry → search the repo by content for `foo`."""
    ctx = ToolContext(repo_path=str(small_repo), artifacts_dir=str(tmp_path / "empty"))
    out = await get_implementation_tool({"name": "foo"}, ctx)
    # Legacy header format: `f"{kind} Implementation: {name}"` with
    # `kind="Potential Implementation"` yields the doubled word — preserved.
    assert "Implementation: foo" in out
    assert "a.swift" in out
    assert "func foo" in out
