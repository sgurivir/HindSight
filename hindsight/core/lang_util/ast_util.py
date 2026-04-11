#!/usr/bin/env python3
# Created by Sridhar Gurivireddy on 08/25/2025
# ASTUtil.py
# Usage:
#   python ASTUtil.py --repo /path/to/project \
#     --exclude .git DerivedData build .build \
#     --merged-functions merged_functions.json \
#     --merged-graph merged_call_graph.json \
#     --clang-args -I/usr/include -DFOO=1
#
# NOTE: --clang-args MUST be the LAST argument as it captures everything after it.
#
# To define preprocessor macros (e.g., for iOS/macOS target selection):
#   Method 1: Using -D/--define (can be placed anywhere):
#     python ASTUtil.py --repo /path/to/project \
#       -D TARGET_OS_IPHONE=1 -D TARGET_OS_OSX=0 \
#       -D TARGET_OS_EMBEDDED=0 -D TARGET_OS_WATCH=0
#
#   Method 2: Using --clang-args (MUST be last):
#     python ASTUtil.py --repo /path/to/project \
#       --exclude .git build \
#       --clang-args -DTARGET_OS_IPHONE=1 -DTARGET_OS_OSX=0

import argparse
import json
import logging
import re
import sys
import traceback

from pathlib import Path
from typing import Dict, List, Any, Set, Union, Iterable, Tuple

from ...utils.log_util import setup_default_logging
from ...utils.file_util import read_json_file, write_json_file

from .ast_function_signature_util import ASTFunctionSignatureGenerator
from .ast_merger import ASTMerger
from .ast_process_manager import ASTProcessManager
from .ast_util_language_helper import (
    ClangAnalysisHelper,
    SwiftAnalysisHelper,
    KotlinAnalysisHelper,
    JavaAnalysisHelper,
    GoAnalysisHelper,
    # JavaScriptTypeScriptAnalysisHelper,  # JS/TS support disabled
    log_ignored_directories
)

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

logger = logging.getLogger(__name__)

