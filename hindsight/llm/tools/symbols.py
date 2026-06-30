"""Symbol lookup tool: getImplementation.

Given a class or function name, look it up in the AST artifact registries
(`merged_defined_classes.json`, `merged_functions.json`, etc.) and return the
implementation source. If the registry has line ranges, return numbered
slices; otherwise return the whole file with `CodeContextPruner` applied.

Falls back to a repo-wide filename + content search when the registries are
absent or do not know the name. Matches the legacy `ImplementationToolsMixin`
behavior verbatim.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from hindsight.core.lang_util.code_context_pruner import CodeContextPruner
from hindsight.utils.file_util import read_file_with_line_numbers
from hindsight.utils.log_util import get_logger

from .registry import MAX_FILE_SIZE_BYTES, ToolContext

logger = get_logger(__name__)


async def get_implementation_tool(args: Dict[str, Any], ctx: ToolContext) -> str:
    return await asyncio.to_thread(_get_implementation_sync, args, ctx)


def _get_implementation_sync(args: Dict[str, Any], ctx: ToolContext) -> str:
    name = args.get("name", "")
    reason = args.get("reason")
    start = time.time()

    logger.info(
        f"[TOOL] getImplementation - name={name!r} | "
        f"[AI REASONING] {reason or 'No reason provided'}"
    )

    try:
        class_files = _find_class_files(name, ctx)
        function_files: List[Dict[str, Any]] = []
        if not class_files:
            function_files = _find_function_files(name, ctx)

        if class_files:
            files = class_files
            kind = "Class"
        elif function_files:
            files = function_files
            kind = "Function"
        else:
            # Last-resort: walk the repo looking for files named like the symbol
            # or files containing the literal name.
            potential = _search_repo_for_symbol(name, ctx.repo_path)
            if not potential:
                msg = (
                    f"Error: '{name}' not found in class or function registry, "
                    "and no potential implementation files found."
                )
                logger.warning(f"[TOOL] getImplementation - {msg}")
                ctx.record("getImplementation", item=name)
                return msg
            files = [{"file_name": p} for p in potential[:3]]
            kind = "Potential Implementation"

        # Group blocks by file so we read each file once.
        by_file: Dict[str, List[Dict[str, int]]] = {}
        for entry in files:
            if isinstance(entry, dict):
                fname = entry.get("file_name", "")
                if not fname:
                    continue
                by_file.setdefault(fname, [])
                s = entry.get("start", 0)
                e = entry.get("end", 0)
                if s > 0 and e > 0:
                    by_file[fname].append({"start": s, "end": e})
            elif isinstance(entry, str):
                by_file.setdefault(entry, [])

        parts: List[str] = [f"{kind} Implementation: {name}"]
        files_read: List[str] = []
        for file_path, blocks in by_file.items():
            file_path_resolved = _resolve_for_implementation(file_path, ctx)
            if not file_path_resolved:
                parts.append(f"\n--- File: {file_path} (NOT FOUND) ---")
                parts.append("Error: File could not be located in the repository")
                continue

            full = os.path.join(ctx.repo_path, file_path_resolved)
            if not os.path.exists(full):
                parts.append(f"\n--- File: {file_path_resolved} (NOT FOUND) ---")
                parts.append("Error: File does not exist at the specified path")
                continue

            if blocks:
                parts.append(_extract_code_blocks(full, file_path_resolved, blocks))
                files_read.append(file_path_resolved)
            else:
                content = _read_full_with_pruning(full, file_path_resolved, ctx)
                if content:
                    parts.append(content)
                    files_read.append(file_path_resolved)

        final = "\n".join(parts)
        ctx.record("getImplementation", chars=len(final), item=name)
        logger.info(
            f"[TOOL] getImplementation completed - {kind}: {name}, files={len(files_read)}, "
            f"chars={len(final)}, time={time.time()-start:.2f}s"
        )
        return final
    except Exception as exc:
        logger.error(f"[TOOL] getImplementation failed - {name}: {exc}")
        ctx.record("getImplementation", item=name)
        return f"Error retrieving implementation for '{name}': {exc}"


# ----------------------------------------------------------------------
# Registry lookups
# ----------------------------------------------------------------------


def _find_class_files(name: str, ctx: ToolContext) -> List[Dict[str, Any]]:
    """Look up a class name in the merged_defined_classes registry.

    Searches several known artifact-dir filenames. Returns a list of
    `{file_name, start?, end?}` dicts; empty if nothing matches.
    """
    registry = _load_class_registry(ctx)
    if not registry:
        return []

    # Exact match wins; otherwise case-insensitive substring.
    matches: List[Dict[str, Any]] = []
    name_lower = name.lower()
    for entry in registry:
        if entry.get("data_type_name") == name:
            return [
                _file_to_dict(f) for f in entry.get("files", []) if _file_to_dict(f)
            ]
    for entry in registry:
        if name_lower in entry.get("data_type_name", "").lower():
            matches.extend(_file_to_dict(f) for f in entry.get("files", []) if _file_to_dict(f))
    return matches


def _file_to_dict(file_entry: Any) -> Optional[Dict[str, Any]]:
    """Normalize a class-registry file entry to a dict."""
    if isinstance(file_entry, dict):
        return file_entry
    if isinstance(file_entry, str):
        return {"file_name": file_entry}
    return None


def _load_class_registry(ctx: ToolContext) -> List[Dict[str, Any]]:
    """Load + cache class registry from whichever known file is present."""
    cache: List[Dict[str, Any]] | None = getattr(ctx, "_class_registry_cache", None)
    if cache is not None:
        return cache

    artifacts = ctx.get_artifacts_dir()
    candidates = [
        os.path.join(artifacts, "merged_defined_data_types.json"),
        os.path.join(artifacts, "merged_defined_classes.json"),
        os.path.join(artifacts, "merged_functions.json"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            logger.warning(f"[TOOL] getImplementation - failed to load {path}: {exc}")
            continue

        parsed = _parse_class_registry_payload(data)
        if parsed is None:
            logger.warning(f"[TOOL] getImplementation - unexpected format in {path}")
            continue

        setattr(ctx, "_class_registry_cache", parsed)
        logger.info(
            f"[TOOL] getImplementation - loaded class registry from {path} ({len(parsed)} entries)"
        )
        return parsed

    setattr(ctx, "_class_registry_cache", [])
    logger.warning("[TOOL] getImplementation - no class registry found; functionality limited")
    return []


def _parse_class_registry_payload(data: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalize the various class-registry on-disk shapes to a list of dicts."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    if "data_type_to_location_and_checksum" in data:
        out: List[Dict[str, Any]] = []
        for class_name, class_info in data["data_type_to_location_and_checksum"].items():
            files: List[str] = []
            code = class_info.get("code") if isinstance(class_info, dict) else None
            if isinstance(code, list):
                for block in code:
                    if isinstance(block, dict) and "file_name" in block:
                        files.append(block["file_name"])
            elif isinstance(code, dict) and "file_name" in code:
                files.append(code["file_name"])
            out.append({"data_type_name": class_name, "files": files})
        return out
    if "data_type_to_location" in data:
        return data["data_type_to_location"]
    if "classes" in data:
        return data["classes"]
    return None


def _find_function_files(name: str, ctx: ToolContext) -> List[Dict[str, Any]]:
    """Look up a function name in the merged-functions registry.

    Checks several known artifact filenames in order. Returns location dicts
    `{file_name, start, end}`. Matches by: exact, suffix (incl. `Class::name`),
    then case-insensitive substring.
    """
    artifacts = ctx.get_artifacts_dir()
    candidates = [
        os.path.join(artifacts, "merged_functions.json"),
        os.path.join(artifacts, "clang_defined_functions.json"),
        os.path.join(artifacts, "swift_defined_functions.json"),
        os.path.join(artifacts, "java_defined_functions.json"),
        os.path.join(artifacts, "kotlin_defined_functions.json"),
    ]

    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            logger.debug(f"[TOOL] function registry load failed {path}: {exc}")
            continue

        functions = _parse_function_registry_payload(data)
        if not functions:
            continue

        # Exact match.
        for entry in functions:
            if entry.get("name") == name:
                ctx_block = entry.get("context", {})
                if ctx_block.get("file"):
                    return [{
                        "file_name": ctx_block["file"],
                        "start": ctx_block.get("start", 0),
                        "end": ctx_block.get("end", 0),
                    }]

        # Suffix match (covers `Class::name`).
        suffix_matches: List[Dict[str, Any]] = []
        seen: set[Tuple[str, int, int]] = set()
        for entry in functions:
            entry_name = entry.get("name", "")
            if entry_name.endswith(name) or entry_name.endswith("::" + name):
                ctx_block = entry.get("context", {})
                fp = ctx_block.get("file", "")
                if fp:
                    key = (fp, ctx_block.get("start", 0), ctx_block.get("end", 0))
                    if key not in seen:
                        seen.add(key)
                        suffix_matches.append({
                            "file_name": fp,
                            "start": ctx_block.get("start", 0),
                            "end": ctx_block.get("end", 0),
                        })
        if suffix_matches:
            return suffix_matches

        # Substring (case-insensitive) match.
        name_lower = name.lower()
        substring_matches: List[Dict[str, Any]] = []
        for entry in functions:
            entry_name = entry.get("name", "")
            if name_lower in entry_name.lower():
                ctx_block = entry.get("context", {})
                fp = ctx_block.get("file", "")
                if fp:
                    key = (fp, ctx_block.get("start", 0), ctx_block.get("end", 0))
                    if key not in seen:
                        seen.add(key)
                        substring_matches.append({
                            "file_name": fp,
                            "start": ctx_block.get("start", 0),
                            "end": ctx_block.get("end", 0),
                        })
        if substring_matches:
            return substring_matches

    return []


def _parse_function_registry_payload(data: Any) -> List[Dict[str, Any]]:
    """Normalize the various function-registry on-disk shapes."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    out: List[Dict[str, Any]] = []
    if "function_to_location" in data:
        for func_name, definitions in data["function_to_location"].items():
            if not isinstance(definitions, list):
                continue
            for d in definitions:
                if isinstance(d, dict) and "file_name" in d:
                    out.append({
                        "name": func_name,
                        "context": {
                            "file": d.get("file_name", ""),
                            "start": d.get("start", 0),
                            "end": d.get("end", 0),
                        },
                    })
        return out

    # Dict keyed directly by function names.
    for func_name, func_info in data.items():
        if not isinstance(func_info, dict):
            continue
        code = func_info.get("code")
        if isinstance(code, list):
            for block in code:
                if isinstance(block, dict) and "file_name" in block:
                    out.append({
                        "name": func_name,
                        "context": {
                            "file": block["file_name"],
                            "start": block.get("start", 0),
                            "end": block.get("end", 0),
                        },
                    })
        elif isinstance(code, dict) and "file_name" in code:
            out.append({
                "name": func_name,
                "context": {
                    "file": code["file_name"],
                    "start": code.get("start", 0),
                    "end": code.get("end", 0),
                },
            })
    return out


