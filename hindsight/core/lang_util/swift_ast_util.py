#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwiftASTUtil.py — Swift AST utilities built on SourceKitten
Author: Sridhar Gurivireddy
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any, NamedTuple

from ...utils.log_util import setup_default_logging


class NormalizedName(NamedTuple):
    """Normalized components of a Swift function/method name for efficient matching."""
    resolved: str      # Full resolved name (e.g., "Board::movePiece")
    no_params: str     # Name without parameters (e.g., "Board::movePiece")
    class_part: Optional[str]  # Class name if qualified (e.g., "Board")
    method: str        # Method name only (e.g., "movePiece")


class RegistryIndex:
    """Pre-built indexes for fast callee name resolution."""
    
    def __init__(self, registry_names: Optional[Set[str]] = None):
        self.exact_names: Set[str] = set()
        self.base_names: Set[str] = set()  # Names without parameters
        self.class_method_map: Dict[Tuple[str, str], Set[str]] = {}  # (class, method) -> full definitions
        self.method_map: Dict[str, Set[str]] = {}  # method_name -> full definitions
        
        if registry_names:
            self._build_indexes(registry_names)
    
    def _build_indexes(self, registry_names: Set[str]) -> None:
        """Build all lookup indexes from registry names."""
        for name in registry_names:
            # Normalize the registry name
            normalized = normalize_name(name)
            
            # Add to exact names
            self.exact_names.add(name)
            self.exact_names.add(normalized.resolved)
            
            # Add to base names (without parameters)
            self.base_names.add(normalized.no_params)
            
            # Add to class-method mapping
            if normalized.class_part:
                key = (normalized.class_part, normalized.method)
                self.class_method_map.setdefault(key, set()).add(name)
            
            # Add to method mapping
            self.method_map.setdefault(normalized.method, set()).add(name)
    
    def find_exact_match(self, name: str) -> bool:
        """O(1) exact name lookup."""
        return name in self.exact_names
    
    def find_base_match(self, name: str) -> bool:
        """O(1) base name lookup (without parameters)."""
        return name in self.base_names
    
    def find_class_method_matches(self, class_name: str, method_name: str) -> Set[str]:
        """O(1) class-method lookup."""
        return self.class_method_map.get((class_name, method_name), set())
    
    def find_method_matches(self, method_name: str) -> Set[str]:
        """O(1) method name lookup."""
        return self.method_map.get(method_name, set())


def normalize_name(callee: str) -> NormalizedName:
    """
    Normalize callee name once to avoid repeated string parsing.
    
    Args:
        callee: Raw callee name (e.g., "Board::movePiece(direction:)", "self.method?", "Class.init()")
    
    Returns:
        NormalizedName with parsed components
    """
    # Clean up Swift operators and optional markers
    clean_callee = callee.rstrip('?!')
    
    # Handle .init normalization - convert "Class.init" to "Class::init"
    if '.init' in clean_callee:
        clean_callee = clean_callee.replace('.init', '::init')
    
    # Split by :: for qualified names
    if '::' in clean_callee:
        parts = clean_callee.split('::', 1)
        class_part = parts[0]
        method_with_params = parts[1]
    else:
        class_part = None
        method_with_params = clean_callee
    
    # Remove parameters to get base method name
    method = method_with_params.split('(')[0]
    
    # Build normalized components
    if class_part:
        resolved = f"{class_part}::{method}"
        no_params = resolved
    else:
        resolved = method
        no_params = method
    
    return NormalizedName(
        resolved=resolved,
        no_params=no_params,
        class_part=class_part,
        method=method
    )


