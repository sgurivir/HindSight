#!/usr/bin/env python3
# Author: Sridhar Gurivireddy

import argparse
import json
import logging
import mmap
import os
import sys

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import tree_sitter
from tree_sitter_languages import get_language, get_parser

from ...utils.log_util import setup_default_logging

# Supported file extensions for Kotlin parsing
SUPPORTED_EXTENSIONS = [".kt", ".kts"]

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


class KotlinASTUtil:
    """
    Utility class for analyzing Kotlin code using tree_sitter.
    Provides:
      - Class definition collection
      - Function definition collection
      - Call graph construction
      - Nested call graph generation
    """

    def __init__(self):
        """Initialize the Kotlin parser using tree_sitter."""
        try:
            self.language = get_language("kotlin")
            self.parser = get_parser("kotlin")
            self.use_tree_sitter = True
        except Exception as e:
            logging.warning(f"tree-sitter Kotlin parser not available: {e}")
            logging.warning("Falling back to simple regex-based parsing for demonstration")
            self.use_tree_sitter = False
            self.language = None
            self.parser = None

    @staticmethod
    def find_source_files(repo_root: Path, ignored_dirs: Set[str]):
        """Find all Kotlin source files in repo that match SUPPORTED_EXTENSIONS."""
        from ...utils.file_filter_util import find_files_with_extensions
        
        # Directories to ignore by default
        default_ignored = {'.idea', 'build', '.gradle', 'gradle'}
        ignored_dirs = ignored_dirs.union(default_ignored)
        
        # Get all Kotlin files using the centralized utility
        collected_files = find_files_with_extensions(repo_root, ignored_dirs, set(SUPPORTED_EXTENSIONS))
        
        # Additional Kotlin-specific filtering for Gradle build scripts
        filtered_files = []
        for path in collected_files:
            # Skip Gradle build scripts and other problematic files
            if path.name.endswith('.gradle.kts') or 'build.gradle' in path.name:
                continue
            filtered_files.append(path)
        
        return filtered_files

    @staticmethod
    def is_project_file(file_path: str, repo_root: Path) -> bool:
        """Check if file is within the project repository."""
        try:
            Path(file_path).relative_to(repo_root)
            return True
        except ValueError:
            return False

    @staticmethod
    def is_special_method(func_name: str) -> bool:
        """Check if function name is a special method that should be filtered out."""
        # Filter out common special methods in Kotlin
        special_methods = {
            'equals', 'hashCode', 'toString', 'clone', 'finalize',
            'getClass', 'notify', 'notifyAll', 'wait'
        }
        if func_name in special_methods:
            return True

        # Filter out getter/setter methods
        if func_name.startswith('get') or func_name.startswith('set'):
            return True

        return False

    def parse_file(self, file_path: Path) -> Optional[Tuple[tree_sitter.Node, bytes]]:
        """Parse a Kotlin file and return the AST root node."""
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

    def find_nodes_by_type(self, node: tree_sitter.Node, node_types: List[str]) -> List[tree_sitter.Node]:
        """Recursively find all nodes of specific types."""
        nodes = []
        if node.type in node_types:
            nodes.append(node)
        for child in node.children:
            nodes.extend(self.find_nodes_by_type(child, node_types))
        return nodes

    def get_package_name(self, root_node: tree_sitter.Node, source_bytes: bytes) -> str:
        """Extract package name from the AST."""
        for child in root_node.children:
            if child.type == "package_header":
                for grandchild in child.children:
                    if grandchild.type == "identifier":
                        return self.get_node_text(grandchild, source_bytes)
        return ""

    def get_identifier_name(self, node: tree_sitter.Node, source_bytes: bytes) -> str:
        """Extract identifier name from a node."""
        if node.type == "simple_identifier":
            return self.get_node_text(node, source_bytes)
        elif node.type == "type_identifier":
            return self.get_node_text(node, source_bytes)

        for child in node.children:
            if child.type in ["simple_identifier", "type_identifier"]:
                return self.get_node_text(child, source_bytes)
        return ""

    def get_fully_qualified_name(self, name: str, package_name: str = "", enclosing_classes: List[str] = None) -> str:
        """Get fully qualified name for a class or function."""
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
            # Top-level function
            parts.append(method_name)
            return ".".join(parts)

    def extract_classes(self, root_node: tree_sitter.Node, source_bytes: bytes, file_path: str, repo_root: Path) -> List[Dict]:
        """Extract class definitions using tree_sitter parsing."""
        classes = []

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
                if node.type in ["class_declaration", "interface_declaration", "object_declaration"]:
                    name = None
                    # Look for type_identifier in class declaration
                    for child in node.children:
                        if child.type == "type_identifier":
                            name = self.get_node_text(child, source_bytes)
                            break

                    if name:
                        qualified_name = self.get_fully_qualified_name(name, package_name, enclosing_classes)

                        # Get line numbers (tree_sitter uses 0-based, convert to 1-based)
                        start_line_num = node.start_point[0] + 1
                        end_line_num = node.end_point[0] + 1

                        classes.append({
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

            # Process all nodes in the source file
            extract_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting classes from {file_path}: {e}")

        return classes

    def extract_functions(self, root_node: tree_sitter.Node, source_bytes: bytes, file_path: str, repo_root: Path) -> List[Dict]:
        """Extract function definitions using tree_sitter parsing."""
        functions = []

        try:
            # Get package name
            package_name = self.get_package_name(root_node, source_bytes)

            # Convert to relative path
            try:
                rel_path = str(Path(file_path).relative_to(repo_root))
            except ValueError:
                rel_path = file_path

            def extract_functions_from_node(node, enclosing_classes=None):
                if enclosing_classes is None:
                    enclosing_classes = []

                # Handle class/interface/object declarations
                if node.type in ["class_declaration", "interface_declaration", "object_declaration"]:
                    class_name = None
                    # Look for type_identifier in class declaration
                    for child in node.children:
                        if child.type == "type_identifier":
                            class_name = self.get_node_text(child, source_bytes)
                            break

                    if class_name:
                        new_enclosing = enclosing_classes + [class_name]

                        for child in node.children:
                            if child.type == "class_body":
                                for grandchild in child.children:
                                    extract_functions_from_node(grandchild, new_enclosing)

                # Handle function declarations
                elif node.type == "function_declaration":
                    func_name = None
                    # Look for simple_identifier in function declaration
                    for child in node.children:
                        if child.type == "simple_identifier":
                            func_name = self.get_node_text(child, source_bytes)
                            break

                    if func_name and not self.is_special_method(func_name):
                        qualified_name = self.get_method_qualified_name(func_name, package_name, enclosing_classes)

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
                        extract_functions_from_node(child, enclosing_classes)

            # Process all nodes in the source file
            extract_functions_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting functions from {file_path}: {e}")

        return functions

    def extract_function_calls(self, root_node: tree_sitter.Node, source_bytes: bytes) -> List[Dict]:
        """Extract function calls from the AST."""
        calls = []

        try:
            # Get package name
            package_name = self.get_package_name(root_node, source_bytes)

            def extract_calls_from_node(node, current_function=None, enclosing_classes=None):
                if enclosing_classes is None:
                    enclosing_classes = []

                # Track current function context
                if node.type == "function_declaration":
                    func_name = None
                    # Look for simple_identifier in function declaration
                    for child in node.children:
                        if child.type == "simple_identifier":
                            func_name = self.get_node_text(child, source_bytes)
                            break
                    if func_name:
                        current_function = self.get_method_qualified_name(func_name, package_name, enclosing_classes)

                # Track class context
                if node.type in ["class_declaration", "interface_declaration", "object_declaration"]:
                    class_name = None
                    # Look for type_identifier in class declaration
                    for child in node.children:
                        if child.type == "type_identifier":
                            class_name = self.get_node_text(child, source_bytes)
                            break
                    if class_name:
                        enclosing_classes = enclosing_classes + [class_name]

                # Extract function calls
                if current_function and node.type == "call_expression":
                    callee_name = None

                    # Try to extract the function name being called
                    for child in node.children:
                        if child.type == "simple_identifier":
                            callee_name = self.get_node_text(child, source_bytes)
                            break
                        elif child.type == "navigation_expression":
                            # Handle method calls like object.method()
                            for nav_child in child.children:
                                if nav_child.type == "simple_identifier":
                                    # Get the last identifier (method name)
                                    callee_name = self.get_node_text(nav_child, source_bytes)

                    if callee_name and not self._is_kotlin_keyword(callee_name):
                        calls.append({
                            "caller": current_function,
                            "callee": callee_name,
                        })

                # Recursively process child nodes
                for child in node.children:
                    extract_calls_from_node(child, current_function, enclosing_classes)

            # Process all nodes in the source file
            extract_calls_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting function calls: {e}")

        return calls

    def extract_constants_usage(self, root_node: tree_sitter.Node, source_bytes: bytes) -> Dict[str, Dict[str, any]]:
        """Extract constants usage from the AST."""
        usage = {}
        constants_registry = {}  # Store constant name -> value mapping

        try:
            # Get package name
            package_name = self.get_package_name(root_node, source_bytes)

            def extract_literal_value(node: tree_sitter.Node) -> any:
                """Extract literal value from different Kotlin node types."""
                try:
                    if node.type == "integer_literal":
                        text = self.get_node_text(node, source_bytes)
                        try:
                            # Handle different integer formats
                            if text.startswith('0x') or text.startswith('0X'):
                                return int(text, 16)
                            elif text.startswith('0b') or text.startswith('0B'):
                                return int(text, 2)
                            else:
                                return int(text)
                        except ValueError:
                            return text
                    elif node.type == "long_literal":
                        # For long literals, extract the integer part (without 'L' suffix)
                        for child in node.children:
                            if child.type == "integer_literal":
                                text = self.get_node_text(child, source_bytes)
                                try:
                                    return int(text)
                                except ValueError:
                                    return text
                        # Fallback: try to parse the whole text
                        text = self.get_node_text(node, source_bytes)
                        if text.endswith('L') or text.endswith('l'):
                            text = text[:-1]
                        try:
                            return int(text)
                        except ValueError:
                            return text
                    elif node.type == "real_literal":
                        text = self.get_node_text(node, source_bytes)
                        try:
                            return float(text)
                        except ValueError:
                            return text
                    elif node.type == "string_literal":
                        text = self.get_node_text(node, source_bytes)
                        return text
                    elif node.type == "character_literal":
                        text = self.get_node_text(node, source_bytes)
                        return text
                    elif node.type == "boolean_literal":
                        # For boolean literals, check if it has 'true' or 'false' children
                        for child in node.children:
                            if child.type in ["true", "false"]:
                                return child.type == "true"
                        # Fallback: parse the text
                        text = self.get_node_text(node, source_bytes)
                        return text.lower() == 'true'
                except Exception:
                    pass
                return None

            def build_constants_registry(node: tree_sitter.Node, enclosing_classes=None):
                """First pass: build a registry of all constant declarations with their values."""
                if enclosing_classes is None:
                    enclosing_classes = []

                # Track class context
                if node.type in ["class_declaration", "interface_declaration", "object_declaration"]:
                    class_name = None
                    # Look for type_identifier in class declaration
                    for child in node.children:
                        if child.type == "type_identifier":
                            class_name = self.get_node_text(child, source_bytes)
                            break
                    if class_name:
                        enclosing_classes = enclosing_classes + [class_name]

                # Look for property declarations that might be constants
                elif node.type == "property_declaration":
                    # Check if this is a const val or val with compile-time constant
                    is_const = False
                    is_val = False
                    property_name = None
                    property_value = None

                    # Parse modifiers and property details
                    for child in node.children:
                        if child.type == "modifiers":
                            modifiers_text = self.get_node_text(child, source_bytes)
                            is_const = "const" in modifiers_text
                        elif child.type == "val":
                            is_val = True
                        elif child.type == "variable_declaration":
                            # Extract property name
                            for grandchild in child.children:
                                if grandchild.type == "simple_identifier":
                                    property_name = self.get_node_text(grandchild, source_bytes)
                        elif child.type in ["integer_literal", "real_literal", "string_literal",
                                          "character_literal", "boolean_literal", "long_literal"]:
                            property_value = extract_literal_value(child)

                    # Kotlin const val or companion object constants
                    is_constant = (is_const and is_val and property_name and property_value is not None)

                    if is_constant:
                        # Only register numeric constants
                        if isinstance(property_value, (int, float)):
                            # Build fully qualified constant name
                            qualified_name = self.get_fully_qualified_name(property_name, package_name, enclosing_classes)
                            constants_registry[property_name] = property_value
                            constants_registry[qualified_name] = property_value

                            # Also register with class prefix for property access patterns
                            if enclosing_classes:
                                class_qualified = f"{enclosing_classes[-1]}.{property_name}"
                                constants_registry[class_qualified] = property_value

                # Recursively process child nodes
                for child in node.children:
                    build_constants_registry(child, enclosing_classes)

            def extract_constants_from_node(node: tree_sitter.Node, current_function=None, enclosing_classes=None):
                """Second pass: find constant usage in functions."""
                if enclosing_classes is None:
                    enclosing_classes = []

                # Track current function context
                if node.type == "function_declaration":
                    func_name = None
                    # Look for simple_identifier in function declaration
                    for child in node.children:
                        if child.type == "simple_identifier":
                            func_name = self.get_node_text(child, source_bytes)
                            break
                    if func_name:
                        current_function = self.get_method_qualified_name(func_name, package_name, enclosing_classes)
                        usage[current_function] = {}

                # Track class context
                if node.type in ["class_declaration", "interface_declaration", "object_declaration"]:
                    class_name = None
                    # Look for type_identifier in class declaration
                    for child in node.children:
                        if child.type == "type_identifier":
                            class_name = self.get_node_text(child, source_bytes)
                            break
                    if class_name:
                        enclosing_classes = enclosing_classes + [class_name]

                # Extract constants when we're inside a function
                if current_function:
                    # Check for navigation expressions that might be constants (e.g., MyClass.CONSTANT)
                    if node.type == "navigation_expression":
                        # Handle property access like MyClass.CONSTANT
                        property_name = None
                        object_name = None

                        # Parse navigation expression: object.property
                        if len(node.children) >= 3 and node.children[1].type == "navigation_suffix":
                            if node.children[0].type == "simple_identifier":
                                object_name = self.get_node_text(node.children[0], source_bytes)
                            # Look for the property name in the navigation suffix
                            for suffix_child in node.children[1].children:
                                if suffix_child.type == "simple_identifier":
                                    property_name = self.get_node_text(suffix_child, source_bytes)
                                    break

                        # Check if this looks like a constant (uppercase naming convention)
                        if property_name and property_name.isupper():
                            constant_name = f"{object_name}.{property_name}" if object_name else property_name

                            # Try to find the actual value in our constants registry
                            actual_value = None
                            for registry_key in [constant_name, property_name]:
                                if registry_key in constants_registry:
                                    actual_value = constants_registry[registry_key]
                                    break

                            # Only include numeric constants
                            if actual_value is not None and isinstance(actual_value, (int, float)):
                                usage[current_function][constant_name] = actual_value

                    # Check for identifier references that might be constants
                    elif node.type == "simple_identifier":
                        identifier_name = self.get_node_text(node, source_bytes)
                        # Check if this looks like a constant (uppercase naming convention)
                        if identifier_name.isupper() and len(identifier_name) > 1:
                            # Try to find the actual value in our constants registry
                            actual_value = constants_registry.get(identifier_name)
                            # Only include numeric constants
                            if actual_value is not None and isinstance(actual_value, (int, float)):
                                usage[current_function][identifier_name] = actual_value

                # Recursively process child nodes
                for child in node.children:
                    extract_constants_from_node(child, current_function, enclosing_classes)

            # First pass: build constants registry
            build_constants_registry(root_node)

            # Second pass: find constant usage
            extract_constants_from_node(root_node)

        except Exception as e:
            logging.error(f"Error extracting constants usage: {e}")

        return usage

    def _extract_kotlin_constants_from_node(self, node: tree_sitter.Node, source_bytes: bytes) -> Dict[str, any]:
        """Extract constants from a specific node."""
        constants = {}

        try:
            # Handle different types of literal values
            if node.type == "integer_literal":
                text = self.get_node_text(node, source_bytes)
                try:
                    # Handle different integer formats
                    if text.startswith('0x') or text.startswith('0X'):
                        value = int(text, 16)
                    elif text.startswith('0b') or text.startswith('0B'):
                        value = int(text, 2)
                    else:
                        value = int(text)
                    constants[text] = value
                except ValueError:
                    pass
            elif node.type == "real_literal":
                text = self.get_node_text(node, source_bytes)
                try:
                    value = float(text)
                    constants[text] = value
                except ValueError:
                    pass
            elif node.type == "simple_identifier":
                # Check if this is a constant reference (uppercase naming convention)
                text = self.get_node_text(node, source_bytes)
                if text.isupper() and len(text) > 1:
                    # We'll resolve the actual value in the parent method
                    constants[text] = text  # Placeholder, will be resolved later

        except Exception as e:
            logging.debug(f"Error extracting constant from node {node.type}: {e}")

        return constants

    def build_constants_usage(self, repo_root: Path, source_files: List[Path]) -> Dict[str, Dict[str, any]]:
        """
        Build constants usage mapping for all functions.
        Returns a dictionary mapping function names to their constants usage.
        """
        logging.info("[+] Building Kotlin constants usage...")

        if not self.use_tree_sitter:
            logging.warning("tree-sitter Kotlin not available, returning empty constants usage")
            return {}

        constants_usage_map = {}

        for source_file in source_files:
            if not self.is_project_file(str(source_file), repo_root):
                continue

            try:
                parse_result = self.parse_file(source_file)
                if parse_result is None:
                    continue

                root_node, source_bytes = parse_result
                constants_usage = self.extract_constants_usage(root_node, source_bytes)

                # Merge constants usage from this file
                for func_name, constants in constants_usage.items():
                    if func_name not in constants_usage_map:
                        constants_usage_map[func_name] = {}

                    # Merge constants (later occurrences override earlier ones)
                    constants_usage_map[func_name].update(constants)

            except Exception as e:
                logging.warning(f"skip {source_file.name}: {e}")
                continue

        total_functions = len(constants_usage_map)
        total_constants = sum(len(constants) for constants in constants_usage_map.values())
        logging.info(f"[+] Built constants usage for {total_functions} functions ({total_constants} total constants usages)")

        return constants_usage_map

    def _is_kotlin_keyword(self, word: str) -> bool:
        """Check if a word is a Kotlin keyword that should be ignored."""
        kotlin_keywords = {
            'if', 'else', 'when', 'for', 'while', 'do', 'try', 'catch', 'finally',
            'return', 'break', 'continue', 'throw', 'class', 'interface', 'object',
            'fun', 'val', 'var', 'is', 'in', 'as', 'typealias', 'this', 'super',
            'null', 'true', 'false', 'package', 'import', 'public', 'private',
            'internal', 'protected', 'override', 'abstract', 'final', 'open',
            'annotation', 'sealed', 'data', 'inner', 'enum', 'lateinit', 'vararg',
            'suspend', 'inline', 'noinline', 'crossinline', 'reified', 'external',
            'operator', 'infix', 'tailrec', 'const', 'expect', 'actual'
        }
        return word.lower() in kotlin_keywords

    def _find_matching_function(self, callee_name: str, function_registry: Set[str]) -> str:
        """Find a matching function in the registry for a given callee name."""
        # First try exact match
        if callee_name in function_registry:
            return callee_name

        # Create a suffix to search for
        method_suffix = f"::{callee_name}"

        # Try to find functions that end with ::callee_name (most common case)
        for func_name in function_registry:
            if func_name.endswith(method_suffix):
                return func_name

        # If no match found, return None (skip less common cases for performance)
        return None

    def build_class_registry(self, repo_root: Path, source_files: List[Path], out_path: str):
        """
        Collect all class definitions with file + line extents.
        Creates kotlin_defined_data_types.json
        """
        logging.info("[+] Building Kotlin class registry...")

        if not self.use_tree_sitter:
            logging.warning("tree-sitter Kotlin not available, creating empty output")
            # Create empty output
            final_output = {"data_type_to_location": []}
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(final_output, f, indent=2, sort_keys=True)
            logging.info(f"[+] Wrote empty Kotlin class entries to {out_path}")
            return {}

        all_classes = []

        for source_file in source_files:
            if not self.is_project_file(str(source_file), repo_root):
                continue

            try:
                parse_result = self.parse_file(source_file)
                if parse_result is None:
                    continue

                root_node, source_bytes = parse_result
                classes = self.extract_classes(root_node, source_bytes, str(source_file), repo_root)
                all_classes.extend(classes)

            except Exception as e:
                logging.warning(f"skip {source_file.name}: {e}")
                continue

        # Wrap in the new dictionary schema
        final_output = {
            "data_type_to_location": all_classes
        }

        # Write to output file
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(final_output, f, indent=2, sort_keys=True)

        logging.info(f"[+] Wrote {len(all_classes)} class definitions to {out_path}")
        return {cls["data_type_name"]: cls["files"] for cls in all_classes}

    def build_function_registry(self, repo_root: Path, source_files: List[Path], out_path: str):
        """
        Collect all function definitions with file + line extents.
        Creates kotlin_defined_functions.json
        """
        logging.info("[+] Building Kotlin function registry...")

        if not self.use_tree_sitter:
            logging.warning("tree-sitter Kotlin not available, creating empty output")
            # Create empty output
            json_output = {"function_to_location": {}}
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, indent=2, sort_keys=True)
            logging.info(f"[+] Wrote empty Kotlin function entries to {out_path}")
            return set(), {}

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
                logging.warning(f"skip {source_file.name}: {e}")
                continue

        # Write to output file with new schema - wrap in "function_to_location"
        json_output = {
            "function_to_location": function_registry
        }

        # Only write to file if out_path is provided
        if out_path is not None:
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
        logging.info("[+] Building Kotlin call graph...")

        if not self.use_tree_sitter:
            logging.warning("tree-sitter Kotlin not available, returning empty call graph")
            return {}

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
                calls = self.extract_function_calls(root_node, source_bytes)

                for call in calls:
                    caller = call["caller"]
                    callee = call["callee"]

                    # Debug: Log all calls found
                    logging.debug(f"Found call: {caller} -> {callee}")

                    # Try to find matching functions in the registry
                    matched_callee = find_matching_function(callee, function_registry)

                    # Only include calls between project-defined functions
                    if caller in function_registry and matched_callee:
                        if caller not in call_graph:
                            call_graph[caller] = set()
                        call_graph[caller].add(matched_callee)
                        logging.debug(f"Added to call graph: {caller} -> {matched_callee}")
                    else:
                        logging.debug(f"Skipped call (not in registry): {caller} -> {callee}")

            except Exception as e:
                logging.warning(f"skip {source_file.name}: {e}")
                continue

        # Convert sets to sorted lists
        result = {}
        for caller, callees in call_graph.items():
            result[caller] = sorted(list(callees))

        total_relationships = sum(len(callees) for callees in result.values())
        logging.info(f"[+] Built call graph with {len(result)} callers and {total_relationships} total call relationships")
        return result

    def build_nested_call_graph(self, definitions_map: Dict, adjacency: Dict, out_path: str, constants_usage: Dict[str, Dict[str, any]] = None):
        """
        Build nested JSON tree of caller→callees with context (line ranges), grouped by file.
        Max depth is forced to 1 for Kotlin - if A calls B and B calls C, A only shows B in its invoking list.
        """
        if constants_usage is None:
            constants_usage = {}
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

            # Kotlin doesn't have data type usage yet
            data_types_used = []

            # Get constants usage for this function
            function_constants = constants_usage.get(fn, {})

            node = {
                "function": fn,
                "context": function_context,
                "data_types_used": data_types_used,
                "file_path": context["file"]  # Store file path temporarily for grouping
            }

            # Only add constants_used if there are constants
            if function_constants:
                node["constants_used"] = function_constants

            # Force max depth to 1 for Kotlin call graphs
            # If A calls B and B calls C, A should only show B, not C
            if depth >= 1:
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

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=False)

        total_functions = sum(len(file_entry["functions"]) for file_entry in result)
        logging.info(f"[+] Wrote nested call graph ({len(result)} files, {total_functions} functions) to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Kotlin AST analysis using tree_sitter")
    parser.add_argument("--repo", type=str, required=True, help="Path to repo root")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Directories to exclude")
    parser.add_argument("--max-depth", type=int, default=1,
                        help="Max depth for expanding call tree")
    parser.add_argument("--data-types-out", default="/tmp/kotlin_defined_data_types.json",
                        help="Path to write class definitions JSON")
    parser.add_argument("--functions-out", default="/tmp/kotlin_defined_functions.json",
                        help="Path to write function definitions JSON")
    parser.add_argument("--nested-out", default="/tmp/kotlin_nested_callgraph.json",
                        help="Path to write nested call graph JSON")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    exclude_set = set(args.exclude)

    # Add project root to Python path for imports
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))

    # Setup logging
    setup_default_logging()

    # Initialize Kotlin AST utility
    kotlin_util = KotlinASTUtil()

    source_files = KotlinASTUtil.find_source_files(repo_root, exclude_set)
    logging.info(f"[+] Found {len(source_files)} Kotlin source files")

    # Build class registry
    _ = kotlin_util.build_class_registry(
        repo_root=repo_root,
        source_files=source_files,
        out_path=args.data_types_out
    )

    # Build function registry
    defined_funcs, definitions_map = kotlin_util.build_function_registry(
        repo_root=repo_root,
        source_files=source_files,
        out_path=args.functions_out
    )

    # Build call graph (caller→callees)
    adjacency = kotlin_util.build_call_graph(
        repo_root=repo_root,
        source_files=source_files,
        function_registry=defined_funcs
    )

    # Build constants usage
    constants_usage = kotlin_util.build_constants_usage(
        repo_root=repo_root,
        source_files=source_files
    )

    # Build nested call graph JSON
    kotlin_util.build_nested_call_graph(
        definitions_map=definitions_map,
        adjacency=adjacency,
        out_path=args.nested_out,
        constants_usage=constants_usage
    )

    logging.info("[+] Kotlin analysis using tree_sitter completed successfully")
    return 0


if __name__ == "__main__":
    exit(main())