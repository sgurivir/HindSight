#!/usr/bin/env python3
# Created by Sridhar Gurivireddy on 11/14/2025
# file_filter_util.py
# Centralized file filtering utilities for ignore directory patterns

from pathlib import Path
from typing import Set


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
        
        # Check for exact relative path matches first
        for ignore_pattern in ignored_dirs:
            if '/' in ignore_pattern or '\\' in ignore_pattern:
                # This is a path pattern - normalize separators and check if the file path starts with it
                ignore_path = ignore_pattern.replace('\\', '/')
                normalized_rel = rel_str.replace('\\', '/')
                
                # Check if file is exactly the ignored path or is within the ignored directory
                if normalized_rel == ignore_path or normalized_rel.startswith(ignore_path + '/'):
                    return True
            else:
                # This is a simple directory name - check if it appears anywhere in the path
                normalized_rel = rel_str.replace('\\', '/')
                path_parts = normalized_rel.split('/')
                
                # Check if the ignore pattern matches any directory in the path
                if ignore_pattern in path_parts:
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