# ----------------------------------------------------------------------
# File reading / extraction
# ----------------------------------------------------------------------


def _resolve_for_implementation(file_path: str, ctx: ToolContext) -> Optional[str]:
    """Resolve a file path the way getImplementation needs it: relative to repo_path.

    Tries the path verbatim, then `file_content_provider.resolve_file_path`,
    then `file_content_provider.guess_path`, then a basename walk. Returns the
    repo-relative path (string) or None.
    """
    full = os.path.join(ctx.repo_path, file_path)
    if os.path.exists(full):
        return file_path

    filename = os.path.basename(file_path)
    if ctx.file_content_provider is not None:
        if hasattr(ctx.file_content_provider, "resolve_file_path"):
            resolved = ctx.file_content_provider.resolve_file_path(filename, file_path)
            if resolved:
                return str(resolved)
        if hasattr(ctx.file_content_provider, "guess_path"):
            dir_part = os.path.dirname(file_path) or ""
            guessed = ctx.file_content_provider.guess_path(filename, dir_part)
            if guessed:
                return guessed

    matches: List[str] = []
    for root, _, files in os.walk(ctx.repo_path):
        if filename in files:
            matches.append(os.path.relpath(os.path.join(root, filename), ctx.repo_path))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Prefer a match that shares the original directory.
        original_dir = os.path.dirname(file_path)
        for m in matches:
            if original_dir and original_dir in m:
                return m
        return matches[0]
    return None