class ASTUtil:
    """
    Orchestrates CASTUtil, SwiftASTUtil, KotlinASTUtil, and JavaASTUtil to create unified symbol tables and call graphs
    for projects containing C/C++/Objective-C, Swift, Kotlin, and Java code.
    """

    @staticmethod
    def initialize_libclang():
        """Initialize libclang path before any Clang operations."""
        ClangAnalysisHelper.initialize_libclang()

    @staticmethod
    def run_clang_analysis(repo: Path,
                           ignore_dirs: Set[str],
                           clang_args: List[str],
                           clang_defined_out: Path,
                           clang_nested_out: Path,
                           max_call_depth: int,
                           clang_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run CASTUtil analysis and return clang_symbols and call graph data."""
        clang_registry, clang_graph = ClangAnalysisHelper.run_clang_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            clang_args=clang_args,
            clang_defined_out=clang_defined_out,
            clang_nested_out=clang_nested_out,
            max_call_depth=max_call_depth,
            clang_classes_out=clang_classes_out
        )
        
        # Convert Clang registry to symbols format
        clang_symbols = ASTUtil.convert_clang_registry_to_symbols(clang_registry)
        
        return clang_symbols, clang_graph

    @staticmethod
    def run_swift_analysis(repo: Path,
                           ignore_dirs: Set[str],
                           swift_symbols_out: Path,
                           swift_graph_out: Path,
                           swift_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run SwiftASTUtil analysis and return symbols and call graph data."""
        return SwiftAnalysisHelper.run_swift_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            swift_symbols_out=swift_symbols_out,
            swift_graph_out=swift_graph_out,
            swift_classes_out=swift_classes_out
        )

    @staticmethod
    def run_kotlin_analysis(repo: Path,
                           ignore_dirs: Set[str],
                           kotlin_functions_out: Path,
                           kotlin_graph_out: Path,
                           kotlin_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run KotlinASTUtil analysis and return symbols and call graph data."""
        return KotlinAnalysisHelper.run_kotlin_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            kotlin_functions_out=kotlin_functions_out,
            kotlin_graph_out=kotlin_graph_out,
            kotlin_classes_out=kotlin_classes_out
        )

    @staticmethod
    def run_java_analysis(repo: Path,
                         ignore_dirs: Set[str],
                         java_functions_out: Path,
                         java_graph_out: Path,
                         java_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run JavaASTUtil analysis and return symbols and call graph data."""
        return JavaAnalysisHelper.run_java_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            java_functions_out=java_functions_out,
            java_graph_out=java_graph_out,
            java_classes_out=java_classes_out
        )

    @staticmethod
    def run_go_analysis(repo: Path,
                        ignore_dirs: Set[str],
                        go_functions_out: Path,
                        go_graph_out: Path,
                        go_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run GoASTUtil analysis and return symbols and call graph data."""
        return GoAnalysisHelper.run_go_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            go_functions_out=go_functions_out,
            go_graph_out=go_graph_out,
            go_classes_out=go_classes_out
        )

    # JS/TS analysis method - disabled but kept for reference
    # @staticmethod
    # def run_js_ts_analysis(repo: Path,
    #                       ignore_dirs: Set[str],
    #                       js_ts_functions_out: Path,
    #                       js_ts_graph_out: Path,
    #                       js_ts_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    #     """Run JavaScriptTypeScriptASTUtil analysis and return symbols and call graph data."""
    #     return JavaScriptTypeScriptAnalysisHelper.run_js_ts_analysis(
    #         repo=repo,
    #         ignore_dirs=ignore_dirs,
    #         js_ts_functions_out=js_ts_functions_out,
    #         js_ts_graph_out=js_ts_graph_out,
    #         js_ts_classes_out=js_ts_classes_out
    #     )

    @staticmethod
    def run_scoped_analysis(repo: Path,
                          target_files: List[Path],
                          ignore_dirs: Set[str],
                          clang_args: List[str],
                          out_dir: Path,
                          merged_symbols_out: Path,
                          merged_graph_out: Path,
                          merged_data_types_out: Path,
                          max_dependency_depth: int = 3,
                          use_subprocess: bool = False) -> None:
        """
        Run scoped analysis for specified files and their dependencies.
        
        This method uses in-process AST generation by default for better debugging and simpler execution.
        For memory-intensive scenarios, it can be run out-of-process when use_subprocess=True.
        
        Args:
            repo: Path to the repository to analyze
            target_files: List of specific files to analyze
            ignore_dirs: Set of directory names to ignore during analysis
            clang_args: List of arguments to pass to clang
            out_dir: Output directory where language-specific artifacts will be stored
            merged_symbols_out: Path for merged symbols output file
            merged_graph_out: Path for merged call graph output file
            merged_data_types_out: Path for merged data types output file
            max_dependency_depth: Maximum depth for dependency discovery
            use_subprocess: Whether to use subprocess for AST generation (default: False)
        """
        if use_subprocess:
            logger.info("Running scoped AST analysis using out-of-process generation")
            
            # Use out-of-process AST generation
            with ASTProcessManager() as process_manager:
                process_manager.run_scoped_analysis(
                    repo=repo,
                    target_files=target_files,
                    ignore_dirs=ignore_dirs,
                    clang_args=clang_args,
                    out_dir=out_dir,
                    merged_symbols_out=merged_symbols_out,
                    merged_graph_out=merged_graph_out,
                    merged_data_types_out=merged_data_types_out,
                    max_dependency_depth=max_dependency_depth
                )
        else:
            logger.info("Running scoped AST analysis using in-process generation")
            
            # Use in-process AST generation (for debugging)
            ASTUtil._run_scoped_analysis_in_process(
                repo=repo,
                target_files=target_files,
                ignore_dirs=ignore_dirs,
                clang_args=clang_args,
                out_dir=out_dir,
                merged_symbols_out=merged_symbols_out,
                merged_graph_out=merged_graph_out,
                merged_data_types_out=merged_data_types_out,
                max_dependency_depth=max_dependency_depth
            )

    @staticmethod
    def find_file_dependencies(repo: Path,
                              target_files: List[Path],
                              ignore_dirs: Set[str],
                              max_depth: int = 3) -> Set[Path]:
        """
        Find dependencies of target files by analyzing imports, includes, and references.
        
        Args:
            repo: Root directory of the repository
            target_files: List of files to analyze
            ignore_dirs: Directories to ignore during dependency discovery
            max_depth: Maximum depth to traverse for dependencies
            
        Returns:
            Set of all files including target files and their dependencies
        """
        from .scoped_ast_util import ScopedASTUtil
        
        return ScopedASTUtil.find_file_dependencies(
            repo_root=repo,
            target_files=target_files,
            ignore_dirs=ignore_dirs,
            max_depth=max_depth
        )

    @staticmethod
    def convert_clang_registry_to_symbols(clang_registry: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert Clang registry format to symbols format matching Swift output."""
        return ClangAnalysisHelper.convert_clang_registry_to_symbols(clang_registry)


    @staticmethod
    def _is_valid_context(context: Dict) -> bool:
        """Check if context has valid file path and line numbers."""
        if not isinstance(context, dict):
            return False

        file_path = context.get("file")
        start_line = context.get("start")
        end_line = context.get("end")

        # All three must be present and not None
        return (file_path is not None and
                start_line is not None and
                end_line is not None)


    @staticmethod
    def run_full_analysis(repo: Path,
                          include_dirs: Set[str],
                          ignore_dirs: Set[str],
                          clang_args: List[str],
                          out_dir: Path,
                          merged_symbols_out: Path,
                          merged_graph_out: Path,
                          merged_data_types_out: Path,
                          use_subprocess: bool = False,
                          max_dependency_depth: int = 3,
                          enable_preprocessor_macros: List[str] = None,
                          expand_macros: bool = True,
                          use_parallel: bool = True,
                          max_workers: int = None) -> None:
        """Run complete analysis for all five languages and generate merged outputs.
        
        This method uses in-process AST generation by default for better debugging and simpler execution.
        For memory-intensive scenarios, it can be run out-of-process when use_subprocess=True.
        
        When include_dirs is specified, the analysis will:
        1. Find files within the included directories
        2. Discover their dependencies (imports, includes) up to max_dependency_depth
        3. Include dependency files in analysis even if they're outside include_dirs
        
        Args:
            repo: Path to the repository to analyze
            include_dirs: Set of directory names to include (with dependency discovery)
            ignore_dirs: Set of directory names to ignore during analysis
            clang_args: List of arguments to pass to clang
            out_dir: Output directory where language-specific artifacts will be stored
            merged_symbols_out: Path for merged symbols output file
            merged_graph_out: Path for merged call graph output file
            merged_data_types_out: Path for merged data types output file
            use_subprocess: Whether to use subprocess for AST generation (default: False)
            max_dependency_depth: Maximum depth for dependency discovery when using include_dirs (default: 3)
            enable_preprocessor_macros: List of preprocessor macro names to enable (e.g., ['TARGET_OS_IPHONE', 'DEBUG'])
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
            use_parallel: Whether to use parallel processing for AST generation (default: True)
            max_workers: Maximum number of worker processes for parallel processing (default: None, uses system default)
        """
        # Convert enable_preprocessor_macros to clang -D arguments
        effective_clang_args = list(clang_args) if clang_args else []
        if enable_preprocessor_macros:
            for macro in enable_preprocessor_macros:
                # Handle both "MACRO=value" and "MACRO" formats
                if '=' in macro:
                    effective_clang_args.append(f"-D{macro}")
                else:
                    effective_clang_args.append(f"-D{macro}=1")
            logger.info(f"Added {len(enable_preprocessor_macros)} preprocessor macros to clang args: {enable_preprocessor_macros}")
        
        # Log expand macros mode if enabled
        if expand_macros:
            logger.info("EXPAND MACROS MODE ENABLED: Will build AST twice (with and without macros) and merge results")
        
        if use_subprocess:
            logger.info("Running full AST analysis using out-of-process generation")
            
            # Use out-of-process AST generation
            with ASTProcessManager() as process_manager:
                process_manager.run_full_analysis(
                    repo=repo,
                    include_dirs=include_dirs,
                    ignore_dirs=ignore_dirs,
                    clang_args=effective_clang_args,
                    out_dir=out_dir,
                    merged_symbols_out=merged_symbols_out,
                    merged_graph_out=merged_graph_out,
                    merged_data_types_out=merged_data_types_out,
                    max_dependency_depth=max_dependency_depth,
                    expand_macros=expand_macros
                )
        else:
            logger.info("Running full AST analysis using in-process generation")
            
            # Log parallel processing settings
            if use_parallel:
                logger.info(f"Parallel processing ENABLED (max_workers={max_workers or 'auto'})")
            else:
                logger.info("Parallel processing DISABLED")
            
            # Use in-process AST generation (for debugging)
            ASTUtil._run_full_analysis_in_process(
                repo=repo,
                include_dirs=include_dirs,
                ignore_dirs=ignore_dirs,
                clang_args=effective_clang_args,
                out_dir=out_dir,
                merged_symbols_out=merged_symbols_out,
                merged_graph_out=merged_graph_out,
                merged_data_types_out=merged_data_types_out,
                max_dependency_depth=max_dependency_depth,
                expand_macros=expand_macros,
                use_parallel=use_parallel,
                max_workers=max_workers
            )


    @staticmethod
    def _find_files_in_include_dirs(repo: Path, include_dirs: Set[str], ignore_dirs: Set[str]) -> List[Path]:
        """
        Find all source files within the specified include directories.
        
        Supports both exact path matching and partial directory name matching:
        - "src/main" matches files in exactly "src/main/"
        - "Positioning" matches files in any directory containing "Positioning" (e.g., "Daemon/Positioning/")
        
        Args:
            repo: Repository root path
            include_dirs: Set of directory names or paths to include
            ignore_dirs: Set of directory names to ignore
            
        Returns:
            List of source files found in include directories
        """
        from ...utils.file_filter_util import find_files_with_extensions
        from .all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
        
        initial_files = []
        
        # Resolve repo path to handle symlinks consistently (same as file_filter_util does)
        resolved_repo = repo.resolve()
        
        # Always honor user's preference of include_dirs - remove them from ignore_dirs to prevent conflicts
        # When include_dirs is specified, those directories should never be ignored
        effective_ignore_dirs = set(ignore_dirs) - set(include_dirs)
        
        logger.info(f"Finding files with include_dirs={include_dirs}, effective_ignore_dirs has {len(effective_ignore_dirs)} entries (removed {len(set(ignore_dirs) - effective_ignore_dirs)} include dirs from ignore list)")
        
        # Find all files in the repository first
        all_files = find_files_with_extensions(repo, effective_ignore_dirs, set(ALL_SUPPORTED_EXTENSIONS))
        
        # Filter to only include files within include_dirs
        for file_path in all_files:
            try:
                # Use resolved repo path for consistent relative path calculation
                relative_path = file_path.relative_to(resolved_repo)
                relative_path_str = str(relative_path).replace('\\', '/')
                
                # Check if file is within any of the include directories
                for include_dir in include_dirs:
                    include_dir_normalized = include_dir.replace('\\', '/')
                    
                    # Check for exact path prefix match first (e.g., "src/main")
                    if '/' in include_dir_normalized:
                        if relative_path_str.startswith(include_dir_normalized + '/') or relative_path_str == include_dir_normalized:
                            initial_files.append(file_path)
                            break
                    else:
                        # Check for partial directory name match (e.g., "Positioning" matches "Daemon/Positioning/")
                        path_parts = relative_path_str.split('/')
                        if include_dir_normalized in path_parts:
                            initial_files.append(file_path)
                            break
            except ValueError:
                continue
        
        logger.info(f"Found {len(initial_files)} files in include directories: {include_dirs}")
        return initial_files

    @staticmethod
    def _run_full_analysis_in_process(repo: Path,
                                      include_dirs: Set[str],
                                      ignore_dirs: Set[str],
                                      clang_args: List[str],
                                      out_dir: Path,
                                      merged_symbols_out: Path,
                                      merged_graph_out: Path,
                                      merged_data_types_out: Path,
                                      max_dependency_depth: int = 3,
                                      expand_macros: bool = True,
                                      use_parallel: bool = True,
                                      max_workers: int = None) -> None:
        """
        Run full AST analysis in the same process (for debugging).
        This reuses the logic from ast_worker.py but runs it directly.
        
        When include_dirs is specified, discovers dependencies and includes them in analysis.
        
        Args:
            repo: Path to the repository to analyze
            include_dirs: Set of directory names to include (with dependency discovery)
            ignore_dirs: Set of directory names to ignore during analysis
            clang_args: List of arguments to pass to clang
            out_dir: Output directory where language-specific artifacts will be stored
            merged_symbols_out: Path for merged symbols output file
            merged_graph_out: Path for merged call graph output file
            merged_data_types_out: Path for merged data types output file
            max_dependency_depth: Maximum depth for dependency discovery when using include_dirs (default: 3)
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
            use_parallel: Whether to use parallel processing for AST generation (default: True)
            max_workers: Maximum number of worker processes for parallel processing (default: None, uses system default)
        """
        # Import the worker class to reuse its logic
        from .ast_worker import ASTWorker
        
        # Initialize libclang before any operations
        ASTUtil.initialize_libclang()
        
        # Handle include_dirs with dependency discovery
        if include_dirs:
            logger.info(f"Running analysis with include directories: {include_dirs}")
            
            # Find initial files in include directories
            initial_files = ASTUtil._find_files_in_include_dirs(repo, include_dirs, ignore_dirs)
            
            if not initial_files:
                logger.warning(f"No files found in include directories: {include_dirs}")
                return
            
            # Discover dependencies
            from .scoped_ast_util import ScopedASTUtil
            all_files = ScopedASTUtil.find_file_dependencies(
                repo_root=repo,
                target_files=initial_files,
                ignore_dirs=ignore_dirs,
                max_depth=max_dependency_depth
            )
            
            logger.info(f"Analysis will include {len(all_files)} files (initial + dependencies)")
            
            # Run scoped analysis with discovered files
            from .scoped_ast_util import ScopedASTUtil
            ScopedASTUtil.run_scoped_analysis(
                repo_root=repo,
                target_files=list(all_files),
                ignore_dirs=ignore_dirs,
                clang_args=clang_args,
                out_dir=out_dir,
                merged_symbols_out=merged_symbols_out,
                merged_graph_out=merged_graph_out,
                merged_data_types_out=merged_data_types_out,
                max_dependency_depth=0  # Dependencies already discovered
            )
            return
        
        # Original full repository analysis when include_dirs is empty
        # Create a temporary config that mimics what ASTProcessManager would create
        import tempfile
        import json
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_config:
            config = {
                "operation": "full_analysis",
                "repo": str(repo),
                "include_dirs": list(include_dirs or []),
                "ignore_dirs": list(ignore_dirs),
                "clang_args": clang_args,
                "out_dir": str(out_dir),
                "merged_symbols_out": str(merged_symbols_out),
                "merged_graph_out": str(merged_graph_out),
                "merged_data_types_out": str(merged_data_types_out),
                "max_dependency_depth": max_dependency_depth,
                "expand_macros": expand_macros,
                "use_parallel": use_parallel,
                "max_workers": max_workers,
                "result_file": "/tmp/in_process_result.json"  # Not used in in-process mode
            }
            json.dump(config, temp_config, indent=2)
            temp_config_path = temp_config.name
        
        try:
            # Create worker and run analysis directly
            worker = ASTWorker(temp_config_path)
            if not worker.load_config():
                raise RuntimeError("Failed to load configuration for in-process analysis")
            
            success = worker._run_full_analysis()
            if not success:
                raise RuntimeError("In-process full analysis failed")
                
        finally:
            # Clean up temporary config file
            import os
            try:
                os.unlink(temp_config_path)
            except OSError:
                pass

    @staticmethod
    def _run_scoped_analysis_in_process(repo: Path,
                                      target_files: List[Path],
                                      ignore_dirs: Set[str],
                                      clang_args: List[str],
                                      out_dir: Path,
                                      merged_symbols_out: Path,
                                      merged_graph_out: Path,
                                      merged_data_types_out: Path,
                                      max_dependency_depth: int = 3) -> None:
        """
        Run scoped AST analysis in the same process (for debugging).
        This reuses the logic from ast_worker.py but runs it directly.
        """
        # Import the worker class to reuse its logic
        from .ast_worker import ASTWorker
        
        # Initialize libclang before any operations
        ASTUtil.initialize_libclang()
        
        # Create a temporary config that mimics what ASTProcessManager would create
        import tempfile
        import json
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_config:
            config = {
                "operation": "scoped_analysis",
                "repo": str(repo),
                "target_files": [str(f) for f in target_files],
                "ignore_dirs": list(ignore_dirs),
                "clang_args": clang_args,
                "out_dir": str(out_dir),
                "merged_symbols_out": str(merged_symbols_out),
                "merged_graph_out": str(merged_graph_out),
                "merged_data_types_out": str(merged_data_types_out),
                "max_dependency_depth": max_dependency_depth,
                "result_file": "/tmp/in_process_result.json"  # Not used in in-process mode
            }
            json.dump(config, temp_config, indent=2)
            temp_config_path = temp_config.name
        
        try:
            # Create worker and run analysis directly
            worker = ASTWorker(temp_config_path)
            if not worker.load_config():
                raise RuntimeError("Failed to load configuration for in-process analysis")
            
            success = worker._run_scoped_analysis()
            if not success:
                raise RuntimeError("In-process scoped analysis failed")
                
        finally:
            # Clean up temporary config file
            import os
            try:
                os.unlink(temp_config_path)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="Analyze mixed Clang/Swift/Kotlin/Java/Go projects and generate unified symbol tables and call graphs"
    )
    parser.add_argument("--repo", type=str, required=True,
                        help="Path to project root directory")
    parser.add_argument("--config", type=Path, required=False,
                        help="Path to configuration JSON file (will use exclude_directories from config)")
    parser.add_argument("--exclude", nargs="*", default=[".git", "DerivedData", "build", ".build"],
                        help="Directory names to exclude at any depth (overridden by --config if provided)")
    parser.add_argument("--clang-args", nargs=argparse.REMAINDER, default=[],
                        help="Additional arguments to pass to Clang parser. MUST BE LAST ARGUMENT. "
                             "Everything after --clang-args is passed to Clang (e.g., --clang-args -DTARGET_OS_IPHONE=1 -I/path)")
    parser.add_argument("--define", "-D", action="append", dest="defines", default=[],
                        help="Define preprocessor macros (e.g., -D TARGET_OS_IPHONE=1 or --define TARGET_OS_IPHONE=1). Can be used multiple times.")
    parser.add_argument("--include-dirs", nargs="*", default=[],
                        help="Directory names to include in analysis (with automatic dependency discovery)")
    parser.add_argument("--expand-macros", action="store_true",
                        help="Build AST twice (with and without macros) and merge results. "
                             "This captures all code paths regardless of macro state, handling transitive macro definitions.")

    # Process mode arguments
    parser.add_argument("--use-subprocess", action="store_true",
                        help="Run AST generation in a separate process to reduce memory usage")
    parser.add_argument("--in-process", action="store_true",
                        help="Explicitly run AST generation in the same process for easier debugging (default behavior)")

    # Scoped analysis arguments
    parser.add_argument("--scoped", action="store_true",
                        help="Run scoped analysis on specific files and their dependencies")
    parser.add_argument("--files", nargs="*", type=Path,
                        help="List of files to analyze for scoped analysis (relative to repo root)")
    parser.add_argument("--max-dependency-depth", type=int, default=3,
                        help="Maximum depth for dependency discovery in scoped analysis (default: 3)")

    # Output file arguments with /tmp defaults
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/language_artifacts"),
                        help="Output directory for language-specific artifacts (default: /tmp/language_artifacts)")
    parser.add_argument("--merged-functions", type=Path, default=Path("/tmp/merged_functions.json"),
                        help="Output path for merged functions JSON (default: /tmp/merged_functions.json)")
    parser.add_argument("--merged-graph", type=Path, default=Path("/tmp/merged_call_graph.json"),
                        help="Output path for merged call graph JSON (default: /tmp/merged_call_graph.json)")
    parser.add_argument("--merged-data-types-out", type=Path, default=Path("/tmp/merged_defined_classes.json"),
                        help="Path to write merged data types JSON (default: /tmp/merged_defined_classes.json)")

    args = parser.parse_args()

    # Properly expand and resolve the repo path to handle tildes and symlinks
    repo_path = Path(args.repo).expanduser().resolve()
    args.repo = repo_path

    # Validate conflicting process mode arguments
    if args.use_subprocess and args.in_process:
        logger.error("Cannot specify both --use-subprocess and --in-process")
        sys.exit(1)

    # Setup logging
    setup_default_logging()

    # Load config file if provided and extract exclude_directories and include_directories
    exclude_dirs = set(args.exclude)  # Default exclude directories
    include_dirs = set(args.include_dirs)  # Include directories from command line

    if args.config:
        try:
            logger.info(f"Loading configuration from: {args.config}")
            config_data = read_json_file(str(args.config))
            
            # Handle exclude_directories
            if config_data and 'exclude_directories' in config_data:
                # Use exclude_directories from config, overriding --exclude
                config_exclude_dirs = config_data['exclude_directories']
                if isinstance(config_exclude_dirs, list):
                    exclude_dirs = set(config_exclude_dirs)
                    logger.info(f"Using exclude_directories from config: {exclude_dirs}")
                else:
                    logger.warning("exclude_directories in config is not a list, using default exclude directories")
            else:
                logger.warning("No exclude_directories found in config file, using default exclude directories")
            
            # Handle include_directories from config (only if not specified on command line)
            if not args.include_dirs and config_data and 'include_directories' in config_data:
                config_include_dirs = config_data['include_directories']
                if isinstance(config_include_dirs, list):
                    include_dirs = set(config_include_dirs)
                    logger.info(f"Using include_directories from config: {include_dirs}")
                else:
                    logger.warning("include_directories in config is not a list, using empty include directories")
            elif args.include_dirs:
                logger.info(f"Using include_directories from command line: {include_dirs}")
            else:
                logger.info("No include_directories specified")
                
        except Exception as e:
            logger.error(f"Failed to load config file {args.config}: {e}")
            logger.info("Using default exclude directories and command line include directories")

    # Validate scoped analysis arguments
    if args.scoped:
        if not args.files:
            logger.error("--files argument is required when using --scoped")
            sys.exit(1)
        
        # Convert file paths to absolute paths relative to repo
        target_files = []
        for file_path in args.files:
            if file_path.is_absolute():
                target_files.append(file_path)
            else:
                target_files.append(args.repo / file_path)

        # Validate that all target files exist
        missing_files = [f for f in target_files if not f.exists()]
        if missing_files:
            logger.error(f"The following target files do not exist: {missing_files}")
            sys.exit(1)

        # Update output paths for scoped analysis
        if args.out_dir == Path("/tmp/language_artifacts"):
            args.out_dir = Path("/tmp/scoped_language_artifacts")
        if args.merged_functions == Path("/tmp/merged_functions.json"):
            args.merged_functions = Path("/tmp/scoped_merged_functions.json")
        if args.merged_graph == Path("/tmp/merged_call_graph.json"):
            args.merged_graph = Path("/tmp/scoped_merged_call_graph.json")
        if args.merged_data_types_out == Path("/tmp/merged_defined_classes.json"):
            args.merged_data_types_out = Path("/tmp/scoped_merged_defined_classes.json")

    # Merge --define/-D arguments into clang_args
    # Convert defines like "TARGET_OS_IPHONE=1" to "-DTARGET_OS_IPHONE=1"
    clang_args = list(args.clang_args) if args.clang_args else []
    if args.defines:
        for define in args.defines:
            # Handle both "MACRO=value" and "MACRO" formats
            if not define.startswith('-D'):
                clang_args.append(f"-D{define}")
            else:
                clang_args.append(define)
        logger.info(f"Added {len(args.defines)} preprocessor macro definitions from -D/--define")

    analysis_type = "Scoped AST" if args.scoped else "AST"
    # Default to in-process, unless --use-subprocess is specified
    use_subprocess = args.use_subprocess and not args.in_process
    process_mode = "out-of-process" if use_subprocess else "in-process"
    logger.info(f"Starting {analysis_type} analysis using {process_mode} generation...")
    logger.info(f"Repository: {args.repo}")
    logger.info(f"Exclude directories: {exclude_dirs}")
    logger.info(f"Clang args: {clang_args}")
    logger.info(f"Process mode: {process_mode}")
    if args.config:
        logger.info(f"Config file: {args.config}")
    if args.scoped:
        logger.info(f"Target files: {[str(f.relative_to(args.repo)) for f in target_files]}")
        logger.info(f"Max dependency depth: {args.max_dependency_depth}")

    try:
        # Create output directory for language-specific artifacts
        args.out_dir.mkdir(parents=True, exist_ok=True)
        
        if args.scoped:
            # Run scoped analysis
            ASTUtil.run_scoped_analysis(
                repo=args.repo,
                target_files=target_files,
                ignore_dirs=exclude_dirs,
                clang_args=clang_args,
                out_dir=args.out_dir,
                merged_symbols_out=args.merged_functions,
                merged_graph_out=args.merged_graph,
                merged_data_types_out=args.merged_data_types_out,
                max_dependency_depth=args.max_dependency_depth,
                use_subprocess=use_subprocess
            )
        else:
            # Run full analysis with enhanced parameters
            ASTUtil.run_full_analysis(
                repo=args.repo,
                include_dirs=include_dirs,
                ignore_dirs=exclude_dirs,
                clang_args=clang_args,
                out_dir=args.out_dir,
                merged_symbols_out=args.merged_functions,
                merged_graph_out=args.merged_graph,
                merged_data_types_out=args.merged_data_types_out,
                use_subprocess=use_subprocess,
                max_dependency_depth=args.max_dependency_depth,
                expand_macros=args.expand_macros
            )

        logger.info(f"{analysis_type} analysis completed successfully!")
        logger.info("Generated files:")
        logger.info(f"  - Language artifacts directory: {args.out_dir}")
        logger.info(f"  - Merged functions: {args.merged_functions}")
        logger.info(f"  - Merged call graph: {args.merged_graph}")
        logger.info(f"  - Merged data types: {args.merged_data_types_out}")

    except Exception as e:
        logger.error(f"{analysis_type} analysis failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()