"""Filesystem tools: readFile, getFileContentByLines, checkFileSize.

Each handler is an async function the registry dispatches to. Blocking I/O
(file read, stat, walk) runs in a worker thread via `asyncio.to_thread` so it
doesn't tie up the event loop while many functions are being analyzed in
parallel.

Behavior matches the legacy `FileToolsMixin` exactly: same return strings,
same pruning, same end-of-file JSON signal, same `checkFileSize` JSON shape.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from hindsight.core.constants import MAX_FILE_CHARACTERS_FOR_READ_FILE
from hindsight.core.lang_util.code_context_aggressive_pruner import CodeContextAggressivePruner
from hindsight.core.lang_util.code_context_pruner import CodeContextPruner
from hindsight.utils.log_util import get_logger

from .registry import MAX_FILE_SIZE_BYTES, ToolContext

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# readFile
# ----------------------------------------------------------------------


async def read_file_tool(args: Dict[str, Any], ctx: ToolContext) -> str:
    """Read a file with size-based pruning (mirrors `execute_read_file_tool`)."""
    file_path = args["path"]
    return await asyncio.to_thread(_read_file_sync, file_path, ctx)


def _read_file_sync(file_path: str, ctx: ToolContext) -> str:
    start = time.time()
    resolved, original = ctx.resolve_file_path(file_path)
    if not resolved:
        msg = f"File '{original}' cannot be found"
        logger.error(f"[TOOL] readFile - {msg}")
        ctx.record("readFile")
        return msg

    try:
        text = resolved.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        err = f"Error: Failed to read file '{resolved}': {exc}"
        logger.error(f"[TOOL] readFile - {err}")
        ctx.record("readFile")
        return err

    # Aggressive pruning for files over the size threshold.
    if len(text) > MAX_FILE_CHARACTERS_FOR_READ_FILE:
        try:
            repo_obj = Path(ctx.repo_path).resolve()
            try:
                rel_path = str(resolved.relative_to(repo_obj))
            except ValueError:
                rel_path = str(resolved)
            pruned = CodeContextAggressivePruner.prune_file(str(repo_obj), rel_path)
            final = f"// File is too large. Here is pruned context for file {rel_path}\n" + pruned
            logger.info(
                f"[TOOL] readFile - aggressively pruned {resolved} "
                f"({len(text)} → {len(final)} chars) in {time.time()-start:.3f}s"
            )
            ctx.record("readFile", chars=len(final), item=str(resolved))
            return final
        except Exception as exc:
            logger.warning(f"[TOOL] readFile - aggressive pruning failed for {resolved}: {exc}")

    # Normal-sized file: add line numbers and prune.
    try:
        numbered = CodeContextPruner.add_line_numbers(text, 1)
        processed = CodeContextPruner.prune_code(numbered)
        logger.info(
            f"[TOOL] readFile - read {resolved} "
            f"({len(text)} → {len(processed)} chars after processing) in {time.time()-start:.3f}s"
        )
        ctx.record("readFile", chars=len(processed), item=str(resolved))
        return processed
    except Exception as exc:
        logger.warning(f"[TOOL] readFile - CodeContextPruner failed for {resolved}: {exc}")
        ctx.record("readFile", chars=len(text), item=str(resolved))
        return text


# ----------------------------------------------------------------------
# getFileContentByLines
# ----------------------------------------------------------------------


async def get_file_content_by_lines_tool(args: Dict[str, Any], ctx: ToolContext) -> str:
    """Read a 1-based inclusive line range from a file."""
    return await asyncio.to_thread(_get_lines_sync, args, ctx)


def _get_lines_sync(args: Dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "")
    start_line = args.get("startLine")
    end_line = args.get("endLine")
    reason = args.get("reason")
    start = time.time()

    logger.info(
        f"[TOOL] getFileContentByLines - path={path!r}, lines={start_line}-{end_line} | "
        f"[AI REASONING] {reason or 'No reason provided'}"
    )

    if not path or not isinstance(path, str):
        ctx.record("getFileContentByLines")
        return "Error: path parameter is required and must be a string"
    if not isinstance(start_line, int) or start_line < 1:
        ctx.record("getFileContentByLines")
        return "Error: startLine must be a positive integer (1-based)"
    if not isinstance(end_line, int) or end_line < 1:
        ctx.record("getFileContentByLines")
        return "Error: endLine must be a positive integer (1-based)"
    if start_line > end_line:
        ctx.record("getFileContentByLines")
        return f"Error: startLine ({start_line}) cannot be greater than endLine ({end_line})"

    path = path.strip()
    resolved, original = ctx.resolve_file_path(path)
    if not resolved:
        ctx.record("getFileContentByLines")
        return f"File '{original}' cannot be found"

    try:
        text = resolved.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        ctx.record("getFileContentByLines")
        return f"Error: Failed to read file '{resolved}': {exc}"

    lines = text.split("\n")
    total_lines = len(lines)

    # End-of-file signal — same JSON shape the legacy code produced.
    if start_line > total_lines:
        end_of_file = {
            "end_of_file": True,
            "error": f"startLine ({start_line}) exceeds file length",
            "file": path,
            "total_lines": total_lines,
            "valid_range": f"1-{total_lines}",
            "message": f"File has only {total_lines} lines. There is no more content to read.",
            "suggestion": (
                f"The entire file content is available in lines 1-{total_lines}. "
                f"Do not request lines beyond {total_lines}."
            ),
        }
        out = json.dumps(end_of_file, indent=2)
        logger.warning(
            f"[TOOL] getFileContentByLines - startLine ({start_line}) > total ({total_lines}); "
            "returning end_of_file signal"
        )
        ctx.record("getFileContentByLines", chars=len(out), item=path)
        return out

    if end_line > total_lines:
        logger.warning(
            f"[TOOL] getFileContentByLines - endLine ({end_line}) > total ({total_lines}); clamping"
        )
        end_line = total_lines

    extracted = lines[start_line - 1:end_line]
    numbered = "\n".join(f"{start_line + i:4d} | {line}" for i, line in enumerate(extracted))
    header = (
        f"File: {path} (lines {start_line}-{end_line} of {total_lines} total)\n"
        + "=" * 50
        + "\n"
    )
    result = header + numbered

    logger.info(
        f"[TOOL] getFileContentByLines completed - path={path}, "
        f"lines={start_line}-{end_line}, chars={len(result)}, time={time.time()-start:.2f}s"
    )
    ctx.record("getFileContentByLines", chars=len(result), item=path)
    return result


# ----------------------------------------------------------------------
# checkFileSize
# ----------------------------------------------------------------------


async def check_file_size_tool(args: Dict[str, Any], ctx: ToolContext) -> str:
    """Return a JSON dict with file size + line count for the model to consume."""
    return await asyncio.to_thread(_check_file_size_sync, args, ctx)


def _check_file_size_sync(args: Dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "")
    reason = args.get("reason")
    start = time.time()

    logger.info(
        f"[TOOL] checkFileSize - path={path!r} | [AI REASONING] {reason or 'No reason provided'}"
    )

    if not isinstance(path, str):
        ctx.record("checkFileSize")
        return json.dumps(
            {"file_available": False, "error": f"Invalid path parameter. Expected string, got {type(path)}: {path}"},
            indent=2,
        )

    path = path.strip()
    if not path:
        ctx.record("checkFileSize")
        return json.dumps(
            {"file_available": False, "error": "Path parameter is required and cannot be empty"},
            indent=2,
        )

    resolved, _ = ctx.resolve_file_path(path)

    # File not directly resolvable — try `file_content_provider.guess_path`,
    # then a repo-wide walk by basename. Mirrors the legacy logic exactly.
    if not resolved:
        filename = os.path.basename(path)
        if ctx.file_content_provider and hasattr(ctx.file_content_provider, "guess_path"):
            dir_part = os.path.dirname(path) or ""
            guessed = ctx.file_content_provider.guess_path(filename, dir_part)
            if guessed:
                full = Path(ctx.repo_path) / guessed
                if full.exists():
                    resolved = full
                    logger.info(f"[TOOL] checkFileSize - resolved via guess_path: {guessed}")

        if not resolved:
            matching = _find_files_by_name(ctx.repo_path, filename)
            if len(matching) == 1:
                resolved = Path(ctx.repo_path) / matching[0]
            elif len(matching) > 1:
                payload = {
                    "file_available": False,
                    "error": (
                        f"Multiple files found with name '{filename}': {matching}. "
                        "Please specify the full path."
                    ),
                }
                ctx.record("checkFileSize", item=path)
                return json.dumps(payload, indent=2)

    if not resolved or not resolved.exists():
        result = {"file_available": False, "error": f"File '{path}' not found in repository"}
    else:
        result = _summarize_file(resolved, ctx)

    out = json.dumps(result, indent=2)
    ctx.record("checkFileSize", chars=len(out), item=path)
    logger.info(
        f"[TOOL] checkFileSize completed - path={path}, "
        f"available={result.get('file_available', False)}, time={time.time()-start:.2f}s"
    )
    return out


def _summarize_file(resolved: Path, ctx: ToolContext) -> Dict[str, Any]:
    """Build the file_available=True payload from a resolved path."""
    try:
        size_bytes = resolved.stat().st_size
        try:
            with open(resolved, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            char_count = len(content)
            line_count: Optional[int] = len(content.splitlines())
        except (OSError, UnicodeDecodeError):
            char_count = size_bytes
            line_count = None

        within_char = char_count <= MAX_FILE_CHARACTERS_FOR_READ_FILE
        within_byte = size_bytes <= MAX_FILE_SIZE_BYTES
        within_limit = within_char and within_byte

        try:
            display = str(resolved.relative_to(Path(ctx.repo_path)))
        except ValueError:
            display = str(resolved)

        payload: Dict[str, Any] = {
            "file_available": True,
            "file_path": display,
            "size_bytes": size_bytes,
            "size_characters": char_count,
            "line_count": line_count,
            "within_size_limit": within_limit,
            "recommended_for_readFile": within_limit,
            "size_limits": {
                "max_characters": MAX_FILE_CHARACTERS_FOR_READ_FILE,
                "max_bytes": MAX_FILE_SIZE_BYTES,
            },
        }
        if not within_limit:
            if not within_char:
                payload["warning"] = (
                    f"File exceeds character limit ({char_count:,} > "
                    f"{MAX_FILE_CHARACTERS_FOR_READ_FILE:,} characters). "
                    "Consider using getSummaryOfFile or getFileContentByLines instead."
                )
            else:
                payload["warning"] = (
                    f"File exceeds byte limit ({size_bytes:,} > {MAX_FILE_SIZE_BYTES:,} bytes). "
                    "Consider using getSummaryOfFile or getFileContentByLines instead."
                )
        return payload
    except Exception as exc:
        return {"file_available": True, "error": f"File found but could not read size information: {exc}"}


def _find_files_by_name(repo_path: str, filename: str) -> list[str]:
    """Walk `repo_path` and return every relative path whose basename matches."""
    matches: list[str] = []
    try:
        for root, _, files in os.walk(repo_path):
            if filename in files:
                matches.append(os.path.relpath(os.path.join(root, filename), repo_path))
    except Exception as exc:
        logger.debug(f"_find_files_by_name walk failed: {exc}")
    return matches
