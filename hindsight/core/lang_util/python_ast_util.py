#!/usr/bin/env python3
# Author: Sridhar Gurivireddy

import argparse
import json
import logging
import subprocess
import sys

from pathlib import Path
from typing import Dict, List, Set

from ...utils.log_util import setup_default_logging

# Supported file extensions for Python parsing
SUPPORTED_EXTENSIONS = [".py"]

class PythonASTUtil:
    """
    Utility class for analyzing Python code using ast-grep.
    Provides:
      - Class definition collection
      - Function definition collection
      - Call graph construction
      - Nested call graph generation
    """

    @staticmethod
    def find_source_files(repo_root: Path, ignored_dirs: Set[str]):
        """Find all Python source files in repo that match SUPPORTED_EXTENSIONS."""
        from ...utils.file_filter_util import find_files_with_extensions
        return find_files_with_extensions(repo_root, ignored_dirs, set(SUPPORTED_EXTENSIONS))

    @staticmethod
    def run_ast_grep(pattern: str, repo_root: Path) -> List[Dict]:
        """Run ast-grep with given pattern and return JSON results."""
        try:
            cmd = [
                "ast-grep",
                "--lang", "python",
                "-p", pattern,
                "--json",
                str(repo_root)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            if result.stdout.strip():
                return json.loads(result.stdout)
            else:
                return []

        except subprocess.CalledProcessError as e:
            logging.error(f"ast-grep failed: {e}")
            logging.error(f"stderr: {e.stderr}")
            return []
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse ast-grep JSON output: {e}")
            return []

    @staticmethod
    def is_project_file(file_path: str, repo_root: Path) -> bool:
        """Check if file is within the project repository."""
        try:
            Path(file_path).relative_to(repo_root)
            return True
        except ValueError:
            return False

    @staticmethod
    def build_class_registry(repo_root: Path, out_path: str):
        """
        Collect all class definitions with file + line extents.
        Creates python_defined_classes.json
        Only includes classes defined within the project.
        """
        logging.info("[+] Building Python class registry...")

        # Use ast-grep to find all class definitions
        ast_results = PythonASTUtil.run_ast_grep('class $NAME', repo_root)

        # Convert to the expected format
        json_output = []

        for result in ast_results:
            try:
                class_name = result['metaVariables']['single']['NAME']['text']
                file_path = result['file']

                # Only include classes defined within the project
                if not PythonASTUtil.is_project_file(file_path, repo_root):
                    continue

                start_line = result['range']['start']['line']
                end_line = result['range']['end']['line']

                # Convert to relative path
                try:
                    rel_path = str(Path(file_path).relative_to(repo_root))
                except ValueError:
                    rel_path = file_path

                # Create entry in the expected format
                class_entry = {
                    "data_type_name": class_name,
                    "files": [{
                        "file_name": rel_path,
                        "start": start_line,
                        "end": end_line
                    }]
                }
                json_output.append(class_entry)

            except (KeyError, TypeError) as e:
                logging.warning(f"Failed to process class result: {e}")
                continue

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
            from pathlib import Path

            # Get repo root by going up from the output file
            repo_root = Path(out_path).parent
            while repo_root.parent != repo_root and not (repo_root / '.git').exists():
                repo_root = repo_root.parent

            ASTFunctionSignatureGenerator.process_data_types_file(
                repo_path=repo_root,
                input_file=Path(out_path),
                output_file=Path(out_path)
            )
            logging.info(f"[+] Added checksums to Python class definitions: {out_path}")
        except Exception as e:
            logging.warning(f"Failed to add checksums to Python class definitions: {e}")
            logging.warning("Python data types will be available without checksums")

        return {entry["data_type_name"]: entry["files"] for entry in json_output}

    @staticmethod
    def is_special_method(func_name: str) -> bool:
        """Check if function name is a special/dunder method that should be filtered out."""
        # Filter out dunder methods (special methods like __init__, __eq__, etc.)
        if func_name.startswith('__') and func_name.endswith('__'):
            return True

        # Filter out common property/descriptor methods
        property_methods = {'getter', 'setter', 'deleter'}
        if func_name in property_methods:
            return True

        return False

    @staticmethod
    def _find_containing_class(file_path: str, line_num: int) -> str:
        """
        Find which class contains the given line number in the file.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            current_class = None
            current_indent = 0

            for _, line in enumerate(lines[:line_num], 1):
                stripped = line.strip()
                if stripped.startswith('class '):
                    # Extract class name
                    class_def = stripped[6:]  # Remove 'class '
                    # Get class name (before the colon or opening parenthesis)
                    if '(' in class_def:
                        class_name = class_def.split('(')[0].strip()
                    else:
                        class_name = class_def.split(':')[0].strip()
                    current_class = class_name
                    current_indent = len(line) - len(line.lstrip())
                elif current_class and line.strip() and len(line) - len(line.lstrip()) <= current_indent:
                    # We've moved out of the current class
                    if not stripped.startswith('class '):
                        current_class = None

            return current_class

        except Exception as e:
            logging.warning(f"Failed to find containing class in {file_path}:{line_num}: {e}")
            return None

    @staticmethod
    def build_function_registry(repo_root: Path, out_path: str):
        """
        Collect all function definitions with file + line extents.
        Creates python_functions.json
        Filters out special methods and dunder methods.
        Only includes functions defined within the project.
        Uses class_name::function_name format for methods.
        """
        logging.info("[+] Building Python function registry...")

        # Use ast-grep to find all function definitions (both def and async def)
        function_results = PythonASTUtil.run_ast_grep('def $NAME($$$)', repo_root)
        async_function_results = PythonASTUtil.run_ast_grep('async def $NAME($$$)', repo_root)

        # Combine results
        all_results = function_results + async_function_results

        # Group by function name (handle overloads/multiple definitions)
        function_registry = {}

        for result in all_results:
            try:
                func_name = result['metaVariables']['single']['NAME']['text']

                # Skip special methods and dunder methods
                if PythonASTUtil.is_special_method(func_name):
                    continue

                file_path = result['file']

                # Only include functions defined within the project
                if not PythonASTUtil.is_project_file(file_path, repo_root):
                    continue

                start_line = result['range']['start']['line']
                end_line = result['range']['end']['line']

                # Check if this function is inside a class
                containing_class = PythonASTUtil._find_containing_class(file_path, start_line)

                # Format function name with class if it's a method
                if containing_class:
                    qualified_func_name = f"{containing_class}::{func_name}"
                else:
                    qualified_func_name = func_name

                # Convert to relative path
                try:
                    rel_path = str(Path(file_path).relative_to(repo_root))
                except ValueError:
                    rel_path = file_path

                # Create function entry
                func_entry = {
                    "file_name": rel_path,
                    "start": start_line,
                    "end": end_line,
                }

                # Group by qualified function name
                if qualified_func_name not in function_registry:
                    function_registry[qualified_func_name] = []

                # Avoid duplicates
                if func_entry not in function_registry[qualified_func_name]:
                    function_registry[qualified_func_name].append(func_entry)

            except (KeyError, TypeError) as e:
                logging.warning(f"Failed to process function result: {e}")
                continue

        # Write to output file with new schema - wrap in "function_to_location"
        json_output = {
            "function_to_location": function_registry
        }

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(json_output, f, indent=2, sort_keys=True)

        total_defs = sum(len(v) for v in function_registry.values())
        logging.info(f"[+] Wrote {total_defs} entries for {len(function_registry)} functions to {out_path}")
        return set(function_registry.keys()), function_registry

    @staticmethod
    def build_call_graph(repo_root: Path, function_registry: Set[str]):
        """
        Build caller → callee adjacency (forward call graph).
        Only includes calls within project-defined functions.
        """
        logging.info("[+] Building Python call graph...")

        # Use ast-grep to find function calls
        call_results = PythonASTUtil.run_ast_grep('$FUNC($$$)', repo_root)

        # Also find method calls
        method_call_results = PythonASTUtil.run_ast_grep('$OBJ.$METHOD($$$)', repo_root)

        # Build call graph mapping
        call_graph = {}

        # Process regular function calls
        for result in call_results:
            try:
                func_name = result['metaVariables']['single']['FUNC']['text']
                file_path = result['file']
                line_num = result['range']['start']['line']

                # Only process calls within project files
                if not PythonASTUtil.is_project_file(file_path, repo_root):
                    continue

                # Find which function this call is inside
                caller_func = PythonASTUtil._find_containing_function(file_path, line_num)

                # Only include calls between project-defined functions
                if caller_func and func_name in function_registry and caller_func in function_registry:
                    if caller_func not in call_graph:
                        call_graph[caller_func] = set()
                    call_graph[caller_func].add(func_name)

            except (KeyError, TypeError) as e:
                logging.warning(f"Failed to process call result: {e}")
                continue

        # Process method calls
        for result in method_call_results:
            try:
                method_name = result['metaVariables']['single']['METHOD']['text']
                file_path = result['file']
                line_num = result['range']['start']['line']

                # Only process calls within project files
                if not PythonASTUtil.is_project_file(file_path, repo_root):
                    continue

                # Find which function this call is inside
                caller_func = PythonASTUtil._find_containing_function(file_path, line_num)

                # Only include calls between project-defined functions
                if caller_func and method_name in function_registry and caller_func in function_registry:
                    if caller_func not in call_graph:
                        call_graph[caller_func] = set()
                    call_graph[caller_func].add(method_name)

            except (KeyError, TypeError) as e:
                logging.warning(f"Failed to process method call result: {e}")
                continue

        # Convert sets to sorted lists
        result = {}
        for caller, callees in call_graph.items():
            result[caller] = sorted(list(callees))

        total_relationships = sum(len(callees) for callees in result.values())
        logging.info(f"[+] Built call graph with {len(result)} callers and {total_relationships} total call relationships")
        return result

    @staticmethod
    def _find_containing_function(file_path: str, line_num: int) -> str:
        """
        Find which function contains the given line number in the file.
        Returns qualified name (class::function) if function is inside a class.
        """
        try:
            # Read the file and find function definitions before this line
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            current_function = None
            current_class = None
            current_function_indent = 0
            current_class_indent = 0

            for _, line in enumerate(lines[:line_num], 1):
                stripped = line.strip()
                line_indent = len(line) - len(line.lstrip())

                # Check for class definition
                if stripped.startswith('class '):
                    class_def = stripped[6:]  # Remove 'class '
                    if '(' in class_def:
                        class_name = class_def.split('(')[0].strip()
                    else:
                        class_name = class_def.split(':')[0].strip()
                    current_class = class_name
                    current_class_indent = line_indent
                    current_function = None  # Reset function when entering new class

                # Check for function definition
                elif stripped.startswith('def ') or stripped.startswith('async def '):
                    # Extract function name
                    if stripped.startswith('async def '):
                        func_def = stripped[10:]  # Remove 'async def '
                    else:
                        func_def = stripped[4:]   # Remove 'def '

                    # Get function name (before the opening parenthesis)
                    func_name = func_def.split('(')[0].strip()
                    current_function = func_name
                    current_function_indent = line_indent

                # Check if we've moved out of current class or function
                elif line.strip() and line_indent <= current_class_indent:
                    if not stripped.startswith('class '):
                        current_class = None
                        current_function = None
                elif current_function and line.strip() and line_indent <= current_function_indent:
                    if not (stripped.startswith('def ') or stripped.startswith('async def ')):
                        current_function = None

            # Return qualified name if function is inside a class
            if current_function:
                if current_class:
                    return f"{current_class}::{current_function}"
                else:
                    return current_function

            return None

        except Exception as e:
            logging.warning(f"Failed to find containing function in {file_path}:{line_num}: {e}")
            return None

    @staticmethod
    def build_nested_call_graph(definitions_map: Dict, adjacency: Dict, max_depth: int, out_path: str):
        """Build nested JSON tree of caller→callees with context (line ranges)."""
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

            # Prepare function_context with all the data
            function_context = context.copy()
            function_context["data_types_used"] = []  # Python doesn't have data type usage yet

            node = {
                "function": fn,
                "function_context": function_context
            }

            if depth >= max_depth:
                return node

            # Add callees
            invoking_list = []
            for callee in adjacency.get(fn, []):
                if callee in seen:
                    continue
                seen.add(callee)
                child_node = build_node(callee, depth + 1, seen)
                if child_node is not None:
                    invoking_list.append(child_node)
                seen.remove(callee)

            if invoking_list:
                function_context["invoking"] = invoking_list

            return node

        roots = []
        for caller in sorted(adjacency.keys()):
            root_node = build_node(caller, 0, seen={caller})
            if root_node is not None:
                roots.append(root_node)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(roots, f, indent=2, sort_keys=False)

        logging.info(f"[+] Wrote nested call graph ({len(roots)} roots) to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Python AST analysis using ast-grep")
    parser.add_argument("--repo", type=str, required=True, help="Path to repo root")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Directories to exclude")
    parser.add_argument("--max-depth", type=int, default=1,
                        help="Max depth for expanding call tree")
    parser.add_argument("--classes-out", default="/tmp/python_defined_classes.json",
                        help="Path to write class definitions JSON")
    parser.add_argument("--functions-out", default="/tmp/python_functions.json",
                        help="Path to write function definitions JSON")
    parser.add_argument("--nested-out", default="/tmp/python_nested_callgraph.json",
                        help="Path to write nested call graph JSON")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    exclude_set = set(args.exclude)

    # Add project root to Python path for imports
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))

    # Setup logging
    setup_default_logging()

    # Check if ast-grep is available
    try:
        subprocess.run(["ast-grep", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.error("ast-grep is not installed or not in PATH. Please install ast-grep first.")
        return 1

    source_files = PythonASTUtil.find_source_files(repo_root, exclude_set)
    logging.info(f"[+] Found {len(source_files)} Python source files")

    # Build class registry
    _ = PythonASTUtil.build_class_registry(
        repo_root=repo_root,
        out_path=args.classes_out
    )

    # Build function registry
    defined_funcs, definitions_map = PythonASTUtil.build_function_registry(
        repo_root=repo_root,
        out_path=args.functions_out
    )

    # Build call graph (caller→callees)
    adjacency = PythonASTUtil.build_call_graph(
        repo_root=repo_root,
        function_registry=defined_funcs
    )

    # Build nested call graph JSON
    PythonASTUtil.build_nested_call_graph(
        definitions_map=definitions_map,
        adjacency=adjacency,
        max_depth=args.max_depth,
        out_path=args.nested_out
    )

    logging.info("[+] Python AST analysis completed successfully")
    return 0


if __name__ == "__main__":
    exit(main())