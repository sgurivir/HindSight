#!/usr/bin/env python3
# Author: Sridhar Gurivireddy

import argparse
import json
import logging
import mmap
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Optional

import tree_sitter
from tree_sitter_languages import get_language, get_parser

from hindsight.utils.log_util import setup_default_logging

# Supported file extensions for Java parsing
SUPPORTED_EXTENSIONS = [".java"]

# Performance optimization for find_matching_function
# Use a simple cache based on the hash of the function registry keys
_LOOKUP_CACHE = {}
_MAX_CACHE_SIZE = 8096  # Limit cache size to prevent memory issues
_MAX_QUERY_CACHE_SIZE = 24000  # Limit per-query cache size

def _normalize_query(q: str) -> str:
    return q.strip()

def _simple_name(sig: str) -> str:
    # simple method name without qualifiers/signature
    # e.g., "a.b.C.m(int)" -> "m"
    head = sig.split('(', 1)[0]
    return head.rsplit('.', 1)[-1]

def _last2(sig: str) -> str:
    # last two qualifiers before params (helps disambiguate overloaded same simple name)
    # e.g., "a.b.C.m(int)" -> "C.m"
    head = sig.split('(', 1)[0]
    parts = head.split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else head

def _build_index(function_keys):
    exact = set(function_keys)
    by_simple = {}
    by_suffix2 = {}
    for k in function_keys:
        s = _simple_name(k)
        by_simple.setdefault(s, []).append(k)
        suf2 = _last2(k)
        by_suffix2.setdefault(suf2, []).append(k)
    return {"exact": exact, "by_simple": by_simple, "by_suffix2": by_suffix2, "cache": {}}

def _get_index(function_registry):
    # Use id() of the registry object as cache key for better performance
    # This works because the same registry object will be reused in the same call context
    registry_id = id(function_registry)

    idx = _LOOKUP_CACHE.get(registry_id)
    if idx is None:
        # Clear cache if it gets too large to prevent memory issues
        if len(_LOOKUP_CACHE) >= _MAX_CACHE_SIZE:
            _LOOKUP_CACHE.clear()

        if isinstance(function_registry, set):
            idx = _build_index(function_registry)
        else:
            idx = _build_index(function_registry.keys())
        _LOOKUP_CACHE[registry_id] = idx
    return idx

