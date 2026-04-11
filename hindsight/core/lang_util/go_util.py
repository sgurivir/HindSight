#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GoASTUtil.py — Go AST utilities built on tree-sitter and tree-sitter-go
Author: Sridhar Gurivireddy
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tree_sitter
from tree_sitter_languages import get_language, get_parser

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any


from ...utils.log_util import setup_default_logging


class GoASTUtil:
    """
    Utilities built on tree-sitter and tree-sitter-go to analyze Go code:
      - find_go_source_files: discover .go sources (automatically excludes *_test.go files)
      - parse_go_ast_to_json: parse Go AST using tree-sitter
      - collect_defined_functions: extract function/method declarations + ranges
      - build_call_graph_adjacency: caller -> callees from call-expression nodes
      - generate_nested_call_graph: nested tree JSON with context
    
    Note: Test files ending in '_test.go' are automatically excluded from AST generation
    as they typically contain test code that is not relevant for analysis.
    """

    _parser = None
    _language = None

    @classmethod
    def _get_parser(cls):
        """Get or create the tree-sitter parser for Go."""
        if cls._parser is None:
            cls._parser = get_parser("go")
        return cls._parser

    # ---------------- File Discovery ----------------

    @staticmethod
    def find_go_source_files(repo_root: Path, ignored_dirs: Set[str] | None = None) -> List[Path]:
        """Find all .go source files in the repository, excluding test files ending in '_test.go'."""
        ignored_dirs = ignored_dirs or set()
        from ...utils.file_filter_util import find_files_with_extensions
        
        # Get all .go files first
        all_go_files = find_files_with_extensions(repo_root, ignored_dirs, {".go"})
        
        # Filter out test files ending in "_test.go"
        filtered_files = []
        excluded_count = 0
        
        for go_file in all_go_files:
            if go_file.name.endswith("_test.go"):
                excluded_count += 1
                logging.debug(f"Excluding Go test file: {go_file}")
            else:
                filtered_files.append(go_file)
        
        if excluded_count > 0:
            logging.info(f"Excluded {excluded_count} Go test files ending in '_test.go' from AST generation")
        
        return filtered_files

    # ---------------- AST Parsing ----------------

    @staticmethod
    def parse_go_ast_to_json(src_path: Path) -> Dict[str, Any]:
        """
        Parse Go source file using tree-sitter.
        Returns parsed JSON dictionary with AST information.
        """
        try:
            with open(src_path, 'rb') as f:
                source_code = f.read()
        except Exception as e:
            raise RuntimeError(f"Failed to read Go file {src_path}: {e}")

        parser = GoASTUtil._get_parser()
        tree = parser.parse(source_code)

        def node_to_dict(node, source_bytes):
            """Convert tree-sitter node to dictionary."""
            result = {
                "type": node.type,
                "start_byte": node.start_byte,
                "end_byte": node.end_byte,
                "start_point": {"row": node.start_point[0], "column": node.start_point[1]},
                "end_point": {"row": node.end_point[0], "column": node.end_point[1]},
                "children": []
            }

            # Add text content for leaf nodes or specific node types
            if node.child_count == 0 or node.type in ['identifier', 'type_identifier', 'field_identifier']:
                try:
                    result["text"] = source_bytes[node.start_byte:node.end_byte].decode('utf-8')
                except UnicodeDecodeError:
                    result["text"] = source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')

            # Recursively process children
            for child in node.children:
                result["children"].append(node_to_dict(child, source_bytes))

            return result

        return {
            "source_file": str(src_path),
            "tree": node_to_dict(tree.root_node, source_code)
        }

    # ---------------- Name Helpers ----------------

    @staticmethod
    def get_function_name(node: Dict[str, Any], package_name: str = "") -> Optional[str]:
        """
        Build a qualified name from function node.
        """
        if node.get("type") not in ["function_declaration", "method_declaration"]:
            return None

        # Find the function name
        func_name = None
        receiver_type = None

        for child in node.get("children", []):
            if child.get("type") == "identifier":
                func_name = child.get("text")
                break

        if not func_name:
            return None

        # For method declarations, find the receiver type
        if node.get("type") == "method_declaration":
            for child in node.get("children", []):
                if child.get("type") == "parameter_list":
                    # This is the receiver
                    for param_child in child.get("children", []):
                        if param_child.get("type") == "parameter_declaration":
                            for param_part in param_child.get("children", []):
                                if param_part.get("type") in ["type_identifier", "pointer_type"]:
                                    if param_part.get("type") == "pointer_type":
                                        # Handle *Type
                                        for ptr_child in param_part.get("children", []):
                                            if ptr_child.get("type") == "type_identifier":
                                                receiver_type = "*" + ptr_child.get("text", "")
                                                break
                                    else:
                                        receiver_type = param_part.get("text", "")
                                    break
                    break

        if receiver_type:
            return f"{receiver_type}.{func_name}"
        elif package_name and package_name != "main":
            return f"{package_name}.{func_name}"
        else:
            return func_name

    @staticmethod
    def _is_function_decl(node_type: str) -> bool:
        """Check if the node type represents a function declaration."""
        return node_type in ["function_declaration", "method_declaration"]

    @staticmethod
    def _is_type_decl(node_type: str) -> bool:
        """Check if the node type represents a type declaration."""
        return node_type in ["type_declaration", "type_spec"]

    @staticmethod
    def _is_call_expr(node_type: str) -> bool:
        """Check if the node type represents a function call."""
        return node_type == "call_expression"

    # ---------------- Function Registry ----------------

    @staticmethod
    def collect_defined_functions(repo_root: Path,
                                  files: List[Path],
                                  _extra_compiler_args: Optional[List[str]] = None,
                                  out_json: Optional[Path] = None) -> Tuple[Set[str], Dict[str, Set[Tuple[str, int, int]]]]:
        """
        Return (set_of_function_names, registry_map)
          registry_map: function_base_name -> set of (file_rel, startLine, endLine)
        """
        func_set: Set[str] = set()
        registry: Dict[str, Set[Tuple[str, int, int]]] = {}

        def extract_package_name(tree_dict):
            """Extract package name from the AST."""
            tree = tree_dict.get("tree", {})
            for child in tree.get("children", []):
                if child.get("type") == "package_clause":
                    for pkg_child in child.get("children", []):
                        if pkg_child.get("type") == "package_identifier":
                            return pkg_child.get("text", "")
            return ""

        def walk_nodes(nodes_list: List[Dict[str, Any]], package_name: str, file_path: Path):
            """Walk through nodes to find function declarations."""
            for node in nodes_list:
                if GoASTUtil._is_function_decl(node.get("type", "")):
                    fq = GoASTUtil.get_function_name(node, package_name)
                    if fq:
                        start_line = node.get("start_point", {}).get("row", 0) + 1  # tree-sitter is 0-based
                        end_line = node.get("end_point", {}).get("row", 0) + 1

                        try:
                            rel = str(file_path.relative_to(repo_root))
                        except ValueError:
                            rel = str(file_path)

                        func_set.add(fq)
                        registry.setdefault(fq, set()).add((rel, start_line, end_line))

                # Recursively process children
                children = node.get("children", [])
                if children:
                    walk_nodes(children, package_name, file_path)

        for f in files:
            try:
                ast_data = GoASTUtil.parse_go_ast_to_json(f)
                package_name = extract_package_name(ast_data)
                tree = ast_data.get("tree", {})
                walk_nodes([tree], package_name, f)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

        if out_json:
            # Convert to JSON output format with new schema - wrap in "function_to_location"
            function_data = {
                fn: [{
                    "file_name": r[0],
                    "start": r[1],
                    "end": r[2]
                } for r in sorted(vals)]
                for fn, vals in registry.items()
            }

            serial = {
                "function_to_location": function_data
            }

            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps(serial, indent=2), encoding="utf-8")
            logging.info(f"[+] Wrote {sum(len(v) for v in function_data.values())} entries for {len(function_data)} functions to {out_json}")

        return func_set, registry

    # ---------------- Class Registry ----------------

    @staticmethod
    def collect_defined_classes(repo_root: Path,
                               files: List[Path],
                               _extra_compiler_args: Optional[List[str]] = None,
                               out_json: Optional[Path] = None) -> Dict[str, Set[Tuple[str, int, int]]]:
        """
        Collect all Go type definitions (structs, interfaces, etc.).
        Returns: class_registry_map: type_name -> set of (file_rel, startLine, endLine)
        """
        logging.info("[+] Building Go type registry...")
        type_registry: Dict[str, Set[Tuple[str, int, int]]] = {}

        def extract_package_name(tree_dict):
            """Extract package name from the AST."""
            tree = tree_dict.get("tree", {})
            for child in tree.get("children", []):
                if child.get("type") == "package_clause":
                    for pkg_child in child.get("children", []):
                        if pkg_child.get("type") == "package_identifier":
                            return pkg_child.get("text", "")
            return ""

        def find_type_specs(node, package_name: str, file_path: Path):
            """Find type specifications in type declarations."""
            if node.get("type") == "type_declaration":
                for child in node.get("children", []):
                    if child.get("type") == "type_spec":
                        # Find the type identifier
                        type_name = None
                        for spec_child in child.get("children", []):
                            if spec_child.get("type") == "type_identifier":
                                type_name = spec_child.get("text")
                                break

                        if type_name:
                            # Build qualified name
                            if package_name and package_name != "main":
                                qualified_name = f"{package_name}.{type_name}"
                            else:
                                qualified_name = type_name

                            start_line = child.get("start_point", {}).get("row", 0) + 1
                            end_line = child.get("end_point", {}).get("row", 0) + 1

                            try:
                                rel = str(file_path.relative_to(repo_root))
                            except ValueError:
                                rel = str(file_path)

                            type_registry.setdefault(qualified_name, set()).add(
                                (rel, start_line, end_line)
                            )

        def walk_nodes(nodes_list: List[Dict[str, Any]], package_name: str, file_path: Path):
            """Walk through nodes to find type declarations."""
            for node in nodes_list:
                find_type_specs(node, package_name, file_path)

                # Recursively process children
                children = node.get("children", [])
                if children:
                    walk_nodes(children, package_name, file_path)

        for f in files:
            try:
                ast_data = GoASTUtil.parse_go_ast_to_json(f)
                package_name = extract_package_name(ast_data)
                tree = ast_data.get("tree", {})
                walk_nodes([tree], package_name, f)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

        if out_json:
            # Convert to the new dictionary schema with "data_type_to_location" key
            json_output_list = []
            for type_name, entries in type_registry.items():
                type_entry = {
                    "data_type_name": type_name,
                    "files": []
                }

                # Add all file entries with line numbers
                for entry in sorted(entries, key=lambda t: (t[0], t[1] or -1, t[2] or -1)):
                    file_entry = {
                        "file_name": entry[0],
                        "start": entry[1],
                        "end": entry[2]
                    }
                    type_entry["files"].append(file_entry)

                if type_entry["files"]:
                    json_output_list.append(type_entry)

            # Wrap in the new dictionary schema
            json_output = {
                "data_type_to_location": json_output_list
            }

            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps(json_output, indent=2, sort_keys=True), encoding="utf-8")
            logging.info(f"[+] Wrote {len(json_output_list)} Go type entries to {out_json}")

        return type_registry

    # ---------------- Call Graph ----------------

    @staticmethod
    def build_call_graph_adjacency(files: List[Path],
                                   _extra_compiler_args: Optional[List[str]] = None,
                                   only_repo_defined: bool = False,
                                   registry_names: Optional[Set[str]] = None) -> Dict[str, List[str]]:
        """
        Build a forward call graph: caller -> [callees].
        """
        forward: Dict[str, Set[str]] = {}
        defined = registry_names if (only_repo_defined and registry_names is not None) else None

        def extract_package_name(tree_dict):
            """Extract package name from the AST."""
            tree = tree_dict.get("tree", {})
            for child in tree.get("children", []):
                if child.get("type") == "package_clause":
                    for pkg_child in child.get("children", []):
                        if pkg_child.get("type") == "package_identifier":
                            return pkg_child.get("text", "")
            return ""

        def extract_call_name(call_node):
            """Extract the called function name from a call expression."""
            for child in call_node.get("children", []):
                if child.get("type") == "identifier":
                    return child.get("text")
                elif child.get("type") == "selector_expression":
                    # Handle method calls like obj.method()
                    parts = []
                    for sel_child in child.get("children", []):
                        if sel_child.get("type") == "identifier":
                            parts.append(sel_child.get("text"))
                        elif sel_child.get("type") == "field_identifier":
                            parts.append(sel_child.get("text"))
                    return ".".join(parts) if parts else None
            return None

        def walk_for_calls(nodes_list: List[Dict[str, Any]], current_fn: Optional[str], package_name: str):
            """Walk nodes to find function calls within function bodies."""
            for node in nodes_list:
                node_type = node.get("type", "")

                # Check if this is a function declaration
                if GoASTUtil._is_function_decl(node_type):
                    current_fn = GoASTUtil.get_function_name(node, package_name)

                # Check if this is a function call
                elif GoASTUtil._is_call_expr(node_type) and current_fn:
                    callee = extract_call_name(node)
                    if callee:
                        matched = False
                        if defined is None:
                            matched = True
                        else:
                            # Check if callee is in defined functions
                            if callee in defined:
                                matched = True
                            # Check if any defined function ends with this callee name
                            elif any(d.endswith("." + callee) for d in defined):
                                matched = True
                            # Check base name matching
                            elif any(d.split(".")[-1] == callee for d in defined):
                                matched = True

                        if matched:
                            forward.setdefault(current_fn, set()).add(callee)

                # Recursively process children
                children = node.get("children", [])
                if children:
                    walk_for_calls(children, current_fn, package_name)

        for f in files:
            try:
                ast_data = GoASTUtil.parse_go_ast_to_json(f)
                package_name = extract_package_name(ast_data)
                tree = ast_data.get("tree", {})
                walk_for_calls([tree], None, package_name)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

        # Normalize to sorted lists and dedupe
        return {k: sorted(v) for k, v in forward.items()}

    # ---------------- Nested Call Graph ----------------

    @staticmethod
    def generate_nested_call_graph(definitions_map: Dict[str, Set[Tuple[str, int, int]]],
                                   adjacency: Dict[str, List[str]],
                                   max_depth: int,
                                   out_json: Path,
                                   data_type_usage: Dict[str, List[str]] = None,
                                   constants_usage: Dict[str, Dict[str, any]] = None) -> None:
        """
        Convert flat adjacency into a nested tree with basic context, data type usage, and constants usage, grouped by file.
        """
        if constants_usage is None:
            constants_usage = {}

        def context_for(fn: str) -> Dict[str, Any]:
            entries = definitions_map.get(fn)
            if not entries:
                # Try to find a match by looking for functions that end with the call name
                method_name = fn.split('.')[-1] if '.' in fn else fn

                # Look for exact matches first
                for def_name in definitions_map:
                    if def_name == fn:
                        entries = definitions_map[def_name]
                        break

                # If no exact match, try pattern matching
                if not entries:
                    for def_name in definitions_map:
                        # Check if defined function ends with the call expression
                        if def_name.endswith("." + fn):
                            entries = definitions_map[def_name]
                            break
                        # Check if defined function ends with the extracted method name
                        elif def_name.endswith("." + method_name):
                            entries = definitions_map[def_name]
                            break
                        # Check if method_name matches the base name (after last .)
                        elif def_name.split(".")[-1] == method_name:
                            entries = definitions_map[def_name]
                            break

                if not entries:
                    return {"file": None, "start": None, "end": None}

            rec = sorted(entries, key=lambda t: (t[0], t[1] or -1, t[2] or -1))[0]
            return {
                "file": rec[0],
                "start": rec[1],
                "end": rec[2],
            }

        def build_node(fn: str, depth: int, seen: Set[str]) -> Dict[str, Any]:
            context = context_for(fn)

            # Skip this node if context is None or missing required fields
            if context is None or context["file"] is None:
                return None

            # Prepare function context with only line numbers (file will be at parent level)
            function_context = {
                "start": context["start"],
                "end": context["end"]
            }

            # Add data type usage if available
            data_types_used = []
            if data_type_usage:
                # Try exact match first
                if fn in data_type_usage:
                    data_types_used = data_type_usage[fn]
                else:
                    # Try to find a match by checking if any key ends with the function name
                    base_fn = fn.split('.')[-1] if '.' in fn else fn
                    for key in data_type_usage:
                        if key == fn or key.endswith("." + fn) or key.endswith("." + base_fn):
                            data_types_used = data_type_usage[key]
                            break

            # Add constants usage if available
            function_constants = {}
            if constants_usage:
                # Try exact match first
                if fn in constants_usage:
                    function_constants = constants_usage[fn]
                else:
                    # Try to find a match by checking if any key ends with the function name
                    base_fn = fn.split('.')[-1] if '.' in fn else fn
                    for key in constants_usage:
                        if key == fn or key.endswith("." + fn) or key.endswith("." + base_fn):
                            function_constants = constants_usage[key]
                            break

            node = {
                "function": fn,
                "context": function_context,
                "data_types_used": data_types_used,
                "file_path": context["file"]  # Store file path temporarily for grouping
            }

            # Only add constants_used if there are constants
            if function_constants:
                node["constants_used"] = function_constants

            if depth >= max_depth:
                return node

            # Add invoking functions with context - use dict format for consistency
            functions_invoked = []
            for callee in adjacency.get(fn, []):
                if callee in seen:
                    continue
                # Only add the function if it exists in the definitions_map (i.e., it's a valid defined function)
                if callee in definitions_map:
                    # Get context for the invoked function
                    callee_context = context_for(callee)
                    invoked_entry = {"function": callee}
                    # Only add context if it has valid file information
                    if callee_context and callee_context.get("file"):
                        invoked_entry["context"] = callee_context
                    functions_invoked.append(invoked_entry)
                # If not in definitions_map, skip it to maintain consistency

            if functions_invoked:
                node["functions_invoked"] = functions_invoked

            return node

        # Build all root nodes first
        all_root_nodes = []
        for caller in sorted(adjacency.keys()):
            root_node = build_node(caller, 0, seen={caller})
            if root_node is not None:
                all_root_nodes.append(root_node)

        # Group functions by file
        files_map = {}

        def process_node_for_grouping(node):
            """Recursively process nodes to group by file and clean up structure."""
            file_path = node.pop("file_path")  # Remove temporary file_path

            # Add to files_map
            if file_path not in files_map:
                files_map[file_path] = []

            # Check if this function is already in the file's function list
            function_exists = False
            for existing_func in files_map[file_path]:
                if existing_func["function"] == node["function"]:
                    function_exists = True
                    break

            if not function_exists:
                func_entry = {
                    "function": node["function"],
                    "context": node["context"],
                    "functions_invoked": node.get("functions_invoked", []),
                    "data_types_used": node.get("data_types_used", [])
                }

                # Only add constants_used if it exists and has content
                if "constants_used" in node and node["constants_used"]:
                    func_entry["constants_used"] = node["constants_used"]

                files_map[file_path].append(func_entry)

            return node

        # Process all root nodes to populate files_map
        for root_node in all_root_nodes:
            process_node_for_grouping(root_node)

        # Convert files_map to the desired output format
        result = []
        for file_path in sorted(files_map.keys()):
            functions = files_map[file_path]
            # Clean up the function structure
            cleaned_functions = []
            for func in functions:
                cleaned_func = {
                    "function": func["function"],
                    "context": {
                        "start": func["context"]["start"],
                        "end": func["context"]["end"]
                    },
                    "functions_invoked": func["functions_invoked"],
                    "data_types_used": func["data_types_used"]
                }

                # Only add constants_used if it exists and has content
                if "constants_used" in func and func["constants_used"]:
                    cleaned_func["constants_used"] = func["constants_used"]
                cleaned_functions.append(cleaned_func)

            result.append({
                "file": file_path,
                "functions": cleaned_functions
            })

        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

        total_functions = sum(len(file_entry["functions"]) for file_entry in result)
        logging.info(f"[+] Wrote Go nested call graph ({len(result)} files, {total_functions} functions) to {out_json}")

    # ---------------- Data Type Usage Analysis ----------------

    @staticmethod
    def _is_go_standard_library_type(type_name: str) -> bool:
        """Check if a type name belongs to Go standard library or built-in types."""
        if not type_name:
            return True

        # Remove package qualifiers for checking
        clean_name = type_name.strip()
        if '.' in clean_name:
            clean_name = clean_name.split('.')[-1]

        # Go built-in types
        go_builtin_types = {
            'bool', 'byte', 'complex64', 'complex128', 'error', 'float32', 'float64',
            'int', 'int8', 'int16', 'int32', 'int64', 'rune', 'string',
            'uint', 'uint8', 'uint16', 'uint32', 'uint64', 'uintptr',
            'interface{}', 'any', 'comparable'
        }

        # Go standard library package prefixes
        std_prefixes = [
            'fmt', 'os', 'io', 'net', 'http', 'time', 'strings', 'strconv',
            'context', 'sync', 'log', 'json', 'xml', 'html', 'url', 'path',
            'filepath', 'bufio', 'bytes', 'crypto', 'encoding', 'errors',
            'flag', 'math', 'rand', 'reflect', 'regexp', 'runtime', 'sort',
            'testing', 'unicode', 'unsafe'
        ]

        # Check if it's a built-in type
        if clean_name in go_builtin_types:
            return True

        # Check for standard library prefixes in the original type name
        for prefix in std_prefixes:
            if type_name.startswith(prefix + '.'):
                return True

        return False

    @staticmethod
    def _extract_go_types_from_node(node: Dict[str, Any]) -> Set[str]:
        """Extract custom type information from a Go AST node."""
        type_names = set()

        node_type = node.get("type", "")

        # Extract from type identifiers
        if node_type == "type_identifier":
            type_name = node.get("text", "")
            if type_name and not GoASTUtil._is_go_standard_library_type(type_name):
                type_names.add(type_name)

        return type_names

    @staticmethod
    def build_data_type_use(files: List[Path],
                           _extra_compiler_args: Optional[List[str]] = None,
                           custom_types_registry: Optional[Set[str]] = None) -> Dict[str, List[str]]:
        """
        Build function → custom data types mapping for Go code.
        """
        data_type_map: Dict[str, List[str]] = {}

        def extract_package_name(tree_dict):
            """Extract package name from the AST."""
            tree = tree_dict.get("tree", {})
            for child in tree.get("children", []):
                if child.get("type") == "package_clause":
                    for pkg_child in child.get("children", []):
                        if pkg_child.get("type") == "package_identifier":
                            return pkg_child.get("text", "")
            return ""

        def traverse_for_go_types(nodes_list: List[Dict[str, Any]], current_fn: str, collected_types: Set[str]):
            """Recursively traverse Go AST nodes to find type usage."""
            for node in nodes_list:
                # Extract types from current node
                node_types = GoASTUtil._extract_go_types_from_node(node)
                collected_types.update(node_types)

                # Recursively process children
                children = node.get("children", [])
                if children:
                    traverse_for_go_types(children, current_fn, collected_types)

        def collect_function_types(nodes_list: List[Dict[str, Any]], package_name: str):
            """Collect type usage for each Go function definition."""
            for node in nodes_list:
                node_type = node.get("type", "")

                if GoASTUtil._is_function_decl(node_type):
                    fn_name = GoASTUtil.get_function_name(node, package_name)
                    if fn_name:
                        collected_types = set()

                        # Analyze function body and parameters
                        children = node.get("children", [])
                        if children:
                            traverse_for_go_types(children, fn_name, collected_types)

                        # Filter to only custom types if registry provided
                        if custom_types_registry:
                            collected_types = {t for t in collected_types if t in custom_types_registry}

                        if collected_types:
                            data_type_map[fn_name] = sorted(collected_types)

                # Recursively process children
                children = node.get("children", [])
                if children:
                    collect_function_types(children, package_name)

        for f in files:
            try:
                ast_data = GoASTUtil.parse_go_ast_to_json(f)
                package_name = extract_package_name(ast_data)
                tree = ast_data.get("tree", {})
                collect_function_types([tree], package_name)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

        return data_type_map

    # ---------------- Constants Usage Analysis ----------------

    @staticmethod
    def _extract_go_constants_from_node(node: Dict[str, Any]) -> Dict[str, any]:
        """Extract constants from a specific Go AST node."""
        constants = {}

        try:
            node_type = node.get("type", "")

            # Only include declared constants with numeric values
            if node_type == "identifier":
                # Check if this is a constant reference (const declarations, etc.)
                text = node.get("text", "")
                # We can't easily determine the value of identifiers in Go AST
                # So we skip them unless we can somehow evaluate them to numeric values
                pass

            # REMOVED: All constants are now filtered out unless we can determine they have numeric values
            # We only want declared constants with numeric values (int, float, double)

        except Exception as e:
            logging.debug(f"Error extracting constant from Go node {node_type}: {e}")

        return constants

    @staticmethod
    def extract_constants_usage(files: List[Path]) -> List[Dict]:
        """Extract constants usage from Go files."""
        constants_usage = []

        def extract_package_name(tree_dict):
            """Extract package name from the AST."""
            tree = tree_dict.get("tree", {})
            for child in tree.get("children", []):
                if child.get("type") == "package_clause":
                    for pkg_child in child.get("children", []):
                        if pkg_child.get("type") == "package_identifier":
                            return pkg_child.get("text", "")
            return ""

        def extract_constants_from_node(nodes_list: List[Dict[str, Any]], current_function: str = None, package_name: str = ""):
            """Walk through nodes to extract constants within function bodies."""
            for node in nodes_list:
                node_type = node.get("type", "")

                # Track current function context
                if GoASTUtil._is_function_decl(node_type):
                    current_function = GoASTUtil.get_function_name(node, package_name)

                # Extract constants if we're in a function
                if current_function:
                    constant_info = GoASTUtil._extract_go_constants_from_node(node)
                    if constant_info:
                        constants_usage.append({
                            "function": current_function,
                            "constants": constant_info
                        })

                # Recursively process children
                children = node.get("children", [])
                if children:
                    extract_constants_from_node(children, current_function, package_name)

        for f in files:
            try:
                ast_data = GoASTUtil.parse_go_ast_to_json(f)
                package_name = extract_package_name(ast_data)
                tree = ast_data.get("tree", {})
                extract_constants_from_node([tree], None, package_name)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

        return constants_usage

    @staticmethod
    def build_constants_usage(files: List[Path],
                             _extra_compiler_args: Optional[List[str]] = None,
                             function_registry: Optional[Set[str]] = None) -> Dict[str, Dict[str, any]]:
        """
        Build constants usage mapping for all Go functions.
        Returns a dictionary mapping function names to their constants usage.
        """
        logging.info("[+] Building Go constants usage...")

        constants_usage_map = {}
        constants_usage = GoASTUtil.extract_constants_usage(files)

        # Aggregate constants by function
        for usage in constants_usage:
            func_name = usage["function"]
            constants = usage["constants"]

            if func_name not in constants_usage_map:
                constants_usage_map[func_name] = {}

            # Merge constants (later occurrences override earlier ones)
            constants_usage_map[func_name].update(constants)

        total_functions = len(constants_usage_map)
        total_constants = sum(len(constants) for constants in constants_usage_map.values())
        logging.info(f"[+] Built Go constants usage for {total_functions} functions ({total_constants} total constants usages)")

        return constants_usage_map


