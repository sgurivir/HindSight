#!/usr/bin/env python3
# Created by Sridhar Gurivireddy on 11/14/2025
# file_filter_util.py
# Centralized file filtering utilities for ignore directory patterns

from pathlib import Path
from typing import List, Set


def should_ignore_file(file_path: Path, repo_root: Path, ignored_dirs: Set[str]) -> bool:
    """
    Check if a file should be ignored based on ignore directory patterns.
    
    Supports both:
    1. Directory names (e.g., "build", "test") - matches any occurrence in the path
    2. Relative paths (e.g., "src/test", "build/generated") - matches exact path prefixes
    
    Args:
        file_path: The file path to check
        repo_root: The repository root path
        ignored_dirs: Set of directory names or relative paths to ignore
        
    Returns:
        True if the file should be ignored, False otherwise
    """
    try:
        rel = file_path.relative_to(repo_root)
        rel_str = str(rel)
        
        normalized_rel = rel_str.replace('\\', '/')
        for ignore_pattern in ignored_dirs:
            if matches_path_components(normalized_rel, ignore_pattern):
                return True
        
        return False
    except Exception:
        # If we can't determine the relative path, don't ignore
        return False


def find_files_with_extensions(repo_root: Path, ignored_dirs: Set[str], extensions: Set[str]):
    """
    Find all files in repo with specified extensions, respecting ignore patterns.
    
    Args:
        repo_root: The repository root path
        ignored_dirs: Set of directory names or relative paths to ignore
        extensions: Set of file extensions to include (e.g., {".java", ".kt"})
        
    Returns:
        Sorted list of file paths that match the criteria
    """
    collected_files = []
    
    # Resolve repo_root to handle symlinks consistently
    resolved_repo_root = repo_root.resolve()
    
    for path in resolved_repo_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in extensions:
            continue
        if should_ignore_file(path, resolved_repo_root, ignored_dirs):
            continue
            
        collected_files.append(path)
    
    collected_files.sort()
    return collected_files


def _consecutive_component_match(haystack_parts: List[str], needle_parts: List[str]) -> int:
    """
    Find where needle_parts appears as consecutive components in haystack_parts.
    Returns the starting index of the match, or -1 if not found.
    """
    needle_len = len(needle_parts)
    for i in range(len(haystack_parts) - needle_len + 1):
        if haystack_parts[i:i + needle_len] == needle_parts:
            return i
    return -1


def matches_path_components(file_path: str, pattern: str) -> bool:
    """
    Check if pattern appears as consecutive directory components in file_path.
    The filename (last component) of file_path is excluded from matching.

    Examples:
        matches_path_components("A/B/C/file.txt", "B/C") -> True
        matches_path_components("B/C/file.txt", "B/C") -> True
        matches_path_components("A/B/D/file.txt", "B/C") -> False
        matches_path_components("A/B/C/file.txt", "C") -> True
    """
    normalized_path = file_path.replace('\\', '/').lstrip('./')
    normalized_pattern = pattern.replace('\\', '/').lstrip('./')
    if not normalized_pattern:
        return False
    file_parts = normalized_path.split('/')
    if len(file_parts) <= 1:
        return False
    dir_parts = file_parts[:-1]
    pattern_parts = normalized_pattern.split('/')
    return _consecutive_component_match(dir_parts, pattern_parts) >= 0


def matches_directory_components(dir_path: str, pattern: str) -> bool:
    """
    Check if pattern appears as consecutive directory components in dir_path.
    Unlike matches_path_components, all components of dir_path are considered
    (no filename to skip).

    Examples:
        matches_directory_components("A/B/C", "B/C") -> True
        matches_directory_components("A/B/C/D", "B/C") -> True
        matches_directory_components("A/B/D", "B/C") -> False
    """
    normalized_path = dir_path.replace('\\', '/').lstrip('./')
    normalized_pattern = pattern.replace('\\', '/').lstrip('./')
    if not normalized_pattern:
        return False
    dir_parts = normalized_path.split('/')
    pattern_parts = normalized_pattern.split('/')
    return _consecutive_component_match(dir_parts, pattern_parts) >= 0