class JavaASTUtil:
    """
    Utility class for analyzing Java code using tree_sitter.
    Provides:
      - Class/Interface/Enum definition collection
      - Function/Method definition collection
      - Call graph construction
      - Nested call graph generation
      - Data type usage analysis
    """

    def __init__(self):
        """Initialize the Java parser using tree_sitter."""
        try:
            self.language = get_language("java")
            self.parser = get_parser("java")
        except Exception as e:
            logging.error(f"Failed to initialize tree_sitter Java parser: {e}")
            raise

    @staticmethod
    def find_source_files(repo_root: Path, ignored_dirs: Set[str]):
        """Find all Java source files in repo that match SUPPORTED_EXTENSIONS."""
        from hindsight.utils.file_filter_util import find_files_with_extensions

        # Directories to ignore by default
        default_ignored = {'.idea', 'target', 'build', '.gradle', 'gradle', '.mvn', 'out'}
        ignored_dirs = ignored_dirs.union(default_ignored)

        return find_files_with_extensions(repo_root, ignored_dirs, set(SUPPORTED_EXTENSIONS))

    @staticmethod
    def is_project_file(file_path: str, repo_root: Path) -> bool:
        """Check if file is within the project repository."""
        try:
            Path(file_path).relative_to(repo_root)
            return True
        except ValueError:
            return False

    def parse_file(self, file_path: Path) -> Optional[tree_sitter.Node]:
        """Parse a Java file and return the AST root node."""
        try:
            with open(file_path, 'rb') as fh:
                with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    source = mm[:]  # bytes
                    
                    # Skip empty files
                    if not source.strip():
                        return None
                    
                    # Parse with tree_sitter
                    tree = self.parser.parse(source)
                    return tree.root_node, source
        except Exception as e:
            logging.warning(f"Failed to parse {file_path}: {type(e).__name__}: {e}")
            return None

    def get_node_text(self, node: tree_sitter.Node, source_bytes: bytes) -> str:
        """Extract text content from a tree_sitter node."""
        return source_bytes[node.start_byte:node.end_byte].decode('utf-8')

    def _get_parent_nodes(self, target_node: tree_sitter.Node, root_node: tree_sitter.Node) -> List[tree_sitter.Node]:
        """Get all parent nodes of a target node by traversing from root."""
        def find_path(node, path):
            if node == target_node:
                return path
            for child in node.children:
                result = find_path(child, path + [node])
                if result is not None:
                    return result
            return None

        path = find_path(root_node, [])
        return path if path else []

    def get_package_name(self, root_node: tree_sitter.Node, source_bytes: bytes) -> str:
        """Extract package name from the AST."""
        for child in root_node.children:
            if child.type == "package_declaration":
                for grandchild in child.children:
                    if grandchild.type == "scoped_identifier" or grandchild.type == "identifier":
                        return self.get_node_text(grandchild, source_bytes)
        return ""

    def find_nodes_by_type(self, node: tree_sitter.Node, node_types: List[str]) -> List[tree_sitter.Node]:
        """Recursively find all nodes of specific types."""
        nodes = []
        if node.type in node_types:
            nodes.append(node)
        for child in node.children:
            nodes.extend(self.find_nodes_by_type(child, node_types))
        return nodes

    def get_identifier_name(self, node: tree_sitter.Node, source_bytes: bytes) -> str:
        """Extract identifier name from a node."""
        for child in node.children:
            if child.type == "identifier":
                return self.get_node_text(child, source_bytes)
        return ""

    def get_fully_qualified_name(self, name: str, package_name: str = "", enclosing_classes: List[str] = None) -> str:
        """Get fully qualified name for a class, interface, enum, or method."""
        if enclosing_classes is None:
            enclosing_classes = []

        parts = []
        if package_name:
            parts.append(package_name)

        # Add enclosing class names
        parts.extend(enclosing_classes)

        # Add the node name
        if name:
            parts.append(name)

        return ".".join(parts) if parts else ""

    def _extract_imports(self, root_node: tree_sitter.Node, source_bytes: bytes) -> Dict[str, str]:
        """Extract import statements and create a mapping from simple names to fully qualified names."""
        imports_mapping = {}
        
        try:
            for child in root_node.children:
                if child.type == "import_declaration":
                    # Extract the imported class/package name
                    for grandchild in child.children:
                        if grandchild.type == "scoped_identifier" or grandchild.type == "identifier":
                            import_text = self.get_node_text(grandchild, source_bytes)
                            # Handle both single class imports and wildcard imports
                            if import_text and not import_text.endswith(".*"):
                                # Single class import: import com.example.MyClass;
                                simple_name = import_text.split('.')[-1]
                                imports_mapping[simple_name] = import_text
                            # Note: We don't handle wildcard imports (.*) as they would require
                            # knowing all classes in the imported package
        except Exception as e:
            logging.warning(f"Error extracting imports: {e}")
        
        return imports_mapping

    def extract_data_types(self, root_node: tree_sitter.Node, source_bytes: bytes, file_path: str, repo_root: Path) -> List[Dict]:
        """Extract class, interface, and enum definitions."""
        data_types = []

        try:
            # Get package name
            package_name = self.get_package_name(root_node, source_bytes)

            # Convert to relative path
            try:
                rel_path = str(Path(file_path).relative_to(repo_root))
            except ValueError:
                rel_path = file_path

            def extract_from_node(node, enclosing_classes=None):
                if enclosing_classes is None:
                    enclosing_classes = []

                # Handle different types of declarations
                if node.type in ["class_declaration", "interface_declaration", "enum_declaration"]:
                    name = self.get_identifier_name(node, source_bytes)
                    if name:
                        qualified_name = self.get_fully_qualified_name(name, package_name, enclosing_classes)

                        # Get line numbers (tree_sitter uses 0-based, convert to 1-based)
                        start_line_num = node.start_point[0] + 1
                        end_line_num = node.end_point[0] + 1

                        data_types.append({
                            "data_type_name": qualified_name,
                            "files": [{
                                "file_name": rel_path,
                                "start": start_line_num,
                                "end": end_line_num
                            }]
                        })

                        # Process nested classes
                        new_enclosing = enclosing_classes + [name]
                        for child in node.children:
                            if child.type == "class_body":
                                for grandchild in child.children:
                                    extract_from_node(grandchild, new_enclosing)

                # Process other nodes that might contain type declarations
                else:
                    for child in node.children:
                        extract_from_node(child, enclosing_classes)

            # Process all nodes in the compilation unit
            extract_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting data types from {file_path}: {e}")

        return data_types

    def extract_functions(self, root_node: tree_sitter.Node, source_bytes: bytes, file_path: str, repo_root: Path) -> List[Dict]:
        """Extract method and constructor definitions."""
        functions = []

        try:
            # Get package name
            package_name = self.get_package_name(root_node, source_bytes)

            # Convert to relative path
            try:
                rel_path = str(Path(file_path).relative_to(repo_root))
            except ValueError:
                rel_path = file_path

            def extract_methods_from_node(node, enclosing_classes=None):
                if enclosing_classes is None:
                    enclosing_classes = []

                # Handle class/interface declarations
                if node.type in ["class_declaration", "interface_declaration"]:
                    class_name = self.get_identifier_name(node, source_bytes)
                    if class_name:
                        new_enclosing = enclosing_classes + [class_name]

                        for child in node.children:
                            if child.type == "class_body":
                                for grandchild in child.children:
                                    extract_methods_from_node(grandchild, new_enclosing)

                # Handle method declarations
                elif node.type == "method_declaration":
                    method_name = self.get_identifier_name(node, source_bytes)
                    if method_name:
                        qualified_name = self.get_method_qualified_name(method_name, package_name, enclosing_classes)

                        # Get line numbers (tree_sitter uses 0-based, convert to 1-based)
                        start_line_num = node.start_point[0] + 1
                        end_line_num = node.end_point[0] + 1

                        functions.append({
                            "name": qualified_name,
                            "file_name": rel_path,
                            "start": start_line_num,
                            "end": end_line_num,
                        })

                # Handle constructor declarations
                elif node.type == "constructor_declaration":
                    constructor_name = self.get_identifier_name(node, source_bytes)
                    if constructor_name:
                        qualified_name = self.get_constructor_qualified_name(constructor_name, package_name, enclosing_classes)

                        # Get line numbers (tree_sitter uses 0-based, convert to 1-based)
                        start_line_num = node.start_point[0] + 1
                        end_line_num = node.end_point[0] + 1

                        functions.append({
                            "name": qualified_name,
                            "file_name": rel_path,
                            "start": start_line_num,
                            "end": end_line_num,
                        })

                # Process nested structures
                else:
                    for child in node.children:
                        extract_methods_from_node(child, enclosing_classes)

            # Process all nodes in the compilation unit
            extract_methods_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting functions from {file_path}: {e}")

        return functions

    def get_method_qualified_name(self, method_name: str, package_name: str, enclosing_classes: List[str]) -> str:
        """Get fully qualified name for a method."""
        parts = []
        if package_name:
            parts.append(package_name)
        parts.extend(enclosing_classes)

        if enclosing_classes:
            # Method in a class
            return "::".join([".".join(parts), method_name])
        else:
            # Top-level method (rare in Java)
            parts.append(method_name)
            return ".".join(parts)

    def get_constructor_qualified_name(self, constructor_name: str, package_name: str, enclosing_classes: List[str]) -> str:
        """Get fully qualified name for a constructor."""
        parts = []
        if package_name:
            parts.append(package_name)
        parts.extend(enclosing_classes)

        # Constructor name is the same as the class name
        if enclosing_classes:
            return "::".join([".".join(parts), constructor_name])
        else:
            parts.append(constructor_name)
            return ".".join(parts)

    def extract_method_calls(self, root_node: tree_sitter.Node, source_bytes: bytes) -> List[Dict]:
        """Extract method calls from the AST."""
        calls = []

        try:
            # Get package name
            package_name = self.get_package_name(root_node, source_bytes)

            # Build a map of variable names to their types within each method
            def extract_calls_from_node(node, current_method=None, enclosing_classes=None, variable_types=None):
                if enclosing_classes is None:
                    enclosing_classes = []
                if variable_types is None:
                    variable_types = {}

                # Track current method context
                if node.type == "method_declaration":
                    method_name = self.get_identifier_name(node, source_bytes)
                    if method_name:
                        current_method = self.get_method_qualified_name(method_name, package_name, enclosing_classes)
                        # Reset variable types for new method
                        variable_types = {}
                elif node.type == "constructor_declaration":
                    constructor_name = self.get_identifier_name(node, source_bytes)
                    if constructor_name:
                        current_method = self.get_constructor_qualified_name(constructor_name, package_name, enclosing_classes)
                        # Reset variable types for new method
                        variable_types = {}

                # Track class context
                if node.type in ["class_declaration", "interface_declaration"]:
                    class_name = self.get_identifier_name(node, source_bytes)
                    if class_name:
                        enclosing_classes = enclosing_classes + [class_name]

                # Track variable declarations to resolve types
                if node.type == "local_variable_declaration" and current_method:
                    # Extract type and variable name
                    type_name = None
                    var_name = None

                    for child in node.children:
                        if child.type == "type_identifier":
                            type_name = self.get_node_text(child, source_bytes)
                        elif child.type == "variable_declarator":
                            for grandchild in child.children:
                                if grandchild.type == "identifier":
                                    var_name = self.get_node_text(grandchild, source_bytes)
                                    break

                    if type_name and var_name:
                        # Build fully qualified type name
                        if type_name in [cls for cls in enclosing_classes]:
                            # Same class
                            qualified_type = self.get_fully_qualified_name(type_name, package_name, [])
                        else:
                            # Assume it's from the same package for now
                            qualified_type = self.get_fully_qualified_name(type_name, package_name, [])

                        variable_types[var_name] = qualified_type

                # Extract method invocations
                if node.type == "method_invocation" and current_method:
                    method_name = None
                    object_name = None

                    # Check if this is a chained method call (obj.method())
                    if len(node.children) >= 3 and node.children[1].type == ".":
                        # This is obj.method() - get the object name (child 0) and method name (child 2)
                        if node.children[0].type == "identifier" and node.children[2].type == "identifier":
                            object_name = self.get_node_text(node.children[0], source_bytes)
                            method_name = self.get_node_text(node.children[2], source_bytes)
                    else:
                        # This is a direct method call - get the first identifier
                        for child in node.children:
                            if child.type == "identifier":
                                method_name = self.get_node_text(child, source_bytes)
                                break

                    if method_name:
                        # Try to build fully qualified callee name
                        callee_name = method_name

                        # If we have an object name, try to resolve it to a fully qualified method
                        if object_name and object_name in variable_types:
                            # We know the type of the object
                            object_type = variable_types[object_name]
                            # Extract class name from qualified type
                            if "." in object_type:
                                class_parts = object_type.split(".")
                                class_name = class_parts[-1]
                                class_package = ".".join(class_parts[:-1])
                                callee_name = self.get_method_qualified_name(method_name, class_package, [class_name])
                            else:
                                callee_name = self.get_method_qualified_name(method_name, package_name, [object_type])
                        elif object_name:
                            # Try to infer from context - check if it matches any known class
                            for class_name in enclosing_classes:
                                if object_name.lower().startswith(class_name.lower()) or class_name.lower() in object_name.lower():
                                    callee_name = self.get_method_qualified_name(method_name, package_name, [class_name])
                                    break

                        calls.append({
                            "caller": current_method,
                            "callee": callee_name,
                        })

                # Recursively process child nodes
                for child in node.children:
                    extract_calls_from_node(child, current_method, enclosing_classes, variable_types)

            # Process all nodes in the compilation unit
            extract_calls_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting method calls: {e}")

        return calls

    def extract_data_type_usage(self, root_node: tree_sitter.Node, source_bytes: bytes,
                               custom_types_registry: Set[str] = None) -> Dict[str, List[str]]:
        """Extract data type usage by methods."""
        usage = {}

        try:
            # Get package name
            package_name = self.get_package_name(root_node, source_bytes)

            # Extract import statements to build a proper mapping
            imports_mapping = self._extract_imports(root_node, source_bytes)
            
            # Create a mapping of simple names to fully qualified names for faster lookup
            simple_to_qualified = {}
            if custom_types_registry:
                for qualified_name in custom_types_registry:
                    simple_name = qualified_name.split('.')[-1]
                    # Prefer imports mapping over registry mapping for accuracy
                    if simple_name not in imports_mapping:
                        simple_to_qualified[simple_name] = qualified_name
            
            # Add imports mapping (this takes precedence over registry mapping)
            simple_to_qualified.update(imports_mapping)

            def extract_usage_from_node(node, current_method=None, enclosing_classes=None):
                if enclosing_classes is None:
                    enclosing_classes = []

                # Track current method context
                if node.type == "method_declaration":
                    method_name = self.get_identifier_name(node, source_bytes)
                    if method_name:
                        current_method = self.get_method_qualified_name(method_name, package_name, enclosing_classes)
                        usage[current_method] = set()
                elif node.type == "constructor_declaration":
                    constructor_name = self.get_identifier_name(node, source_bytes)
                    if constructor_name:
                        current_method = self.get_constructor_qualified_name(constructor_name, package_name, enclosing_classes)
                        usage[current_method] = set()

                # Track class context
                if node.type in ["class_declaration", "interface_declaration"]:
                    class_name = self.get_identifier_name(node, source_bytes)
                    if class_name:
                        enclosing_classes = enclosing_classes + [class_name]

                # Extract type references when we're inside a method/constructor
                if current_method and node.type == "type_identifier":
                    type_name = self.get_node_text(node, source_bytes)
                    # Filter out primitive types and common Java types
                    if type_name not in ["String", "int", "long", "double", "float", "boolean", "char", "byte", "short", "void", "List", "ArrayList", "Map", "HashMap", "Set", "HashSet"]:
                        # ONLY include types that are defined in the current project (custom_types_registry)
                        if custom_types_registry is None:
                            # If no registry provided, skip this type
                            pass
                        else:
                            # Check if the type (either simple or fully qualified) is in our project's custom types registry
                            fully_qualified_name = None
                            
                            # First, try to get the fully qualified name from imports
                            if type_name in simple_to_qualified:
                                fully_qualified_name = simple_to_qualified[type_name]
                            
                            # Check if the fully qualified name is in our project's registry
                            if fully_qualified_name and fully_qualified_name in custom_types_registry:
                                usage[current_method].add(fully_qualified_name)
                            # Check if the simple name directly matches a fully qualified name in registry
                            elif type_name in custom_types_registry:
                                usage[current_method].add(type_name)
                            # If neither the simple name nor the fully qualified name is in the project registry, skip it
                            # This filters out external library types like java.io.IOException, android.graphics.Bitmap, etc.

                # Recursively process child nodes
                for child in node.children:
                    extract_usage_from_node(child, current_method, enclosing_classes)

            # Process all nodes in the compilation unit
            extract_usage_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting data type usage: {e}")

        # Convert sets to sorted lists
        result = {}
        for method, types in usage.items():
            if types:  # Only include methods that use custom types
                result[method] = sorted(list(types))

        return result

    def build_data_types_registry(self, repo_root: Path, source_files: List[Path], out_path: str):
        """
        Collect all data type definitions (classes, interfaces, enums) with file paths and line numbers.
        Creates java_defined_data_types.json with same schema as cast_util.py
        """
        logging.info("[+] Building Java data types registry...")

        all_data_types = []

        for source_file in source_files:
            if not self.is_project_file(str(source_file), repo_root):
                continue

            try:
                parse_result = self.parse_file(source_file)
                if parse_result is None:
                    continue

                root_node, source_bytes = parse_result
                data_types = self.extract_data_types(root_node, source_bytes, str(source_file), repo_root)
                all_data_types.extend(data_types)

            except Exception as e:
                logging.error(f"Error processing {source_file}: {e}")
                continue

        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        # Wrap in the new dictionary schema
        final_output = {
            "data_type_to_location": all_data_types
        }

        # Write to output file
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(final_output, f, indent=2, sort_keys=True)

        logging.info(f"[+] Wrote {len(all_data_types)} data type definitions to {out_path}")
        return {dt["data_type_name"]: dt["files"] for dt in all_data_types}

    def build_function_registry(self, repo_root: Path, source_files: List[Path], out_path: str):
        """
        Collect all function/method definitions with file + line extents.
        Creates java_defined_functions.json with same schema as cast_util.py
        """
        logging.info("[+] Building Java function registry...")

        function_registry = {}

        for source_file in source_files:
            if not self.is_project_file(str(source_file), repo_root):
                continue

            try:
                parse_result = self.parse_file(source_file)
                if parse_result is None:
                    continue

                root_node, source_bytes = parse_result
                functions = self.extract_functions(root_node, source_bytes, str(source_file), repo_root)

                for func in functions:
                    func_name = func["name"]
                    func_entry = {
                        "file_name": func["file_name"],
                        "start": func["start"],
                        "end": func["end"],
                    }

                    if func_name not in function_registry:
                        function_registry[func_name] = []

                    # Avoid duplicates
                    if func_entry not in function_registry[func_name]:
                        function_registry[func_name].append(func_entry)

            except Exception as e:
                logging.error(f"Error processing {source_file}: {e}")
                continue

        # Write to output file with new schema - wrap in "function_to_location"
        json_output = {
            "function_to_location": function_registry
        }

        # Only write to file if out_path is provided
        if out_path is not None:
            # Create output directory if it doesn't exist
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, indent=2, sort_keys=True)

            total_defs = sum(len(v) for v in function_registry.values())
            logging.info(f"[+] Wrote {total_defs} entries for {len(function_registry)} functions to {out_path}")
        else:
            total_defs = sum(len(v) for v in function_registry.values())
            logging.info(f"[+] Built function registry with {total_defs} entries for {len(function_registry)} functions (no output file specified)")

        return set(function_registry.keys()), function_registry

    def build_call_graph(self, repo_root: Path, source_files: List[Path], function_registry: Set[str]):
        """
        Build caller → callee adjacency (forward call graph).
        """
        logging.info("[+] Building Java call graph...")

        call_graph = {}

        def find_matching_function(callee_name: str, function_registry: Set[str]) -> Optional[str]:
            """Fast strict lookup – no suffix fallback."""
            q = _normalize_query(callee_name)
            idx = _get_index(function_registry)
            cache = idx["cache"]

            if q in cache:
                return cache[q]

            if len(cache) >= _MAX_QUERY_CACHE_SIZE:
                cache.clear()

            # Exact match
            if q in idx["exact"]:
                cache[q] = q
                return q

            # Simple name narrowing
            cands = idx["by_simple"].get(_simple_name(q))
            if not cands:
                cache[q] = None
                return None

            if len(cands) == 1:
                cache[q] = cands[0]
                return cands[0]

            # Disambiguate by last two components
            s2 = _last2(q)
            c2 = [k for k in cands if _last2(k) == s2]
            if len(c2) == 1:
                cache[q] = c2[0]
                return c2[0]

            cache[q] = None
            return None

        for source_file in source_files:
            if not self.is_project_file(str(source_file), repo_root):
                continue

            try:
                parse_result = self.parse_file(source_file)
                if parse_result is None:
                    continue

                root_node, source_bytes = parse_result
                calls = self.extract_method_calls(root_node, source_bytes)

                for call in calls:
                    caller = call["caller"]
                    callee = call["callee"]

                    # Try to find matching functions in the registry
                    matched_callee = find_matching_function(callee, function_registry)

                    # Only include calls between project-defined functions
                    if caller in function_registry and matched_callee:
                        if caller not in call_graph:
                            call_graph[caller] = set()
                        call_graph[caller].add(matched_callee)

            except Exception as e:
                logging.error(f"Error processing {source_file}: {e}")
                continue

        # Convert sets to sorted lists
        result = {}
        for caller, callees in call_graph.items():
            result[caller] = sorted(list(callees))

        total_relationships = sum(len(callees) for callees in result.values())
        logging.info(f"[+] Built call graph with {len(result)} callers and {total_relationships} total call relationships")
        return result

    def build_data_type_use(self, repo_root: Path, source_files: List[Path],
                           custom_types_registry: Set[str] = None):
        """
        Build function → custom data types mapping.
        """
        logging.info("[+] Building Java data type usage...")

        data_type_usage = {}

        for source_file in source_files:
            if not self.is_project_file(str(source_file), repo_root):
                continue

            try:
                parse_result = self.parse_file(source_file)
                if parse_result is None:
                    continue

                root_node, source_bytes = parse_result
                usage = self.extract_data_type_usage(root_node, source_bytes, custom_types_registry)
                data_type_usage.update(usage)

            except Exception as e:
                logging.error(f"Error processing {source_file}: {e}")
                continue

        total_functions = len(data_type_usage)
        total_type_usages = sum(len(types) for types in data_type_usage.values())
        logging.info(f"[+] Built data type usage for {total_functions} functions "
                    f"({total_type_usages} total type usages)")

        return data_type_usage

    def extract_constants_usage(self, root_node: tree_sitter.Node, source_bytes: bytes) -> Dict[str, Dict[str, any]]:
        """Extract constants usage by methods."""
        usage = {}
        constants_registry = {}  # Store constant name -> value mapping

        try:
            # Get package name
            package_name = self.get_package_name(root_node, source_bytes)

            def extract_literal_value(node: tree_sitter.Node) -> any:
                """Extract literal value from different Java node types."""
                try:
                    if node.type == "decimal_integer_literal":
                        text = self.get_node_text(node, source_bytes)
                        try:
                            # Handle different integer formats
                            if text.startswith('0x') or text.startswith('0X'):
                                return int(text, 16)
                            elif text.startswith('0b') or text.startswith('0B'):
                                return int(text, 2)
                            elif text.startswith('0') and len(text) > 1 and text.isdigit():
                                return int(text, 8)
                            else:
                                return int(text)
                        except ValueError:
                            return text
                    elif node.type == "hex_integer_literal":
                        text = self.get_node_text(node, source_bytes)
                        try:
                            return int(text, 16)
                        except ValueError:
                            return text
                    elif node.type == "octal_integer_literal":
                        text = self.get_node_text(node, source_bytes)
                        try:
                            return int(text, 8)
                        except ValueError:
                            return text
                    elif node.type == "binary_integer_literal":
                        text = self.get_node_text(node, source_bytes)
                        try:
                            return int(text, 2)
                        except ValueError:
                            return text
                    elif node.type == "decimal_floating_point_literal":
                        text = self.get_node_text(node, source_bytes)
                        try:
                            return float(text)
                        except ValueError:
                            return text
                    elif node.type == "hex_floating_point_literal":
                        text = self.get_node_text(node, source_bytes)
                        return text  # Keep as string for hex floats
                    elif node.type == "string_literal":
                        text = self.get_node_text(node, source_bytes)
                        return text
                    elif node.type == "character_literal":
                        text = self.get_node_text(node, source_bytes)
                        return text
                    elif node.type == "true" or node.type == "false":
                        text = self.get_node_text(node, source_bytes)
                        return text.lower() == 'true'
                    elif node.type == "null_literal":
                        return None
                except Exception:
                    pass
                return None

            def build_constants_registry(node: tree_sitter.Node, enclosing_classes=None):
                """First pass: build a registry of all constant declarations with their values."""
                if enclosing_classes is None:
                    enclosing_classes = []

                # Track class context
                if node.type in ["class_declaration", "interface_declaration"]:
                    class_name = self.get_identifier_name(node, source_bytes)
                    if class_name:
                        enclosing_classes = enclosing_classes + [class_name]

                # Look for field declarations that might be constants
                elif node.type == "field_declaration":
                    # Check if this is a static final field (constant)
                    is_static = False
                    is_final = False
                    field_name = None
                    field_value = None
                    is_in_interface = any(cls_type == "interface_declaration" for cls_type in [parent.type for parent in self._get_parent_nodes(node, root_node)])

                    # Parse modifiers and field details
                    for child in node.children:
                        if child.type == "modifiers":
                            modifiers_text = self.get_node_text(child, source_bytes)
                            is_static = "static" in modifiers_text
                            is_final = "final" in modifiers_text
                        elif child.type == "variable_declarator":
                            # Extract field name and value
                            for grandchild in child.children:
                                if grandchild.type == "identifier":
                                    field_name = self.get_node_text(grandchild, source_bytes)
                                elif grandchild.type in ["decimal_integer_literal", "hex_integer_literal",
                                                       "octal_integer_literal", "binary_integer_literal",
                                                       "decimal_floating_point_literal", "hex_floating_point_literal",
                                                       "string_literal", "character_literal", "true", "false", "null_literal"]:
                                    field_value = extract_literal_value(grandchild)

                    # Interface fields are implicitly public static final, so treat them as constants
                    # Class fields need explicit static final modifiers
                    is_constant = (is_in_interface and field_name and field_value is not None) or \
                                 (is_static and is_final and field_name and field_value is not None)

                    if is_constant:
                        # Only register numeric constants
                        if isinstance(field_value, (int, float)):
                            # Build fully qualified constant name
                            qualified_name = self.get_fully_qualified_name(field_name, package_name, enclosing_classes)
                            constants_registry[field_name] = field_value
                            constants_registry[qualified_name] = field_value

                            # Also register with class prefix for field access patterns
                            if enclosing_classes:
                                class_qualified = f"{enclosing_classes[-1]}.{field_name}"
                                constants_registry[class_qualified] = field_value

                # Recursively process child nodes
                for child in node.children:
                    build_constants_registry(child, enclosing_classes)

            def extract_constants_from_node(node: tree_sitter.Node, current_method=None, enclosing_classes=None):
                """Second pass: find constant usage in methods."""
                if enclosing_classes is None:
                    enclosing_classes = []

                # Track current method context
                if node.type == "method_declaration":
                    method_name = self.get_identifier_name(node, source_bytes)
                    if method_name:
                        current_method = self.get_method_qualified_name(method_name, package_name, enclosing_classes)
                        usage[current_method] = {}
                elif node.type == "constructor_declaration":
                    constructor_name = self.get_identifier_name(node, source_bytes)
                    if constructor_name:
                        current_method = self.get_constructor_qualified_name(constructor_name, package_name, enclosing_classes)
                        usage[current_method] = {}

                # Track class context
                if node.type in ["class_declaration", "interface_declaration"]:
                    class_name = self.get_identifier_name(node, source_bytes)
                    if class_name:
                        enclosing_classes = enclosing_classes + [class_name]

                # Extract constants when we're inside a method/constructor
                if current_method:
                    # Check for field references that might be constants (e.g., MyClass.CONSTANT)
                    if node.type == "field_access":
                        # Handle static field access like MyClass.CONSTANT
                        field_name = None
                        object_name = None

                        # Parse field access: object.field
                        if len(node.children) >= 3 and node.children[1].type == ".":
                            if node.children[0].type == "identifier":
                                object_name = self.get_node_text(node.children[0], source_bytes)
                            if node.children[2].type == "identifier":
                                field_name = self.get_node_text(node.children[2], source_bytes)

                        # Check if this looks like a constant (uppercase naming convention)
                        if field_name and field_name.isupper():
                            constant_name = f"{object_name}.{field_name}" if object_name else field_name

                            # Try to find the actual value in our constants registry
                            actual_value = None
                            for registry_key in [constant_name, field_name]:
                                if registry_key in constants_registry:
                                    actual_value = constants_registry[registry_key]
                                    break

                            # Only include numeric constants
                            if actual_value is not None and isinstance(actual_value, (int, float)):
                                usage[current_method][constant_name] = actual_value

                    # Check for identifier references that might be constants
                    elif node.type == "identifier":
                        identifier_name = self.get_node_text(node, source_bytes)
                        # Check if this looks like a constant (uppercase naming convention)
                        if identifier_name.isupper() and len(identifier_name) > 1:
                            # Try to find the actual value in our constants registry
                            actual_value = constants_registry.get(identifier_name)
                            # Only include numeric constants
                            if actual_value is not None and isinstance(actual_value, (int, float)):
                                usage[current_method][identifier_name] = actual_value

                # Recursively process child nodes
                for child in node.children:
                    extract_constants_from_node(child, current_method, enclosing_classes)

            # First pass: build constants registry
            build_constants_registry(root_node)

            # Second pass: find constant usage
            extract_constants_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting constants usage: {e}")

        return usage

    def build_constants_usage(self, repo_root: Path, source_files: List[Path],
                             function_registry: Set[str] = None) -> Dict[str, Dict[str, any]]:
        """
        Build function → constants mapping for Java code.

        Args:
            repo_root: Path to repository root
            source_files: List of Java files to analyze
            function_registry: Optional set of known function names to filter against

        Returns:
            Dict mapping function names to dicts of constants they use
        """
        logging.info("[+] Building Java constants usage...")

        constants_usage = {}

        for source_file in source_files:
            if not self.is_project_file(str(source_file), repo_root):
                continue

            try:
                parse_result = self.parse_file(source_file)
                if parse_result is None:
                    continue

                root_node, source_bytes = parse_result
                usage = self.extract_constants_usage(root_node, source_bytes)
                constants_usage.update(usage)

            except Exception as e:
                logging.error(f"Error processing {source_file}: {e}")
                continue

        total_functions = len(constants_usage)
        total_constants_usages = sum(len(constants) for constants in constants_usage.values())
        logging.info(f"[+] Built constants usage for {total_functions} functions "
                    f"({total_constants_usages} total constants usages)")

        return constants_usage

    def build_nested_call_graph(self, definitions_map: Dict, adjacency: Dict,
                               max_depth: int, out_path: str, data_type_usage: Dict = None, constants_usage: Dict = None):
        """
        Build nested JSON tree of caller→callees with context (line ranges) and data type usage, grouped by file.
        Same schema as cast_util.py
        """
        sys.setrecursionlimit(10000)

        def context_for(func_name):
            entries = definitions_map.get(func_name)
            if not entries:
                return None

            # Get the first entry (sorted by file, then line)
            rec = sorted(entries, key=lambda t: (t["file_name"], t["start"]))[0]

            return {
                "file": rec["file_name"],
                "start": rec["start"],
                "end": rec["end"]
            }

        def build_node(fn, depth, seen):
            context = context_for(fn)

            # Skip this node if context is None
            if context is None:
                return None

            # Prepare function context with only line numbers (file will be at parent level)
            function_context = {
                "start": context["start"],
                "end": context["end"]
            }

            # Add data type usage if available
            data_types_used = []
            if data_type_usage and fn in data_type_usage:
                data_types_used = data_type_usage[fn]

            # Add constants usage if available
            constants_used = {}
            if constants_usage and fn in constants_usage:
                constants_used = constants_usage[fn]

            node = {
                "function": fn,
                "context": function_context,
                "data_types_used": data_types_used,
                "file_path": context["file"]  # Store file path temporarily for grouping
            }

            # Only add constants_used if there are constants
            if constants_used:
                node["constants_used"] = constants_used

            if depth >= max_depth:
                return node

            # Add callees with context - use dict format for consistency
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

            # Process functions_invoked (no recursive processing needed since it's just function names)
            if "functions_invoked" in node:
                # Keep the functions_invoked list as-is since it's just function names
                pass

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

        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=False)

        total_functions = sum(len(file_entry["functions"]) for file_entry in result)
        logging.info(f"[+] Wrote nested call graph ({len(result)} files, {total_functions} functions) to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Java AST analysis using tree_sitter")
    parser.add_argument("--repo", type=str, required=True, help="Path to repo root")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Directories to exclude")
    parser.add_argument("--max-depth", type=int, default=1,
                        help="Max depth for expanding call tree")
    parser.add_argument("--functions-out", default="/tmp/java_defined_functions.json",
                        help="Path to write function definitions JSON")
    parser.add_argument("--nested-out", default="/tmp/java_nested_call_graph.json",
                        help="Path to write nested call graph JSON")
    parser.add_argument("--defined-data-types-out", default="/tmp/java_defined_data_types.json",
                        help="Path to write data type definitions JSON")
    parser.add_argument("--data-type-use-out", default="/tmp/java_data_type_usage.json",
                        help="Path to write function data type usage JSON")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    exclude_set = set(args.exclude)

    # Add project root to Python path for imports
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))

    # Use centralized logging
    setup_default_logging()

    # Initialize Java AST utility
    java_util = JavaASTUtil()

    source_files = JavaASTUtil.find_source_files(repo_root, exclude_set)
    logging.info(f"[+] Found {len(source_files)} Java source files")

    # Build data types registry
    data_types_registry = java_util.build_data_types_registry(
        repo_root=repo_root,
        source_files=source_files,
        out_path=args.defined_data_types_out
    )

    # Build function registry
    defined_funcs, definitions_map = java_util.build_function_registry(
        repo_root=repo_root,
        source_files=source_files,
        out_path=args.functions_out
    )

    # Build call graph (caller→callees)
    adjacency = java_util.build_call_graph(
        repo_root=repo_root,
        source_files=source_files,
        function_registry=defined_funcs
    )

    # Build data type usage mapping
    custom_types = set(data_types_registry.keys()) if data_types_registry else None
    data_type_usage = java_util.build_data_type_use(
        repo_root=repo_root,
        source_files=source_files,
        custom_types_registry=custom_types
    )

    # Create output directory if it doesn't exist
    os.makedirs(os.path.dirname(args.data_type_use_out), exist_ok=True)

    with open(args.data_type_use_out, 'w', encoding='utf-8') as f:
        json.dump(data_type_usage, f, indent=2, sort_keys=True)

    total_functions = len(data_type_usage)
    total_type_usages = sum(len(types) for types in data_type_usage.values())
    logging.info(f"[+] Wrote data type usage for {total_functions} functions "
                f"({total_type_usages} total type usages) to {args.data_type_use_out}")

    # Build constants usage mapping
    constants_usage = java_util.build_constants_usage(
        repo_root=repo_root,
        source_files=source_files,
        function_registry=defined_funcs
    )

    constants_out = args.data_type_use_out.replace('data_type_usage.json', 'constants_usage.json')
    os.makedirs(os.path.dirname(constants_out), exist_ok=True)
    with open(constants_out, 'w', encoding='utf-8') as f:
        json.dump(constants_usage, f, indent=2, sort_keys=True)

    total_constants_functions = len(constants_usage)
    total_constants_usages = sum(len(constants) for constants in constants_usage.values())
    logging.info(f"[+] Wrote constants usage for {total_constants_functions} functions "
                f"({total_constants_usages} total constants usages) to {constants_out}")

    # Build nested call graph JSON
    java_util.build_nested_call_graph(
        definitions_map=definitions_map,
        adjacency=adjacency,
        max_depth=args.max_depth,
        out_path=args.nested_out,
        data_type_usage=data_type_usage,
        constants_usage=constants_usage
    )

    logging.info("[+] Java analysis using tree_sitter completed successfully")


if __name__ == "__main__":
    main()