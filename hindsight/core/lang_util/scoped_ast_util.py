
#!/usr/bin/env python3
# Created by Sridhar Gurivireddy on 12/02/2025
# scoped_ast_util.py
# Provides scoped AST analysis functionality for a specific set of files and their dependencies

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Set, Union, Tuple

# Add project root to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.utils.log_util import setup_default_logging
from hindsight.utils.file_util import read_json_file, write_json_file
from hindsight.core.lang_util.ast_function_signature_util import ASTFunctionSignatureGenerator
from hindsight.core.lang_util.ast_merger import ASTMerger
from hindsight.core.lang_util.ast_util_language_helper import ClangAnalysisHelper
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
    # JS_TS_DEFINED_FUNCTIONS_FILE,  # JS/TS support disabled
    # JS_TS_CALL_GRAPH_FILE,  # JS/TS support disabled
    CLANG_DEFINED_CLASSES_FILE,
    SWIFT_DEFINED_CLASSES_FILE,
    KOTLIN_DEFINED_CLASSES_FILE,
    JAVA_DEFINED_CLASSES_FILE,
    GO_DEFINED_CLASSES_FILE
    # JS_TS_DEFINED_CLASSES_FILE  # JS/TS support disabled
)
from hindsight.core.lang_util.cast_util import CASTUtil
from hindsight.core.lang_util.swift_ast_util import SwiftASTUtil
from hindsight.core.lang_util.kotlin_ast_util import KotlinASTUtil
from hindsight.core.lang_util.java_ast_util import JavaASTUtil
from hindsight.core.lang_util.go_util import GoASTUtil
# from hindsight.core.lang_util.javascript_typescript_ast_util import JavaScriptTypeScriptASTUtil  # JS/TS support disabled


logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for performance
INCLUDE_RE = re.compile(r'#include\s*[<"]([^>"]+)[>"]')
SWIFT_IMPORT_RE = re.compile(r'import\s+(\w+)')
JAVA_KT_IMPORT_RE = re.compile(r'import\s+([a-zA-Z_][a-zA-Z0-9_.]*)')
GO_IMPORT_RE = re.compile(r'import\s+(?:"([^"]+)"|`([^`]+)`)')

