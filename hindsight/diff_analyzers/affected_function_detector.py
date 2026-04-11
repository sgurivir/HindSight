#!/usr/bin/env python3
"""
Affected Function Detector

Detects functions affected by git changes using AST and call graph analysis.
This module identifies:
1. Directly modified functions - functions whose bodies contain changed lines
2. Transitively affected functions - functions that call or are called by modified functions
"""

import re
from pathlib import Path
from typing import Dict, List, Any, Set, Optional, Tuple

from ..utils.log_util import get_logger

logger = get_logger(__name__)


class AffectedFunctionDetector:
    """
    Detects functions affected by git changes using AST and call graph.
    
    This class analyzes:
    - Which functions have their bodies modified by the diff
    - Which functions call modified functions (callers)
    - Which functions are called by modified functions (callees)
    """

    def __init__(self,
                 call_graph: Dict[str, Any],
                 functions: Dict[str, Any],
                 changed_lines_per_file: Dict[str, Dict[str, List[int]]],
                 repo_path: str = ""):
        """
        Initialize the AffectedFunctionDetector.
        
        Args:
            call_graph: Merged call graph with functions_invoked and invoked_by.
                       Expected format: {'call_graph': [{'file': str, 'functions': [...]}]}
            functions: Function to location mapping.
                      Expected format: {'function_to_location': {func_name: [{'file_name': str, 'start': int, 'end': int}]}}
            changed_lines_per_file: Output from _extract_changed_lines_per_file().
                                   Format: {file_path: {'added': [line_nums], 'removed': [line_nums], 'modified_ranges': [(start, end)]}}
            repo_path: Repository root path for resolving relative paths
        """
        self.call_graph = call_graph
        self.functions = functions
        self.changed_lines = changed_lines_per_file
        self.repo_path = repo_path
        
        # Build lookup structures for efficient access
        self._function_locations = self._build_function_locations()
        self._call_graph_by_function = self._build_call_graph_lookup()
        
        logger.info(f"Initialized AffectedFunctionDetector with {len(self._function_locations)} functions, "
                   f"{len(self.changed_lines)} changed files")

    def _build_function_locations(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Build a lookup dictionary for function locations.
        
        Expects the new checksum format from AST processing:
        {
            "functionName": {
                "checksum": "abc123...",
                "code": [{"file_name": "...", "start": 10, "end": 50}]
            }
        }
        
        Returns:
            Dict mapping function_name -> list of location dicts
        """
        locations = {}
        
        if not self.functions:
            return locations
        
        # New checksum format: {"funcName": {"checksum": "...", "code": [...]}}
        for func_name, func_data in self.functions.items():
            if isinstance(func_data, dict) and 'code' in func_data:
                # New format with checksum - extract locations from 'code' key
                loc_data = func_data['code']
            else:
                # Direct location data (fallback, shouldn't happen with new format)
                loc_data = func_data
                
            if isinstance(loc_data, list):
                locations[func_name] = loc_data
            else:
                locations[func_name] = [loc_data]
                
        return locations

    def _build_call_graph_lookup(self) -> Dict[str, Dict[str, Any]]:
        """
        Build a lookup dictionary for call graph entries by function name.
        
        Returns:
            Dict mapping function_name -> call graph entry with functions_invoked, invoked_by, etc.
        """
        lookup = {}
        
        if not self.call_graph:
            return lookup
            
        call_graph_list = self.call_graph.get('call_graph', [])
        
        for file_entry in call_graph_list:
            file_name = file_entry.get('file', '')
            functions = file_entry.get('functions', [])
            
            for func_entry in functions:
                func_name = func_entry.get('function', '')
                if func_name:
                    # Store with file context
                    lookup[func_name] = {
                        'file': file_name,
                        'functions_invoked': func_entry.get('functions_invoked', []),
                        'invoked_by': func_entry.get('invoked_by', []),
                        'data_types_used': func_entry.get('data_types_used', []),
                        'constants_used': func_entry.get('constants_used', {}),
                        'context': func_entry.get('context', {})
                    }
                    
        return lookup

    def _normalize_file_path(self, file_path: str) -> str:
        """
        Normalize a file path for comparison.
        
        Args:
            file_path: File path to normalize
            
        Returns:
            Normalized file path (relative, forward slashes)
        """
        if not file_path:
            return ""
            
        # Convert to Path for normalization
        path = Path(file_path)
        
        # If it's absolute and we have repo_path, make it relative
        if path.is_absolute() and self.repo_path:
            try:
                path = path.relative_to(self.repo_path)
            except ValueError:
                pass
                
        # Return with forward slashes
        return str(path).replace('\\', '/')

    def _file_paths_match(self, path1: str, path2: str) -> bool:
        """
        Check if two file paths refer to the same file.
        
        Args:
            path1: First file path
            path2: Second file path
            
        Returns:
            True if paths match
        """
        norm1 = self._normalize_file_path(path1)
        norm2 = self._normalize_file_path(path2)
        
        # Direct match
        if norm1 == norm2:
            return True
            
        # Check if one ends with the other (handles relative vs absolute)
        if norm1.endswith(norm2) or norm2.endswith(norm1):
            return True
            
        # Check just the filename as last resort
        if Path(norm1).name == Path(norm2).name:
            # Only match by filename if the paths are similar enough
            return norm1.endswith(Path(norm2).name) or norm2.endswith(Path(norm1).name)
            
        return False

    def is_function_modified(self, function_name: str, file_path: str = None) -> bool:
        """
        Check if a function body overlaps with changed lines.
        
        Args:
            function_name: Name of the function to check
            file_path: Optional file path to narrow down the search
            
        Returns:
            True if the function contains changed lines
        """
        locations = self._function_locations.get(function_name, [])
        
        for location in locations:
            loc_file = location.get('file_name', '')
            
            # If file_path specified, check it matches
            if file_path and not self._file_paths_match(loc_file, file_path):
                continue
                
            start_line = location.get('start', 0)
            end_line = location.get('end', 0)
            
            # Check if any changed file matches this location
            for changed_file, changes in self.changed_lines.items():
                if not self._file_paths_match(changed_file, loc_file):
                    continue
                    
                # Check if any changed lines fall within function bounds
                added_lines = changes.get('added', [])
                removed_lines = changes.get('removed', [])
                
                for line_num in added_lines + removed_lines:
                    if start_line <= line_num <= end_line:
                        return True
                        
        return False

    def get_directly_modified_functions(self) -> List[Dict[str, Any]]:
        """
        Return list of functions whose bodies contain changed lines.
        
        Returns:
            List of dicts with:
                - function: str - function name
                - file: str - file path
                - start: int - start line
                - end: int - end line
                - changed_lines: List[int] - lines within function that changed
        """
        modified_functions = []
        
        for func_name, locations in self._function_locations.items():
            for location in locations:
                loc_file = location.get('file_name', '')
                start_line = location.get('start', 0)
                end_line = location.get('end', 0)
                
                # Find changed lines within this function
                changed_in_function = []
                
                for changed_file, changes in self.changed_lines.items():
                    if not self._file_paths_match(changed_file, loc_file):
                        continue
                        
                    added_lines = changes.get('added', [])
                    removed_lines = changes.get('removed', [])
                    
                    for line_num in added_lines:
                        if start_line <= line_num <= end_line:
                            changed_in_function.append(line_num)
                            
                    # Note: removed lines are from the old file, so we track them separately
                    # but they still indicate the function was modified
                    for line_num in removed_lines:
                        if start_line <= line_num <= end_line:
                            # Mark as modified but don't add to changed_lines
                            # since these lines don't exist in the new file
                            if not changed_in_function:
                                changed_in_function.append(-1)  # Marker for "has removals"
                
                if changed_in_function:
                    # Filter out the marker
                    actual_changed = [l for l in changed_in_function if l > 0]
                    
                    modified_functions.append({
                        'function': func_name,
                        'file': loc_file,
                        'start': start_line,
                        'end': end_line,
                        'changed_lines': sorted(set(actual_changed)),
                        'has_removals': -1 in changed_in_function
                    })
                    
        logger.info(f"Found {len(modified_functions)} directly modified functions")
        return modified_functions

    def get_affected_functions(self,
                              include_callers: bool = True,
                              include_callees: bool = True,
                              max_depth: int = 1) -> List[Dict[str, Any]]:
        """
        Return all affected functions (modified + transitively affected).
        
        Args:
            include_callers: Include functions that call modified functions
            include_callees: Include functions called by modified functions
            max_depth: How many levels of transitive relationships to include
            
        Returns:
            List of dicts with:
                - function: str - function name
                - file: str - file path
                - start: int - start line
                - end: int - end line
                - affected_reason: 'modified' | 'calls_modified' | 'called_by_modified'
                - related_functions: List[str] - which modified functions relate to this
                - changed_lines: List[int] - lines that changed (for modified functions)
        """
        affected = {}
        
        # Step 1: Find directly modified functions
        modified = self.get_directly_modified_functions()
        for func in modified:
            func_key = f"{func['function']}:{func['file']}"
            affected[func_key] = {
                **func,
                'affected_reason': 'modified',
                'related_functions': []
            }
        
        # Step 2: Find transitively affected (BFS up to max_depth)
        current_level = set(f"{func['function']}:{func['file']}" for func in modified)
        current_level_names = set(func['function'] for func in modified)
        
        for depth in range(max_depth):
            next_level = set()
            next_level_names = set()
            
            for func_key in current_level:
                func_name = func_key.split(':')[0]
                func_entry = self._call_graph_by_function.get(func_name, {})
                
                if not func_entry:
                    continue
                
                # Add callers (functions that invoke this one)
                if include_callers:
                    for caller in func_entry.get('invoked_by', []):
                        caller_key = self._get_function_key(caller)
                        if caller_key and caller_key not in affected:
                            caller_info = self._get_function_info(caller)
                            if caller_info:
                                affected[caller_key] = {
                                    **caller_info,
                                    'affected_reason': 'calls_modified',
                                    'related_functions': [func_name],
                                    'changed_lines': []
                                }
                                next_level.add(caller_key)
                                next_level_names.add(caller)
                        elif caller_key in affected:
                            # Add to related functions
                            if func_name not in affected[caller_key]['related_functions']:
                                affected[caller_key]['related_functions'].append(func_name)
                
                # Add callees (functions this one invokes)
                if include_callees:
                    for callee in func_entry.get('functions_invoked', []):
                        callee_key = self._get_function_key(callee)
                        if callee_key and callee_key not in affected:
                            callee_info = self._get_function_info(callee)
                            if callee_info:
                                affected[callee_key] = {
                                    **callee_info,
                                    'affected_reason': 'called_by_modified',
                                    'related_functions': [func_name],
                                    'changed_lines': []
                                }
                                next_level.add(callee_key)
                                next_level_names.add(callee)
                        elif callee_key in affected:
                            # Add to related functions
                            if func_name not in affected[callee_key]['related_functions']:
                                affected[callee_key]['related_functions'].append(func_name)
            
            current_level = next_level
            current_level_names = next_level_names
        
        result = list(affected.values())
        logger.info(f"Found {len(result)} total affected functions "
                   f"({len(modified)} modified, {len(result) - len(modified)} transitively affected)")
        return result

    def _get_function_key(self, func_name: str) -> Optional[str]:
        """
        Get a unique key for a function (name:file).
        
        Args:
            func_name: Function name
            
        Returns:
            Key string or None if function not found
        """
        locations = self._function_locations.get(func_name, [])
        if locations:
            file_name = locations[0].get('file_name', '')
            return f"{func_name}:{file_name}"
        
        # Try call graph
        entry = self._call_graph_by_function.get(func_name, {})
        if entry:
            return f"{func_name}:{entry.get('file', '')}"
            
        return None

    def _get_function_info(self, func_name: str) -> Optional[Dict[str, Any]]:
        """
        Get function info (file, start, end) for a function.
        
        Args:
            func_name: Function name
            
        Returns:
            Dict with function, file, start, end or None
        """
        locations = self._function_locations.get(func_name, [])
        if locations:
            loc = locations[0]
            return {
                'function': func_name,
                'file': loc.get('file_name', ''),
                'start': loc.get('start', 0),
                'end': loc.get('end', 0)
            }
        
        # Try call graph for context
        entry = self._call_graph_by_function.get(func_name, {})
        if entry:
            context = entry.get('context', {})
            return {
                'function': func_name,
                'file': entry.get('file', ''),
                'start': context.get('start', 0),
                'end': context.get('end', 0)
            }
            
        return None

    def is_function_affected(self, function_name: str) -> bool:
        """
        Helper to check if a specific function is affected.
        
        Args:
            function_name: Name of the function to check
            
        Returns:
            True if the function is affected (modified or transitively)
        """
        # Check if directly modified
        if self.is_function_modified(function_name):
            return True
            
        # Check if transitively affected
        affected = self.get_affected_functions()
        return any(f['function'] == function_name for f in affected)

    def get_function_call_context(self, function_name: str) -> Dict[str, Any]:
        """
        Get the call context for a function (what it calls and what calls it).
        
        Args:
            function_name: Name of the function
            
        Returns:
            Dict with:
                - functions_invoked: List[str] - functions this function calls
                - invoked_by: List[str] - functions that call this function
                - data_types_used: List[str] - data types used by this function
                - constants_used: Dict - constants used by this function
        """
        entry = self._call_graph_by_function.get(function_name, {})
        return {
            'functions_invoked': entry.get('functions_invoked', []),
            'invoked_by': entry.get('invoked_by', []),
            'data_types_used': entry.get('data_types_used', []),
            'constants_used': entry.get('constants_used', {})
        }

    def get_all_related_functions(self, function_name: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get all functions related to a given function (callers and callees).
        
        This returns ALL related functions regardless of whether they were modified,
        which is useful for providing complete context to the LLM.
        
        Args:
            function_name: Name of the function
            
        Returns:
            Dict with:
                - invoked_functions: List of dicts with function info and is_modified flag
                - invoking_functions: List of dicts with function info and is_modified flag
        """
        entry = self._call_graph_by_function.get(function_name, {})
        
        invoked_functions = []
        for callee in entry.get('functions_invoked', []):
            callee_info = self._get_function_info(callee)
            if callee_info:
                callee_info['is_modified'] = self.is_function_modified(callee)
                invoked_functions.append(callee_info)
        
        invoking_functions = []
        for caller in entry.get('invoked_by', []):
            caller_info = self._get_function_info(caller)
            if caller_info:
                caller_info['is_modified'] = self.is_function_modified(caller)
                invoking_functions.append(caller_info)
        
        return {
            'invoked_functions': invoked_functions,
            'invoking_functions': invoking_functions
        }


def extract_changed_lines_per_file(diff_content: str) -> Dict[str, Dict[str, List[int]]]:
    """
    Parse diff to extract changed line numbers per file.
    
    This is a standalone function that can be used by GitSimpleCommitAnalyzer.
    
    Args:
        diff_content: Unified diff content
        
    Returns:
        Dict mapping file_path -> {
            'added': [line_numbers],      # Lines with + prefix (in new file)
            'removed': [line_numbers],    # Lines with - prefix (in old file)
            'modified_ranges': [(start, end), ...]  # Hunk ranges in new file
        }
    """
    result = {}
    current_file = None
    old_line = 0
    new_line = 0
    
    lines = diff_content.split('\n')
    
    for line in lines:
        # Match file header
        if line.startswith('diff --git'):
            # Extract file path from diff header
            # Format: diff --git a/path/to/file b/path/to/file
            parts = line.split()
            if len(parts) >= 4:
                current_file = parts[3][2:]  # Remove 'b/' prefix
                if current_file not in result:
                    result[current_file] = {
                        'added': [],
                        'removed': [],
                        'modified_ranges': []
                    }
            continue
            
        # Match +++ header for new file path
        if line.startswith('+++'):
            file_path = line[4:].strip()
            if file_path.startswith('b/'):
                file_path = file_path[2:]
            if file_path != '/dev/null':
                current_file = file_path
                if current_file not in result:
                    result[current_file] = {
                        'added': [],
                        'removed': [],
                        'modified_ranges': []
                    }
            continue
            
        # Match hunk header
        # Format: @@ -old_start,old_count +new_start,new_count @@
        if line.startswith('@@'):
            match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if match:
                old_line = int(match.group(1))
                new_line = int(match.group(2))
                
                # Extract the range from the hunk header for modified_ranges
                new_match = re.search(r'\+(\d+)(?:,(\d+))?', line)
                if new_match and current_file:
                    start = int(new_match.group(1))
                    count = int(new_match.group(2)) if new_match.group(2) else 1
                    end = start + count - 1
                    result[current_file]['modified_ranges'].append((start, end))
            continue
            
        # Skip if no current file
        if not current_file:
            continue
            
        # Process diff lines
        if line.startswith('+') and not line.startswith('+++'):
            # Added line in new file
            result[current_file]['added'].append(new_line)
            new_line += 1
        elif line.startswith('-') and not line.startswith('---'):
            # Removed line from old file
            result[current_file]['removed'].append(old_line)
            old_line += 1
        elif not line.startswith('\\'):  # Ignore "\ No newline at end of file"
            # Context line - advance both counters
            old_line += 1
            new_line += 1
    
    # Log summary
    total_added = sum(len(f['added']) for f in result.values())
    total_removed = sum(len(f['removed']) for f in result.values())
    logger.info(f"Extracted changed lines: {len(result)} files, {total_added} added lines, {total_removed} removed lines")
    
    return result