# ----------------------------- CLI -----------------------------

def _build_arg_parser():
    ap = argparse.ArgumentParser(description="Go AST utilities powered by tree-sitter")
    ap.add_argument("--repo", required=True, help="Path to repository root")
    ap.add_argument("--exclude", nargs="*", default=[], help="Directory names to exclude (top-level components)")
    ap.add_argument("--compiler-arg", dest="compiler_args", action="append", default=[],
                    help="Extra compiler args (repeatable)")
    ap.add_argument("--defined-out", default="/tmp/go_defined_functions.json",
                    help="Where to write function definitions JSON")
    ap.add_argument("--nested-out", default="/tmp/go_nested_call_graph.json",
                    help="Where to write nested call graph JSON")
    ap.add_argument("--defined-classes-out", default="/tmp/go_defined_classes.json",
                    help="Path to write Go type definitions JSON with file locations")
    ap.add_argument("--data-type-use-out", default="/tmp/go_data_type_usage.json",
                    help="Path to write function data type usage JSON")
    ap.add_argument("--max-depth", type=int, default=1, help="Nested expansion depth")
    ap.add_argument("--filter-external-calls", action="store_true",
                    help="Keep only callees that are defined in this repo")
    return ap


def main():
    # Add project root to Python path for imports
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))

    # Use centralized logging
    setup_default_logging()

    ap = _build_arg_parser()
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    exclude = set(args.exclude or [])
    comp_args = args.compiler_args or []

    go_files = GoASTUtil.find_go_source_files(repo, exclude)

    defined_out = Path(args.defined_out)
    nested_out = Path(args.nested_out)

    # Collect function definitions
    names, definitions = GoASTUtil.collect_defined_functions(
        repo_root=repo,
        files=go_files,
        _extra_compiler_args=comp_args,
        out_json=defined_out
    )

    # Build adjacency
    adjacency = GoASTUtil.build_call_graph_adjacency(
        files=go_files,
        _extra_compiler_args=comp_args,
        only_repo_defined=args.filter_external_calls,
        registry_names=names
    )

    # Build class registry
    class_registry = GoASTUtil.collect_defined_classes(
        repo_root=repo,
        files=go_files,
        _extra_compiler_args=comp_args,
        out_json=Path(args.defined_classes_out)
    )

    # Build data type usage mapping
    custom_types = set(class_registry.keys()) if class_registry else None
    data_type_usage = GoASTUtil.build_data_type_use(
        files=go_files,
        _extra_compiler_args=comp_args,
        custom_types_registry=custom_types
    )

    data_type_out = Path(args.data_type_use_out)
    data_type_out.parent.mkdir(parents=True, exist_ok=True)
    data_type_out.write_text(json.dumps(data_type_usage, indent=2, sort_keys=True), encoding="utf-8")

    total_functions = len(data_type_usage)
    total_type_usages = sum(len(types) for types in data_type_usage.values())
    logging.info(f"[+] Wrote Go data type usage for {total_functions} functions "
                f"({total_type_usages} total type usages) to {data_type_out}")

    # Build constants usage mapping
    constants_usage = GoASTUtil.build_constants_usage(
        files=go_files,
        _extra_compiler_args=comp_args,
        function_registry=names
    )

    # Nested graph
    GoASTUtil.generate_nested_call_graph(
        definitions_map=definitions,
        adjacency=adjacency,
        max_depth=args.max_depth,
        out_json=nested_out,
        data_type_usage=data_type_usage,
        constants_usage=constants_usage
    )

if __name__ == "__main__":
    main()