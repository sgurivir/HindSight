#!/usr/bin/env python3
"""
Commit Additional Context Provider
Provides additional context for LLM diff analysis by generating AST artifacts
for files touched by a commit and creating natural language descriptions.
"""

import os
import sys
import json
import tempfile
import traceback
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Set, Optional

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from ..core.lang_util.scoped_ast_util import ScopedASTUtil
from ..utils.log_util import get_logger
from ..utils.file_util import read_json_file

# Global cache for commit-level AST artifacts to avoid regeneration across chunks
_COMMIT_AST_CACHE = {}


def clear_commit_ast_cache():
    """Clear the global AST cache. Useful for testing or memory management."""
    global _COMMIT_AST_CACHE
    _COMMIT_AST_CACHE.clear()


def get_cache_stats():
    """Get statistics about the current AST cache."""
    global _COMMIT_AST_CACHE
    return {
        'cached_commits': len(_COMMIT_AST_CACHE),
        'cache_keys': list(_COMMIT_AST_CACHE.keys())
    }


class CommitExtendedContextProvider:
    """
    Provides additional context for LLM diff analysis by generating AST artifacts
    for files touched by a commit and creating natural language descriptions.
    
    This class:
    1. Determines files touched by commit
    2. Uses scoped_ast_util to generate AST artifacts for those files (nested depth of 3)
    3. Creates natural language descriptions of the AST artifacts
    4. Provides concise context with file names and line numbers only
    """

    def __init__(self, repo_path: str, exclude_directories: List[str] = None, commit_hash: str = None):
        """
        Initialize the context provider.
        
        Args:
            repo_path: Path to the repository
            exclude_directories: List of directories to exclude from AST analysis
            commit_hash: Optional commit hash for caching (if not provided, caching is disabled)
        """
        self.repo_path = Path(repo_path)
        self.exclude_directories = set(exclude_directories or [])
        self.commit_hash = commit_hash
        self.logger = get_logger(__name__)
        
        # Generate cache key for this commit and repository
        if self.commit_hash:
            cache_input = f"{str(self.repo_path)}:{self.commit_hash}"
            self.cache_key = hashlib.md5(cache_input.encode()).hexdigest()
            self.logger.debug(f"Initialized context provider with cache key: {self.cache_key}")
        else:
            self.cache_key = None
            self.logger.debug("Initialized context provider without caching (no commit hash provided)")
        
    def generate_additional_context(self, changed_files: List[str],
                                  clang_args: List[str] = None,
                                  all_commit_files: List[str] = None,
                                  out_dir: Path = None,
                                  use_subprocess: bool = False) -> str:
        """
        Generate additional context for the given changed files.
        
        Args:
            changed_files: List of files touched by the commit (for this chunk)
            clang_args: Optional clang arguments for C/C++ analysis
            all_commit_files: Optional list of all files changed in the entire commit (for caching)
            out_dir: Output directory for AST artifacts (required for proper file persistence)
            use_subprocess: Whether to run AST generation in subprocess (default: False for in-process)
            
        Returns:
            Natural language description of the AST context
        """
        if not changed_files:
            return "No files were changed in this commit."
            
        self.logger.info(f"Generating additional context for {len(changed_files)} changed files")
        
        try:
            # Convert file paths to absolute paths
            target_files = []
            for file_path in changed_files:
                abs_path = self.repo_path / file_path
                if abs_path.exists():
                    target_files.append(abs_path)
                else:
                    # File might not exist in current checkout but could exist in the commit being analyzed
                    # Log as debug instead of warning since this is expected for deleted files
                    self.logger.debug(f"File not found in current checkout: {abs_path}")
            
            if not target_files:
                return "No valid files found for context generation."
            
            # Store all commit files for caching if provided
            if all_commit_files:
                self._all_commit_files = [self.repo_path / f for f in all_commit_files if (self.repo_path / f).exists()]
            
            # Generate AST artifacts using scoped analysis with caching
            ast_artifacts = self._get_or_generate_ast_artifacts(target_files, clang_args or [], all_commit_files, out_dir, use_subprocess)
            
            # Create natural language description
            context_description = self._create_natural_language_description(ast_artifacts)
            
            return context_description
            
        except Exception as e:
            self.logger.error(f"Error generating additional context: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return f"Error generating context: {str(e)}"
    
    def _get_or_generate_ast_artifacts(self, target_files: List[Path],
                                     clang_args: List[str], all_commit_files: List[str] = None, out_dir: Path = None, use_subprocess: bool = False) -> Dict[str, Any]:
        """
        Get cached AST artifacts or generate them if not cached.
        
        Args:
            target_files: List of file paths to analyze
            clang_args: Clang arguments for C/C++ analysis
            all_commit_files: Optional list of all files changed in the entire commit (for caching)
            out_dir: Output directory for AST artifacts (required for proper file persistence)
            use_subprocess: Whether to run AST generation in subprocess (default: False for in-process)
            
        Returns:
            Dictionary containing AST artifacts filtered for target files
        """
        if out_dir is None:
            raise ValueError("out_dir parameter is required for AST artifact generation")
            
        # If caching is disabled, generate artifacts directly
        if not self.cache_key:
            self.logger.debug("Cache disabled, generating AST artifacts directly")
            return self._generate_scoped_ast_artifacts(target_files, clang_args, out_dir, use_subprocess)
        
        # Check if we have cached artifacts for this commit
        if self.cache_key in _COMMIT_AST_CACHE:
            self.logger.info(f"Using cached AST artifacts for commit (cache key: {self.cache_key[:8]}...)")
            cached_artifacts = _COMMIT_AST_CACHE[self.cache_key]
            
            return cached_artifacts
        
        # Generate artifacts for all changed files in the commit and cache them
        self.logger.info(f"Generating and caching AST artifacts for commit (cache key: {self.cache_key[:8]}...)")
        
        # Use provided all_commit_files if available, otherwise fall back to stored files or repository scan
        if all_commit_files:
            # Convert to Path objects and filter for existing files
            commit_file_paths = [self.repo_path / f for f in all_commit_files if (self.repo_path / f).exists()]
        else:
            # Fall back to the existing method
            commit_file_paths = self._get_all_commit_files()
        
        # Generate AST artifacts for all files in the commit
        full_artifacts = self._generate_scoped_ast_artifacts(commit_file_paths, clang_args, out_dir, use_subprocess)
        
        # Cache the full artifacts
        _COMMIT_AST_CACHE[self.cache_key] = full_artifacts
        self.logger.info(f"Cached AST artifacts for {len(commit_file_paths)} files in commit")
        
        return full_artifacts

    def _get_all_commit_files(self) -> List[Path]:
        """
        Get all files changed in the commit.
        
        Returns:
            List of all file paths changed in the commit
        """
        # Use the stored all_commit_files if available
        if hasattr(self, '_all_commit_files') and self._all_commit_files:
            self.logger.debug(f"Using stored commit files: {len(self._all_commit_files)} files")
            return self._all_commit_files
        
        # Fallback: try to find all supported files in the repository
        self.logger.warning("No commit files provided, using fallback approach (may be inefficient)")
        try:
            from ..core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
            
            all_files = []
            for ext in ALL_SUPPORTED_EXTENSIONS:
                all_files.extend(self.repo_path.rglob(f"*{ext}"))
            
            # Filter out files in excluded directories
            filtered_files = []
            for file_path in all_files:
                if self._is_file_allowed(file_path):
                    filtered_files.append(file_path)
            
            self.logger.debug(f"Found {len(filtered_files)} potential commit files")
            return filtered_files[:100]  # Limit to avoid excessive processing
            
        except Exception as e:
            self.logger.warning(f"Could not determine all commit files: {e}")
            return []

    def _is_file_allowed(self, file_path: Path) -> bool:
        """Check if a file is allowed (not in excluded directories)."""
        try:
            # Check if any parent directory is in exclude list
            for parent in file_path.parents:
                if parent.name in self.exclude_directories:
                    return False
            return True
        except Exception:
            return False

    def _filter_ast_artifacts_for_files(self, artifacts: Dict[str, Any],
                                      target_files: List[Path]) -> Dict[str, Any]:
        """
        Filter AST artifacts to only include data for the specified target files.
        
        Args:
            artifacts: Full AST artifacts dictionary
            target_files: List of files to filter for
            
        Returns:
            Filtered AST artifacts dictionary
        """
        if not artifacts:
            return artifacts
        
        # Convert target files to relative paths for comparison
        target_file_strs = set()
        for file_path in target_files:
            try:
                rel_path = str(file_path.relative_to(self.repo_path))
                target_file_strs.add(rel_path)
                # Also add the absolute path string for comparison
                target_file_strs.add(str(file_path))
            except ValueError:
                # File is outside repo, use absolute path
                target_file_strs.add(str(file_path))
        
        filtered_artifacts = {}
        
        # Filter functions
        if 'functions' in artifacts and artifacts['functions']:
            filtered_artifacts['functions'] = self._filter_functions_for_files(
                artifacts['functions'], target_file_strs
            )
        
        # Filter call graph
        if 'call_graph' in artifacts and artifacts['call_graph']:
            filtered_artifacts['call_graph'] = self._filter_call_graph_for_files(
                artifacts['call_graph'], target_file_strs
            )
        
        # Filter data types
        if 'data_types' in artifacts and artifacts['data_types']:
            filtered_artifacts['data_types'] = self._filter_data_types_for_files(
                artifacts['data_types'], target_file_strs
            )
        
        self.logger.debug(f"Filtered AST artifacts for {len(target_files)} target files")
        return filtered_artifacts

    def _filter_functions_for_files(self, functions_data: Dict[str, Any],
                                   target_files: Set[str]) -> Dict[str, Any]:
        """Filter functions data for specific files."""
        if not functions_data or 'function_to_location' not in functions_data:
            return functions_data
        
        filtered_functions = {}
        function_locations = functions_data['function_to_location']
        
        for func_name, locations in function_locations.items():
            filtered_locations = []
            
            if isinstance(locations, list):
                for location in locations:
                    if self._is_location_in_target_files(location, target_files):
                        filtered_locations.append(location)
            else:
                if self._is_location_in_target_files(locations, target_files):
                    filtered_locations.append(locations)
            
            if filtered_locations:
                filtered_functions[func_name] = filtered_locations
        
        return {'function_to_location': filtered_functions}

    def _filter_call_graph_for_files(self, call_graph_data: Dict[str, Any],
                                    target_files: Set[str]) -> Dict[str, Any]:
        """Filter call graph data for specific files."""
        if not call_graph_data or 'call_graph' not in call_graph_data:
            return call_graph_data
        
        filtered_graph = []
        call_graph = call_graph_data['call_graph']
        
        for file_entry in call_graph:
            file_name = file_entry.get('file', '')
            if any(target_file in file_name or file_name in target_file for target_file in target_files):
                filtered_graph.append(file_entry)
        
        return {'call_graph': filtered_graph}

    def _filter_data_types_for_files(self, data_types_data: Dict[str, Any],
                                    target_files: Set[str]) -> Dict[str, Any]:
        """Filter data types data for specific files."""
        if not data_types_data:
            return data_types_data
        
        # Handle different data type formats
        if 'data_type_to_location_and_checksum' in data_types_data:
            filtered_types = {}
            for type_name, type_info in data_types_data['data_type_to_location_and_checksum'].items():
                if self._is_type_in_target_files(type_info, target_files):
                    filtered_types[type_name] = type_info
            return {'data_type_to_location_and_checksum': filtered_types}
        
        elif 'data_type_to_location' in data_types_data:
            filtered_types = {}
            for type_name, type_info in data_types_data['data_type_to_location'].items():
                if self._is_type_in_target_files(type_info, target_files):
                    filtered_types[type_name] = type_info
            return {'data_type_to_location': filtered_types}
        
        return data_types_data

    def _is_location_in_target_files(self, location: Dict[str, Any], target_files: Set[str]) -> bool:
        """Check if a location is in the target files."""
        file_name = location.get('file_name', '')
        return any(target_file in file_name or file_name in target_file for target_file in target_files)

    def _is_type_in_target_files(self, type_info: Any, target_files: Set[str]) -> bool:
        """Check if a type definition is in the target files."""
        if isinstance(type_info, dict):
            if 'locations' in type_info:
                return any(self._is_location_in_target_files(loc, target_files)
                          for loc in type_info['locations'])
            elif 'file_name' in type_info:
                return self._is_location_in_target_files(type_info, target_files)
        elif isinstance(type_info, list):
            return any(self._is_location_in_target_files(loc, target_files)
                      for loc in type_info if isinstance(loc, dict))
        return False

    def _generate_scoped_ast_artifacts(self, target_files: List[Path],
                                     clang_args: List[str], out_dir: Path, use_subprocess: bool = False) -> Dict[str, Any]:
        """
        Generate AST artifacts for the target files using scoped analysis.
        
        Args:
            target_files: List of file paths to analyze
            clang_args: Clang arguments for C/C++ analysis
            out_dir: Output directory for AST artifacts (should be persistent directory like code_insights_dir)
            use_subprocess: Whether to run AST generation in subprocess (default: False for in-process)
            
        Returns:
            Dictionary containing AST artifacts with both data content and file paths.
            
            NOTE: This method has two different callers with different needs:
            1. Internal caller (generate_additional_context): needs data content for natural language description
            2. External caller (git_simple_diff_analyzer._get_merged_functions_path): needs file paths for diff enhancement
            
            Therefore, we return both data content (functions, call_graph, data_types) and
            file paths (merged_functions_file, merged_call_graph_file, merged_data_types_file).
        """
        self.logger.info(f"Generating scoped AST artifacts for {len(target_files)} files")
        
        # Ensure output directory exists
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Define output paths in the persistent directory
        # Use same filenames as code_analyzer for consistency with RepoAstIndex validation
        merged_functions_out = out_dir / "merged_functions.json"
        merged_graph_out = out_dir / "merged_call_graph.json"
        merged_data_types_out = out_dir / "merged_defined_classes.json"
        
        try:
            # Run scoped AST analysis with depth 3 as requested
            ScopedASTUtil.run_scoped_analysis(
                repo_root=self.repo_path,
                target_files=target_files,
                ignore_dirs=self.exclude_directories,
                clang_args=clang_args,
                out_dir=out_dir,
                merged_symbols_out=merged_functions_out,
                merged_graph_out=merged_graph_out,
                merged_data_types_out=merged_data_types_out,
                max_dependency_depth=3,  # Nested depth of 3 as requested
                use_subprocess=use_subprocess
            )
            
            # Read the generated artifacts and prepare return dictionary
            artifacts = {}
            
            if merged_functions_out.exists():
                artifacts['functions'] = read_json_file(str(merged_functions_out))
                artifacts['merged_functions_file'] = str(merged_functions_out)
                
            if merged_graph_out.exists():
                artifacts['call_graph'] = read_json_file(str(merged_graph_out))
                artifacts['merged_call_graph_file'] = str(merged_graph_out)
                
            if merged_data_types_out.exists():
                artifacts['data_types'] = read_json_file(str(merged_data_types_out))
                artifacts['merged_data_types_file'] = str(merged_data_types_out)
            
            self.logger.info(f"Successfully generated AST artifacts: {list(artifacts.keys())}")
            return artifacts
            
        except Exception as e:
            self.logger.error(f"Error during scoped AST analysis: {e}")
            return {}
    
    def _create_natural_language_description(self, ast_artifacts: Dict[str, Any]) -> str:
        """
        Create a natural language description of the AST artifacts.
        
        Args:
            ast_artifacts: Dictionary containing AST artifacts
            
        Returns:
            Natural language description with file names and line numbers
        """
        if not ast_artifacts:
            return "No AST artifacts were generated for the changed files."
        
        description_parts = []
        
        # Process functions
        if 'functions' in ast_artifacts and ast_artifacts['functions']:
            functions_desc = self._describe_functions(ast_artifacts['functions'])
            if functions_desc:
                description_parts.append(functions_desc)
        
        # Process call graph
        if 'call_graph' in ast_artifacts and ast_artifacts['call_graph']:
            call_graph_desc = self._describe_call_graph(ast_artifacts['call_graph'])
            if call_graph_desc:
                description_parts.append(call_graph_desc)
        
        # Process data types
        if 'data_types' in ast_artifacts and ast_artifacts['data_types']:
            data_types_desc = self._describe_data_types(ast_artifacts['data_types'])
            if data_types_desc:
                description_parts.append(data_types_desc)
        
        if not description_parts:
            return "No significant code structures found in the changed files."
        
        # Combine all descriptions
        full_description = "\n\n".join(description_parts)
        
        # Add header
        header = "=== Additional Context from Code Analysis ===\n"
        header += "The following information describes the code structure and relationships "
        header += "in the files touched by this commit:\n\n"
        
        return header + full_description
    
    def _describe_functions(self, functions_data: Dict[str, Any]) -> str:
        """
        Create natural language description of functions.
        
        Args:
            functions_data: Functions data from AST analysis
            
        Returns:
            Natural language description of functions
        """
        if not functions_data:
            return ""
        
        # Extract function information from the data structure
        function_locations = functions_data.get('function_to_location', {})
        if not function_locations:
            return ""
        
        descriptions = []
        descriptions.append("FUNCTIONS FOUND:")
        
        for func_name, locations in function_locations.items():
            if not locations:
                continue
                
            # Handle both single location and multiple locations
            if isinstance(locations, list):
                for location in locations:
                    file_name = location.get('file_name', 'unknown')
                    start_line = location.get('start', 'unknown')
                    end_line = location.get('end', 'unknown')
                    descriptions.append(f"- Function '{func_name}' in {file_name} (lines {start_line}-{end_line})")
            else:
                # Single location
                file_name = locations.get('file_name', 'unknown')
                start_line = locations.get('start', 'unknown')
                end_line = locations.get('end', 'unknown')
                descriptions.append(f"- Function '{func_name}' in {file_name} (lines {start_line}-{end_line})")
        
        return "\n".join(descriptions) if len(descriptions) > 1 else ""
    
    def _describe_call_graph(self, call_graph_data: Dict[str, Any]) -> str:
        """
        Create natural language description of call graph relationships.
        
        Args:
            call_graph_data: Call graph data from AST analysis
            
        Returns:
            Natural language description of function relationships
        """
        if not call_graph_data:
            return ""
        
        # Extract call graph information
        call_graph = call_graph_data.get('call_graph', [])
        if not call_graph:
            return ""
        
        descriptions = []
        descriptions.append("FUNCTION RELATIONSHIPS:")
        
        for file_entry in call_graph:
            file_name = file_entry.get('file', 'unknown')
            functions = file_entry.get('functions', [])
            
            for func_entry in functions:
                func_name = func_entry.get('function', 'unknown')
                functions_invoked = func_entry.get('functions_invoked', [])
                data_types_used = func_entry.get('data_types_used', [])
                
                context = func_entry.get('context', {})
                start_line = context.get('start', 'unknown')
                end_line = context.get('end', 'unknown')
                
                # Describe function invocations
                if functions_invoked:
                    invoked_list = ", ".join(functions_invoked[:5])  # Limit to first 5
                    if len(functions_invoked) > 5:
                        invoked_list += f" (and {len(functions_invoked) - 5} more)"
                    descriptions.append(f"- Function '{func_name}' in {file_name} (lines {start_line}-{end_line}) invokes: {invoked_list}")
                
                # Describe data types used
                if data_types_used:
                    types_list = ", ".join(data_types_used[:3])  # Limit to first 3
                    if len(data_types_used) > 3:
                        types_list += f" (and {len(data_types_used) - 3} more)"
                    descriptions.append(f"- Function '{func_name}' uses data types: {types_list}")
        
        return "\n".join(descriptions) if len(descriptions) > 1 else ""
    
    def _describe_data_types(self, data_types_data: Dict[str, Any]) -> str:
        """
        Create natural language description of data types.
        
        Args:
            data_types_data: Data types data from AST analysis
            
        Returns:
            Natural language description of data types
        """
        if not data_types_data:
            return ""
        
        # Extract data type information
        data_type_locations = data_types_data.get('data_type_to_location_and_checksum', {})
        if not data_type_locations:
            # Try alternative format
            data_type_locations = data_types_data.get('data_type_to_location', {})
        
        if not data_type_locations:
            return ""
        
        descriptions = []
        descriptions.append("DATA TYPES FOUND:")
        
        for type_name, type_info in data_type_locations.items():
            if isinstance(type_info, dict):
                # Handle format with location and checksum
                locations = type_info.get('locations', [])
                if not locations and 'file_name' in type_info:
                    # Single location format
                    file_name = type_info.get('file_name', 'unknown')
                    start_line = type_info.get('start', 'unknown')
                    end_line = type_info.get('end', 'unknown')
                    descriptions.append(f"- Data type '{type_name}' defined in {file_name} (lines {start_line}-{end_line})")
                else:
                    # Multiple locations
                    for location in locations:
                        file_name = location.get('file_name', 'unknown')
                        start_line = location.get('start', 'unknown')
                        end_line = location.get('end', 'unknown')
                        descriptions.append(f"- Data type '{type_name}' defined in {file_name} (lines {start_line}-{end_line})")
            elif isinstance(type_info, list):
                # Handle list format
                for location in type_info:
                    if isinstance(location, dict):
                        file_name = location.get('file_name', 'unknown')
                        start_line = location.get('start', 'unknown')
                        end_line = location.get('end', 'unknown')
                        descriptions.append(f"- Data type '{type_name}' defined in {file_name} (lines {start_line}-{end_line})")
        
        return "\n".join(descriptions) if len(descriptions) > 1 else ""
    
    def get_context_for_files(self, changed_files: List[str],
                            clang_args: List[str] = None,
                            all_commit_files: List[str] = None,
                            out_dir: Path = None,
                            use_subprocess: bool = False) -> str:
        """
        Public method to get additional context for changed files.
        This is the main entry point for the DiffAnalysisRunner.
        
        Args:
            changed_files: List of files touched by the commit (for this chunk)
            clang_args: Optional clang arguments for C/C++ analysis
            all_commit_files: Optional list of all files changed in the entire commit (for caching)
            out_dir: Output directory for AST artifacts (required for proper file persistence)
            use_subprocess: Whether to run AST generation in subprocess (default: False for in-process)
            
        Returns:
            Additional context string to be added to the LLM prompt
        """
        return self.generate_additional_context(changed_files, clang_args, all_commit_files, out_dir, use_subprocess)