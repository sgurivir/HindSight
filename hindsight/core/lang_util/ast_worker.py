#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
# AST Worker - Subprocess script for out-of-process AST generation

import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Dict, List, Any, Set

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.utils.log_util import setup_default_logging
from hindsight.utils.file_util import read_json_file, write_json_file
from hindsight.core.lang_util.ast_function_signature_util import ASTFunctionSignatureGenerator
from hindsight.core.lang_util.ast_merger import ASTMerger
from hindsight.core.lang_util.ast_util_language_helper import (
    ClangAnalysisHelper,
    SwiftAnalysisHelper,
    KotlinAnalysisHelper,
    JavaAnalysisHelper,
    GoAnalysisHelper
    # JavaScriptTypeScriptAnalysisHelper - JS/TS support disabled
)

logger = logging.getLogger(__name__)

class ASTWorker:
    """
    Worker process for AST generation.
    
    This class runs in a separate process and handles the actual AST generation
    based on JSON configuration files. It provides isolation from the main process
    to allow memory reclamation after AST operations complete.
    """
    
    def __init__(self, config_file: str):
        """
        Initialize the AST worker with configuration.
        
        Args:
            config_file: Path to JSON configuration file
        """
        self.config_file = config_file
        self.config = None
        self.result_file = None
        
    def load_config(self) -> bool:
        """
        Load configuration from JSON file.
        
        Returns:
            bool: True if configuration loaded successfully
        """
        try:
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
            
            # Validate required fields
            required_fields = [
                "operation", "repo", "ignore_dirs", "clang_args",
                "out_dir", "merged_symbols_out", "merged_graph_out",
                "merged_data_types_out", "result_file"
            ]
            
            for field in required_fields:
                if field not in self.config:
                    logger.error(f"Missing required field in config: {field}")
                    return False
            
            self.result_file = self.config["result_file"]
            logger.info(f"Loaded configuration for operation: {self.config['operation']}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load configuration from {self.config_file}: {e}")
            return False
    
    def run_analysis(self) -> bool:
        """
        Execute the AST analysis based on configuration.
        
        Returns:
            bool: True if analysis completed successfully
        """
        try:
            operation = self.config["operation"]
            
            if operation == "full_analysis":
                return self._run_full_analysis()
            elif operation == "scoped_analysis":
                return self._run_scoped_analysis()
            else:
                logger.error(f"Unknown operation: {operation}")
                return False
                
        except Exception as e:
            logger.error(f"AST analysis failed: {e}")
            traceback.print_exc()
            return False
    
    def _run_full_analysis(self) -> bool:
        """Execute full AST analysis with original in-process logic."""
        logger.info("Starting full AST analysis in worker process")
        
        try:
            # Convert string paths back to Path objects
            repo = Path(self.config["repo"])
            include_dirs = set(self.config.get("include_dirs", []))
            ignore_dirs = set(self.config["ignore_dirs"])
            clang_args = self.config["clang_args"]
            out_dir = Path(self.config["out_dir"])
            merged_symbols_out = Path(self.config["merged_symbols_out"])
            merged_graph_out = Path(self.config["merged_graph_out"])
            merged_data_types_out = Path(self.config["merged_data_types_out"])
            max_dependency_depth = self.config.get("max_dependency_depth", 3)
            expand_macros = self.config.get("expand_macros", True)
            
            # Parallel processing parameters
            use_parallel = self.config.get("use_parallel", True)
            max_workers = self.config.get("max_workers", None)
            
            # Log expand macros mode if enabled
            if expand_macros:
                logger.info("EXPAND MACROS MODE: Will build AST twice (with and without macros) and merge results")
            
            # Handle include_dirs with dependency discovery
            if include_dirs:
                logger.info(f"Running analysis with include directories: {include_dirs}")
                
                # Find initial files in include directories
                from hindsight.core.lang_util.ast_util import ASTUtil
                initial_files = ASTUtil._find_files_in_include_dirs(repo, include_dirs, ignore_dirs)
                
                if not initial_files:
                    logger.warning(f"No files found in include directories: {include_dirs}")
                    return True  # Not an error, just no files to process
                
                # Discover dependencies
                from hindsight.core.lang_util.scoped_ast_util import ScopedASTUtil
                all_files = ScopedASTUtil.find_file_dependencies(
                    repo_root=repo,
                    target_files=initial_files,
                    ignore_dirs=ignore_dirs,
                    max_depth=max_dependency_depth
                )
                
                logger.info(f"Analysis will include {len(all_files)} files (initial + dependencies)")
                
                # Run scoped analysis with discovered files
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
                
                logger.info("Full AST analysis with include directories completed successfully in worker process")
                return True
            
            # Original full repository analysis when include_dirs is empty
            # Initialize libclang before any Clang operations
            ClangAnalysisHelper.initialize_libclang()
            
            # Create output directory
            out_dir.mkdir(parents=True, exist_ok=True)
            
            # Import constants for file names
            from hindsight.core.constants import (
                C_DEFINED_FUNCTIONS_FILE,
                CLANG_NESTED_CALL_GRAPH_FILE,
                SWIFT_DEFINED_FUNCTIONS_FILE,
                SWIFT_CALL_GRAPH_FILE,
                KOTLIN_DEFINED_FUNCTIONS_FILE,
                KOTLIN_CALL_GRAPH_FILE,
                JAVA_DEFINED_FUNCTIONS_FILE,
                JAVA_CALL_GRAPH_FILE,
                GO_DEFINED_FUNCTIONS_FILE,
                GO_CALL_GRAPH_FILE,
                CLANG_DEFINED_CLASSES_FILE,
                SWIFT_DEFINED_CLASSES_FILE,
                KOTLIN_DEFINED_CLASSES_FILE,
                JAVA_DEFINED_CLASSES_FILE,
                GO_DEFINED_CLASSES_FILE
            )
            
            # JS/TS constants - kept for reference but JS/TS analysis is disabled
            # JS_TS_DEFINED_FUNCTIONS_FILE = "js_ts_functions.json"
            # JS_TS_CALL_GRAPH_FILE = "js_ts_nested_callgraph.json"
            # JS_TS_DEFINED_CLASSES_FILE = "js_ts_defined_classes.json"

            # Determine artifact directory based on context
            # Use /tmp/ when ast_util is run directly, code_insights/ when run through analyzers
            if out_dir.parent.name == "code_insights" or "code_insights" in str(out_dir):
                artifact_dir = out_dir.parent / "code_insights" if out_dir.parent.name != "code_insights" else out_dir.parent
            else:
                artifact_dir = Path("/tmp")
            
            # Calculate language-specific output paths using constants
            clang_defined_out = artifact_dir / C_DEFINED_FUNCTIONS_FILE
            clang_nested_out = artifact_dir / CLANG_NESTED_CALL_GRAPH_FILE
            clang_data_types_out = artifact_dir / CLANG_DEFINED_CLASSES_FILE

            swift_symbols_out = artifact_dir / SWIFT_DEFINED_FUNCTIONS_FILE
            swift_graph_out = artifact_dir / SWIFT_CALL_GRAPH_FILE
            swift_data_types_out = artifact_dir / SWIFT_DEFINED_CLASSES_FILE

            kotlin_symbols_out = artifact_dir / KOTLIN_DEFINED_FUNCTIONS_FILE
            kotlin_graph_out = artifact_dir / KOTLIN_CALL_GRAPH_FILE
            kotlin_data_types_out = artifact_dir / KOTLIN_DEFINED_CLASSES_FILE

            java_symbols_out = artifact_dir / JAVA_DEFINED_FUNCTIONS_FILE
            java_graph_out = artifact_dir / JAVA_CALL_GRAPH_FILE
            java_data_types_out = artifact_dir / JAVA_DEFINED_CLASSES_FILE

            go_symbols_out = artifact_dir / GO_DEFINED_FUNCTIONS_FILE
            go_graph_out = artifact_dir / GO_CALL_GRAPH_FILE
            go_data_types_out = artifact_dir / GO_DEFINED_CLASSES_FILE

            # JS/TS output paths - disabled
            # js_ts_symbols_out = artifact_dir / JS_TS_DEFINED_FUNCTIONS_FILE
            # js_ts_graph_out = artifact_dir / JS_TS_CALL_GRAPH_FILE
            # js_ts_data_types_out = artifact_dir / JS_TS_DEFINED_CLASSES_FILE
            
            # Log parallel processing mode
            if use_parallel:
                logger.info(f"PARALLEL MODE: Processing files with up to {max_workers or 'auto'} workers")
            else:
                logger.info("SEQUENTIAL MODE: Processing files sequentially")
            
            # --- Clang ---
            clang_symbols, clang_graph = self._run_clang_analysis(
                repo=repo,
                ignore_dirs=ignore_dirs,
                clang_args=clang_args,
                clang_defined_out=clang_defined_out,
                clang_nested_out=clang_nested_out,
                max_call_depth=1,
                clang_classes_out=clang_data_types_out,
                expand_macros=expand_macros,
                use_parallel=use_parallel,
                max_workers=max_workers
            )

            # --- Swift ---
            swift_symbols, swift_graph = self._run_swift_analysis(
                repo=repo,
                ignore_dirs=ignore_dirs,
                swift_symbols_out=swift_symbols_out,
                swift_graph_out=swift_graph_out,
                swift_classes_out=swift_data_types_out
            )

            # --- Kotlin ---
            kotlin_symbols, kotlin_graph = self._run_kotlin_analysis(
                repo=repo,
                ignore_dirs=ignore_dirs,
                kotlin_functions_out=kotlin_symbols_out,
                kotlin_graph_out=kotlin_graph_out,
                kotlin_classes_out=kotlin_data_types_out
            )

            # --- Java ---
            java_symbols, java_graph = self._run_java_analysis(
                repo=repo,
                ignore_dirs=ignore_dirs,
                java_functions_out=java_symbols_out,
                java_graph_out=java_graph_out,
                java_classes_out=java_data_types_out
            )

            # --- Go ---
            go_symbols, go_graph = [], []
            if go_symbols_out and go_graph_out:
                go_symbols, go_graph = self._run_go_analysis(
                    repo=repo,
                    ignore_dirs=ignore_dirs,
                    go_functions_out=go_symbols_out,
                    go_graph_out=go_graph_out,
                    go_classes_out=go_data_types_out
                )

            # --- JavaScript/TypeScript --- (DISABLED)
            # JS/TS analysis is intentionally disabled - the code exists but is not called
            # js_ts_symbols, js_ts_graph = [], []
            # if js_ts_symbols_out and js_ts_graph_out:
            #     js_ts_symbols, js_ts_graph = self._run_js_ts_analysis(
            #         repo=repo,
            #         ignore_dirs=ignore_dirs,
            #         js_ts_functions_out=js_ts_symbols_out,
            #         js_ts_graph_out=js_ts_graph_out,
            #         js_ts_classes_out=js_ts_data_types_out
            #     )

            # Merge results (JS/TS excluded)
            merged_symbols = ASTMerger.merge_symbols(clang_symbols, swift_symbols, kotlin_symbols, java_symbols, go_symbols)
            merged_graph = ASTMerger.merge_call_graphs(clang_graph, swift_graph, kotlin_graph, java_graph, go_graph)

            # Write merged symbols with new schema - wrap in "function_to_location"
            logger.info(f"[+] Writing merged symbols to {merged_symbols_out}")
            
            # Convert symbols list back to function name -> locations format
            merged_functions_dict = {}
            for symbol in merged_symbols:
                func_name = symbol["name"]
                location = {
                    "file_name": symbol["context"]["file"],
                    "start": symbol["context"]["start"],
                    "end": symbol["context"]["end"]
                }
                if func_name not in merged_functions_dict:
                    merged_functions_dict[func_name] = []
                merged_functions_dict[func_name].append(location)

            # Wrap in new schema
            merged_output = {
                "function_to_location": merged_functions_dict
            }

            # Add checksums to the merged functions and write to file
            ASTFunctionSignatureGenerator.process_functions_with_checksums_and_write(
                repo, merged_output, merged_symbols_out
            )

            # Write merged call graph
            logger.info(f"[+] Writing merged call graph to {merged_graph_out}")
            write_json_file(str(merged_graph_out), merged_graph)

            # Always merge data types outputs if merged_data_types_out is provided
            # This ensures merged_defined_classes.json is generated even with only one language
            if merged_data_types_out:
                # Merge the data types (checksums are now added in individual language analysis methods)
                # JS/TS excluded from merge
                ASTMerger.merge_data_types_outputs(clang_data_types_out, swift_data_types_out, kotlin_data_types_out, java_data_types_out, go_data_types_out, merged_data_types_out, repo)

            # Add checksums to the merged call graph
            ASTFunctionSignatureGenerator.process_call_graph_file(
                repo_path=repo,
                input_file=merged_graph_out,
                data_types_file=merged_data_types_out,
                functions_file=merged_symbols_out
            )

            # Log summary
            logger.info("AST Analysis Complete!")
            logger.info(f"  Total Symbols: {len(merged_symbols)}")
            logger.info(f"  Total Graph Nodes: {len(merged_graph)}")
            logger.info(f"  Total Functions: {len(merged_symbols)}")
            
            logger.info("Full AST analysis completed successfully in worker process")
            return True
            
        except Exception as e:
            logger.error(f"Full AST analysis failed in worker process: {e}")
            traceback.print_exc()
            return False
    
    def _run_scoped_analysis(self) -> bool:
        """Execute scoped AST analysis using ScopedASTUtil."""
        logger.info("Starting scoped AST analysis in worker process")
        
        try:
            # Convert string paths back to Path objects
            repo = Path(self.config["repo"])
            target_files = [Path(f) for f in self.config["target_files"]]
            ignore_dirs = set(self.config["ignore_dirs"])
            clang_args = self.config["clang_args"]
            out_dir = Path(self.config["out_dir"])
            merged_symbols_out = Path(self.config["merged_symbols_out"])
            merged_graph_out = Path(self.config["merged_graph_out"])
            merged_data_types_out = Path(self.config["merged_data_types_out"])
            max_dependency_depth = self.config["max_dependency_depth"]
            
            # Initialize libclang before any Clang operations
            ClangAnalysisHelper.initialize_libclang()
            
            # Create output directory
            out_dir.mkdir(parents=True, exist_ok=True)
            
            # Import and use ScopedASTUtil directly
            from hindsight.core.lang_util.scoped_ast_util import ScopedASTUtil
            
            # Run the scoped analysis
            ScopedASTUtil.run_scoped_analysis(
                repo_root=repo,
                target_files=target_files,
                ignore_dirs=ignore_dirs,
                clang_args=clang_args,
                out_dir=out_dir,
                merged_symbols_out=merged_symbols_out,
                merged_graph_out=merged_graph_out,
                merged_data_types_out=merged_data_types_out,
                max_dependency_depth=max_dependency_depth
            )
            
            logger.info("Scoped AST analysis completed successfully in worker process")
            return True
            
        except Exception as e:
            logger.error(f"Scoped AST analysis failed in worker process: {e}")
            traceback.print_exc()
            return False
    
    def _run_clang_analysis(self, repo, ignore_dirs, clang_args, clang_defined_out, clang_nested_out, max_call_depth, clang_classes_out, expand_macros=True, use_parallel=True, max_workers=None):
        """Run CASTUtil analysis and return clang_symbols and call graph data.
        
        Args:
            repo: Path to the repository to analyze
            ignore_dirs: Set of directory names to ignore during analysis
            clang_args: List of arguments to pass to clang
            clang_defined_out: Path for defined functions output file
            clang_nested_out: Path for nested call graph output file
            max_call_depth: Maximum depth for nested call graph
            clang_classes_out: Path for data types output file
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: True)
            use_parallel: If True, use parallel processing for AST generation. (default: True)
            max_workers: Maximum number of worker processes for parallel processing. (default: None, auto-detect)
        
        Returns:
            Tuple of (clang_symbols, clang_graph)
        """
        clang_registry, clang_graph = ClangAnalysisHelper.run_clang_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            clang_args=clang_args,
            clang_defined_out=clang_defined_out,
            clang_nested_out=clang_nested_out,
            max_call_depth=max_call_depth,
            clang_classes_out=clang_classes_out,
            expand_macros=expand_macros,
            use_parallel=use_parallel,
            max_workers=max_workers
        )
        
        # Convert Clang registry to symbols format
        clang_symbols = ClangAnalysisHelper.convert_clang_registry_to_symbols(clang_registry)
        
        return clang_symbols, clang_graph

    def _run_swift_analysis(self, repo, ignore_dirs, swift_symbols_out, swift_graph_out, swift_classes_out):
        """Run SwiftASTUtil analysis and return symbols and call graph data."""
        return SwiftAnalysisHelper.run_swift_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            swift_symbols_out=swift_symbols_out,
            swift_graph_out=swift_graph_out,
            swift_classes_out=swift_classes_out
        )

    def _run_kotlin_analysis(self, repo, ignore_dirs, kotlin_functions_out, kotlin_graph_out, kotlin_classes_out):
        """Run KotlinASTUtil analysis and return symbols and call graph data."""
        return KotlinAnalysisHelper.run_kotlin_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            kotlin_functions_out=kotlin_functions_out,
            kotlin_graph_out=kotlin_graph_out,
            kotlin_classes_out=kotlin_classes_out
        )

    def _run_java_analysis(self, repo, ignore_dirs, java_functions_out, java_graph_out, java_classes_out):
        """Run JavaASTUtil analysis and return symbols and call graph data."""
        return JavaAnalysisHelper.run_java_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            java_functions_out=java_functions_out,
            java_graph_out=java_graph_out,
            java_classes_out=java_classes_out
        )

    def _run_go_analysis(self, repo, ignore_dirs, go_functions_out, go_graph_out, go_classes_out):
        """Run GoASTUtil analysis and return symbols and call graph data."""
        return GoAnalysisHelper.run_go_analysis(
            repo=repo,
            ignore_dirs=ignore_dirs,
            go_functions_out=go_functions_out,
            go_graph_out=go_graph_out,
            go_classes_out=go_classes_out
        )

    # JS/TS analysis method - disabled but kept for reference
    # def _run_js_ts_analysis(self, repo, ignore_dirs, js_ts_functions_out, js_ts_graph_out, js_ts_classes_out):
    #     """Run JavaScriptTypeScriptASTUtil analysis and return symbols and call graph data."""
    #     return JavaScriptTypeScriptAnalysisHelper.run_js_ts_analysis(
    #         repo=repo,
    #         ignore_dirs=ignore_dirs,
    #         js_ts_functions_out=js_ts_functions_out,
    #         js_ts_graph_out=js_ts_graph_out,
    #         js_ts_classes_out=js_ts_classes_out
    #     )

    def write_result(self, success: bool, error_message: str = None) -> None:
        """
        Write result to the result file.
        
        Args:
            success: Whether the operation was successful
            error_message: Error message if operation failed
        """
        try:
            result = {
                "success": success,
                "operation": self.config["operation"] if self.config else "unknown",
                "timestamp": str(Path(__file__).stat().st_mtime)  # Simple timestamp
            }
            
            if not success and error_message:
                result["error"] = error_message
            
            with open(self.result_file, 'w') as f:
                json.dump(result, f, indent=2)
                
            logger.info(f"Result written to {self.result_file}: success={success}")
            
        except Exception as e:
            logger.error(f"Failed to write result file: {e}")


def main():
    """Main entry point for the AST worker process."""
    if len(sys.argv) != 2:
        print("Usage: ast_worker.py <config_file>", file=sys.stderr)
        sys.exit(1)
    
    config_file = sys.argv[1]
    
    # Setup logging for the worker process
    setup_default_logging()
    logger.info(f"AST Worker starting with config: {config_file}")
    
    # Create worker instance
    worker = ASTWorker(config_file)
    
    # Load configuration
    if not worker.load_config():
        logger.error("Failed to load configuration")
        if worker.result_file:
            worker.write_result(False, "Failed to load configuration")
        sys.exit(1)
    
    # Run analysis
    success = worker.run_analysis()
    
    # Write result
    error_message = None if success else "AST analysis failed"
    worker.write_result(success, error_message)
    
    # Exit with appropriate code
    exit_code = 0 if success else 1
    logger.info(f"AST Worker exiting with code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()