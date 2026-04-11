#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
# AST Process Manager - Handles out-of-process AST generation to reduce memory consumption

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Set

logger = logging.getLogger(__name__)

class ASTProcessManager:
    """
    Manages out-of-process AST generation to reduce memory consumption.
    
    This class provides the same interface as the original ast_util.py methods
    but runs AST generation in separate processes, allowing the main process
    to reclaim memory after each language analysis completes.
    """
    
    def __init__(self):
        """Initialize the AST Process Manager."""
        self.temp_dir = None
        
    def __enter__(self):
        """Context manager entry - create temporary directory for communication."""
        self.temp_dir = tempfile.mkdtemp(prefix="ast_process_")
        logger.info(f"Created temporary directory for AST process communication: {self.temp_dir}")
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup temporary directory."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            logger.info(f"Cleaned up temporary directory: {self.temp_dir}")
    
    def run_full_analysis(self,
                         repo: Path,
                         include_dirs: Set[str],
                         ignore_dirs: Set[str],
                         clang_args: List[str],
                         out_dir: Path,
                         merged_symbols_out: Path,
                         merged_graph_out: Path,
                         merged_data_types_out: Path,
                         max_dependency_depth: int = 3,
                         expand_macros: bool = True) -> None:
        """
        Run complete AST analysis for all languages in a separate process.
        
        This method maintains the same interface as ASTUtil.run_full_analysis()
        but executes in a separate process to reduce memory consumption.
        
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
        """
        return self._run_full_analysis_out_of_process(
            repo=repo,
            include_dirs=include_dirs,
            ignore_dirs=ignore_dirs,
            clang_args=clang_args,
            out_dir=out_dir,
            merged_symbols_out=merged_symbols_out,
            merged_graph_out=merged_graph_out,
            merged_data_types_out=merged_data_types_out,
            max_dependency_depth=max_dependency_depth,
            expand_macros=expand_macros
        )
    
    def run_scoped_analysis(self,
                           repo: Path,
                           target_files: List[Path],
                           ignore_dirs: Set[str],
                           clang_args: List[str],
                           out_dir: Path,
                           merged_symbols_out: Path,
                           merged_graph_out: Path,
                           merged_data_types_out: Path,
                           max_dependency_depth: int = 3) -> None:
        """
        Run scoped AST analysis in a separate process.
        
        This method maintains the same interface as ASTUtil.run_scoped_analysis()
        """
        return self._run_scoped_analysis_out_of_process(
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
    
    def _run_full_analysis_out_of_process(self, **kwargs) -> None:
        """Execute full AST analysis in a separate process."""
        logger.info("Starting out-of-process AST generation (full analysis)")
        
        # Handle include_dirs with dependency discovery
        include_dirs = kwargs["include_dirs"] or []
        if include_dirs:
            logger.info(f"Running analysis with include directories: {include_dirs}")
            
            # Find initial files in include directories
            from .ast_util import ASTUtil
            initial_files = ASTUtil._find_files_in_include_dirs(
                kwargs["repo"], include_dirs, kwargs["ignore_dirs"]
            )
            
            if not initial_files:
                logger.warning(f"No files found in include directories: {include_dirs}")
                return
            
            # Discover dependencies
            from .scoped_ast_util import ScopedASTUtil
            all_files = ScopedASTUtil.find_file_dependencies(
                repo_root=kwargs["repo"],
                target_files=initial_files,
                ignore_dirs=kwargs["ignore_dirs"],
                max_depth=kwargs.get("max_dependency_depth", 3)
            )
            
            logger.info(f"Analysis will include {len(all_files)} files (initial + dependencies)")
            
            # Run scoped analysis with discovered files
            ScopedASTUtil.run_scoped_analysis(
                repo_root=kwargs["repo"],
                target_files=list(all_files),
                ignore_dirs=kwargs["ignore_dirs"],
                clang_args=kwargs["clang_args"],
                out_dir=kwargs["out_dir"],
                merged_symbols_out=kwargs["merged_symbols_out"],
                merged_graph_out=kwargs["merged_graph_out"],
                merged_data_types_out=kwargs["merged_data_types_out"],
                max_dependency_depth=0  # Dependencies already discovered
            )
            return
        
        # Original full repository analysis when include_dirs is empty
        # Create communication files
        config_file = os.path.join(self.temp_dir, "ast_config.json")
        result_file = os.path.join(self.temp_dir, "ast_result.json")
        
        # Prepare configuration for the subprocess
        config = {
            "operation": "full_analysis",
            "repo": str(kwargs["repo"]),
            "include_dirs": list(kwargs["include_dirs"] or []),
            "ignore_dirs": list(kwargs["ignore_dirs"]),
            "clang_args": kwargs["clang_args"],
            "out_dir": str(kwargs["out_dir"]),
            "merged_symbols_out": str(kwargs["merged_symbols_out"]),
            "merged_graph_out": str(kwargs["merged_graph_out"]),
            "merged_data_types_out": str(kwargs["merged_data_types_out"]),
            "max_dependency_depth": kwargs.get("max_dependency_depth", 3),
            "expand_macros": kwargs.get("expand_macros", False),
            "result_file": result_file
        }
        
        # Write configuration to file
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Execute the subprocess
        success = self._execute_ast_subprocess(config_file)
        
        if not success:
            raise RuntimeError("Out-of-process AST generation failed")
        
        # Read and validate results
        self._validate_ast_results(result_file, config)
        
        logger.info("Out-of-process AST generation completed successfully")
    
    def _run_scoped_analysis_out_of_process(self, **kwargs) -> None:
        """Execute scoped AST analysis in a separate process."""
        logger.info("Starting out-of-process AST generation (scoped analysis)")
        
        # Create communication files
        config_file = os.path.join(self.temp_dir, "ast_config.json")
        result_file = os.path.join(self.temp_dir, "ast_result.json")
        
        # Prepare configuration for the subprocess
        config = {
            "operation": "scoped_analysis",
            "repo": str(kwargs["repo"]),
            "target_files": [str(f) for f in kwargs["target_files"]],
            "ignore_dirs": list(kwargs["ignore_dirs"]),
            "clang_args": kwargs["clang_args"],
            "out_dir": str(kwargs["out_dir"]),
            "merged_symbols_out": str(kwargs["merged_symbols_out"]),
            "merged_graph_out": str(kwargs["merged_graph_out"]),
            "merged_data_types_out": str(kwargs["merged_data_types_out"]),
            "max_dependency_depth": kwargs["max_dependency_depth"],
            "result_file": result_file
        }
        
        # Write configuration to file
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Execute the subprocess
        success = self._execute_ast_subprocess(config_file)
        
        if not success:
            raise RuntimeError("Out-of-process AST generation failed")
        
        # Read and validate results
        self._validate_ast_results(result_file, config)
        
        logger.info("Out-of-process AST generation completed successfully")
    
    def _execute_ast_subprocess(self, config_file: str) -> bool:
        """
        Execute the AST generation subprocess with real-time log streaming.
        
        Args:
            config_file: Path to the JSON configuration file
            
        Returns:
            bool: True if subprocess completed successfully
        """
        # Get the path to the AST worker script
        worker_script = Path(__file__).parent / "ast_worker.py"
        
        # Prepare the command
        cmd = [
            sys.executable,
            str(worker_script),
            config_file
        ]
        
        logger.info(f"Executing AST subprocess: {' '.join(cmd)}")
        
        try:
            # Start the subprocess with streaming output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout for unified streaming
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True
            )
            
            # Stream output in real-time
            stdout_lines = []
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    # Strip newline and log immediately
                    line = output.strip()
                    stdout_lines.append(line)
                    # Log with AST subprocess prefix for clarity
                    logger.info(f"AST subprocess: {line}")
            
            # Wait for process to complete and get return code
            return_code = process.wait(timeout=3600)  # 1 hour timeout
            
            if return_code != 0:
                logger.error(f"AST subprocess failed with return code {return_code}")
                # Log any remaining output
                remaining_output = process.stdout.read()
                if remaining_output:
                    for line in remaining_output.strip().split('\n'):
                        if line:
                            logger.error(f"AST subprocess (final): {line}")
                return False
            
            logger.info("AST subprocess completed successfully")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("AST subprocess timed out after 1 hour")
            # Kill the process if it's still running
            try:
                process.kill()
                process.wait()
            except:
                pass
            return False
        except Exception as e:
            logger.error(f"Failed to execute AST subprocess: {e}")
            # Kill the process if it's still running
            try:
                if 'process' in locals():
                    process.kill()
                    process.wait()
            except:
                pass
            return False
    
    def _validate_ast_results(self, result_file: str, config: Dict[str, Any]) -> None:
        """
        Validate that the AST generation produced the expected output files.
        
        Args:
            result_file: Path to the result JSON file
            config: Configuration used for AST generation
        """
        # Check if result file exists
        if not os.path.exists(result_file):
            raise RuntimeError(f"AST subprocess did not create result file: {result_file}")
        
        # Read result file
        try:
            with open(result_file, 'r') as f:
                result = json.load(f)
        except Exception as e:
            raise RuntimeError(f"Failed to read AST result file: {e}")
        
        # Check if subprocess reported success
        if not result.get("success", False):
            error_msg = result.get("error", "Unknown error")
            raise RuntimeError(f"AST subprocess reported failure: {error_msg}")
        
        # Validate that expected output files were created
        expected_files = [
            config["merged_symbols_out"],
            config["merged_graph_out"],
            config["merged_data_types_out"]
        ]
        
        missing_files = []
        for file_path in expected_files:
            if not os.path.exists(file_path):
                missing_files.append(file_path)
        
        if missing_files:
            raise RuntimeError(f"AST subprocess did not create expected files: {missing_files}")
        
        logger.info("AST result validation completed successfully")


def create_ast_process_manager() -> ASTProcessManager:
    """
    Factory function to create an AST Process Manager.
    
    Returns:
        ASTProcessManager instance
    """
    return ASTProcessManager()