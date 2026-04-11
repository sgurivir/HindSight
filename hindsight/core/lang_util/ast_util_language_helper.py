#!/usr/bin/env python3
# Created by Sridhar Gurivireddy on 11/02/2025
# ast_util_language_helper.py
# Helper classes for language-specific AST analysis operations

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple, Union

from .cast_util import CASTUtil
from .Environment import Environment
from .go_util import GoASTUtil
from .java_ast_util import JavaASTUtil
from .kotlin_ast_util import KotlinASTUtil
from .swift_ast_util import SwiftASTUtil
from .javascript_typescript_ast_util import JavaScriptTypeScriptASTUtil
from .ast_function_signature_util import ASTFunctionSignatureGenerator

from ...utils.file_util import read_json_file, write_json_file

logger = logging.getLogger(__name__)

def log_ignored_directories(ignored_dirs: set):
    """Log the directories that are being ignored during analysis."""
    if ignored_dirs:
        logger.info(f"Ignoring directories: {sorted(ignored_dirs)}")
    else:
        logger.info("No directories are being ignored")

class ClangAnalysisHelper:
    """Helper class for C/C++/Objective-C analysis operations."""

    @staticmethod
    def initialize_libclang():
        """Initialize libclang path before any Clang operations."""
        Environment.initialize_libclang()

    @staticmethod
    def run_clang_analysis(repo: Path,
                           ignore_dirs: Set[str],
                           clang_args: List[str],
                           clang_defined_out: Path,
                           clang_nested_out: Path,
                           max_call_depth: int,
                           clang_classes_out: Path = None,
                           expand_macros: bool = True,
                           use_parallel: Optional[bool] = None,
                           max_workers: Optional[int] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Run CASTUtil analysis and return registry and call graph data.
        
        Args:
            repo: Path to the repository to analyze
            ignore_dirs: Set of directory names to ignore during analysis
            clang_args: List of arguments to pass to clang
            clang_defined_out: Path for defined functions output file
            clang_nested_out: Path for nested call graph output file
            max_call_depth: Maximum depth for nested call graph
            clang_classes_out: Path for data types output file (optional)
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
            use_parallel: If True, use parallel processing. If None, auto-detect based on file count.
                         Parallel processing is enabled by default when file count >= 10.
            max_workers: Maximum number of worker processes. If None, use default (4 or cpu_count).
        
        Returns:
            Tuple of (clang_registry, clang_graph)
        """

        # Initialize libclang path before any analysis
        Environment.initialize_libclang()

        # Ensure clang_args is always a list, not None
        clang_args = clang_args or ["-O2"]

        logger.info("Extracting Clang symbols...")
        
        # Log expand macros mode if enabled
        if expand_macros:
            logger.info("EXPAND MACROS MODE: Will build AST twice (with and without macros) and merge results")
        
        # Log parallel processing mode
        if use_parallel is True:
            logger.info(f"PARALLEL MODE: Enabled with max_workers={max_workers or 'auto'}")
        elif use_parallel is False:
            logger.info("PARALLEL MODE: Disabled (sequential processing)")
        else:
            logger.info("PARALLEL MODE: Auto-detect based on file count (enabled if >= 10 files)")

        # Get implementation files
        logger.info("Building function map. It might take a while. Please wait...")
        # Convert ignore_dirs to set if it's not already
        ignore_dirs_set = set(ignore_dirs) if not isinstance(ignore_dirs, set) else ignore_dirs
        impl_files = CASTUtil.find_source_files(repo, ignore_dirs_set)
        
        
        function_map = {}  # Not used in the current implementation

        if not impl_files:
            logger.info("No Clang files found")
            return {}, []

        logger.info(f"Found {len(impl_files)} implementation files")
        logger.info(f"Found {len(function_map)} unique functions")

        # Build defined functions registry
        logger.info("[+] Building defined functions registry. It might take few minutes. Please wait...")
        # Pass macros=[] to trigger auto-detection when expand_macros is True
        # macros=None would skip macro detection entirely
        registry_set, registry_map = CASTUtil.build_function_registry(
            repo_root=repo,
            source_files=impl_files,
            clang_args=clang_args,
            out_path=clang_defined_out,
            macros=[] if expand_macros else None,
            expand_macros=expand_macros,
            use_parallel=use_parallel,
            max_workers=max_workers
        )

        # Build adjacency list (call graph)
        logger.info("[+] Building call graph adjacency. It might take a while. Please wait...")
        adjacency = CASTUtil.build_forward_call_graph(
            repo_root=repo,
            source_files=impl_files,
            clang_args=clang_args,
            filter_external_calls=True,
            registry=registry_set,
            macros=[] if expand_macros else None,
            expand_macros=expand_macros,
            use_parallel=use_parallel,
            max_workers=max_workers
        )

        # Build data types registry (always run unconditionally)
        logger.info("[+] Building data types registry. It might take a while. Please wait...")
        data_types_registry = CASTUtil.build_data_types_registry(
            repo_root=repo,
            source_files=impl_files,
            clang_args=clang_args,
            out_path=clang_classes_out,
            macros=[] if expand_macros else None,
            expand_macros=expand_macros,
            use_parallel=use_parallel,
            max_workers=max_workers
        )
        
        # Add checksums to Clang data types if file exists
        if clang_classes_out and clang_classes_out.exists():
            try:
                logger.info("[+] Adding checksums to Clang data types...")
                ASTFunctionSignatureGenerator.process_data_types_file(
                    repo_path=repo,
                    input_file=clang_classes_out,
                    output_file=clang_classes_out
                )
            except Exception as e:
                logger.warning(f"Failed to add checksums to Clang data types: {e}")

        # Build data type usage mapping
        logger.info("[+] Building data type usage mapping. It might take a while. Please wait...")
        custom_types = set(data_types_registry.keys()) if data_types_registry else None
        data_type_usage = CASTUtil.build_data_type_use(
            repo_root=repo,
            source_files=impl_files,
            clang_args=clang_args,
            custom_types_registry=custom_types,
            macros=[] if expand_macros else None,
            expand_macros=expand_macros
        )


        # Build constants usage mapping
        logger.info("[+] Building constants usage mapping. It might take a while. Please wait...")
        constants_usage = CASTUtil.build_constants_usage(
            repo_root=repo,
            source_files=impl_files,
            clang_args=clang_args,
            function_registry=registry_set,
            macros=[] if expand_macros else None,
            expand_macros=expand_macros
        )


        # Build nested call graph
        logger.info("[+] Building nested call graph. It might take long time. Please wait...")
        CASTUtil.build_nested_call_graph(
            definitions_map=registry_map,
            adjacency=adjacency,
            max_depth=max_call_depth,
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

        return clang_registry, clang_graph

    @staticmethod
    def convert_clang_registry_to_symbols(clang_registry: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert Clang registry format to symbols format matching Swift output."""
        symbols = []

        # Handle case when there are no Clang files in the repository
        if not clang_registry or 'function_to_location' not in clang_registry:
            logger.info("No Clang functions found or registry is empty")
            return symbols

        # Use current schema with "function_to_location" wrapper
        function_data = clang_registry['function_to_location']

        for func_name, definitions in function_data.items():
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

        return symbols

class SwiftAnalysisHelper:
    """Helper class for Swift analysis operations."""

    @staticmethod
    def run_swift_analysis(repo: Path,
                           ignore_dirs: Set[str],
                           swift_symbols_out: Path,
                           swift_graph_out: Path,
                           swift_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run SwiftASTUtil analysis and return symbols and call graph data."""
        logger.info("Running Swift analysis...")

        try:
            # Run Swift analysis
            # Get Swift files
            # Convert ignore_dirs to set if it's not already
            ignore_dirs_set = set(ignore_dirs) if not isinstance(ignore_dirs, set) else ignore_dirs
            swift_files = SwiftASTUtil.find_swift_source_files(repo, ignore_dirs_set)

            # Build symbols (defined functions)
            symbols_set, symbols_map = SwiftASTUtil.collect_defined_functions(
                repo_root=repo,
                files=swift_files,
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

            # Build data types registry BEFORE call graph generation (always build it for data type usage filtering)
            logger.info("[+] Building Swift data types registry. It might take a while. Please wait...")
            class_registry = SwiftASTUtil.collect_defined_classes(
                repo_root=repo,
                files=swift_files,
                _extra_compiler_args=None,
                out_json=swift_classes_out
            )

            # Build call graph AFTER function and class registries are complete
            adjacency = SwiftASTUtil.build_call_graph_adjacency(
                files=swift_files,
                _extra_compiler_args=None,
                only_repo_defined=True,
                registry_names=symbols_set
            )
            
            # Add checksums to Swift data types if file exists
            if swift_classes_out and swift_classes_out.exists():
                try:
                    logger.info("[+] Adding checksums to Swift data types...")
                    ASTFunctionSignatureGenerator.process_data_types_file(
                        repo_path=repo,
                        input_file=swift_classes_out,
                        output_file=swift_classes_out
                    )
                except Exception as e:
                    logger.warning(f"Failed to add checksums to Swift data types: {e}")

            # Build data type usage for Swift using the class registry for filtering
            logger.info("[+] Building Swift data type usage mapping. It might take a while. Please wait...")
            custom_types = set(class_registry.keys()) if class_registry else None
            data_type_usage = SwiftASTUtil.build_data_type_use(
                files=swift_files,
                _extra_compiler_args=None,
                custom_types_registry=custom_types
            )

            # Build constants usage mapping for Swift
            logger.info("[+] Building Swift constants usage mapping. It might take a while. Please wait...")
            constants_usage = SwiftASTUtil.build_constants_usage(
                files=swift_files,
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

            # Load the generated nested call graph to return it
            swift_graph = read_json_file(str(swift_graph_out))
            if swift_graph is None:
                swift_graph = []

            # Write outputs
            logger.info(f"[+] Writing swift symbols to {swift_symbols_out}")
            write_json_file(str(swift_symbols_out), symbols)

            return symbols, swift_graph

        except Exception as e:
            logger.error(f"Swift analysis failed: {e}")
            return [], []

class KotlinAnalysisHelper:
    """Helper class for Kotlin analysis operations."""

    @staticmethod
    def run_kotlin_analysis(repo: Path,
                           ignore_dirs: Set[str],
                           kotlin_functions_out: Path,
                           kotlin_graph_out: Path,
                           kotlin_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run KotlinASTUtil analysis and return symbols and call graph data."""
        logger.info("Running Kotlin analysis...")

        try:
            # Initialize Kotlin utility
            kotlin_util = KotlinASTUtil()

            # Get Kotlin files
            # Convert ignore_dirs to set if it's not already
            ignore_dirs_set = set(ignore_dirs) if not isinstance(ignore_dirs, set) else ignore_dirs
            kotlin_files = KotlinASTUtil.find_source_files(repo, ignore_dirs_set)

            if not kotlin_files:
                logger.info("No Kotlin files found")
                return [], []

            logger.info(f"Found {len(kotlin_files)} Kotlin files")

            # Build function registry (with small function filtering already applied)
            function_names, definitions_map = kotlin_util.build_function_registry(
                repo_root=repo,
                source_files=kotlin_files,
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

            # Build class registry BEFORE call graph if output path provided
            if kotlin_classes_out:
                logger.info("[+] Building Kotlin class registry...")
                kotlin_util.build_class_registry(
                    repo_root=repo,
                    source_files=kotlin_files,
                    out_path=str(kotlin_classes_out)
                )

            # Build call graph AFTER function and class registries (small functions already filtered from function_names)
            adjacency = kotlin_util.build_call_graph(
                repo_root=repo,
                source_files=kotlin_files,
                function_registry=function_names
            )
            
            # Add checksums to Kotlin data types if file exists
            if kotlin_classes_out and kotlin_classes_out.exists():
                try:
                    logger.info("[+] Adding checksums to Kotlin data types...")
                    ASTFunctionSignatureGenerator.process_data_types_file(
                        repo_path=repo,
                        input_file=kotlin_classes_out,
                        output_file=kotlin_classes_out
                    )
                except Exception as e:
                    logger.warning(f"Failed to add checksums to Kotlin data types: {e}")

            # Build constants usage mapping for Kotlin
            logger.info("[+] Building Kotlin constants usage mapping. It might take a while. Please wait...")
            constants_usage = kotlin_util.build_constants_usage(
                repo_root=repo,
                source_files=kotlin_files
            )


            # Build nested call graph (max depth 1, small functions already filtered)
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

            logger.info(f"Kotlin analysis complete: {len(symbols)} symbols, {len(kotlin_graph)} graph nodes")
            return symbols, kotlin_graph

        except Exception as e:
            logger.error(f"Kotlin analysis failed: {e}")
            return [], []

class JavaAnalysisHelper:
    """Helper class for Java analysis operations."""

    @staticmethod
    def run_java_analysis(repo: Path,
                         ignore_dirs: Set[str],
                         java_functions_out: Path,
                         java_graph_out: Path,
                         java_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run JavaASTUtil analysis and return symbols and call graph data."""
        logger.info("Running Java analysis...")

        try:
            # Initialize Java utility
            java_util = JavaASTUtil()

            # Get Java files
            # Convert ignore_dirs to set if it's not already
            ignore_dirs_set = set(ignore_dirs) if not isinstance(ignore_dirs, set) else ignore_dirs
            java_files = JavaASTUtil.find_source_files(repo, ignore_dirs_set)

            if not java_files:
                logger.info("No Java files found")
                return [], []

            logger.info(f"Found {len(java_files)} Java files")

            # Build function registry
            function_names, definitions_map = java_util.build_function_registry(
                repo_root=repo,
                source_files=java_files,
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

            # Build class registry BEFORE call graph if output path provided
            if java_classes_out:
                logger.info("[+] Building Java class registry...")
                java_util.build_data_types_registry(
                    repo_root=repo,
                    source_files=java_files,
                    out_path=str(java_classes_out)
                )

            # Build call graph AFTER function and class registries
            adjacency = java_util.build_call_graph(
                repo_root=repo,
                source_files=java_files,
                function_registry=function_names
            )
            
            # Add checksums to Java data types if file exists
            if java_classes_out and java_classes_out.exists():
                try:
                    logger.info("[+] Adding checksums to Java data types...")
                    ASTFunctionSignatureGenerator.process_data_types_file(
                        repo_path=repo,
                        input_file=java_classes_out,
                        output_file=java_classes_out
                    )
                except Exception as e:
                    logger.warning(f"Failed to add checksums to Java data types: {e}")

            # Build data type usage for Java using the class registry for filtering
            logger.info("[+] Building Java data type usage mapping. It might take a while. Please wait...")
            class_registry = {}
            if java_classes_out:
                # Load the class registry we just built
                try:
                    with open(str(java_classes_out), 'r', encoding='utf-8') as f:
                        class_data = json.load(f)
                        
                        # Use current schema format with checksums
                        class_entries = class_data['data_type_to_location_and_checksum']
                        class_registry = {class_name: class_info.get('code', []) for class_name, class_info in class_entries.items()}
                except Exception as e:
                    logger.warning(f"Could not load Java class registry: {e}")

            custom_types = set(class_registry.keys()) if class_registry else None
            data_type_usage = java_util.build_data_type_use(
                repo_root=repo,
                source_files=java_files,
                custom_types_registry=custom_types
            )

            # Build constants usage mapping for Java
            logger.info("[+] Building Java constants usage mapping. It might take a while. Please wait...")
            constants_usage = java_util.build_constants_usage(
                repo_root=repo,
                source_files=java_files,
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

            logger.info(f"Java analysis complete: {len(symbols)} symbols, {len(java_graph)} graph nodes")
            return symbols, java_graph

        except Exception as e:
            logger.error(f"Java analysis failed: {e}")
            return [], []

class GoAnalysisHelper:
    """Helper class for Go analysis operations."""

    @staticmethod
    def run_go_analysis(repo: Path,
                       ignore_dirs: Set[str],
                       go_functions_out: Path,
                       go_graph_out: Path,
                       go_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run GoASTUtil analysis and return symbols and call graph data."""
        logger.info("Running Go analysis...")

        try:
            # Get Go files
            # Convert ignore_dirs to set if it's not already
            ignore_dirs_set = set(ignore_dirs) if not isinstance(ignore_dirs, set) else ignore_dirs
            go_files = GoASTUtil.find_go_source_files(repo, ignore_dirs_set)

            if not go_files:
                logger.info("No Go files found")
                return [], []

            logger.info(f"Found {len(go_files)} Go files")

            # Build function registry
            function_names, definitions_map = GoASTUtil.collect_defined_functions(
                repo_root=repo,
                files=go_files,
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

            # Build class registry BEFORE call graph if output path provided
            if go_classes_out:
                logger.info("[+] Building Go type registry...")
                class_registry = GoASTUtil.collect_defined_classes(
                    repo_root=repo,
                    files=go_files,
                    _extra_compiler_args=None,
                    out_json=go_classes_out
                )
            else:
                class_registry = {}

            # Build call graph AFTER function and class registries
            adjacency = GoASTUtil.build_call_graph_adjacency(
                files=go_files,
                _extra_compiler_args=None,
                only_repo_defined=True,
                registry_names=function_names
            )
            
            # Add checksums to Go data types if file exists
            if go_classes_out and go_classes_out.exists():
                try:
                    logger.info("[+] Adding checksums to Go data types...")
                    ASTFunctionSignatureGenerator.process_data_types_file(
                        repo_path=repo,
                        input_file=go_classes_out,
                        output_file=go_classes_out
                    )
                except Exception as e:
                    logger.warning(f"Failed to add checksums to Go data types: {e}")

            # Build data type usage mapping
            logger.info("[+] Building Go data type usage mapping. It might take a while. Please wait...")
            custom_types = set(class_registry.keys()) if class_registry else None
            data_type_usage = GoASTUtil.build_data_type_use(
                files=go_files,
                _extra_compiler_args=None,
                custom_types_registry=custom_types
            )

            # Build constants usage mapping for Go
            logger.info("[+] Building Go constants usage mapping. It might take a while. Please wait...")
            constants_usage = GoASTUtil.build_constants_usage(
                files=go_files,
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

            logger.info(f"Go analysis complete: {len(symbols)} symbols, {len(go_graph)} graph nodes")
            return symbols, go_graph

        except Exception as e:
            logger.error(f"Go analysis failed: {e}")
            return [], []

class JavaScriptTypeScriptAnalysisHelper:
    """Helper class for JavaScript/TypeScript analysis operations."""

    @staticmethod
    def run_js_ts_analysis(repo: Path,
                          ignore_dirs: Set[str],
                          js_ts_functions_out: Path,
                          js_ts_graph_out: Path,
                          js_ts_classes_out: Path = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run JavaScriptTypeScriptASTUtil analysis and return symbols and call graph data."""
        logger.info("Running JavaScript/TypeScript analysis...")

        try:
            # Initialize JS/TS utility
            js_ts_util = JavaScriptTypeScriptASTUtil()

            # Get JS/TS files
            ignore_dirs_set = set(ignore_dirs) if not isinstance(ignore_dirs, set) else ignore_dirs
            js_ts_files = JavaScriptTypeScriptASTUtil.find_source_files(repo, ignore_dirs_set)

            if not js_ts_files:
                logger.info("No JavaScript/TypeScript files found")
                return [], []

            logger.info(f"Found {len(js_ts_files)} JavaScript/TypeScript files")

            # Build function registry
            function_names, definitions_map = js_ts_util.build_function_registry(
                repo_root=repo,
                source_files=js_ts_files,
                out_path=str(js_ts_functions_out)
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

            # Build class registry if output path provided
            if js_ts_classes_out:
                logger.info("[+] Building JavaScript/TypeScript class registry...")
                js_ts_util.build_class_registry(
                    repo_root=repo,
                    source_files=js_ts_files,
                    out_path=str(js_ts_classes_out)
                )

            # Build call graph
            adjacency = js_ts_util.build_call_graph(
                repo_root=repo,
                source_files=js_ts_files,
                function_registry=function_names
            )

            # Build nested call graph
            js_ts_util.build_nested_call_graph(
                definitions_map=definitions_map,
                adjacency=adjacency,
                max_depth=1,
                out_path=str(js_ts_graph_out)
            )

            # Load the generated nested call graph
            js_ts_graph = read_json_file(str(js_ts_graph_out))
            if js_ts_graph is None:
                js_ts_graph = []

            logger.info(f"JavaScript/TypeScript analysis complete: {len(symbols)} symbols, {len(js_ts_graph)} graph nodes")
            return symbols, js_ts_graph

        except Exception as e:
            logger.error(f"JavaScript/TypeScript analysis failed: {e}")
            return [], []