def _extract_code_blocks(full_path: str, file_path: str, blocks: List[Dict[str, int]]) -> str:
    """Pull each line-range block out of the file with 1-based numbering.

    For total > 500 lines we don't dump the body — just print the ranges so we
    don't blow out the prompt budget.
    """
    total = sum(b["end"] - b["start"] + 1 for b in blocks)
    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
            all_lines = fh.readlines()
    except Exception as exc:
        logger.error(f"[TOOL] getImplementation - read failed {file_path}: {exc}")
        return f"\n--- File: {file_path} (READ ERROR) ---\nError: Failed to extract code blocks - {exc}"

    parts: List[str] = []
    if total < 500:
        parts.append(f"\n=== Path: {file_path} ===")
        for block in blocks:
            s, e = block["start"], block["end"]
            parts.append(f"=== Start_line: {s} ===")
            parts.append("")
            if s > 0 and e <= len(all_lines):
                slice_ = all_lines[s - 1:e]
                numbered = "\n".join(f"{s + i:4d} | {line.rstrip()}" for i, line in enumerate(slice_))
                parts.append(numbered)
                parts.append("")
            else:
                parts.append(f"Error: Invalid line range {s}-{e} for file with {len(all_lines)} lines")
                parts.append("")
    else:
        parts.append(f"\n=== File: {file_path} (Large - showing line ranges only) ===")
        for block in blocks:
            parts.append(f"Path: {file_path}")
            parts.append(f"Start_line: {block['start']}")
            parts.append(f"End_line: {block['end']}")
            parts.append("---")

    return "\n".join(parts)


