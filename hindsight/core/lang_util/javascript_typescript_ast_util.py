#!/usr/bin/env python3
# Author: Sridhar Gurivireddy
# JavaScript/TypeScript AST analysis using Tree-sitter

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Any, Optional, Tuple

from tree_sitter_languages import get_language, get_parser
from tree_sitter import Node

from ...utils.log_util import setup_default_logging

# Supported file extensions for JS/TS parsing
SUPPORTED_EXTENSIONS = [".js", ".jsx", ".ts", ".tsx"]

class JavaScriptTypeScriptASTUtil:
    """
    Utility class for analyzing JavaScript and TypeScript code using Tree-sitter.
    Provides:
      - Function/component definition collection
      - Class definition collection
      - Call graph construction
      - Import/export tracking
      - React component detection
    """

    def __init__(self):
        """Initialize parsers for JavaScript and TypeScript."""
        self.js_parser = get_parser("javascript")
        self.ts_parser = get_parser("typescript")

    @staticmethod
    def find_source_files(repo_root: Path, ignored_dirs: Set[str]) -> List[Path]:
        """Find all JS/TS source files in repo that match SUPPORTED_EXTENSIONS."""
        from ...utils.file_filter_util import find_files_with_extensions
        return find_files_with_extensions(repo_root, ignored_dirs, set(SUPPORTED_EXTENSIONS))

    def _get_parser_for_file(self, file_path: Path):
        """Get appropriate parser based on file extension."""
        suffix = file_path.suffix.lower()
        if suffix in ['.ts', '.tsx']:
            return self.ts_parser
        else:  # .js, .jsx
            return self.js_parser

    def _parse_file(self, file_path: Path) -> Optional[Node]:
        """Parse a file and return the AST root node."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            parser = self._get_parser_for_file(file_path)
            tree = parser.parse(bytes(source_code, 'utf8'))
            return tree.root_node
        except Exception as e:
            logging.warning(f"Failed to parse {file_path}: {e}")
            return None

    def _is_react_component(self, node: Node, source_code: str) -> bool:
        """
        Heuristic to detect if a function is a React component.
        Checks for:
        - PascalCase name
        - Returns JSX (jsx_element, jsx_fragment, jsx_self_closing_element)
        - TypeScript React.FC type annotation
        """
        # Get function name
        name_node = None
        func_name = None
        
        if node.type == 'function_declaration':
            name_node = node.child_by_field_name('name')
        elif node.type == 'variable_declarator':
            # Handle const Component = () => {} or const Component = function() {}
            name_node = node.child_by_field_name('name')
        elif node.type == 'arrow_function':
            # For arrow functions, we need to check the parent context
            parent = node.parent
            if parent and parent.type == 'variable_declarator':
                name_node = parent.child_by_field_name('name')
        
        if name_node and name_node.type == 'identifier':
            func_name = source_code[name_node.start_byte:name_node.end_byte]
        
        if not func_name:
            return False
        
        # Check if name is PascalCase (starts with uppercase)
        if not func_name[0].isupper():
            return False
        
        # Check for TypeScript React.FC type annotation
        if self._has_react_fc_type(node, source_code):
            return True
        
        # Check if function body contains JSX
        return self._contains_jsx(node, source_code)

    def _contains_jsx(self, node: Node, source_code: str) -> bool:
        """Check if a node contains JSX elements."""
        jsx_types = {
            'jsx_element', 'jsx_fragment', 'jsx_self_closing_element',
            'jsx_opening_element', 'jsx_closing_element'
        }
        
        def check_node(n: Node) -> bool:
            if n.type in jsx_types:
                return True
            for child in n.children:
                if check_node(child):
                    return True
            return False
        
        return check_node(node)
    
    def _has_react_fc_type(self, node: Node, source_code: str) -> bool:
        """Check if a variable declarator has React.FC type annotation."""
        if node.type == 'variable_declarator' or (node.parent and node.parent.type == 'variable_declarator'):
            # Look for type annotation in the variable declarator
            target_node = node if node.type == 'variable_declarator' else node.parent
            
            # Check for type annotation
            type_annotation = target_node.child_by_field_name('type')
            if type_annotation:
                type_text = source_code[type_annotation.start_byte:type_annotation.end_byte]
                return 'React.FC' in type_text or 'FunctionComponent' in type_text
        
        return False

    def _extract_function_name(self, node: Node, source_code: str) -> Optional[str]:
        """Extract function name from various function declaration types."""
        if node.type == 'function_declaration':
            name_node = node.child_by_field_name('name')
            if name_node:
                return source_code[name_node.start_byte:name_node.end_byte]
        
        elif node.type == 'variable_declarator':
            # Handle const funcName = () => {} or const funcName = function() {}
            name_node = node.child_by_field_name('name')
            if name_node and name_node.type == 'identifier':
                return source_code[name_node.start_byte:name_node.end_byte]
        
        elif node.type == 'method_definition':
            # Handle class methods
            name_node = node.child_by_field_name('name')
            if name_node:
                return source_code[name_node.start_byte:name_node.end_byte]
        
        elif node.type == 'export_statement':
            # Handle export function declarations and export const Component = () => {}
            for child in node.children:
                if child.type in ['function_declaration', 'lexical_declaration']:
                    return self._extract_function_name(child, source_code)
        
        elif node.type == 'lexical_declaration':
            # Handle const/let declarations - look for variable_declarator children
            for child in node.children:
                if child.type == 'variable_declarator':
                    return self._extract_function_name(child, source_code)
        
        return None

    def _extract_class_name(self, node: Node, source_code: str) -> Optional[str]:
        """Extract class name from class declaration."""
        if node.type == 'class_declaration':
            name_node = node.child_by_field_name('name')
            if name_node:
                return source_code[name_node.start_byte:name_node.end_byte]
        return None

    def _find_imports_exports(self, root_node: Node, source_code: str) -> Tuple[Dict[str, str], List[str]]:
        """
        Extract import and export information.
        Returns (imports_map, exports_list)
        imports_map: {local_name: module_path}
        exports_list: [exported_names]
        """
        imports = {}
        exports = []
        
        def traverse(node: Node):
            if node.type == 'import_statement':
                # Handle various import patterns
                module_path = None
                imported_names = []
                
                for child in node.children:
                    if child.type == 'string' and 'from' in source_code[node.start_byte:node.end_byte]:
                        # Extract module path from string literal
                        module_text = source_code[child.start_byte:child.end_byte]
                        module_path = module_text.strip('"\'')
                    elif child.type == 'import_clause':
                        # Extract imported names
                        imported_names.extend(self._extract_import_names(child, source_code))
                
                # Map imported names to module path
                for name in imported_names:
                    imports[name] = module_path or ''
            
            elif node.type in ['export_statement', 'export_declaration']:
                # Handle exports
                export_names = self._extract_export_names(node, source_code)
                exports.extend(export_names)
            
            for child in node.children:
                traverse(child)
        
        traverse(root_node)
        return imports, exports

    def _extract_import_names(self, import_clause: Node, source_code: str) -> List[str]:
        """Extract imported names from import clause."""
        names = []
        
        def traverse(node: Node):
            if node.type == 'identifier':
                names.append(source_code[node.start_byte:node.end_byte])
            elif node.type == 'import_specifier':
                # Handle { name } or { name as alias }
                for child in node.children:
                    if child.type == 'identifier':
                        names.append(source_code[child.start_byte:child.end_byte])
                        break  # Take the first identifier (local name)
            
            for child in node.children:
                traverse(child)
        
        traverse(import_clause)
        return names

    def _extract_export_names(self, export_node: Node, source_code: str) -> List[str]:
        """Extract exported names from export statement."""
        names = []
        
        def traverse(node: Node):
            if node.type == 'identifier' and node.parent and node.parent.type != 'string':
                names.append(source_code[node.start_byte:node.end_byte])
            elif node.type in ['function_declaration', 'class_declaration']:
                name = self._extract_function_name(node, source_code) or self._extract_class_name(node, source_code)
                if name:
                    names.append(name)
            
            for child in node.children:
                traverse(child)
        
        traverse(export_node)
        return names

    def _find_function_calls(self, root_node: Node, source_code: str) -> List[Tuple[str, int, int]]:
        """
        Find function calls in the AST.
        Returns list of (function_name, start_line, end_line)
        """
        calls = []
        
        def traverse(node: Node):
            if node.type == 'call_expression':
                # Extract function name from call expression
                function_node = node.child_by_field_name('function')
                if function_node:
                    func_name = self._extract_call_target(function_node, source_code)
                    if func_name:
                        start_line = node.start_point[0] + 1  # Tree-sitter uses 0-based lines
                        end_line = node.end_point[0] + 1
                        calls.append((func_name, start_line, end_line))
            
            for child in node.children:
                traverse(child)
        
        traverse(root_node)
        return calls

    def _extract_call_target(self, function_node: Node, source_code: str) -> Optional[str]:
        """Extract the target function name from a call expression."""
        if function_node.type == 'identifier':
            return source_code[function_node.start_byte:function_node.end_byte]
        elif function_node.type == 'member_expression':
            # Handle obj.method() calls - extract method name
            property_node = function_node.child_by_field_name('property')
            if property_node and property_node.type == 'property_identifier':
                return source_code[property_node.start_byte:property_node.end_byte]
        return None

    def build_function_registry(self, repo_root: Path, source_files: List[Path], out_path: str) -> Tuple[Set[str], Dict[str, List[Dict[str, Any]]]]:
        """
        Collect all function and component definitions with file + line extents.
        Creates js_ts_functions.json
        Returns (function_names_set, definitions_map)
        """
        logging.info("[+] Building JavaScript/TypeScript function registry...")
        
        function_registry = {}
        
        for file_path in source_files:
            try:
                root_node = self._parse_file(file_path)
                if not root_node:
                    continue
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    source_code = f.read()
                
                # Get relative path
                try:
                    rel_path = str(file_path.relative_to(repo_root))
                except ValueError:
                    rel_path = str(file_path)
                
                # Find function definitions
                self._extract_functions_from_node(root_node, source_code, rel_path, function_registry)
                
            except Exception as e:
                logging.warning(f"Failed to process {file_path}: {e}")
                continue
        
        # Write to output file
        json_output = {
            "function_to_location": function_registry
        }
        
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(json_output, f, indent=2, sort_keys=True)
        
        total_defs = sum(len(v) for v in function_registry.values())
        logging.info(f"[+] Wrote {total_defs} entries for {len(function_registry)} functions to {out_path}")
        
        return set(function_registry.keys()), function_registry

    def _extract_functions_from_node(self, node: Node, source_code: str, file_path: str, registry: Dict[str, List[Dict[str, Any]]]):
        """Recursively extract function definitions from AST node."""
        
        # Check if current node is a function definition
        func_name = None
        is_component = False
        
        if node.type in ['function_declaration', 'method_definition']:
            func_name = self._extract_function_name(node, source_code)
            is_component = self._is_react_component(node, source_code)
        
        elif node.type == 'variable_declarator':
            # Handle const Component = () => {} or const func = function() {}
            value_node = node.child_by_field_name('value')
            if value_node and value_node.type in ['arrow_function', 'function']:
                func_name = self._extract_function_name(node, source_code)
                is_component = self._is_react_component(value_node, source_code)
        
        elif node.type == 'export_statement':
            # Handle export function declarations and export const Component = () => {}
            for child in node.children:
                if child.type in ['function_declaration', 'variable_declarator', 'lexical_declaration']:
                    self._extract_functions_from_node(child, source_code, file_path, registry)
        
        elif node.type == 'lexical_declaration':
            # Handle const/let declarations that might contain functions
            for child in node.children:
                if child.type == 'variable_declarator':
                    self._extract_functions_from_node(child, source_code, file_path, registry)
        
        # If we found a function, add it to registry
        if func_name:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            
            # Add React component prefix for better identification
            if is_component:
                func_name = f"React::{func_name}"
            
            func_entry = {
                "file_name": file_path,
                "start": start_line,
                "end": end_line,
            }
            
            if func_name not in registry:
                registry[func_name] = []
            
            # Avoid duplicates
            if func_entry not in registry[func_name]:
                registry[func_name].append(func_entry)
        
        # Recursively process children
        for child in node.children:
            self._extract_functions_from_node(child, source_code, file_path, registry)

    def build_class_registry(self, repo_root: Path, source_files: List[Path], out_path: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Collect all class definitions with file + line extents.
        Creates js_ts_defined_classes.json
        """
        logging.info("[+] Building JavaScript/TypeScript class registry...")
        
        class_registry = {}
        
        for file_path in source_files:
            try:
                root_node = self._parse_file(file_path)
                if not root_node:
                    continue
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    source_code = f.read()
                
                # Get relative path
                try:
                    rel_path = str(file_path.relative_to(repo_root))
                except ValueError:
                    rel_path = str(file_path)
                
                # Find class definitions
                self._extract_classes_from_node(root_node, source_code, rel_path, class_registry)
                
            except Exception as e:
                logging.warning(f"Failed to process {file_path}: {e}")
                continue
        
        # Convert to expected format
        json_output = []
        for class_name, definitions in class_registry.items():
            class_entry = {
                "data_type_name": class_name,
                "files": definitions
            }
            json_output.append(class_entry)
        
        # Wrap in the new dictionary schema
        final_output = {
            "data_type_to_location": json_output
        }
        
        # Write to output file
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(final_output, f, indent=2, sort_keys=True)
        
        logging.info(f"[+] Wrote {len(json_output)} class definitions to {out_path}")
        
        # Add checksums to the generated file for consistency with other languages
        try:
            from .ast_function_signature_util import ASTFunctionSignatureGenerator
            
            ASTFunctionSignatureGenerator.process_data_types_file(
                repo_path=repo_root,
                input_file=Path(out_path),
                output_file=Path(out_path)
            )
            logging.info(f"[+] Added checksums to JS/TS class definitions: {out_path}")
        except Exception as e:
            logging.warning(f"Failed to add checksums to JS/TS class definitions: {e}")
        
        return class_registry

    def _extract_classes_from_node(self, node: Node, source_code: str, file_path: str, registry: Dict[str, List[Dict[str, Any]]]):
        """Recursively extract class definitions from AST node."""
        
        if node.type == 'class_declaration':
            class_name = self._extract_class_name(node, source_code)
            if class_name:
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                
                class_entry = {
                    "file_name": file_path,
                    "start": start_line,
                    "end": end_line,
                }
                
                if class_name not in registry:
                    registry[class_name] = []
                
                # Avoid duplicates
                if class_entry not in registry[class_name]:
                    registry[class_name].append(class_entry)
        
        # Recursively process children
        for child in node.children:
            self._extract_classes_from_node(child, source_code, file_path, registry)

    def build_call_graph(self, repo_root: Path, source_files: List[Path], function_registry: Set[str]) -> Dict[str, List[str]]:
        """
        Build caller → callee adjacency (forward call graph).
        Only includes calls within project-defined functions.
        """
        logging.info("[+] Building JavaScript/TypeScript call graph...")
        
        call_graph = {}
        
        for file_path in source_files:
            try:
                root_node = self._parse_file(file_path)
                if not root_node:
                    continue
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    source_code = f.read()
                
                # Get imports for this file to resolve cross-file calls
                imports, _ = self._find_imports_exports(root_node, source_code)
                
                # Find all function calls in this file
                calls = self._find_function_calls(root_node, source_code)
                
                # Map calls to their containing functions
                for call_name, call_start, call_end in calls:
                    caller_func = self._find_containing_function_at_line(root_node, source_code, call_start)
                    
                    if caller_func and caller_func in function_registry:
                        # Resolve call target (handle imports)
                        resolved_call = self._resolve_call_target(call_name, imports, function_registry)
                        
                        if resolved_call and resolved_call in function_registry:
                            if caller_func not in call_graph:
                                call_graph[caller_func] = []
                            
                            if resolved_call not in call_graph[caller_func]:
                                call_graph[caller_func].append(resolved_call)
                
            except Exception as e:
                logging.warning(f"Failed to build call graph for {file_path}: {e}")
                continue
        
        # Sort call lists for consistent output
        for caller in call_graph:
            call_graph[caller].sort()
        
        total_relationships = sum(len(callees) for callees in call_graph.values())
        logging.info(f"[+] Built call graph with {len(call_graph)} callers and {total_relationships} total call relationships")
        
        return call_graph

    def _find_containing_function_at_line(self, root_node: Node, source_code: str, line_num: int) -> Optional[str]:
        """Find which function contains the given line number."""
        target_byte = self._line_to_byte_offset(source_code, line_num)
        
        def find_function(node: Node) -> Optional[str]:
            # Check if this node contains the target byte
            if node.start_byte <= target_byte <= node.end_byte:
                # Check if this is a function definition
                if node.type in ['function_declaration', 'method_definition']:
                    func_name = self._extract_function_name(node, source_code)
                    if func_name:
                        # Check if this is a React component
                        if self._is_react_component(node, source_code):
                            return f"React::{func_name}"
                        return func_name
                
                elif node.type == 'variable_declarator':
                    value_node = node.child_by_field_name('value')
                    if value_node and value_node.type in ['arrow_function', 'function']:
                        func_name = self._extract_function_name(node, source_code)
                        if func_name:
                            # Check if this is a React component
                            if self._is_react_component(value_node, source_code):
                                return f"React::{func_name}"
                            return func_name
                
                elif node.type == 'lexical_declaration':
                    # Handle const/let declarations that might contain functions
                    for child in node.children:
                        if child.type == 'variable_declarator':
                            value_node = child.child_by_field_name('value')
                            if value_node and value_node.type in ['arrow_function', 'function']:
                                func_name = self._extract_function_name(child, source_code)
                                if func_name:
                                    # Check if this is a React component
                                    if self._is_react_component(value_node, source_code):
                                        return f"React::{func_name}"
                                    return func_name
                
                # Recursively check children
                for child in node.children:
                    result = find_function(child)
                    if result:
                        return result
            
            return None
        
        return find_function(root_node)

    def _line_to_byte_offset(self, source_code: str, line_num: int) -> int:
        """Convert line number to byte offset in source code."""
        lines = source_code.split('\n')
        if line_num <= 0 or line_num > len(lines):
            return 0
        
        # Sum up bytes for all lines before target line
        byte_offset = 0
        for i in range(line_num - 1):
            byte_offset += len(lines[i].encode('utf-8')) + 1  # +1 for newline
        
        return byte_offset

    def _resolve_call_target(self, call_name: str, imports: Dict[str, str], function_registry: Set[str]) -> Optional[str]:
        """
        Resolve call target considering imports and React component prefixes.
        """
        # First, try direct match
        if call_name in function_registry:
            return call_name
        
        # Try with React prefix
        react_name = f"React::{call_name}"
        if react_name in function_registry:
            return react_name
        
        # Try to resolve through imports (simplified - just check if imported name exists)
        if call_name in imports:
            # For now, we don't do cross-file resolution, but this is where it would go
            pass
        
        return None

    def build_nested_call_graph(self, definitions_map: Dict[str, List[Dict[str, Any]]],
                               adjacency: Dict[str, List[str]], max_depth: int, out_path: str):
        """Build call graph in file-based format expected by AST merger."""
        sys.setrecursionlimit(10000)
        
        # Group functions by file to match the format expected by AST merger
        files_map = {}
        
        # Process all functions from definitions_map
        for func_name, definitions in definitions_map.items():
            if not definitions:
                continue
                
            # Get the first definition (sorted by file, then line)
            definition = sorted(definitions, key=lambda t: (t["file_name"], t["start"]))[0]
            file_path = definition["file_name"]
            
            # Create function entry in the format expected by merger
            function_entry = {
                "function": func_name,
                "context": {
                    "file": file_path,
                    "start": definition["start"],
                    "end": definition["end"]
                },
                "functions_invoked": adjacency.get(func_name, []),  # Functions this function calls
                "data_types_used": [],  # JS/TS doesn't have data type usage yet
                "invoked_by": []  # Will be populated by merger
            }
            
            # Group by file
            if file_path not in files_map:
                files_map[file_path] = {
                    "file": file_path,
                    "functions": []
                }
            
            files_map[file_path]["functions"].append(function_entry)
        
        # Convert to list format expected by merger
        file_based_graph = list(files_map.values())
        
        # Write in the format expected by AST merger (file-based format)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(file_based_graph, f, indent=2, sort_keys=False)
        
        logging.info(f"[+] Wrote nested call graph ({len(file_based_graph)} files, {sum(len(f['functions']) for f in file_based_graph)} functions) to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="JavaScript/TypeScript AST analysis using Tree-sitter")
    parser.add_argument("--repo", type=str, required=True, help="Path to repo root")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Directories to exclude")
    parser.add_argument("--max-depth", type=int, default=1,
                        help="Max depth for expanding call tree")
    parser.add_argument("--classes-out", default="/tmp/js_ts_defined_classes.json",
                        help="Path to write class definitions JSON")
    parser.add_argument("--functions-out", default="/tmp/js_ts_functions.json",
                        help="Path to write function definitions JSON")
    parser.add_argument("--nested-out", default="/tmp/js_ts_nested_callgraph.json",
                        help="Path to write nested call graph JSON")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    exclude_set = set(args.exclude)

    # Add project root to Python path for imports
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))

    # Setup logging
    setup_default_logging()

    # Initialize the utility
    js_ts_util = JavaScriptTypeScriptASTUtil()

    source_files = JavaScriptTypeScriptASTUtil.find_source_files(repo_root, exclude_set)
    logging.info(f"[+] Found {len(source_files)} JavaScript/TypeScript source files")

    # Build class registry
    _ = js_ts_util.build_class_registry(
        repo_root=repo_root,
        source_files=source_files,
        out_path=args.classes_out
    )

    # Build function registry
    defined_funcs, definitions_map = js_ts_util.build_function_registry(
        repo_root=repo_root,
        source_files=source_files,
        out_path=args.functions_out
    )

    # Build call graph (caller→callees)
    adjacency = js_ts_util.build_call_graph(
        repo_root=repo_root,
        source_files=source_files,
        function_registry=defined_funcs
    )

    # Build nested call graph JSON
    js_ts_util.build_nested_call_graph(
        definitions_map=definitions_map,
        adjacency=adjacency,
        max_depth=args.max_depth,
        out_path=args.nested_out
    )

    logging.info("[+] JavaScript/TypeScript AST analysis completed successfully")
    return 0


if __name__ == "__main__":
    exit(main())