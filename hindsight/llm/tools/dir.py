"""Directory tools: list_files, inspectDirectoryHierarchy.

Both delegate to existing helpers (`DirectoryTreeUtil` and
`RepositoryDirHierarchy`) that are out of scope for this rewrite. Blocking
I/O is wrapped in `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional

from hindsight.report.issue_directory_organizer import RepositoryDirHierarchy
from hindsight.utils.log_util import get_logger

from .registry import ToolContext

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# list_files
# ----------------------------------------------------------------------


async def list_files_tool(args: Dict[str, Any], ctx: ToolContext) -> str:
    """List a directory's contents using the session's `DirectoryTreeUtil`."""
    return await asyncio.to_thread(_list_files_sync, args, ctx)


def _list_files_sync(args: Dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "")
    recursive = bool(args.get("recursive", False))
    reason = args.get("reason")
    start = time.time()

    if not isinstance(path, str):
        msg = f"Error: path parameter must be a string, got {type(path)}: {path}"
        logger.error(f"[TOOL] list_files - {msg}")
        ctx.record("list_files")
        return msg

    path = path.strip() or ctx.repo_path
    logger.info(
        f"[TOOL] list_files - path={path}, recursive={recursive} | "
        f"[AI REASONING] {reason or 'No reason provided'}"
    )

    if not ctx.directory_tree_util:
        msg = "Error: DirectoryTreeUtil not available. This tool requires DirectoryTreeUtil to be initialized."
        logger.error(f"[TOOL] list_files - {msg}")
        ctx.record("list_files")
        return msg

    try:
        result = ctx.directory_tree_util.get_directory_listing(
            repo_path=ctx.repo_path,
            relative_path=path,
            recursive=recursive,
        )
        if result and not result.startswith("Path not found"):
            mode = "recursive" if recursive else "single level"
            result = f"Directory listing for '{path}' ({mode}):\n{result}"

        ctx.record("list_files", chars=len(result), item=path)
        logger.info(
            f"[TOOL] list_files completed - path={path}, chars={len(result)}, "
            f"time={time.time()-start:.2f}s"
        )
        return result
    except Exception as exc:
        msg = f"Error getting directory listing for '{path}': {exc}"
        logger.error(f"[TOOL] list_files failed - {msg}")
        ctx.record("list_files", item=path)
        return msg


# ----------------------------------------------------------------------
# inspectDirectoryHierarchy
# ----------------------------------------------------------------------


async def inspect_directory_hierarchy_tool(args: Dict[str, Any], ctx: ToolContext) -> str:
    """Return either a single directory's hierarchy or the whole repo's structure."""
    return await asyncio.to_thread(_inspect_dir_sync, args, ctx)


def _inspect_dir_sync(args: Dict[str, Any], ctx: ToolContext) -> str:
    directory_path: Optional[str] = args.get("path")
    reason = args.get("reason")
    start = time.time()

    if directory_path is not None and not isinstance(directory_path, str):
        msg = (
            f"Error: directory_path parameter must be a string, got {type(directory_path)}: {directory_path}"
        )
        logger.error(f"[TOOL] inspectDirectoryHierarchy - {msg}")
        ctx.record("inspectDirectoryHierarchy")
        return msg
    if isinstance(directory_path, str):
        directory_path = directory_path.strip() or None

    logger.info(
        f"[TOOL] inspectDirectoryHierarchy - path={directory_path or 'root'} | "
        f"[AI REASONING] {reason or 'No reason provided'}"
    )

    try:
        if directory_path:
            result = _hierarchy_for_path(ctx.repo_path, directory_path)
        else:
            try:
                structure = RepositoryDirHierarchy.get_directory_structure_for_repo(ctx.repo_path)
                result = f"Repository directory structure:\n{structure}"
            except Exception as exc:
                result = f"Error getting repository directory structure: {exc}"

        ctx.record("inspectDirectoryHierarchy", chars=len(result), item=directory_path or "root")
        logger.info(
            f"[TOOL] inspectDirectoryHierarchy completed - "
            f"path={directory_path or 'root'}, chars={len(result)}, "
            f"time={time.time()-start:.2f}s"
        )
        return result
    except Exception as exc:
        msg = f"Error inspecting directory hierarchy for '{directory_path or 'root'}': {exc}"
        logger.error(f"[TOOL] inspectDirectoryHierarchy failed - {msg}")
        ctx.record("inspectDirectoryHierarchy", item=directory_path or "root")
        return msg


def _hierarchy_for_path(repo_path: str, directory_path: str) -> str:
    """Try exact path, then fall back to a name match across the repo."""
    try:
        hierarchy = RepositoryDirHierarchy(repo_path)
        structure = hierarchy.get_directory_hierarchy_by_path(directory_path)
        if structure:
            return f"Directory hierarchy for '{directory_path}':\n{structure}"

        dir_name = os.path.basename(directory_path.rstrip("/"))
        matching = hierarchy.find_directories_by_name(dir_name)
        if not matching:
            return f"Directory '{directory_path}' not found in repository"

        parts: list[str] = [
            f"Directory '{directory_path}' not found at exact path, "
            f"but found {len(matching)} directories with name '{dir_name}':"
        ]
        for i, node in enumerate(matching, 1):
            relative = os.path.relpath(node.path, repo_path)
            parts.append(f"\n{i}. {relative}/")
            dir_structure = hierarchy.get_directory_hierarchy_by_path(relative)
            if not dir_structure:
                continue
            lines = dir_structure.split("\n")[:10]
            if len(lines) == 10:
                lines.append("   ... (truncated)")
            parts.append("\n".join(f"   {line}" for line in lines))
        return "\n".join(parts)
    except Exception as exc:
        return f"Error inspecting directory '{directory_path}': {exc}"