class ScopedASTUtil:
    """
    Provides scoped AST analysis functionality that analyzes only specified files
    and their dependencies up to a maximum depth. This is useful for focused
    analysis of specific parts of a codebase without scanning the entire repository.
    """

    @staticmethod
    def _build_file_index(repo_root: Path, ignore_dirs: Set[str]) -> Dict[str, List[Path]]:
        """
        Build a one-time file index mapping filenames to their full paths.
        This replaces repeated os.walk/rglob calls for better performance.
        
        Args:
            repo_root: Root directory of the repository
            ignore_dirs: Directories to ignore during indexing
            
        Returns:
            Dictionary mapping filename to list of full paths
        """
        logger.info("Building file index for dependency resolution...")
        
        file_index = {}
        total_files_scanned = 0
        ignored_files_count = 0
        
        for path in repo_root.rglob("*"):
            total_files_scanned += 1
            
            # Skip ignored directories
            if any(p.name in ignore_dirs for p in path.parents):
                ignored_files_count += 1
                continue
            if path.is_file():
                filename = path.name
                if filename not in file_index:
                    file_index[filename] = []
                file_index[filename].append(path)
        
        logger.info(f"Built file index with {len(file_index)} unique filenames")
        
        return file_index

    @staticmethod
    def find_file_dependencies(repo_root: Path,
                              target_files: List[Path],
                              ignore_dirs: Set[str],
                              max_depth: int = 3) -> Set[Path]:
        """
        Find dependencies of target files by analyzing imports, includes, and references.
        
        Args:
            repo_root: Root directory of the repository
            target_files: List of files to analyze
            ignore_dirs: Directories to ignore during dependency discovery
            max_depth: Maximum depth to traverse for dependencies
            
        Returns:
            Set of all files including target files and their dependencies
        """
        logger.info(f"Finding dependencies for {len(target_files)} target files with max depth {max_depth}")
        
        # Build file index once for all dependency extraction
        file_index = ScopedASTUtil._build_file_index(repo_root, ignore_dirs)
        
        all_files = set(target_files)
        current_level = set(target_files)
        
        for depth in range(max_depth):
            if not current_level:
                break
                
            logger.info(f"Analyzing dependency level {depth + 1} with {len(current_level)} files")
            next_level = set()
            
            for file_path in current_level:
                dependencies = ScopedASTUtil._extract_file_dependencies(
                    repo_root, file_path, ignore_dirs, file_index
                )
                new_deps = dependencies - all_files
                
                next_level.update(new_deps)
                all_files.update(new_deps)
            
            current_level = next_level
            logger.info(f"Found {len(next_level)} new dependencies at level {depth + 1}")
        logger.info(f"Total files after dependency analysis: {len(all_files)}")
        
        
        return all_files

    @staticmethod
    def _extract_file_dependencies(repo_root: Path,
                                 file_path: Path,
                                 ignore_dirs: Set[str],
                                 file_index: Dict[str, List[Path]]) -> Set[Path]:
        """
        Extract dependencies from a single file by analyzing imports/includes.
        
        Args:
            repo_root: Root directory of the repository
            file_path: File to analyze for dependencies
            ignore_dirs: Directories to ignore
            file_index: Pre-built file index for fast lookups
            
        Returns:
            Set of dependency file paths
        """
        dependencies = set()
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"Could not read file {file_path}: {e}")
            return dependencies
        
        # Extract dependencies based on file type
        suffix = file_path.suffix.lower()
        
        if suffix in ['.c', '.cpp', '.cc', '.cxx', '.h', '.hpp', '.m', '.mm']:
            dependencies.update(ScopedASTUtil._extract_c_dependencies(
                repo_root, file_path, content, ignore_dirs, file_index
            ))
        elif suffix == '.swift':
            dependencies.update(ScopedASTUtil._extract_swift_dependencies(
                repo_root, file_path, content, ignore_dirs, file_index
            ))
        elif suffix in ['.java', '.kt']:
            dependencies.update(ScopedASTUtil._extract_java_kotlin_dependencies(
                repo_root, file_path, content, ignore_dirs, file_index
            ))
        elif suffix == '.go':
            dependencies.update(ScopedASTUtil._extract_go_dependencies(
                repo_root, file_path, content, ignore_dirs, file_index
            ))
        # JS/TS dependency extraction disabled
        # elif suffix in ['.js', '.jsx', '.ts', '.tsx']:
        #     dependencies.update(ScopedASTUtil._extract_js_ts_dependencies(
        #         repo_root, file_path, content, ignore_dirs, file_index
        #     ))
        
        return dependencies

    @staticmethod
    def _extract_c_dependencies(repo_root: Path,
                              file_path: Path,
                              content: str,
                              ignore_dirs: Set[str],
                              file_index: Dict[str, List[Path]]) -> Set[Path]:
        """Extract C/C++/Objective-C dependencies from #include statements."""
        dependencies = set()
        
        # Find all #include statements using pre-compiled regex
        includes = INCLUDE_RE.findall(content)
        
        for include in includes:
            # Try to resolve relative to current file directory
            current_dir = file_path.parent
            potential_path = current_dir / include
            
            if potential_path.exists() and ScopedASTUtil._is_path_allowed(potential_path, repo_root, ignore_dirs):
                dependencies.add(potential_path.resolve())
                continue
            
            # Use file index for fast lookup instead of os.walk
            candidates = file_index.get(include, [])
            
            for candidate in candidates:
                if ScopedASTUtil._is_path_allowed(candidate, repo_root, ignore_dirs):
                    dependencies.add(candidate.resolve())
                    break
        
        return dependencies

    @staticmethod
    def _extract_swift_dependencies(repo_root: Path,
                                  file_path: Path,
                                  content: str,
                                  ignore_dirs: Set[str],
                                  file_index: Dict[str, List[Path]]) -> Set[Path]:
        """Extract Swift dependencies from import statements."""
        dependencies = set()
        
        # Find import statements using pre-compiled regex
        imports = SWIFT_IMPORT_RE.findall(content)
        
        for import_name in imports:
            # Look for Swift files with similar names using file index
            swift_filename = f"{import_name}.swift"
            for candidate in file_index.get(swift_filename, []):
                if ScopedASTUtil._is_path_allowed(candidate, repo_root, ignore_dirs):
                    dependencies.add(candidate.resolve())
        
        return dependencies

    @staticmethod
    def _extract_java_kotlin_dependencies(repo_root: Path,
                                        file_path: Path,
                                        content: str,
                                        ignore_dirs: Set[str],
                                        file_index: Dict[str, List[Path]]) -> Set[Path]:
        """Extract Java/Kotlin dependencies from import statements."""
        dependencies = set()
        
        # Find import statements using pre-compiled regex
        imports = JAVA_KT_IMPORT_RE.findall(content)
        
        for import_stmt in imports:
            # Convert package.ClassName to file path
            parts = import_stmt.split('.')
            if len(parts) > 1:
                class_name = parts[-1]
                
                # Look for Java/Kotlin files with this class name using file index
                for ext in ['.java', '.kt']:
                    class_filename = f"{class_name}{ext}"
                    for candidate in file_index.get(class_filename, []):
                        if ScopedASTUtil._is_path_allowed(candidate, repo_root, ignore_dirs):
                            dependencies.add(candidate.resolve())
        
        return dependencies

    @staticmethod
    def _extract_go_dependencies(repo_root: Path,
                               file_path: Path,
                               content: str,
                               ignore_dirs: Set[str],
                               file_index: Dict[str, List[Path]]) -> Set[Path]:
        """Extract Go dependencies from import statements."""
        dependencies = set()
        
        # Find import statements using pre-compiled regex
        imports = GO_IMPORT_RE.findall(content)
        
        for import_tuple in imports:
            import_path = import_tuple[0] or import_tuple[1]
            
            # For local imports (relative paths), try to resolve them
            if import_path.startswith('.'):
                current_dir = file_path.parent
                potential_path = current_dir / import_path
                
                if potential_path.exists() and potential_path.is_dir():
                    # Look for Go files in the directory using file index
                    # Since we need files in a specific directory, we still need to check paths
                    for filename, paths in file_index.items():
                        if filename.endswith('.go'):
                            for go_file in paths:
                                if go_file.parent == potential_path and ScopedASTUtil._is_path_allowed(go_file, repo_root, ignore_dirs):
                                    dependencies.add(go_file.resolve())
        
        return dependencies

    # JS/TS dependency extraction method - disabled but kept for reference
    # @staticmethod
    # def _extract_js_ts_dependencies(repo_root: Path,
    #                                file_path: Path,
    #                                content: str,
    #                                ignore_dirs: Set[str],
    #                                file_index: Dict[str, List[Path]]) -> Set[Path]:
    #     """Extract JavaScript/TypeScript dependencies from import statements."""
    #     dependencies = set()
    #
    #     # Find import statements using regex patterns
    #     import_patterns = [
    #         r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',  # import ... from 'module'
    #         r'import\s+[\'"]([^\'"]+)[\'"]',               # import 'module'
    #         r'require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)',   # require('module')
    #     ]
    #
    #     for pattern in import_patterns:
    #         matches = re.findall(pattern, content)
    #         for import_path in matches:
    #             # Skip external modules (those without relative paths)
    #             if not import_path.startswith('.'):
    #                 continue
    #
    #             # Resolve relative imports
    #             current_dir = file_path.parent
    #             potential_path = current_dir / import_path
    #
    #             # Try different extensions for the imported file
    #             for ext in ['.js', '.jsx', '.ts', '.tsx', '.json']:
    #                 test_path = potential_path.with_suffix(ext)
    #                 if test_path.exists() and ScopedASTUtil._is_path_allowed(test_path, repo_root, ignore_dirs):
    #                     dependencies.add(test_path.resolve())
    #                     break
    #
    #                 # Also try with index files
    #                 index_path = potential_path / f"index{ext}"
    #                 if index_path.exists() and ScopedASTUtil._is_path_allowed(index_path, repo_root, ignore_dirs):
    #                     dependencies.add(index_path.resolve())
    #                     break
    #
    #     return dependencies

    @staticmethod
    def _is_path_allowed(path: Path, repo_root: Path, ignore_dirs: Set[str]) -> bool:
        """Check if a path is allowed (not in ignored directories and within repo)."""
        try:
            # Check if path is within repository
            path.relative_to(repo_root)
            
            # Check if any parent directory is in ignore list
            for parent in path.parents:
                if parent.name in ignore_dirs:
                    return False
            
            return True
        except ValueError:
            # Path is outside repository
            return False

    @staticmethod
    def run_scoped_analysis(repo_root: Path,
                          target_files: List[Path],
                          ignore_dirs: Set[str],
                          clang_args: List[str],
                          out_dir: Path,
                          merged_symbols_out: Path,
                          merged_graph_out: Path,
                          merged_data_types_out: Path,
                          max_dependency_depth: int = 3,
                          use_subprocess: bool = False,
                          use_parallel: bool = True,
                          max_workers: int = None) -> None:
        """
        Run scoped analysis for specified files and their dependencies.
        
        Args:
            repo_root: Path to the repository to analyze
            target_files: List of specific files to analyze
            ignore_dirs: Set of directory names to ignore during analysis
            clang_args: List of arguments to pass to clang
            out_dir: Output directory where language-specific artifacts will be stored
            merged_symbols_out: Path for merged symbols output file
            merged_graph_out: Path for merged call graph output file
            merged_data_types_out: Path for merged data types output file
            max_dependency_depth: Maximum depth for dependency discovery
            use_subprocess: Whether to run AST generation in subprocess (default: False for in-process)
            use_parallel: If True, use parallel processing for AST generation. (default: True)
            max_workers: Maximum number of worker processes for parallel processing. (default: None, auto-detect)
        """
        logger.info(f"Starting scoped AST analysis for {len(target_files)} target files")
        
        # Find all files to analyze (target files + dependencies)
        all_files = ScopedASTUtil.find_file_dependencies(
            repo_root, target_files, ignore_dirs, max_dependency_depth
        )
        
        # Filter files by language and create language-specific file lists
        clang_files = []
        swift_files = []
        kotlin_files = []
        java_files = []
        go_files = []
        # js_ts_files = []  # JS/TS support disabled
        
        for file_path in all_files:
            suffix = file_path.suffix.lower()
            if suffix in ['.c', '.cpp', '.cc', '.cxx', '.h', '.hpp', '.m', '.mm']:
                clang_files.append(file_path)
            elif suffix == '.swift':
                swift_files.append(file_path)
            elif suffix == '.kt':
                kotlin_files.append(file_path)
            elif suffix == '.java':
                java_files.append(file_path)
            elif suffix == '.go':
                go_files.append(file_path)
            # JS/TS file filtering disabled
            # elif suffix in ['.js', '.jsx', '.ts', '.tsx']:
            #     js_ts_files.append(file_path)
        
        logger.info(f"Scoped analysis will process:")
        logger.info(f"  - {len(clang_files)} C/C++/Objective-C files")
        logger.info(f"  - {len(swift_files)} Swift files")
        logger.info(f"  - {len(kotlin_files)} Kotlin files")
        logger.info(f"  - {len(java_files)} Java files")
        logger.info(f"  - {len(go_files)} Go files")
        # logger.info(f"  - {len(js_ts_files)} JavaScript/TypeScript files")  # JS/TS support disabled
        

        # Determine artifact directory - use the provided out_dir
        artifact_dir = out_dir
        
        # Calculate language-specific output paths using constants
        clang_defined_out = artifact_dir / f"scoped_{C_DEFINED_FUNCTIONS_FILE}"
        clang_nested_out = artifact_dir / f"scoped_{CLANG_NESTED_CALL_GRAPH_FILE}"
        clang_data_types_out = artifact_dir / f"scoped_{CLANG_DEFINED_CLASSES_FILE}"

        swift_symbols_out = artifact_dir / f"scoped_{SWIFT_DEFINED_FUNCTIONS_FILE}"
        swift_graph_out = artifact_dir / f"scoped_{SWIFT_CALL_GRAPH_FILE}"
        swift_data_types_out = artifact_dir / f"scoped_{SWIFT_DEFINED_CLASSES_FILE}"

        kotlin_symbols_out = artifact_dir / f"scoped_{KOTLIN_DEFINED_FUNCTIONS_FILE}"
        kotlin_graph_out = artifact_dir / f"scoped_{KOTLIN_CALL_GRAPH_FILE}"
        kotlin_data_types_out = artifact_dir / f"scoped_{KOTLIN_DEFINED_CLASSES_FILE}"

        java_symbols_out = artifact_dir / f"scoped_{JAVA_DEFINED_FUNCTIONS_FILE}"
        java_graph_out = artifact_dir / f"scoped_{JAVA_CALL_GRAPH_FILE}"
        java_data_types_out = artifact_dir / f"scoped_{JAVA_DEFINED_CLASSES_FILE}"

        go_symbols_out = artifact_dir / f"scoped_{GO_DEFINED_FUNCTIONS_FILE}"
        go_graph_out = artifact_dir / f"scoped_{GO_CALL_GRAPH_FILE}"
        go_data_types_out = artifact_dir / f"scoped_{GO_DEFINED_CLASSES_FILE}"

        # JS/TS output paths disabled
        # js_ts_symbols_out = artifact_dir / f"scoped_{JS_TS_DEFINED_FUNCTIONS_FILE}"
        # js_ts_graph_out = artifact_dir / f"scoped_{JS_TS_CALL_GRAPH_FILE}"
        # js_ts_data_types_out = artifact_dir / f"scoped_{JS_TS_DEFINED_CLASSES_FILE}"
        
        # Log parallel processing mode
        if use_parallel:
            logger.info(f"PARALLEL MODE: Processing files with up to {max_workers or 'auto'} workers")
        else:
            logger.info("SEQUENTIAL MODE: Processing files sequentially")
        
        # Run language-specific analyses with scoped file lists
        clang_symbols, clang_graph = [], []
        if clang_files:
            clang_symbols, clang_graph = ScopedASTUtil._run_scoped_clang_analysis(
                repo_root=repo_root,
                source_files=clang_files,
                ignore_dirs=ignore_dirs,
                clang_args=clang_args,
                clang_defined_out=clang_defined_out,
                clang_nested_out=clang_nested_out,
                clang_classes_out=clang_data_types_out,
                use_parallel=use_parallel,
                max_workers=max_workers
            )

        swift_symbols, swift_graph = [], []
        if swift_files:
            swift_symbols, swift_graph = ScopedASTUtil._run_scoped_swift_analysis(
                repo_root=repo_root,
                source_files=swift_files,
                ignore_dirs=ignore_dirs,
                swift_symbols_out=swift_symbols_out,
                swift_graph_out=swift_graph_out,
                swift_classes_out=swift_data_types_out
            )

        kotlin_symbols, kotlin_graph = [], []
        if kotlin_files:
            kotlin_symbols, kotlin_graph = ScopedASTUtil._run_scoped_kotlin_analysis(
                repo_root=repo_root,
                source_files=kotlin_files,
                ignore_dirs=ignore_dirs,
                kotlin_functions_out=kotlin_symbols_out,
                kotlin_graph_out=kotlin_graph_out,
                kotlin_classes_out=kotlin_data_types_out
            )

        java_symbols, java_graph = [], []
        if java_files:
            java_symbols, java_graph = ScopedASTUtil._run_scoped_java_analysis(
                repo_root=repo_root,
                source_files=java_files,
                ignore_dirs=ignore_dirs,
                java_functions_out=java_symbols_out,
                java_graph_out=java_graph_out,
                java_classes_out=java_data_types_out
            )

        go_symbols, go_graph = [], []
        if go_files:
            go_symbols, go_graph = ScopedASTUtil._run_scoped_go_analysis(
                repo_root=repo_root,
                source_files=go_files,
                ignore_dirs=ignore_dirs,
                go_functions_out=go_symbols_out,
                go_graph_out=go_graph_out,
                go_classes_out=go_data_types_out
            )

        # JS/TS analysis disabled
        # js_ts_symbols, js_ts_graph = [], []
        # if js_ts_files:
        #     js_ts_symbols, js_ts_graph = ScopedASTUtil._run_scoped_js_ts_analysis(
        #         repo_root=repo_root,
        #         source_files=js_ts_files,
        #         ignore_dirs=ignore_dirs,
        #         js_ts_functions_out=js_ts_symbols_out,
        #         js_ts_graph_out=js_ts_graph_out,
        #         js_ts_classes_out=js_ts_data_types_out
        #     )

        # Merge results (JS/TS excluded)
        merged_symbols = ASTMerger.merge_symbols(clang_symbols, swift_symbols, kotlin_symbols, java_symbols, go_symbols)
        merged_graph = ASTMerger.merge_call_graphs(clang_graph, swift_graph, kotlin_graph, java_graph, go_graph)

        # Write merged symbols with new schema - wrap in "function_to_location"
        logger.info(f"[+] Writing scoped merged symbols to {merged_symbols_out}")
        
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
            repo_root, merged_output, merged_symbols_out
        )

        # Write merged call graph
        logger.info(f"[+] Writing scoped merged call graph to {merged_graph_out}")
        write_json_file(str(merged_graph_out), merged_graph)

        # Merge data types outputs if merged_data_types_out is provided
        # JS/TS excluded from merge
        if merged_data_types_out:
            ASTMerger.merge_data_types_outputs(
                clang_data_types_out, swift_data_types_out, kotlin_data_types_out,
                java_data_types_out, go_data_types_out, merged_data_types_out, repo_root
            )

        # Add checksums to the merged call graph
        ASTFunctionSignatureGenerator.process_call_graph_file(
            repo_path=repo_root,
            input_file=merged_graph_out,
            data_types_file=merged_data_types_out,
            functions_file=merged_symbols_out
        )

        # Log summary
        logger.info("Scoped AST Analysis Complete!")
        logger.info(f"  Target Files: {len(target_files)}")
        logger.info(f"  Total Files Analyzed: {len(all_files)}")
        logger.info(f"  Total Symbols: {len(merged_symbols)}")
        logger.info(f"  Total Graph Nodes: {len(merged_graph)}")

    @staticmethod
    def _run_scoped_clang_analysis(repo_root: Path,
                                 source_files: List[Path],
                                 ignore_dirs: Set[str],
                                 clang_args: List[str],
                                 clang_defined_out: Path,
                                 clang_nested_out: Path,
                                 clang_classes_out: Path,
                                 use_parallel: bool = True,
                                 max_workers: int = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run Clang analysis on scoped file list.
        
        Args:
            repo_root: Path to the repository root
            source_files: List of source files to analyze
            ignore_dirs: Set of directory names to ignore
            clang_args: List of arguments to pass to clang
            clang_defined_out: Path for defined functions output file
            clang_nested_out: Path for nested call graph output file
            clang_classes_out: Path for data types output file
            use_parallel: If True, use parallel processing for AST generation. (default: True)
            max_workers: Maximum number of worker processes for parallel processing. (default: None, auto-detect)
        
        Returns:
            Tuple of (clang_symbols, clang_graph)
        """
        
        logger.info(f"Running scoped Clang analysis on {len(source_files)} files")
        
        # Initialize libclang
        ClangAnalysisHelper.initialize_libclang()
        
        # Build function registry with scoped files
        registry_set, registry_map = CASTUtil.build_function_registry(
            repo_root=repo_root,
            source_files=source_files,
            clang_args=clang_args,
            out_path=clang_defined_out,
            use_parallel=use_parallel,
            max_workers=max_workers
        )
        
        # Build call graph with scoped files
        adjacency = CASTUtil.build_forward_call_graph(
            repo_root=repo_root,
            source_files=source_files,
            clang_args=clang_args,
            filter_external_calls=True,
            registry=registry_set,
            use_parallel=use_parallel,
            max_workers=max_workers
        )
        
        # Build data types registry with scoped files
        data_types_registry = CASTUtil.build_data_types_registry(
            repo_root=repo_root,
            source_files=source_files,
            clang_args=clang_args,
            out_path=clang_classes_out,
            use_parallel=use_parallel,
            max_workers=max_workers
        )
        
        # Add checksums to Clang data types if file exists
        if clang_classes_out and clang_classes_out.exists():
            try:
                logger.info("[+] Adding checksums to scoped Clang data types...")
                ASTFunctionSignatureGenerator.process_data_types_file(
                    repo_path=repo_root,
                    input_file=clang_classes_out,
                    output_file=clang_classes_out
                )
            except Exception as e:
                logger.warning(f"Failed to add checksums to scoped Clang data types: {e}")
        
        # Build data type usage mapping
        custom_types = set(data_types_registry.keys()) if data_types_registry else None
        data_type_usage = CASTUtil.build_data_type_use_with_macros(
            repo_root=repo_root,
            source_files=source_files,
            clang_args=clang_args,
            custom_types_registry=custom_types,
            use_parallel=use_parallel,
            max_workers=max_workers
        )
        
        # Build constants usage mapping
        logger.info("[+] Building constants usage mapping for scoped Clang analysis...")
        constants_usage = CASTUtil.build_constants_usage_with_macros(
            repo_root=repo_root,
            source_files=source_files,
            clang_args=clang_args,
            function_registry=registry_set,
            use_parallel=use_parallel,
            max_workers=max_workers
        )
        
        # Build nested call graph
        CASTUtil.build_nested_call_graph(
            definitions_map=registry_map,
            adjacency=adjacency,
            max_depth=1,
            out_path=clang_nested_out,
            data_type_usage=data_type_usage,
            constants_usage=constants_usage
        )
        
        # Load the generated files
        clang_registry = read_json_file(str(clang_defined_out))
        clang_graph = read_json_file(str(clang_nested_out))
        
        if clang_registry is None:
            clang_registry = {}
        if clang_graph is None:
            clang_graph = []
        
        # Convert Clang registry to symbols format
        clang_symbols = ClangAnalysisHelper.convert_clang_registry_to_symbols(clang_registry)
        
        return clang_symbols, clang_graph

    @staticmethod
    def _run_scoped_swift_analysis(repo_root: Path,
                                 source_files: List[Path],
                                 ignore_dirs: Set[str],
                                 swift_symbols_out: Path,
                                 swift_graph_out: Path,
                                 swift_classes_out: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run Swift analysis on scoped file list."""
        
        logger.info(f"Running scoped Swift analysis on {len(source_files)} files")
        
        # Build symbols (defined functions)
        symbols_set, symbols_map = SwiftASTUtil.collect_defined_functions(
            repo_root=repo_root,
            files=source_files,
            _extra_compiler_args=None,
            out_json=None
        )
        
        # Convert to symbols format
        symbols = []
        for func_name, definitions in symbols_map.items():
            for defn in definitions:
                symbol = {
                    "name": func_name,
                    "context": {
                        "file": defn[0],
                        "start": defn[1],
                        "end": defn[2]
                    }
                }
                symbols.append(symbol)
        
        # Build data types registry
        class_registry = SwiftASTUtil.collect_defined_classes(
            repo_root=repo_root,
            files=source_files,
            _extra_compiler_args=None,
            out_json=swift_classes_out
        )
        
        # Build call graph
        adjacency = SwiftASTUtil.build_call_graph_adjacency(
            files=source_files,
            _extra_compiler_args=None,
            only_repo_defined=True,
            registry_names=symbols_set
        )
        
        # Add checksums to Swift data types if file exists
        if swift_classes_out and swift_classes_out.exists():
            try:
                logger.info("[+] Adding checksums to scoped Swift data types...")
                ASTFunctionSignatureGenerator.process_data_types_file(
                    repo_path=repo_root,
                    input_file=swift_classes_out,
                    output_file=swift_classes_out
                )
            except Exception as e:
                logger.warning(f"Failed to add checksums to scoped Swift data types: {e}")
        
        # Build data type usage
        custom_types = set(class_registry.keys()) if class_registry else None
        data_type_usage = SwiftASTUtil.build_data_type_use(
            files=source_files,
            _extra_compiler_args=None,
            custom_types_registry=custom_types
        )
        
        # Build constants usage mapping
        logger.info("[+] Building constants usage mapping for scoped Swift analysis...")
        constants_usage = SwiftASTUtil.build_constants_usage(
            files=source_files,
            _extra_compiler_args=None,
            function_registry=symbols_set
        )
        
        # Generate nested call graph
        SwiftASTUtil.generate_nested_call_graph(
            definitions_map=symbols_map,
            adjacency=adjacency,
            max_depth=1,
            out_json=swift_graph_out,
            data_type_usage=data_type_usage,
            constants_usage=constants_usage
        )
        
        # Load the generated nested call graph
        swift_graph = read_json_file(str(swift_graph_out))
        if swift_graph is None:
            swift_graph = []
        
        # Write outputs
        logger.info(f"[+] Writing scoped swift symbols to {swift_symbols_out}")
        write_json_file(str(swift_symbols_out), symbols)
        
        return symbols, swift_graph

    @staticmethod
    def _run_scoped_kotlin_analysis(repo_root: Path,
                                  source_files: List[Path],
                                  ignore_dirs: Set[str],
                                  kotlin_functions_out: Path,
                                  kotlin_graph_out: Path,
                                  kotlin_classes_out: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run Kotlin analysis on scoped file list."""
        
        logger.info(f"Running scoped Kotlin analysis on {len(source_files)} files")
        
        kotlin_util = KotlinASTUtil()
        
        # Build function registry
        function_names, definitions_map = kotlin_util.build_function_registry(
            repo_root=repo_root,
            source_files=source_files,
            out_path=str(kotlin_functions_out)
        )
        
        # Convert to symbols format
        symbols = []
        for func_name, definitions in definitions_map.items():
            for defn in definitions:
                symbol = {
                    "name": func_name,
                    "context": {
                        "file": defn["file_name"],
                        "start": defn["start"],
                        "end": defn["end"]
                    }
                }
                symbols.append(symbol)
        
        # Build class registry
        if kotlin_classes_out:
            kotlin_util.build_class_registry(
                repo_root=repo_root,
                source_files=source_files,
                out_path=str(kotlin_classes_out)
            )
        
        # Build call graph
        adjacency = kotlin_util.build_call_graph(
            repo_root=repo_root,
            source_files=source_files,
            function_registry=function_names
        )
        
        # Add checksums to Kotlin data types if file exists
        if kotlin_classes_out and kotlin_classes_out.exists():
            try:
                logger.info("[+] Adding checksums to scoped Kotlin data types...")
                ASTFunctionSignatureGenerator.process_data_types_file(
                    repo_path=repo_root,
                    input_file=kotlin_classes_out,
                    output_file=kotlin_classes_out
                )
            except Exception as e:
                logger.warning(f"Failed to add checksums to scoped Kotlin data types: {e}")
        
        # Build constants usage mapping
        logger.info("[+] Building constants usage mapping for scoped Kotlin analysis...")
        constants_usage = kotlin_util.build_constants_usage(
            repo_root=repo_root,
            source_files=source_files
        )
        
        # Build nested call graph
        kotlin_util.build_nested_call_graph(
            definitions_map=definitions_map,
            adjacency=adjacency,
            out_path=str(kotlin_graph_out),
            constants_usage=constants_usage
        )
        
        # Load the generated nested call graph
        kotlin_graph = read_json_file(str(kotlin_graph_out))
        if kotlin_graph is None:
            kotlin_graph = []
        
        return symbols, kotlin_graph

    @staticmethod
    def _run_scoped_java_analysis(repo_root: Path,
                                source_files: List[Path],
                                ignore_dirs: Set[str],
                                java_functions_out: Path,
                                java_graph_out: Path,
                                java_classes_out: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run Java analysis on scoped file list."""
        
        logger.info(f"Running scoped Java analysis on {len(source_files)} files")
        
        java_util = JavaASTUtil()
        
        # Build function registry
        function_names, definitions_map = java_util.build_function_registry(
            repo_root=repo_root,
            source_files=source_files,
            out_path=str(java_functions_out)
        )
        
        # Convert to symbols format
        symbols = []
        for func_name, definitions in definitions_map.items():
            for defn in definitions:
                symbol = {
                    "name": func_name,
                    "context": {
                        "file": defn["file_name"],
                        "start": defn["start"],
                        "end": defn["end"]
                    }
                }
                symbols.append(symbol)
        
        # Build class registry
        if java_classes_out:
            java_util.build_data_types_registry(
                repo_root=repo_root,
                source_files=source_files,
                out_path=str(java_classes_out)
            )
        
        # Build call graph
        adjacency = java_util.build_call_graph(
            repo_root=repo_root,
            source_files=source_files,
            function_registry=function_names
        )
        
        # Add checksums to Java data types if file exists
        if java_classes_out and java_classes_out.exists():
            try:
                logger.info("[+] Adding checksums to scoped Java data types...")
                ASTFunctionSignatureGenerator.process_data_types_file(
                    repo_path=repo_root,
                    input_file=java_classes_out,
                    output_file=java_classes_out
                )
            except Exception as e:
                logger.warning(f"Failed to add checksums to scoped Java data types: {e}")
        
        # Build data type usage
        class_registry = {}
        if java_classes_out:
            try:
                with open(str(java_classes_out), 'r', encoding='utf-8') as f:
                    class_data = json.load(f)
                    
                    if isinstance(class_data, dict):
                        if 'data_type_to_location_and_checksum' in class_data:
                            class_entries = class_data['data_type_to_location_and_checksum']
                            class_registry = {class_name: class_info.get('code', []) for class_name, class_info in class_entries.items()}
                        elif 'data_type_to_location' in class_data:
                            class_entries = class_data['data_type_to_location']
                            if isinstance(class_entries, list):
                                class_registry = {dt["data_type_name"]: dt["files"] for dt in class_entries if isinstance(dt, dict) and "data_type_name" in dt}
                            else:
                                class_registry = {}
                        else:
                            class_registry = {class_name: class_info for class_name, class_info in class_data.items()}
                    else:
                        logger.warning(f"Unexpected class_data format: {type(class_data)}, expected dict")
                        class_registry = {}
            except Exception as e:
                logger.warning(f"Could not load Java class registry: {e}")
        
        custom_types = set(class_registry.keys()) if class_registry else None
        data_type_usage = java_util.build_data_type_use(
            repo_root=repo_root,
            source_files=source_files,
            custom_types_registry=custom_types
        )
        
        # Build constants usage mapping
        logger.info("[+] Building constants usage mapping for scoped Java analysis...")
        constants_usage = java_util.build_constants_usage(
            repo_root=repo_root,
            source_files=source_files,
            function_registry=function_names
        )
        
        # Build nested call graph
        java_util.build_nested_call_graph(
            definitions_map=definitions_map,
            adjacency=adjacency,
            max_depth=1,
            out_path=str(java_graph_out),
            data_type_usage=data_type_usage,
            constants_usage=constants_usage
        )
        
        # Load the generated nested call graph
        java_graph = read_json_file(str(java_graph_out))
        if java_graph is None:
            java_graph = []
        
        return symbols, java_graph

    @staticmethod
    def _run_scoped_go_analysis(repo_root: Path,
                              source_files: List[Path],
                              ignore_dirs: Set[str],
                              go_functions_out: Path,
                              go_graph_out: Path,
                              go_classes_out: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run Go analysis on scoped file list."""
        
        logger.info(f"Running scoped Go analysis on {len(source_files)} files")
        
        # Build function registry
        function_names, definitions_map = GoASTUtil.collect_defined_functions(
            repo_root=repo_root,
            files=source_files,
            _extra_compiler_args=None,
            out_json=go_functions_out
        )
        
        # Convert to symbols format
        symbols = []
        for func_name, definitions in definitions_map.items():
            for defn in definitions:
                symbol = {
                    "name": func_name,
                    "context": {
                        "file": defn[0],
                        "start": defn[1],
                        "end": defn[2]
                    }
                }
                symbols.append(symbol)
        
        # Build class registry
        if go_classes_out:
            class_registry = GoASTUtil.collect_defined_classes(
                repo_root=repo_root,
                files=source_files,
                _extra_compiler_args=None,
                out_json=go_classes_out
            )
        else:
            class_registry = {}
        
        # Build call graph
        adjacency = GoASTUtil.build_call_graph_adjacency(
            files=source_files,
            _extra_compiler_args=None,
            only_repo_defined=True,
            registry_names=function_names
        )
        
        # Add checksums to Go data types if file exists
        if go_classes_out and go_classes_out.exists():
            try:
                logger.info("[+] Adding checksums to scoped Go data types...")
                ASTFunctionSignatureGenerator.process_data_types_file(
                    repo_path=repo_root,
                    input_file=go_classes_out,
                    output_file=go_classes_out
                )
            except Exception as e:
                logger.warning(f"Failed to add checksums to scoped Go data types: {e}")
        
        # Build data type usage mapping
        custom_types = set(class_registry.keys()) if class_registry else None
        data_type_usage = GoASTUtil.build_data_type_use(
            files=source_files,
            _extra_compiler_args=None,
            custom_types_registry=custom_types
        )
        
        # Build constants usage mapping
        logger.info("[+] Building constants usage mapping for scoped Go analysis...")
        constants_usage = GoASTUtil.build_constants_usage(
            files=source_files,
            _extra_compiler_args=None,
            function_registry=function_names
        )
        
        # Build nested call graph
        GoASTUtil.generate_nested_call_graph(
            definitions_map=definitions_map,
            adjacency=adjacency,
            max_depth=1,
            out_json=go_graph_out,
            data_type_usage=data_type_usage,
            constants_usage=constants_usage
        )
        
        # Load the generated nested call graph
        go_graph = read_json_file(str(go_graph_out))
        if go_graph is None:
            go_graph = []
        
        return symbols, go_graph

    # JS/TS scoped analysis method - disabled but kept for reference
    # @staticmethod
    # def _run_scoped_js_ts_analysis(repo_root: Path,
    #                               source_files: List[Path],
    #                               ignore_dirs: Set[str],
    #                               js_ts_functions_out: Path,
    #                               js_ts_graph_out: Path,
    #                               js_ts_classes_out: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    #     """Run JavaScript/TypeScript analysis on scoped file list."""
    #
    #     logger.info(f"Running scoped JavaScript/TypeScript analysis on {len(source_files)} files")
    #
    #     js_ts_util = JavaScriptTypeScriptASTUtil()
    #
    #     # Build function registry
    #     function_names, definitions_map = js_ts_util.build_function_registry(
    #         repo_root=repo_root,
    #         source_files=source_files,
    #         out_path=str(js_ts_functions_out)
    #     )
    #
    #     # Convert to symbols format
    #     symbols = []
    #     for func_name, definitions in definitions_map.items():
    #         for defn in definitions:
    #             symbol = {
    #                 "name": func_name,
    #                 "context": {
    #                     "file": defn["file_name"],
    #                     "start": defn["start"],
    #                     "end": defn["end"]
    #                 }
    #             }
    #             symbols.append(symbol)
    #
    #     # Build class registry
    #     if js_ts_classes_out:
    #         js_ts_util.build_class_registry(
    #             repo_root=repo_root,
    #             source_files=source_files,
    #             out_path=str(js_ts_classes_out)
    #         )
    #
    #     # Build call graph
    #     adjacency = js_ts_util.build_call_graph(
    #         repo_root=repo_root,
    #         source_files=source_files,
    #         function_registry=function_names
    #     )
    #
    #     # Add checksums to JS/TS data types if file exists
    #     if js_ts_classes_out and js_ts_classes_out.exists():
    #         try:
    #             logger.info("[+] Adding checksums to scoped JavaScript/TypeScript data types...")
    #             ASTFunctionSignatureGenerator.process_data_types_file(
    #                 repo_path=repo_root,
    #                 input_file=js_ts_classes_out,
    #                 output_file=js_ts_classes_out
    #             )
    #         except Exception as e:
    #             logger.warning(f"Failed to add checksums to scoped JavaScript/TypeScript data types: {e}")
    #
    #     # Build nested call graph
    #     js_ts_util.build_nested_call_graph(
    #         definitions_map=definitions_map,
    #         adjacency=adjacency,
    #         max_depth=1,
    #         out_path=str(js_ts_graph_out)
    #     )
    #
    #     # Load the generated nested call graph
    #     js_ts_graph = read_json_file(str(js_ts_graph_out))
    #     if js_ts_graph is None:
    #         js_ts_graph = []
    #
    #     return symbols, js_ts_graph


def main():
    parser = argparse.ArgumentParser(
        description="Run scoped AST analysis on specific files and their dependencies"
    )
    parser.add_argument("--repo", type=Path, required=True,
                        help="Path to project root directory")
    parser.add_argument("--files", nargs="+", type=Path, required=True,
                        help="List of files to analyze (relative to repo root)")
    parser.add_argument("--config", type=Path, required=False,
                        help="Path to configuration JSON file (will use exclude_directories from config)")
    parser.add_argument("--exclude", nargs="*", default=[".git", "DerivedData", "build", ".build"],
                        help="Directory names to exclude at any depth (overridden by --config if provided)")
    parser.add_argument("--clang-args", nargs="*", default=[],
                        help="Additional arguments to pass to Clang parser")
    parser.add_argument("--max-dependency-depth", type=int, default=3,
                        help="Maximum depth for dependency discovery (default: 3)")

    # Output file arguments with /tmp defaults
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/scoped_language_artifacts"),
                        help="Output directory for language-specific artifacts (default: /tmp/scoped_language_artifacts)")
    parser.add_argument("--merged-functions", type=Path, default=Path("/tmp/scoped_merged_functions.json"),
                        help="Output path for merged functions JSON (default: /tmp/scoped_merged_functions.json)")
    parser.add_argument("--merged-graph", type=Path, default=Path("/tmp/scoped_merged_call_graph.json"),
                        help="Output path for merged call graph JSON (default: /tmp/scoped_merged_call_graph.json)")
    parser.add_argument("--merged-data-types-out", type=Path, default=Path("/tmp/scoped_merged_defined_classes.json"),
                        help="Path to write merged data types JSON (default: /tmp/scoped_merged_defined_classes.json)")

    # Parallel processing arguments
    parser.add_argument("--no-parallel", action="store_true",
                        help="Disable parallel processing for AST generation (default: parallel enabled)")
    parser.add_argument("--max-workers", type=int, default=None,
                        help="Maximum number of worker processes for parallel processing (default: auto-detect based on CPU count)")

    args = parser.parse_args()

    # Setup logging
    setup_default_logging()

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

    # Load config file if provided and extract exclude_directories
    exclude_dirs = set(args.exclude)

    if args.config:
        try:
            logger.info(f"Loading configuration from: {args.config}")
            config_data = read_json_file(str(args.config))
            
            if config_data and 'exclude_directories' in config_data:
                config_exclude_dirs = config_data['exclude_directories']
                if isinstance(config_exclude_dirs, list):
                    exclude_dirs = set(config_exclude_dirs)
                    logger.info(f"Using exclude_directories from config: {exclude_dirs}")
                else:
                    logger.warning("exclude_directories in config is not a list, using default exclude directories")
            else:
                logger.warning("No exclude_directories found in config file, using default exclude directories")
                
        except Exception as e:
            logger.error(f"Failed to load config file {args.config}: {e}")
            logger.info("Using default exclude directories")

    logger.info("Starting scoped AST analysis...")
    logger.info(f"Repository: {args.repo}")
    logger.info(f"Target files: {[str(f.relative_to(args.repo)) for f in target_files]}")
    logger.info(f"Exclude directories: {exclude_dirs}")
    logger.info(f"Max dependency depth: {args.max_dependency_depth}")
    logger.info(f"Clang args: {args.clang_args}")
    if args.config:
        logger.info(f"Config file: {args.config}")

    try:
        # Create output directory for language-specific artifacts
        args.out_dir.mkdir(parents=True, exist_ok=True)
        
        # Run scoped analysis
        ScopedASTUtil.run_scoped_analysis(
            repo_root=args.repo,
            target_files=target_files,
            ignore_dirs=exclude_dirs,
            clang_args=args.clang_args,
            out_dir=args.out_dir,
            merged_symbols_out=args.merged_functions,
            merged_graph_out=args.merged_graph,
            merged_data_types_out=args.merged_data_types_out,
            max_dependency_depth=args.max_dependency_depth,
            use_parallel=not args.no_parallel,
            max_workers=args.max_workers
        )

        logger.info("Scoped AST analysis completed successfully!")
        logger.info("Generated files:")
        logger.info(f"  - Language artifacts directory: {args.out_dir}")
        logger.info(f"  - Merged functions: {args.merged_functions}")
        logger.info(f"  - Merged call graph: {args.merged_graph}")
        logger.info(f"  - Merged data types: {args.merged_data_types_out}")

    except Exception as e:
        logger.error(f"Scoped AST analysis failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()