def _read_full_with_pruning(full_path: str, file_path: str, ctx: ToolContext) -> Optional[str]:
    """Read the entire file and prune; bail with a helpful error if too large.

    Used when the registry doesn't have line ranges — we need the whole file
    so the model can navigate by signatures.
    """
    try:
        size = os.path.getsize(full_path)
    except OSError as exc:
        return f"\n--- File: {file_path} (STAT ERROR) ---\nError: {exc}"
    if size > MAX_FILE_SIZE_BYTES:
        return (
            f"\n--- File: {file_path} (TOO LARGE) ---\n"
            f"Error: File '{file_path}' is too large ({size} bytes). "
            f"Maximum size is {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB.\n"
            "Suggestion: Use getFileContentByLines tool to read specific sections"
        )

    content = read_file_with_line_numbers(file_path, ctx.repo_path, 1)
    if content and content.strip():
        try:
            return f"\n--- File: {file_path} ---\n{CodeContextPruner.prune_code(content)}"
        except Exception as exc:
            logger.warning(f"[TOOL] getImplementation - prune failed for {file_path}: {exc}")
            return f"\n--- File: {file_path} ---\n{content}"

    # Fallback path: read raw and prune.
    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
            raw = fh.read()
    except Exception as exc:
        return f"\n--- File: {file_path} (READ ERROR) ---\nError: Failed to read file content - {exc}"
    if not raw.strip():
        return f"\n--- File: {file_path} (EMPTY FILE) ---\nWarning: File exists but appears to be empty"
    try:
        numbered = CodeContextPruner.add_line_numbers(raw, 1)
        pruned = CodeContextPruner.prune_code(numbered)
        return f"\n--- File: {file_path} ---\n{pruned}"
    except Exception as exc:
        logger.debug(f"[TOOL] getImplementation - prune fallback failed for {file_path}: {exc}")
        numbered = "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(raw.split("\n")))
        return f"\n--- File: {file_path} ---\n{numbered}"


def _search_repo_for_symbol(name: str, repo_path: str) -> List[str]:
    """Walk the repo looking for files named like the symbol or containing it.

    Returns a list of repo-relative paths. Cheap: only opens files with a
    supported source extension.
    """
    search_exts = tuple(ALL_SUPPORTED_EXTENSIONS + [".proto"])
    patterns = [f"{name}{ext}" for ext in search_exts]
    found: List[str] = []

    for root, _, files in os.walk(repo_path):
        for fname in files:
            if any(fname.lower() == p.lower() for p in patterns):
                found.append(os.path.relpath(os.path.join(root, fname), repo_path))
                continue
            if fname.endswith(search_exts):
                try:
                    full = os.path.join(root, fname)
                    with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                        if name in fh.read():
                            rel = os.path.relpath(full, repo_path)
                            if rel not in found:
                                found.append(rel)
                except (OSError, UnicodeDecodeError):
                    continue
    return found
