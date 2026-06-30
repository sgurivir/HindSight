"""File summary tool: getSummaryOfFile.

Returns a pruned version of the file (signatures, comments, structure) using
the existing `CodeContextPruner` from `hindsight.core.lang_util`. Falls back
to a structural summary built from the AST artifacts (merged_call_graph.json
+ merged_defined_classes.json) when full file reading isn't possible.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from hindsight.core.lang_util.code_context_pruner import CodeContextPruner
from hindsight.utils.log_util import get_logger

from .registry import ToolContext

logger = get_logger(__name__)


async def get_summary_of_file_tool(args: Dict[str, Any], ctx: ToolContext) -> str:
    return await asyncio.to_thread(_summary_sync, args, ctx)


def _summary_sync(args: Dict[str, Any], ctx: ToolContext) -> str:
    raw_path = args.get("path")
    reason = args.get("reason")
    start = time.time()

    if not raw_path or not isinstance(raw_path, str):
        ctx.record("getSummaryOfFile")
        return "Error: 'path' parameter is required and must be a file path string."
    file_path = raw_path.strip()
    if not file_path:
        ctx.record("getSummaryOfFile")
        return "Error: 'path' parameter cannot be empty."

    logger.info(
        f"[TOOL] getSummaryOfFile - path={file_path} | "
        f"[AI REASONING] {reason or 'No reason provided'}"
    )

    try:
        summary = _summary_for_path(file_path, ctx)
        if summary:
            final = f"Summary for file: {file_path}\n{summary}"
            ctx.record("getSummaryOfFile", chars=len(final), item=file_path)
        else:
            # Fallback: search for files with the same basename.
            filename = os.path.basename(file_path)
            matching = _find_files_by_name(ctx.repo_path, filename)
            if len(matching) == 1:
                found = matching[0]
                fallback = _summary_for_path(found, ctx)
                if fallback:
                    final = (
                        f"Summary for file: {found} (found by filename '{filename}')\n{fallback}"
                    )
                    ctx.record("getSummaryOfFile", chars=len(final), item=found)
                else:
                    final = (
                        f"No summary available for file '{found}' "
                        f"(found by filename '{filename}')"
                    )
                    ctx.record("getSummaryOfFile", chars=len(final), item=file_path)
            elif len(matching) > 1:
                final = (
                    f"Multiple files found with name '{filename}': {matching}. "
                    "Please specify the full path."
                )
                ctx.record("getSummaryOfFile", chars=len(final), item=file_path)
            else:
                final = f"No summary available for file '{file_path}'. File not found in repository."
                ctx.record("getSummaryOfFile", chars=len(final), item=file_path)

        logger.info(
            f"[TOOL] getSummaryOfFile completed - path={file_path}, chars={len(final)}, "
            f"time={time.time()-start:.2f}s"
        )
        return final
    except Exception as exc:
        logger.error(f"[TOOL] getSummaryOfFile failed - path={file_path}: {exc}")
        ctx.record("getSummaryOfFile", item=file_path)
        return f"Error retrieving summary for file '{file_path}': {exc}"


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


def _summary_for_path(file_path: str, ctx: ToolContext) -> Optional[str]:
    """Generate a pruned summary; fall back to AST-derived basic summary."""
    full = Path(ctx.repo_path) / file_path
    if not full.exists():
        return None

    try:
        content = full.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        logger.debug(f"_summary_for_path read failed: {exc}")
        return None

    if not content.strip():
        return f"File: {file_path}\nNote: File is empty"

    try:
        numbered = CodeContextPruner.add_line_numbers(content, 1)
        pruned = CodeContextPruner.prune_code(numbered)
        file_size = full.stat().st_size
        ext = os.path.splitext(file_path)[1].lower()
        header = (
            f"File: {file_path}\n"
            f"Size: {file_size:,} bytes\n"
            f"Type: {ext} file\n"
            f"Lines: {len(content.splitlines())}\n\n"
            "Pruned Content (signatures and comments, implementations removed):\n"
            + "=" * 50
            + "\n"
        )
        return header + pruned
    except Exception as exc:
        logger.debug(f"CodeContextPruner failed for {file_path}: {exc}; falling back")
        return _basic_summary_from_artifacts(file_path, ctx) or _fallback_summary(file_path, ctx)


def _basic_summary_from_artifacts(file_path: str, ctx: ToolContext) -> Optional[str]:
    """Build a structural summary by looking up the file in AST artifacts."""
    artifacts = ctx.get_artifacts_dir()
    call_graph_path = os.path.join(artifacts, "merged_call_graph.json")
    classes_path = os.path.join(artifacts, "merged_defined_classes.json")

    parts: List[str] = [f"File: {file_path}", ""]

    classes_data = _load_json(classes_path)
    if classes_data:
        for entry in _classes_in_file(classes_data, file_path):
            class_name = entry.get("data_type_name", "Unknown")
            for info in entry.get("files", []):
                if not isinstance(info, dict):
                    continue
                name = info.get("file_name", "")
                if not name or not (name == file_path or file_path.endswith(name)):
                    continue
                parts.extend(
                    [
                        f"== {class_name} is at",
                        f"File: {name}",
                        f"starting_line : {info.get('start', '')} , "
                        f"ending_line : {info.get('end', '')}",
                        "",
                    ]
                )

    call_graph = _load_json(call_graph_path)
    if call_graph:
        for entry in _methods_in_file(call_graph, file_path):
            method = entry.get("function", "Unknown")
            context = entry.get("context", {})
            name = context.get("file", "")
            if not name or not (name == file_path or file_path.endswith(name)):
                continue
            parts.extend(
                [
                    f"== {method} is at",
                    f"File: {name}",
                    f"starting_line : {context.get('start', '')} ,  "
                    f"ending_line : {context.get('end', '')}",
                    "",
                ]
            )

    if len(parts) <= 2:
        return None
    return "\n".join(parts)


def _fallback_summary(file_path: str, ctx: ToolContext) -> Optional[str]:
    """Minimal summary when no AST data is available."""
    full = Path(ctx.repo_path) / file_path
    if not full.exists():
        return None
    try:
        size = full.stat().st_size
        ext = os.path.splitext(file_path)[1].lower()
        return (
            f"File: {file_path}\n"
            f"Size: {size:,} bytes\n"
            f"Type: {ext} file\n\n"
            "Note: No structured analysis data available from merged_call_graph.json "
            "or merged_defined_classes.json"
        )
    except Exception as exc:
        return f"File: {file_path}\nError: Could not analyze file - {exc}"


def _load_json(path: str) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        logger.debug(f"_load_json failed for {path}: {exc}")
    return None


def _classes_in_file(classes_data: Any, target_file: str) -> List[Dict[str, Any]]:
    if not isinstance(classes_data, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in classes_data:
        if not isinstance(entry, dict):
            continue
        for info in entry.get("files", []):
            if isinstance(info, dict):
                fn = info.get("file_name", "")
                if fn and (fn == target_file or target_file.endswith(fn)):
                    out.append(entry)
                    break
    return out


def _methods_in_file(call_graph: Any, target_file: str) -> List[Dict[str, Any]]:
    if not isinstance(call_graph, list):
        return []
    out: List[Dict[str, Any]] = []
    for file_entry in call_graph:
        name = file_entry.get("file", "")
        if not name or not (name == target_file or target_file.endswith(name)):
            continue
        for func in file_entry.get("functions", []):
            copy = dict(func)
            if "context" not in copy:
                copy["context"] = {}
            if "file" not in copy["context"]:
                copy["context"]["file"] = name
            out.append(copy)
    return out


def _find_files_by_name(repo_path: str, filename: str) -> List[str]:
    matches: List[str] = []
    try:
        for root, _, files in os.walk(repo_path):
            if filename in files:
                matches.append(os.path.relpath(os.path.join(root, filename), repo_path))
    except Exception as exc:
        logger.debug(f"_find_files_by_name walk failed: {exc}")
    return matches
