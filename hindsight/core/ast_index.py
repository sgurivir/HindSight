#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
RepoAstIndex Module
Centralized AST + merged-index loading with lazy loading and caching
"""

import os
from pathlib import Path
from typing import Optional, Union, Dict, Any

from ..utils.file_util import read_json_file
from ..utils.log_util import get_logger
from ..utils.output_directory_provider import get_output_directory_provider


logger = get_logger(__name__)


class RepoAstIndex:
    """
    Centralized AST and merged-index loader with lazy loading and caching.
    
    This class provides a single point of control for loading:
    - merged_functions.json
    - merged_defined_classes.json  
    - merged_call_graph.json
    
    Benefits:
    - One place to control caching, validation, and logging
    - Easy to enforce invariants ("AST must be built before analysis")
    - Eliminates duplicate loading across components
    - Consistent error handling and logging
    """
    
    def __init__(self, ast_dir: Optional[Path] = None):
        """
        Initialize RepoAstIndex.
        
        Args:
            ast_dir: Optional path to AST directory. If None, will use OutputDirectoryProvider
        """
        self.ast_dir = ast_dir
        self._merged_functions = None
        self._merged_types = None
        self._merged_call_graph = None
        self._ast_dir_resolved = None
        
    def _get_ast_dir(self) -> str:
        """
        Get the AST directory path, resolving it if necessary.
        
        Returns:
            str: Path to the AST directory (code_insights subdirectory)
        """
        if self._ast_dir_resolved is not None:
            return self._ast_dir_resolved
            
        if self.ast_dir:
            # Use provided ast_dir
            ast_base = str(self.ast_dir)
        else:
            # Use OutputDirectoryProvider to get artifacts directory
            try:
                output_provider = get_output_directory_provider()
                ast_base = output_provider.get_repo_artifacts_dir()
            except RuntimeError:
                logger.error("OutputDirectoryProvider not configured and no ast_dir provided")
                raise RuntimeError("Cannot determine AST directory - OutputDirectoryProvider not configured")
        
        # AST files are in the code_insights subdirectory
        self._ast_dir_resolved = os.path.join(ast_base, "code_insights")
        return self._ast_dir_resolved
    
    @property
    def merged_functions(self) -> Optional[Union[Dict, list]]:
        """
        Lazy-loaded merged functions data from merged_functions.json.
        
        Returns:
            Parsed JSON data or None if file doesn't exist or parsing fails
        """
        if self._merged_functions is None:
            ast_dir = self._get_ast_dir()
            functions_file = os.path.join(ast_dir, "merged_functions.json")
            
            if os.path.exists(functions_file):
                logger.info(f"Loading merged_functions.json from: {functions_file}")
                self._merged_functions = read_json_file(functions_file)
                
                if self._merged_functions:
                    logger.info("Successfully loaded merged functions data")
                else:
                    logger.warning("Failed to load merged functions data")
            else:
                logger.debug(f"Merged functions file not found: {functions_file}")
                self._merged_functions = None
                
        return self._merged_functions
    
    @property 
    def merged_types(self) -> Optional[Union[Dict, list]]:
        """
        Lazy-loaded merged types data from merged_defined_classes.json.
        
        Returns:
            Parsed JSON data or None if file doesn't exist or parsing fails
        """
        if self._merged_types is None:
            ast_dir = self._get_ast_dir()
            types_file = os.path.join(ast_dir, "merged_defined_classes.json")
            
            if os.path.exists(types_file):
                logger.info(f"Loading merged_defined_classes.json from: {types_file}")
                self._merged_types = read_json_file(types_file)
                
                if self._merged_types:
                    logger.info("Successfully loaded merged types data")
                else:
                    logger.warning("Failed to load merged types data")
            else:
                logger.debug(f"Merged types file not found: {types_file}")
                self._merged_types = None
                
        return self._merged_types
    
    @property
    def merged_call_graph(self) -> Optional[Union[Dict, list]]:
        """
        Lazy-loaded merged call graph data from merged_call_graph.json.
        
        Returns:
            Parsed JSON data or None if file doesn't exist or parsing fails
        """
        if self._merged_call_graph is None:
            ast_dir = self._get_ast_dir()
            call_graph_file = os.path.join(ast_dir, "merged_call_graph.json")
            
            if os.path.exists(call_graph_file):
                logger.info(f"Loading merged_call_graph.json from: {call_graph_file}")
                self._merged_call_graph = read_json_file(call_graph_file)
                
                if self._merged_call_graph:
                    logger.info("Successfully loaded merged call graph data")
                else:
                    logger.warning("Failed to load merged call graph data")
            else:
                logger.debug(f"Merged call graph file not found: {call_graph_file}")
                self._merged_call_graph = None
                
        return self._merged_call_graph
    
    def is_ast_available(self) -> bool:
        """
        Check if AST data is available (at least one of the merged files exists).
        
        Returns:
            bool: True if at least one AST file is available
        """
        ast_dir = self._get_ast_dir()
        
        files_to_check = [
            "merged_functions.json",
            "merged_defined_classes.json", 
            "merged_call_graph.json"
        ]
        
        for filename in files_to_check:
            file_path = os.path.join(ast_dir, filename)
            if os.path.exists(file_path):
                return True
                
        return False
    
    def validate_ast_built(self) -> None:
        """
        Validate that AST has been built before analysis.
        
        Raises:
            RuntimeError: If no AST files are found
        """
        if not self.is_ast_available():
            ast_dir = self._get_ast_dir()
            raise RuntimeError(
                f"AST must be built before analysis. No merged AST files found in: {ast_dir}. "
                f"Please run AST generation first."
            )
    
    def clear_cache(self) -> None:
        """
        Clear all cached data, forcing reload on next access.
        """
        logger.debug("Clearing RepoAstIndex cache")
        self._merged_functions = None
        self._merged_types = None
        self._merged_call_graph = None
    
    def preload_all(self) -> None:
        """
        Preload all AST data (functions, types, call graph).
        This can be useful for warming up the cache.
        """
        logger.info("Preloading all AST data...")
        
        # Access properties to trigger lazy loading
        _ = self.merged_functions
        _ = self.merged_types  
        _ = self.merged_call_graph
        
        logger.info("AST data preloading completed")
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of loaded AST data.
        
        Returns:
            Dict containing summary information about loaded data
        """
        summary = {
            "ast_dir": self._get_ast_dir(),
            "functions_loaded": self._merged_functions is not None,
            "types_loaded": self._merged_types is not None,
            "call_graph_loaded": self._merged_call_graph is not None,
        }
        
        # Add counts if data is loaded
        if self._merged_functions:
            if isinstance(self._merged_functions, dict):
                summary["functions_count"] = len(self._merged_functions.get("functions", []))
            elif isinstance(self._merged_functions, list):
                summary["functions_count"] = len(self._merged_functions)
        
        if self._merged_types:
            if isinstance(self._merged_types, dict):
                summary["types_count"] = len(self._merged_types.get("data_types", []))
            elif isinstance(self._merged_types, list):
                summary["types_count"] = len(self._merged_types)
        
        if self._merged_call_graph:
            if isinstance(self._merged_call_graph, dict):
                summary["call_graph_entries"] = len(self._merged_call_graph.get("call_graph", []))
            elif isinstance(self._merged_call_graph, list):
                summary["call_graph_entries"] = len(self._merged_call_graph)
        
        return summary