#!/usr/bin/env python3
"""
call_graph_helper.py - Common utilities for call graph analysis scripts.

This module provides helper functions and classes that are shared across
multiple call graph analysis scripts in the dev/ast_inspection directory.

It extracts common patterns from:
- call_graph_stats.py
- call_tree_generator.py
- context_generation_profiler.py
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add the project root to the path to import from hindsight
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.lang_util.call_graph_util import CallGraph, load_call_graph_from_json
from hindsight.core.lang_util.call_tree_util import extract_implementations


class CallGraphHelper:
    """Helper class for common call graph operations."""
    
    @staticmethod
    def validate_json_path(json_path: str) -> None:
        """
        Validate that JSON file exists and is readable.
        
        Args:
            json_path: Path to the JSON file
            
        Raises:
            FileNotFoundError: If the file doesn't exist
        """
        if not os.path.exists(json_path):
            raise FileNotFoundError(f"File not found: {json_path}")
    
    @staticmethod
    def load_json_file(json_path: str) -> Any:
        """
        Load and parse a JSON file.
        
        Args:
            json_path: Path to the JSON file
            
        Returns:
            Parsed JSON data
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
        """
        CallGraphHelper.validate_json_path(json_path)
        
        with open(json_path, 'r') as f:
            return json.load(f)
    
    @staticmethod
    def load_call_graph(json_path: str) -> Tuple[CallGraph, Dict[str, List[Dict[str, Any]]], Any]:
        """
        Load call graph, implementations, and raw data from JSON file.
        
        Args:
            json_path: Path to the merged_call_graph.json file
            
        Returns:
            Tuple of (CallGraph, implementations dict, raw JSON data)
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
        """
        data = CallGraphHelper.load_json_file(json_path)
        graph = load_call_graph_from_json(data)
        implementations = extract_implementations(data)
        
        return graph, implementations, data
    
    @staticmethod
    def compute_function_line_count(
        implementations: Dict[str, List[Dict[str, Any]]],
        func_name: str
    ) -> int:
        """
        Compute line count for a single function from its implementations.
        
        Handles multiple implementations by summing all.
        
        Args:
            implementations: Dictionary mapping function names to implementation locations
            func_name: Name of the function to compute line count for
            
        Returns:
            Total line count for the function (sum of all implementations)
        """
        locations = implementations.get(func_name, [])
        total_lines = 0
        
        for loc in locations:
            start = loc.get("start_line", 0)
            end = loc.get("end_line", 0)
            if start > 0 and end > 0:
                total_lines += (end - start + 1)
        
        return total_lines
    
    @staticmethod
    def ensure_output_directory(output_path: str) -> None:
        """
        Ensure the output directory exists.
        
        Args:
            output_path: Path to the output file
        """
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
    
    @staticmethod
    def write_json_output(data: Any, output_path: str, pretty: bool = False) -> None:
        """
        Write data to a JSON file.
        
        Args:
            data: Data to write
            output_path: Path to the output file
            pretty: If True, format with indentation
        """
        CallGraphHelper.ensure_output_directory(output_path)
        
        indent = 2 if pretty else None
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=indent)
    
    @staticmethod
    def write_text_output(content: str, output_path: str) -> None:
        """
        Write text content to a file.
        
        Args:
            content: Text content to write
            output_path: Path to the output file
        """
        CallGraphHelper.ensure_output_directory(output_path)
        
        with open(output_path, 'w') as f:
            f.write(content)