def _run(cmd: List[str], capture: bool = True) -> str:
    p = subprocess.run(cmd, capture_output=capture, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nSTDERR:\n{p.stderr}")
    return p.stdout if capture else ""


def _sdk_path() -> Optional[str]:
    try:
        return _run(["xcrun", "--show-sdk-path"]).strip() or None
    except Exception:
        return None


def _byte_to_line_offsets(text: str) -> List[int]:
    # prefix sum of line lengths (including newlines) so we can map byte offsets -> (line)
    offs = [0]
    total = 0
    for ln in text.splitlines(True):
        total += len(ln)
        offs.append(total)
    return offs


def _line_for_offset(offset: int, line_offsets: List[int]) -> int:
    # binary search
    lo, hi = 0, len(line_offsets) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if line_offsets[mid] <= offset < line_offsets[mid + 1] if mid + 1 < len(line_offsets) else offset >= line_offsets[mid]:
            return mid + 1  # 1-based lines
        if offset < line_offsets[mid]:
            hi = mid - 1
        else:
            lo = mid + 1
    return max(1, min(len(line_offsets), lo))




# Pre-compiled sets for O(1) lookups instead of tuple membership tests
_SWIFT_FUNCTION_KINDS = frozenset([
    "source.lang.swift.decl.function.free",
    "source.lang.swift.decl.function.method.instance",
    "source.lang.swift.decl.function.method.static",
    "source.lang.swift.decl.function.method.class",
    "source.lang.swift.decl.function.constructor",
    "source.lang.swift.decl.function.destructor",
    "source.lang.swift.decl.function.operator",
    "source.lang.swift.decl.function.subscript",
])

_SWIFT_TYPE_CONTEXT_KINDS = frozenset([
    "source.lang.swift.decl.class",
    "source.lang.swift.decl.struct",
    "source.lang.swift.decl.enum",
    "source.lang.swift.decl.extension",
    "source.lang.swift.decl.protocol",
])

_SWIFT_CLASS_TYPE_KINDS = frozenset([
    "source.lang.swift.decl.class",
    "source.lang.swift.decl.struct",
    "source.lang.swift.decl.enum",
    "source.lang.swift.decl.protocol",
    "source.lang.swift.decl.extension",
])

_SWIFT_CALL_EXPR_KINDS = frozenset([
    "source.lang.swift.expr.call",
    "source.lang.swift.expr.call.unresolved",
])


class SwiftASTUtil:
    """
    Utilities built on SourceKitten to analyze Swift code:
      - find_swift_source_files: discover .swift sources
      - parse_swift_ast_to_json: run SourceKitten structure and return JSON
      - collect_defined_functions: extract function/method declarations + ranges
      - build_call_graph_adjacency: caller -> callees from call-expression nodes
      - generate_nested_call_graph: nested tree JSON with context
    """

    # ---------------- File Discovery ----------------

    @staticmethod
    def find_swift_source_files(repo_root: Path, ignored_dirs: Set[str] | None = None) -> List[Path]:
        ignored_dirs = ignored_dirs or set()
        from ...utils.file_filter_util import find_files_with_extensions
        return find_files_with_extensions(repo_root, ignored_dirs, {".swift"})

    # ---------------- AST Parsing ----------------

    @staticmethod
    def parse_swift_ast_to_json(src_path: Path) -> Dict[str, Any]:
        """
        Runs: sourcekitten structure --file file.swift
        Returns parsed JSON dictionary produced by SourceKitten.
        Note: Current SourceKitten version doesn't support compiler arguments or SDK flags.
        """
        # Current SourceKitten only supports --file and --text options
        cmd = ["sourcekitten", "structure", "--file", str(src_path)]

        out = _run(cmd)
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to decode SourceKitten JSON for {src_path}: {e}\nOutput head: {out[:200]}")

    # ---------------- Name Helpers ----------------

    @staticmethod
    def get_function_name(node: Dict[str, Any], context_stack: List[str]) -> Optional[str]:
        """
        Build a qualified name from node + context (e.g., TypeName.methodName or freeFunction).
        Uses 'key.name' when available; falls back to 'key.name' sans params if needed.
        """
        name = node.get("key.name")
        if not name:
            return None
        # key.name for Swift usually includes signature like "foo(bar:)" for methods/functions;
        # we keep it as-is for display; for base matching, callers can strip params if desired.
        if context_stack:
            return "::".join(context_stack + [name])
        return name

    @staticmethod
    def _is_decl_function(kind: str) -> bool:
        # O(1) lookup using pre-compiled frozenset
        return kind in _SWIFT_FUNCTION_KINDS

    @staticmethod
    def _is_type_context(kind: str) -> bool:
        # O(1) lookup using pre-compiled frozenset
        return kind in _SWIFT_TYPE_CONTEXT_KINDS

    @staticmethod
    def _is_class_type(kind: str) -> bool:
        """Check if the kind represents a Swift class/struct/enum/protocol/extension."""
        # O(1) lookup using pre-compiled frozenset
        return kind in _SWIFT_CLASS_TYPE_KINDS

    @staticmethod
    def _is_call_expr(kind: str) -> bool:
        # O(1) lookup using pre-compiled frozenset
        return kind in _SWIFT_CALL_EXPR_KINDS

    @staticmethod
    def _resolve_self_method_call(callee: str, current_fn: str, registry_names: Optional[Set[str]] = None) -> str:
        """
        Resolve 'self.methodName' calls to proper qualified names like 'ClassName::methodName'.
        Handles complex Swift patterns like optional chaining and force unwrapping.
        
        Args:
            callee: The raw callee name (e.g., "self.movePieceDown", "self.board!.getPieceAt", "self.spinnyNode?.copy")
            current_fn: The current function name (e.g., "Board::movePiece(direction:)")
            registry_names: Optional set of defined function names to validate against
            
        Returns:
            Resolved function name or original callee if resolution fails
        """
        if not callee.startswith("self."):
            return callee
            
        # Extract the class name from current function
        if "::" not in current_fn:
            return callee
            
        class_name = current_fn.split("::")[0]
        
        # Don't allow "Self" as a class name - this indicates unresolved context
        if class_name == "Self":
            return callee
        
        # Remove "self." prefix and replace with "ClassName::"
        method_part = callee[5:]  # Remove "self." prefix
        resolved_name = f"{class_name}::{method_part}"
        
        # If we have a registry, try to validate that the resolved name exists
        if registry_names is not None:
            # Check exact match first
            if resolved_name in registry_names:
                return resolved_name
                
            # For complex patterns, try to find a match by checking the base method name
            if '.' in method_part:
                # Handle cases like "board!.getPieceAt" or "spinnyNode?.copy"
                final_method = method_part.split('.')[-1]
                clean_method = final_method.rstrip('?!').split('(')[0]
                
                # Look for any defined function that matches the pattern
                class_prefix = f"{class_name}::"
                for defined_fn in registry_names:
                    if (defined_fn.startswith(class_prefix) and
                        defined_fn.split("::")[-1].split("(")[0] == clean_method):
                        return defined_fn
            else:
                # Simple case: "self.methodName"
                clean_method = method_part.split('(')[0]
                class_prefix = f"{class_name}::"
                for defined_fn in registry_names:
                    if (defined_fn.startswith(class_prefix) and
                        defined_fn.split("::")[-1].split("(")[0] == clean_method):
                        return defined_fn
        
        # Always return resolved name instead of original callee
        return resolved_name

    @staticmethod
    def _build_variable_type_mapping(ast: Dict[str, Any]) -> Dict[str, str]:
        """
        Build a mapping of variable names to their types from the AST.
        This helps resolve calls like 'board!.method' to 'Board::method'.
        """
        variable_types = {}
        
        # Pre-compile the set of variable declaration kinds for O(1) lookup
        var_decl_kinds = {
            "source.lang.swift.decl.var.instance",
            "source.lang.swift.decl.var.static",
            "source.lang.swift.decl.var.class",
            "source.lang.swift.decl.var.local",
            "source.lang.swift.decl.var.global",
            "source.lang.swift.decl.var.parameter"
        }
        
        def traverse_for_variables(node: Dict[str, Any], ctx: List[str]):
            kind = node.get("key.kind", "")
            name = node.get("key.name")
            type_name = node.get("key.typename")
            
            # Track type context
            pushed = False
            if SwiftASTUtil._is_type_context(kind) and name:
                ctx.append(name)
                pushed = True
            
            # Collect variable declarations with types (O(1) lookup)
            if kind in var_decl_kinds and name and type_name:
                # Clean up type name (remove optionals, etc.)
                clean_type = type_name.rstrip('?!').strip()
                if clean_type:
                    variable_types[name] = clean_type
            
            # Recurse into children - remove redundant `or []`
            for child in node.get("key.substructure", []):
                traverse_for_variables(child, ctx)
                
            if pushed:
                ctx.pop()
        
        # Remove redundant isinstance check and `or []`
        substructure = ast.get("key.substructure", [])
        for child in substructure:
            traverse_for_variables(child, [])
        
        return variable_types

    @staticmethod
    def _resolve_callee_name(callee: str, current_fn: str, registry_index: Optional['RegistryIndex'],
                           variable_types: Dict[str, str], ctx: List[str]) -> str:
        """
        Optimized callee name resolution using pre-built indexes.
        Handles cases like:
        1. 'movePiece' -> 'Board::movePiece' (when in Board context)
        2. 'board!.method' -> 'Board::method' (variable type resolution)
        3. 'direction.opposite' -> 'Direction.opposite' (enum type resolution)
        4. 'ClassName.shared.method' -> 'ClassName::method' (shared instance pattern)
        5. 'object?.method' -> 'ObjectType::method' (optional chaining)
        6. 'Super.method' -> 'ParentClass::method' (super calls)
        """
        
        # Early exit for invalid patterns
        if (callee.startswith("$") or callee == ".init" or callee.startswith(".") or
            callee.startswith("Super.") or callee == "Super" or "Super." in callee or
            callee == "Self" or callee.startswith("Self.")):
            return None
        
        # If no registry index, can't resolve
        if not registry_index:
            return None
        
        # Normalize the callee name once
        normalized = normalize_name(callee)
        
        # Handle self method calls
        if callee.startswith("self."):
            return SwiftASTUtil._resolve_self_method_call(callee, current_fn, registry_index.exact_names)
        
        # Fast exact match check
        if registry_index.find_exact_match(callee) or registry_index.find_exact_match(normalized.resolved):
            return callee if registry_index.find_exact_match(callee) else normalized.resolved
        
        # Fast base name match check
        if registry_index.find_base_match(normalized.no_params):
            return normalized.resolved
        
        # Handle complex property chains and method calls
        if '.' in callee:
            parts = callee.split('.')
            
            # Handle shared instance pattern: ClassName.shared.method
            if len(parts) >= 3 and parts[1] == 'shared':
                class_name = parts[0]
                method_name = parts[2].split('(')[0]
                
                # Fast class-method lookup
                matches = registry_index.find_class_method_matches(class_name, method_name)
                if matches:
                    return next(iter(matches))  # Return first match
                
                # Fallback to basic qualification
                return f"{class_name}::{method_name}"
            
            # Handle static property access: ClassName.staticProperty.method
            if len(parts) >= 2:
                first_part = parts[0].rstrip('?!')
                
                # For simple two-part calls like "tableView.deselectRow"
                if len(parts) == 2:
                    method_name = parts[1].split('(')[0]
                    
                    # Look up variable type first
                    if first_part in variable_types:
                        var_type = variable_types[first_part]
                        matches = registry_index.find_class_method_matches(var_type, method_name)
                        if matches:
                            return next(iter(matches))
                        return None
                    
                    # Check if first part is a known class name (static method call)
                    matches = registry_index.find_class_method_matches(first_part, method_name)
                    if matches:
                        return next(iter(matches))
                    
                    return None
                
                # For longer chains, skip complex property chains
                return None
        
        # Handle method chaining - only if we can find exact matches
        if '.' in callee:
            final_method = callee.split('.')[-1]
            clean_method = final_method.rstrip('?!').split('(')[0]
            
            matches = registry_index.find_method_matches(clean_method)
            if matches:
                return next(iter(matches))
            
            return None
        
        # Handle unqualified method calls within class context
        if current_fn and "::" in current_fn:
            class_name = current_fn.split("::")[0]
            call_method = callee.split("(")[0]
            
            # Fast class-method lookup
            matches = registry_index.find_class_method_matches(class_name, call_method)
            if matches:
                return next(iter(matches))
        
        # Handle simple method names - but be more selective
        if not '.' in callee and not '::' in callee:
            call_method = callee.split("(")[0]
            
            # Skip common system functions (converted to set for O(1) lookup)
            system_functions = {
                'round', 'ceil', 'floor', 'abs', 'min', 'max', 'print', 'debugPrint',
                'fatalError', 'precondition', 'assert', 'assertionFailure',
                'zip', 'stride', 'sequence', 'repeatElement'
            }
            
            if call_method in system_functions:
                return None
            
            # Only match if we're in a class context and can qualify the call
            if current_fn and "::" in current_fn:
                class_name = current_fn.split("::")[0]
                matches = registry_index.find_class_method_matches(class_name, call_method)
                if matches:
                    return next(iter(matches))
        
        return None

    @staticmethod
    def _extract_final_method_from_chain(callee: str) -> str:
        """
        Extract the final method name from complex Swift method chains.
        
        Examples:
        - "[SRAbsoluteTime]::min()!.toDate().roundUpToNext15Minutes" -> "roundUpToNext15Minutes"
        - "ecgSample.date.elapsedSeconds" -> "elapsedSeconds"
        - "newGraphData.getSampleCounts" -> "getSampleCounts"
        """
        # Handle method chaining with dots
        if '.' in callee:
            # Split by dots and get the last component
            parts = callee.split('.')
            final_part = parts[-1]
            
            # Clean up any Swift operators and parameters
            clean_final = final_part.rstrip('?!').split('(')[0]
            return clean_final
        
        # Handle qualified names with ::
        if '::' in callee:
            # Get the method part after ::
            method_part = callee.split('::')[-1]
            # Clean up parameters and operators
            clean_method = method_part.rstrip('?!').split('(')[0]
            return clean_method
        
        # Simple method name
        return callee.rstrip('?!').split('(')[0]

    @staticmethod
    def _node_byte_range(node: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
        begin = node.get("key.offset")
        length = node.get("key.length")
        if begin is None or length is None:
            return None, None
        return begin, begin + length

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

        for f in files:
            try:
                ast = SwiftASTUtil.parse_swift_ast_to_json(f)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

            text = f.read_text(encoding="utf-8", errors="ignore")
            line_offsets = _byte_to_line_offsets(text)

            def walk(node: Dict[str, Any], ctx: List[str]):
                kind = node.get("key.kind", "")
                name = node.get("key.name")

                # Manage context stack for type scopes
                pushed = False
                if SwiftASTUtil._is_type_context(kind):
                    type_name = name or node.get("key.usr", "")
                    if type_name:
                        ctx.append(type_name)
                        pushed = True

                if SwiftASTUtil._is_decl_function(kind):
                    fq = SwiftASTUtil.get_function_name(node, ctx)
                    if fq:
                        start_b, end_b = SwiftASTUtil._node_byte_range(node)
                        if start_b is not None and end_b is not None:
                            start_ln = _line_for_offset(start_b, line_offsets)
                            end_ln = _line_for_offset(end_b, line_offsets)
                            try:
                                # Try to get relative path without resolving symbolic links first
                                rel = str(f.relative_to(repo_root))
                            except ValueError:
                                # If that fails, try with resolved paths
                                try:
                                    rel = str(f.resolve().relative_to(repo_root.resolve()))
                                except ValueError:
                                    # Final fallback: use the file path as-is
                                    rel = str(f)
                            base_name = fq  # keep as-is; caller can strip params if desired
                            func_set.add(base_name)
                            registry.setdefault(base_name, set()).add(
                                (rel, start_ln, end_ln)
                            )

                # Recurse
                for child in node.get("key.substructure", []) or []:
                    walk(child, ctx)

                if pushed:
                    ctx.pop()

            root = ast
            if isinstance(root, dict) and "key.substructure" in root:
                for child in root.get("key.substructure") or []:
                    walk(child, [])

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
        Collect all Swift class/struct/enum/protocol/extension definitions.
        Returns: class_registry_map: class_name -> set of (file_rel, startLine, endLine)
        """
        logging.info("[+] Building class registry in SwiftASTUtil.py...")
        class_registry: Dict[str, Set[Tuple[str, int, int]]] = {}

        for f in files:
            try:
                ast = SwiftASTUtil.parse_swift_ast_to_json(f)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

            text = f.read_text(encoding="utf-8", errors="ignore")
            line_offsets = _byte_to_line_offsets(text)

            def walk(node: Dict[str, Any], ctx: List[str]):
                kind = node.get("key.kind", "")
                name = node.get("key.name")

                # Manage context stack for nested types
                pushed = False
                if SwiftASTUtil._is_type_context(kind):
                    type_name = name or node.get("key.usr", "")
                    if type_name:
                        ctx.append(type_name)
                        pushed = True

                # Collect class/struct/enum/protocol/extension definitions
                if SwiftASTUtil._is_class_type(kind):
                    class_name = name
                    if class_name:
                        # Build qualified name with parent context only (don't include the class itself in context)
                        parent_context = [c for c in ctx if c != class_name]  # Exclude self from context
                        if parent_context:
                            qualified_name = "::".join(parent_context + [class_name])
                        else:
                            qualified_name = class_name

                        start_b, end_b = SwiftASTUtil._node_byte_range(node)
                        if start_b is not None and end_b is not None:
                            start_ln = _line_for_offset(start_b, line_offsets)
                            end_ln = _line_for_offset(end_b, line_offsets)
                            try:
                                # Try to get relative path without resolving symbolic links first
                                rel = str(f.relative_to(repo_root))
                            except ValueError:
                                # If that fails, try with resolved paths
                                try:
                                    rel = str(f.resolve().relative_to(repo_root.resolve()))
                                except ValueError:
                                    # Final fallback: use the file path as-is
                                    rel = str(f)

                            class_registry.setdefault(qualified_name, set()).add(
                                (rel, start_ln, end_ln)
                            )

                # Recurse
                for child in node.get("key.substructure", []) or []:
                    walk(child, ctx)

                if pushed:
                    ctx.pop()

            root = ast
            if isinstance(root, dict) and "key.substructure" in root:
                for child in root.get("key.substructure") or []:
                    walk(child, [])

        if out_json:
            # Convert to the new dictionary schema with "data_type_to_location" key
            json_output_list = []
            for class_name, entries in class_registry.items():
                class_entry = {
                    "data_type_name": class_name,
                    "files": []
                }

                # Add all file entries with line numbers
                for entry in sorted(entries, key=lambda t: (t[0], t[1] or -1, t[2] or -1)):
                    file_entry = {
                        "file_name": entry[0],
                        "start": entry[1],
                        "end": entry[2]
                    }
                    class_entry["files"].append(file_entry)

                if class_entry["files"]:
                    json_output_list.append(class_entry)

            # Wrap in the new dictionary schema
            json_output = {
                "data_type_to_location": json_output_list
            }

            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps(json_output, indent=2, sort_keys=True), encoding="utf-8")
            logging.info(f"[+] Wrote {len(json_output_list)} Swift class entries to {out_json}")

        return class_registry

    # ---------------- Call Graph ----------------

    @staticmethod
    def build_call_graph_adjacency(files: List[Path],
                                   _extra_compiler_args: Optional[List[str]] = None,
                                   only_repo_defined: bool = False,
                                   registry_names: Optional[Set[str]] = None) -> Dict[str, List[str]]:
        """
        Build a forward call graph: caller -> [callees].
        Names are qualified as produced by get_function_name; callees use 'key.name' discovered in call exprs.
        """
        forward: Dict[str, Set[str]] = {}
        defined = registry_names if (only_repo_defined and registry_names is not None) else None
        
        # Create optimized registry index once
        registry_index = RegistryIndex(registry_names) if registry_names else None

        files_processed = 0
        functions_found = 0
        calls_found = 0

        for f in files:
            try:
                ast = SwiftASTUtil.parse_swift_ast_to_json(f)
                files_processed += 1
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

            # Build variable type mapping for this file
            variable_types = SwiftASTUtil._build_variable_type_mapping(ast)

            # Traverse to collect calls inside each function body
            def walk(node: Dict[str, Any], ctx: List[str], current_fn: Optional[str]):
                nonlocal functions_found, calls_found
                kind = node.get("key.kind", "")
                name = node.get("key.name")

                # Enter type scopes
                popped = False
                if SwiftASTUtil._is_type_context(kind):
                    if name:
                        ctx.append(name)
                        popped = True

                # Enter function
                if SwiftASTUtil._is_decl_function(kind):
                    current_fn = SwiftASTUtil.get_function_name(node, ctx)
                    if current_fn:
                        functions_found += 1

                # Record call expressions under current function
                if current_fn and SwiftASTUtil._is_call_expr(kind):
                    callee = name  # SourceKitten puts the called name (e.g., "foo(bar:)")
                    if callee:
                        calls_found += 1

                        # Resolve the callee to proper qualified name using optimized index
                        resolved_callee = SwiftASTUtil._resolve_callee_name(
                            callee, current_fn, registry_index, variable_types, ctx
                        )

                        # Skip unresolved callees (None means we couldn't resolve to a proper type)
                        if resolved_callee is None:
                            pass
                        else:
                            # Optimized matching logic using registry index
                            matched = False
                            if defined is None:
                                matched = True
                            elif registry_index:
                                # Use fast index lookups instead of linear searches
                                normalized = normalize_name(resolved_callee)
                                
                                # Fast exact match
                                if registry_index.find_exact_match(resolved_callee):
                                    matched = True
                                # Fast base match
                                elif registry_index.find_base_match(normalized.no_params):
                                    matched = True
                                # Fast class-method match
                                elif normalized.class_part:
                                    matches = registry_index.find_class_method_matches(
                                        normalized.class_part, normalized.method)
                                    if matches:
                                        matched = True
                                # Fast method match
                                else:
                                    matches = registry_index.find_method_matches(normalized.method)
                                    if matches:
                                        matched = True
                                
                                # Handle special cases with optimized lookups
                                if not matched:
                                    # Handle initializer calls
                                    if resolved_callee.endswith('.init'):
                                        class_name = resolved_callee[:-5]
                                        init_matches = registry_index.find_class_method_matches(class_name, 'init')
                                        if init_matches:
                                            matched = True
                                    
                                    # Handle complex method chains
                                    elif '.' in resolved_callee and '::' not in resolved_callee:
                                        final_component = resolved_callee.split('.')[-1]
                                        final_matches = registry_index.find_method_matches(final_component)
                                        if final_matches:
                                            matched = True
                                    
                                    # Enhanced method chain extraction
                                    else:
                                        final_method = SwiftASTUtil._extract_final_method_from_chain(resolved_callee)
                                        if final_method and final_method != resolved_callee:
                                            final_matches = registry_index.find_method_matches(final_method)
                                            if final_matches:
                                                matched = True

                            if matched:
                                forward.setdefault(current_fn, set()).add(resolved_callee)

                for child in node.get("key.substructure", []):
                    walk(child, ctx, current_fn)

                if popped:
                    ctx.pop()

            root = ast
            if isinstance(root, dict) and "key.substructure" in root:
                for child in root.get("key.substructure", []):
                    walk(child, [], None)

        # Convert to sorted lists
        return {k: sorted(v) for k, v in forward.items()}

    # ---------------- Nested Call Graph ----------------

    @staticmethod
    def generate_nested_call_graph(definitions_map: Dict[str, Set[Tuple[str, int, int]]],
                                   adjacency: Dict[str, List[str]],
                                   max_depth: int,
                                   out_json: Path,
                                   data_type_usage: Dict[str, List[str]] = None,
                                   constants_usage: Dict[str, Dict[str, Any]] = None) -> None:
        """
        Convert flat adjacency into a nested tree with basic context, data type usage, and constants usage, grouped by file.
        """

        def context_for(fn: str) -> Dict[str, Any]:
            entries = definitions_map.get(fn)
            if not entries:
                # Try to find a match by looking for functions that end with the call name
                # Handle cases like "motion_alarm.checkin" -> "MotionAlarm::checkin()"
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
                        if def_name.endswith("::"+fn):
                            entries = definitions_map[def_name]
                            break
                        # Check if defined function ends with the extracted method name
                        elif def_name.endswith("::"+method_name):
                            entries = definitions_map[def_name]
                            break
                        # Check if method_name matches the base name (after last ::)
                        elif def_name.split("::")[-1].split('(')[0] == method_name:
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
                # Look for data types used by this function
                # Try exact match first, then try with different function name formats

                # Try exact match
                if fn in data_type_usage:
                    data_types_used = data_type_usage[fn]
                # Try to find a match by checking if any key ends with the function name
                else:
                    # Extract base function name (remove parameters if present)
                    base_fn = fn.split('(')[0] if '(' in fn else fn
                    for key in data_type_usage:
                        if key == fn or key.endswith("::" + fn) or key.endswith("::" + base_fn):
                            data_types_used = data_type_usage[key]
                            break

            # Add constants usage if available
            constants_used = {}
            if constants_usage:
                # Look for constants used by this function
                # Try exact match first, then try with different function name formats

                # Try exact match
                if fn in constants_usage:
                    constants_used = constants_usage[fn]
                # Try to find a match by checking if any key ends with the function name
                else:
                    # Extract base function name (remove parameters if present)
                    base_fn = fn.split('(')[0] if '(' in fn else fn
                    for key in constants_usage:
                        if key == fn or key.endswith("::" + fn) or key.endswith("::" + base_fn):
                            constants_used = constants_usage[key]
                            break

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

        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

        total_functions = sum(len(file_entry["functions"]) for file_entry in result)
        logging.info(f"[+] Wrote Swift nested call graph ({len(result)} files, {total_functions} functions) to {out_json}")


    # ---------------- Data Type Usage Analysis ----------------

    @staticmethod
    def _is_swift_standard_library_type(type_name: str) -> bool:
        """Check if a type name belongs to Swift standard library or system frameworks."""
        if not type_name:
            return True

        # Remove generic parameters and qualifiers for checking
        clean_name = type_name.strip()

        # Remove generic parameters like Array<String> -> Array
        if '<' in clean_name:
            clean_name = clean_name.split('<')[0].strip()

        # Remove optional markers
        clean_name = clean_name.rstrip('?!').strip()

        # Swift built-in types
        swift_builtin_types = {
            'Int', 'Int8', 'Int16', 'Int32', 'Int64',
            'UInt', 'UInt8', 'UInt16', 'UInt32', 'UInt64',
            'Float', 'Double', 'Bool', 'String', 'Character',
            'Void', 'Never', 'Any', 'AnyObject', 'AnyClass',
            'Self', 'Type', 'Protocol'
        }

        # Swift standard library types
        swift_std_types = {
            'Array', 'Dictionary', 'Set', 'Optional', 'Result',
            'Range', 'ClosedRange', 'PartialRangeFrom', 'PartialRangeUpTo', 'PartialRangeThrough',
            'Slice', 'ArraySlice', 'Substring', 'ContiguousArray',
            'LazySequence', 'LazyCollection', 'LazyMapSequence', 'LazyFilterSequence',
            'ReversedCollection', 'EnumeratedSequence', 'ZipSequence',
            'IndexingIterator', 'AnyIterator', 'AnySequence', 'AnyCollection',
            'Mirror', 'ObjectIdentifier', 'Unmanaged', 'UnsafePointer', 'UnsafeMutablePointer',
            'UnsafeBufferPointer', 'UnsafeMutableBufferPointer', 'UnsafeRawPointer', 'UnsafeMutableRawPointer',
            'UnsafeRawBufferPointer', 'UnsafeMutableRawBufferPointer', 'AutoreleasingUnsafeMutablePointer'
        }

        # System framework prefixes (iOS/macOS)
        system_prefixes = [
            'UI', 'NS', 'CG', 'CA', 'CF', 'CI', 'CL', 'CM', 'CT', 'CV',
            'GL', 'SC', 'SK', 'SL', 'WK', 'MK', 'MP', 'AV', 'AL', 'AR',
            'HK', 'PK', 'GK', 'ML', 'NL', 'VN', 'OS', 'XC', 'Swift.',
            'Foundation.', 'UIKit.', 'AppKit.', 'CoreData.', 'CoreGraphics.',
            'QuartzCore.', 'Metal.', 'MetalKit.', 'SceneKit.', 'SpriteKit.',
            'GameplayKit.', 'AVFoundation.', 'CoreLocation.', 'MapKit.',
            'WebKit.', 'Network.', 'Combine.', 'SwiftUI.', 'RealityKit.',
            'CreateML.', 'NaturalLanguage.', 'Vision.', 'CoreML.',
            'HealthKit.', 'PassKit.', 'GameKit.', 'StoreKit.',
            'CloudKit.', 'EventKit.', 'Contacts.', 'Photos.',
            'PhotosUI.', 'Messages.', 'CallKit.', 'Intents.',
            'IntentsUI.', 'UserNotifications.', 'NotificationCenter.',
            'BackgroundTasks.', 'WidgetKit.', 'AppClip.'
        ]

        # Check if it's a built-in type
        if clean_name in swift_builtin_types or clean_name in swift_std_types:
            return True

        # Check for system framework prefixes
        for prefix in system_prefixes:
            if clean_name.startswith(prefix):
                return True

        # Check for common patterns that indicate system types
        system_patterns = [
            'dispatch_', 'os_', 'xpc_', '__', '_', 'objc_',
            'Dispatch', 'OSLog', 'Logger'
        ]

        clean_lower = clean_name.lower()
        for pattern in system_patterns:
            if pattern.lower() in clean_lower:
                return True

        return False

    @staticmethod
    def _extract_swift_types_from_node(node: Dict[str, Any]) -> Set[str]:
        """Extract custom type information from a Swift AST node."""
        type_names = set()

        # Handle different node kinds that can contain type information
        kind = node.get("key.kind", "")

        # Extract from type annotations
        type_name = node.get("key.typename")
        if type_name and not SwiftASTUtil._is_swift_standard_library_type(type_name):
            # Clean up the type name
            clean_type = type_name.strip()
            if clean_type:
                type_names.add(clean_type)

        # Extract from variable declarations
        if kind in ("source.lang.swift.decl.var.instance",
                   "source.lang.swift.decl.var.static",
                   "source.lang.swift.decl.var.class",
                   "source.lang.swift.decl.var.local",
                   "source.lang.swift.decl.var.global",
                   "source.lang.swift.decl.var.parameter"):
            type_name = node.get("key.typename")
            if type_name and not SwiftASTUtil._is_swift_standard_library_type(type_name):
                clean_type = type_name.strip()
                if clean_type:
                    type_names.add(clean_type)

        # Extract from function parameters and return types
        if kind in ("source.lang.swift.decl.function.free",
                   "source.lang.swift.decl.function.method.instance",
                   "source.lang.swift.decl.function.method.static",
                   "source.lang.swift.decl.function.method.class",
                   "source.lang.swift.decl.function.constructor"):
            type_name = node.get("key.typename")  # Return type
            if type_name and not SwiftASTUtil._is_swift_standard_library_type(type_name):
                clean_type = type_name.strip()
                if clean_type:
                    type_names.add(clean_type)

        # Extract from type references
        if kind == "source.lang.swift.ref.struct" or kind == "source.lang.swift.ref.class":
            name = node.get("key.name")
            if name and not SwiftASTUtil._is_swift_standard_library_type(name):
                type_names.add(name)

        return type_names

    @staticmethod
    def build_data_type_use(files: List[Path],
                           _extra_compiler_args: Optional[List[str]] = None,
                           custom_types_registry: Optional[Set[str]] = None) -> Dict[str, List[str]]:
        """
        Build function → custom data types mapping for Swift code.

        Args:
            repo_root: Path to repository root
            files: List of Swift files to analyze
            extra_compiler_args: Optional compiler arguments for SourceKitten
            custom_types_registry: Optional set of known custom type names to filter against

        Returns:
            Dict mapping function names to lists of custom data types they use
        """
        data_type_map: Dict[str, List[str]] = {}

        # If no custom types registry provided, we'll collect all non-standard types
        _ = custom_types_registry or set()

        def traverse_for_swift_types(node: Dict[str, Any], current_fn: str, collected_types: Set[str]):
            """Recursively traverse Swift AST nodes to find type usage."""
            # Extract types from current node
            node_types = SwiftASTUtil._extract_swift_types_from_node(node)
            collected_types.update(node_types)

            # Recursively process children
            for child in node.get("key.substructure", []) or []:
                traverse_for_swift_types(child, current_fn, collected_types)

        for f in files:
            try:
                ast = SwiftASTUtil.parse_swift_ast_to_json(f)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

            def collect_function_types(node: Dict[str, Any], ctx: List[str]):
                """Collect type usage for each Swift function definition."""
                kind = node.get("key.kind", "")
                name = node.get("key.name")

                # Manage context stack for type scopes
                pushed = False
                if SwiftASTUtil._is_type_context(kind):
                    type_name = name or node.get("key.usr", "")
                    if type_name:
                        ctx.append(type_name)
                        pushed = True

                if SwiftASTUtil._is_decl_function(kind):
                    fn_name = SwiftASTUtil.get_function_name(node, ctx)
                    if fn_name:
                        collected_types = set()

                        # Analyze function return type
                        return_type = node.get("key.typename")
                        if return_type and not SwiftASTUtil._is_swift_standard_library_type(return_type):
                            clean_type = return_type.strip()
                            if clean_type:
                                collected_types.add(clean_type)

                        # Analyze function body and parameters
                        for child in node.get("key.substructure", []) or []:
                            traverse_for_swift_types(child, fn_name, collected_types)

                        # Filter to only custom types if registry provided
                        if custom_types_registry:
                            collected_types = {t for t in collected_types if t in custom_types_registry}

                        if collected_types:
                            data_type_map[fn_name] = sorted(collected_types)

                # Recursively process children
                for child in node.get("key.substructure", []) or []:
                    collect_function_types(child, ctx)

                if pushed:
                    ctx.pop()

            root = ast
            if isinstance(root, dict) and "key.substructure" in root:
                for child in root.get("key.substructure") or []:
                    collect_function_types(child, [])

        return data_type_map

    # ---------------- Constants Detection ----------------

    @staticmethod
    def _extract_swift_constants_from_node(node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract constant values from a Swift AST node."""
        constants = {}

        def extract_literal_value(node: Dict[str, Any]) -> Any:
            """Extract literal value from different Swift node types."""
            try:
                kind = node.get("key.kind", "")

                # Handle different literal types
                if kind == "source.lang.swift.expr.literal.integer":
                    # Integer literals
                    name = node.get("key.name", "")
                    if name:
                        try:
                            # Handle different integer formats
                            if name.startswith('0x') or name.startswith('0X'):
                                return int(name, 16)
                            elif name.startswith('0b') or name.startswith('0B'):
                                return int(name, 2)
                            elif name.startswith('0o') or name.startswith('0O'):
                                return int(name, 8)
                            else:
                                return int(name)
                        except ValueError:
                            return name
                elif kind == "source.lang.swift.expr.literal.float":
                    # Float literals
                    name = node.get("key.name", "")
                    if name:
                        try:
                            return float(name)
                        except ValueError:
                            return name
                elif kind == "source.lang.swift.expr.literal.string":
                    # String literals
                    name = node.get("key.name", "")
                    if name:
                        return name
                elif kind == "source.lang.swift.expr.literal.boolean":
                    # Boolean literals
                    name = node.get("key.name", "")
                    if name:
                        return name.lower() == 'true'
                elif kind == "source.lang.swift.expr.literal.nil":
                    # Nil literal
                    return None
                elif kind == "source.lang.swift.expr.literal.array":
                    # Array literals - return the literal representation
                    name = node.get("key.name", "")
                    if name:
                        return name
                elif kind == "source.lang.swift.expr.literal.dictionary":
                    # Dictionary literals - return the literal representation
                    name = node.get("key.name", "")
                    if name:
                        return name
            except Exception:
                pass
            return None

        def traverse_for_constants(node: Dict[str, Any]):
            """Recursively traverse Swift AST nodes to find constant usage."""
            try:
                kind = node.get("key.kind", "")
                name = node.get("key.name", "")

                # Check for constant variable declarations
                if kind in ("source.lang.swift.decl.var.static",
                           "source.lang.swift.decl.var.class",
                           "source.lang.swift.decl.var.global"):
                    # Check if it's a constant (let declaration)
                    if name and name.isupper():  # Common Swift constant naming convention
                        # Try to get the constant value from child nodes
                        for child in node.get("key.substructure", []) or []:
                            value = extract_literal_value(child)
                            if value is not None and isinstance(value, (int, float)):
                                constants[name] = value
                                break

                # Check for enum case references
                elif kind == "source.lang.swift.ref.enumelement":
                    if name:
                        # We can't easily determine the numeric value of enum cases
                        # So we skip them unless we can somehow evaluate them to numeric values
                        pass

                # Check for constant references (identifiers that might be constants)
                elif kind == "source.lang.swift.ref.var.global" or kind == "source.lang.swift.ref.var.static":
                    if name and (name.isupper() or name.startswith('k')):  # Common constant naming patterns
                        # We can't easily determine the value of global/static references
                        # So we skip them unless we can somehow evaluate them to numeric values
                        pass

                # REMOVED: All constants are now filtered out unless we can determine they have numeric values
                # We only want declared constants with numeric values (int, float, double)

                # Recursively process children
                for child in node.get("key.substructure", []) or []:
                    traverse_for_constants(child)
            except Exception:
                pass

        traverse_for_constants(node)
        return constants

    @staticmethod
    def build_constants_usage(files: List[Path],
                             _extra_compiler_args: Optional[List[str]] = None,
                             function_registry: Optional[Set[str]] = None) -> Dict[str, Dict[str, Any]]:
        """
        Build function → constants mapping for Swift code.

        Args:
            files: List of Swift files to analyze
            _extra_compiler_args: Optional compiler arguments for SourceKitten
            function_registry: Optional set of known function names to filter against

        Returns:
            Dict mapping function names to dicts of constants they use
        """
        constants_map: Dict[str, Dict[str, Any]] = {}

        def traverse_for_constants(node: Dict[str, Any], current_fn: str, collected_constants: Dict[str, Any]):
            """Recursively traverse Swift AST nodes to find constants usage."""
            # Extract constants from current node
            node_constants = SwiftASTUtil._extract_swift_constants_from_node(node)
            collected_constants.update(node_constants)

            # Recursively process children
            for child in node.get("key.substructure", []) or []:
                traverse_for_constants(child, current_fn, collected_constants)

        for f in files:
            try:
                ast = SwiftASTUtil.parse_swift_ast_to_json(f)
            except Exception as e:
                logging.warning(f"skip {f.name}: {e}")
                continue

            def collect_function_constants(node: Dict[str, Any], ctx: List[str]):
                """Collect constants usage for each Swift function definition."""
                kind = node.get("key.kind", "")
                name = node.get("key.name")

                # Manage context stack for type scopes
                pushed = False
                if SwiftASTUtil._is_type_context(kind):
                    type_name = name or node.get("key.usr", "")
                    if type_name:
                        ctx.append(type_name)
                        pushed = True

                if SwiftASTUtil._is_decl_function(kind):
                    fn_name = SwiftASTUtil.get_function_name(node, ctx)
                    if fn_name:
                        collected_constants = {}

                        # Analyze function body for constants
                        for child in node.get("key.substructure", []) or []:
                            traverse_for_constants(child, fn_name, collected_constants)

                        if collected_constants:
                            constants_map[fn_name] = collected_constants

                # Recursively process children
                for child in node.get("key.substructure", []) or []:
                    collect_function_constants(child, ctx)

                if pushed:
                    ctx.pop()

            root = ast
            if isinstance(root, dict) and "key.substructure" in root:
                for child in root.get("key.substructure") or []:
                    collect_function_constants(child, [])

        return constants_map

# ----------------------------- CLI -----------------------------

def _build_arg_parser():
    ap = argparse.ArgumentParser(description="Swift AST utilities powered by SourceKitten")
    ap.add_argument("--repo", required=True, help="Path to repository root")
    ap.add_argument("--exclude", nargs="*", default=[], help="Directory names to exclude (top-level components)")
    ap.add_argument("--clang-arg", dest="compiler_args", action="append", default=[],
                    help="Extra compiler args passed to SourceKitten (repeatable)")
    ap.add_argument("--defined-out", default="/tmp/swift_defined_functions.json",
                    help="Where to write function definitions JSON")
    ap.add_argument("--nested-out", default="/tmp/swift_nested_call_graph.json",
                    help="Where to write nested call graph JSON")
    ap.add_argument("--defined-classes-out", default="/tmp/swift_defined_classes.json",
                    help="Path to write Swift class definitions JSON with file locations")
    ap.add_argument("--data-type-use-out", default="/tmp/swift_data_type_usage.json",
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

    swift_files = SwiftASTUtil.find_swift_source_files(repo, exclude)

    defined_out = Path(args.defined_out)
    nested_out = Path(args.nested_out)

    # Collect function definitions
    names, definitions = SwiftASTUtil.collect_defined_functions(
        repo_root=repo,
        files=swift_files,
        _extra_compiler_args=comp_args,
        out_json=defined_out
    )

    # Build class registry BEFORE call graph generation
    class_registry = SwiftASTUtil.collect_defined_classes(
        repo_root=repo,
        files=swift_files,
        _extra_compiler_args=comp_args,
        out_json=Path(args.defined_classes_out)
    )

    # Build adjacency AFTER function and class registries are complete
    adjacency = SwiftASTUtil.build_call_graph_adjacency(
        files=swift_files,
        _extra_compiler_args=comp_args,
        only_repo_defined=args.filter_external_calls,
        registry_names=names
    )

    # Build data type usage mapping
    custom_types = set(class_registry.keys()) if class_registry else None
    data_type_usage = SwiftASTUtil.build_data_type_use(
        files=swift_files,
        _extra_compiler_args=comp_args,
        custom_types_registry=custom_types
    )

    data_type_out = Path(args.data_type_use_out)
    data_type_out.parent.mkdir(parents=True, exist_ok=True)
    data_type_out.write_text(json.dumps(data_type_usage, indent=2, sort_keys=True), encoding="utf-8")

    total_functions = len(data_type_usage)
    total_type_usages = sum(len(types) for types in data_type_usage.values())
    logging.info(f"[+] Wrote Swift data type usage for {total_functions} functions "
                f"({total_type_usages} total type usages) to {data_type_out}")


    # Nested graph
    SwiftASTUtil.generate_nested_call_graph(
        definitions_map=definitions,
        adjacency=adjacency,
        max_depth=args.max_depth,
        out_json=nested_out,
        data_type_usage=data_type_usage
    )

if __name__ == "__main__":
    main()