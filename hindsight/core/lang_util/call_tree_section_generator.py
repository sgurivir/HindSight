"""
call_tree_section_generator.py - Generates focused call tree sections for LLM prompts.

This module provides functionality to generate a focused call tree section centered
on a specific function, showing:
- Ancestors (callers) up to a configurable depth
- Descendants (callees) up to a configurable depth

Unlike CallTreeGenerator which builds the entire tree from roots, this generates
a subtree centered on a specific function for inclusion in LLM prompts.

The output includes relative file paths and line numbers that can be used with
the getFileContentByLines tool to inspect any function in the tree.
"""

import logging
from collections import defaultdict
from typing import Dict, List, Set, Any, Optional, Tuple

from .call_graph_util import CallGraph, load_call_graph_from_json
from .call_tree_util import extract_implementations, format_location

logger = logging.getLogger(__name__)


class CallTreeSectionGenerator:
    """
    Generates a focused call tree section for a specific function.
    
    Unlike CallTreeGenerator which builds the entire tree from roots,
    this generates a subtree centered on a specific function showing:
    - Ancestors (callers) up to a configurable depth
    - Descendants (callees) up to a configurable depth
    
    The output is designed for inclusion in LLM prompts and includes
    relative file paths and line numbers for use with getFileContentByLines.
    """
    
    # Token estimation constants
    TOKENS_PER_TREE_NODE = 25  # Estimated tokens per node (function name + path + lines)
    TOKENS_PER_HEADER_LINE = 10  # Estimated tokens per header/separator line
    
    def __init__(
        self,
        call_graph_data: Dict[str, Any],
        max_ancestor_depth: int = 2,
        max_descendant_depth: int = 3,
        max_children_per_node: int = 5
    ):
        """
        Initialize the CallTreeSectionGenerator.
        
        Args:
            call_graph_data: The merged_call_graph.json data
            max_ancestor_depth: How many levels of callers to include (default: 2)
            max_descendant_depth: How many levels of callees to include (default: 3)
            max_children_per_node: Max children to show per node to prevent explosion (default: 5)
        """
        self.max_ancestor_depth = max_ancestor_depth
        self.max_descendant_depth = max_descendant_depth
        self.max_children_per_node = max_children_per_node
        
        # Load call graph and implementations
        self.graph = load_call_graph_from_json(call_graph_data)
        self.implementations = extract_implementations(call_graph_data)
        
        # Track current depth settings for dynamic adjustment
        self._current_ancestor_depth = max_ancestor_depth
        self._current_descendant_depth = max_descendant_depth
        
        logger.debug(
            f"CallTreeSectionGenerator initialized with {self.graph.get_num_nodes()} nodes, "
            f"ancestor_depth={max_ancestor_depth}, descendant_depth={max_descendant_depth}"
        )
        
        # Build normalized name lookup for fuzzy matching
        self._normalized_name_map = self._build_normalized_name_map()
    
    def _build_normalized_name_map(self) -> Dict[str, str]:
        """
        Build a map from normalized function names to original names.
        This enables fuzzy matching for function names with variations.
        
        Returns:
            Dictionary mapping normalized names to original names in the graph
        """
        name_map = {}
        for original_name in self.graph.nodes:
            normalized = self._normalize_function_name(original_name)
            # Store the first occurrence (in case of collisions)
            if normalized not in name_map:
                name_map[normalized] = original_name
        return name_map
    
    @staticmethod
    def _normalize_function_name(func_name: str) -> str:
        """
        Normalize a function name to handle variations.
        
        Handles cases like:
        - 'AppDelegate::application:didFinishLaunchingWithOptions:' vs
          'AppDelegate::application(_:didFinishLaunchingWithOptions:)'
        - 'MyClass::method()' vs 'MyClass::method'
        - Swift/Objective-C selector variations
        
        Args:
            func_name: The function name to normalize
            
        Returns:
            Normalized function name for comparison
        """
        if not func_name:
            return func_name
        
        # Handle parentheses in function names
        if '(' in func_name:
            paren_start = func_name.find('(')
            paren_end = func_name.rfind(')')
            if paren_end > paren_start:
                inside_parens = func_name[paren_start+1:paren_end]
                # Check if this looks like a Swift selector (has colons inside parentheses)
                # e.g., 'AppDelegate::application(_:didFinishLaunchingWithOptions:)'
                if ':' in inside_parens:
                    prefix = func_name[:paren_start]
                    # Normalize the selector part: replace '_:' with ':'
                    selector = inside_parens.replace('_:', ':')
                    func_name = prefix + ':' + selector
                else:
                    # Regular function with parameters or empty parens - remove them
                    # e.g., 'MyClass::method()' -> 'MyClass::method'
                    # e.g., 'func(int x, int y)' -> 'func'
                    func_name = func_name[:paren_start]
        
        # Normalize Swift/Objective-C selector syntax
        # Replace '_:' with ':' (Swift external parameter labels)
        func_name = func_name.replace('_:', ':')
        
        # Remove trailing colons for consistency
        func_name = func_name.rstrip(':')
        
        # Strip whitespace
        return func_name.strip()
    
    def _find_function_in_graph(self, function_name: str) -> Optional[str]:
        """
        Find a function in the graph, trying exact match first, then normalized match.
        
        Args:
            function_name: The function name to find
            
        Returns:
            The actual function name in the graph, or None if not found
        """
        # Try exact match first
        if function_name in self.graph.nodes:
            return function_name
        
        # Try normalized match
        normalized = self._normalize_function_name(function_name)
        if normalized in self._normalized_name_map:
            actual_name = self._normalized_name_map[normalized]
            logger.debug(f"Function '{function_name}' matched to '{actual_name}' via normalization")
            return actual_name
        
        # Try partial matching as last resort
        for node_name in self.graph.nodes:
            if normalized in self._normalize_function_name(node_name):
                logger.debug(f"Function '{function_name}' matched to '{node_name}' via partial match")
                return node_name
        
        return None
    
    def generate_section(self, function_name: str) -> Dict[str, Any]:
        """
        Generate a call tree section centered on the given function.
        
        Args:
            function_name: The name of the function to center the tree on
            
        Returns:
            Dictionary containing:
            {
                "target_function": {...},  # The target function info
                "ancestors": [...],        # Caller chains (list of ancestor paths)
                "descendants": {...}       # Callee tree (nested structure)
            }
        """
        # Try to find the function using fuzzy matching
        actual_function_name = self._find_function_in_graph(function_name)
        
        if actual_function_name is None:
            logger.warning(f"Function '{function_name}' not found in call graph (tried exact and normalized matching)")
            return {
                "target_function": {"name": function_name, "location": []},
                "ancestors": [],
                "descendants": {}
            }
        
        # Use the actual function name found in the graph
        if actual_function_name != function_name:
            logger.info(f"Using matched function name '{actual_function_name}' for requested '{function_name}'")
        
        # Get target function info
        target_info = {
            "name": actual_function_name,
            "location": self.implementations.get(actual_function_name, [])
        }
        
        # Build ancestor paths (callers) - multiple paths showing different call chains
        ancestors = self._build_ancestor_chain(actual_function_name)
        
        # Build descendant tree (callees)
        descendants = self._build_descendant_tree(actual_function_name)
        
        return {
            "target_function": target_info,
            "ancestors": ancestors,
            "descendants": descendants
        }
    
    def _build_ancestor_chain(self, function_name: str) -> List[List[Dict[str, Any]]]:
        """
        Build the ancestor chains (callers) for a function.
        
        Returns a list of paths from root callers to the target function.
        Each path is a list of function info dicts showing a call chain.
        
        Args:
            function_name: The target function name
            
        Returns:
            List of ancestor paths, each path being a list of function info dicts
        """
        if self._current_ancestor_depth <= 0:
            return []
        
        paths = []
        visited = set()
        
        def find_paths(
            current: str,
            path: List[Dict[str, Any]],
            depth: int
        ) -> None:
            """Recursively find paths from callers to the target."""
            if depth > self._current_ancestor_depth:
                return
            
            callers = self.graph.get_incoming_edges(current)
            
            if not callers or depth == self._current_ancestor_depth:
                # End of chain or max depth reached
                if path:
                    paths.append(list(path))
                return
            
            # Limit number of callers to explore
            sorted_callers = sorted(callers)[:self.max_children_per_node]
            
            for caller in sorted_callers:
                if caller in visited:
                    continue
                
                visited.add(caller)
                caller_info = {
                    "name": caller,
                    "location": self.implementations.get(caller, [])
                }
                path.insert(0, caller_info)
                find_paths(caller, path, depth + 1)
                path.pop(0)
                visited.discard(caller)
        
        find_paths(function_name, [], 0)
        
        # Limit total number of paths
        return paths[:self.max_children_per_node]
    
    def _build_descendant_tree(
        self,
        function_name: str,
        visited: Optional[Set[str]] = None,
        depth: int = 0
    ) -> Dict[str, Any]:
        """
        Build the descendant tree (callees) for a function.
        
        Args:
            function_name: The function to build tree for
            visited: Set of already visited functions (for cycle detection)
            depth: Current depth in the tree
            
        Returns:
            Dictionary representing the tree node with children
        """
        if visited is None:
            visited = set()
        
        node = {
            "name": function_name,
            "location": self.implementations.get(function_name, []),
            "children": []
        }
        
        # Check depth limit
        if depth >= self._current_descendant_depth:
            return node
        
        # Mark as visited
        visited = visited | {function_name}
        
        # Get callees
        callees = self.graph.get_outgoing_edges(function_name)
        
        # Filter out visited (cycle detection) and limit count
        unvisited_callees = [c for c in callees if c not in visited]
        sorted_callees = sorted(unvisited_callees)[:self.max_children_per_node]
        
        # Build child nodes
        for callee in sorted_callees:
            child_node = self._build_descendant_tree(callee, visited, depth + 1)
            node["children"].append(child_node)
        
        return node
    
    def format_as_text(self, section: Dict[str, Any]) -> str:
        """
        Format the section as compact text for prompt inclusion.
        Uses relative file paths (relative to repo root) for all locations.
        
        Args:
            section: The section dictionary from generate_section()
            
        Returns:
            Formatted text string for inclusion in LLM prompt
            
        Example output:
        === CALL TREE CONTEXT ===
        Use getFileContentByLines tool to read code at any location below
        
        CALLERS (who calls this function):
        main()  {src/main.c:10-50}
        └── process_data()  {src/processor/processor.c:100-200}
            └── [TARGET] analyze_item()  {src/analyzer/analyzer.c:50-80}
        
        CALLEES (what this function calls):
        [TARGET] analyze_item()  {src/analyzer/analyzer.c:50-80}
        ├── validate_input()  {src/utils/validator.c:20-40}
        │   └── check_bounds()  {src/utils/validator.c:60-70}
        ├── transform_data()  {src/transform/transformer.c:100-150}
        │   ├── apply_filter()  {src/transform/filter.c:30-50}
        │   └── normalize()  {src/transform/normalizer.c:10-25}
        └── store_result()  {src/storage/storage.c:200-250}
        """
        lines = []
        
        # Header
        lines.append("// === CALL TREE CONTEXT ===")
        lines.append("// This shows the function's position in the call hierarchy")
        lines.append("// Use getFileContentByLines tool to read code at any location shown below")
        lines.append("//")
        
        target = section.get("target_function", {})
        target_name = target.get("name", "Unknown")
        
        # Format ancestors (callers)
        ancestors = section.get("ancestors", [])
        if ancestors:
            lines.append("// CALLERS (who calls this function):")
            for path in ancestors:
                self._format_ancestor_path(lines, path, target_name)
            lines.append("//")
        
        # Format descendants (callees)
        descendants = section.get("descendants", {})
        if descendants:
            lines.append("// CALLEES (what this function calls):")
            self._format_descendant_tree(lines, descendants, "", True, is_target=True)
        
        return "\n".join(lines)
    
    def _format_ancestor_path(
        self,
        lines: List[str],
        path: List[Dict[str, Any]],
        target_name: str
    ) -> None:
        """Format a single ancestor path."""
        for i, func_info in enumerate(path):
            name = func_info.get("name", "")
            location = func_info.get("location", [])
            location_str = self._format_location_compact(location)
            
            # Build indentation
            indent = "//     " + "    " * i
            
            # Determine branch character
            if i == len(path) - 1:
                branch = "└── "
            else:
                branch = ""
            
            # Format the line
            if location_str:
                lines.append(f"{indent}{branch}{name}()  {{{location_str}}}")
            else:
                lines.append(f"{indent}{branch}{name}()")
        
        # Add target function at the end
        target_indent = "//     " + "    " * len(path)
        lines.append(f"{target_indent}└── [TARGET] {target_name}()")
    
    def _format_descendant_tree(
        self,
        lines: List[str],
        node: Dict[str, Any],
        prefix: str,
        is_last: bool,
        is_target: bool = False
    ) -> None:
        """Format the descendant tree recursively."""
        name = node.get("name", "")
        location = node.get("location", [])
        children = node.get("children", [])
        
        location_str = self._format_location_compact(location)
        
        # Build the node text
        if is_target:
            node_text = f"[TARGET] {name}()"
        else:
            node_text = f"{name}()"
        
        if location_str:
            node_text += f"  {{{location_str}}}"
        
        # Determine branch character
        if is_target:
            # Root of descendant tree
            lines.append(f"// {node_text}")
            child_prefix = "// "
        else:
            branch = "└── " if is_last else "├── "
            lines.append(f"{prefix}{branch}{node_text}")
            child_prefix = prefix + ("    " if is_last else "│   ")
        
        # Format children
        for i, child in enumerate(children):
            is_last_child = (i == len(children) - 1)
            self._format_descendant_tree(lines, child, child_prefix, is_last_child)
    
    def _format_location_compact(self, location: List[Dict[str, Any]]) -> str:
        """
        Format location information compactly.
        
        Args:
            location: List of location dicts with file_path, start_line, end_line
            
        Returns:
            Compact string like "src/file.c:10-50" or empty string if no location
        """
        if not location:
            return ""
        
        # Take first location
        loc = location[0]
        file_path = loc.get("file_path", "")
        start_line = loc.get("start_line", 0)
        end_line = loc.get("end_line", 0)
        
        if not file_path:
            return ""
        
        if start_line > 0 and end_line > 0:
            return f"{file_path}:{start_line}-{end_line}"
        elif start_line > 0:
            return f"{file_path}:{start_line}"
        else:
            return file_path
    
    def estimate_token_count(self, section: Dict[str, Any]) -> int:
        """
        Estimate the number of tokens needed for this section.
        
        Args:
            section: The section dictionary from generate_section()
            
        Returns:
            Estimated token count
        """
        token_count = 0
        
        # Header lines (approximately 4 lines)
        token_count += 4 * self.TOKENS_PER_HEADER_LINE
        
        # Count ancestor nodes
        ancestors = section.get("ancestors", [])
        for path in ancestors:
            token_count += len(path) * self.TOKENS_PER_TREE_NODE
            token_count += self.TOKENS_PER_TREE_NODE  # For target at end of path
        
        # Count descendant nodes
        descendants = section.get("descendants", {})
        token_count += self._count_tree_nodes(descendants) * self.TOKENS_PER_TREE_NODE
        
        # Add some buffer for formatting characters
        token_count = int(token_count * 1.1)
        
        return token_count
    
    def _count_tree_nodes(self, node: Dict[str, Any]) -> int:
        """Count total nodes in a tree structure."""
        if not node:
            return 0
        
        count = 1  # This node
        for child in node.get("children", []):
            count += self._count_tree_nodes(child)
        
        return count
    
    def can_reduce_depth(self) -> bool:
        """
        Check if depth can be reduced further.
        
        Returns:
            True if either ancestor or descendant depth can be reduced
        """
        return self._current_ancestor_depth > 0 or self._current_descendant_depth > 1
    
    def reduce_depth(self) -> None:
        """
        Reduce the depth settings to generate a smaller tree.
        
        Reduces descendant depth first (more impactful), then ancestor depth.
        """
        if self._current_descendant_depth > 1:
            self._current_descendant_depth -= 1
            logger.debug(f"Reduced descendant depth to {self._current_descendant_depth}")
        elif self._current_ancestor_depth > 0:
            self._current_ancestor_depth -= 1
            logger.debug(f"Reduced ancestor depth to {self._current_ancestor_depth}")
    
    def reset_depth(self) -> None:
        """Reset depth settings to original values."""
        self._current_ancestor_depth = self.max_ancestor_depth
        self._current_descendant_depth = self.max_descendant_depth


def generate_call_tree_section_for_function(
    call_graph_data: Dict[str, Any],
    function_name: str,
    max_ancestor_depth: int = 2,
    max_descendant_depth: int = 3,
    max_children_per_node: int = 5,
    max_tokens: int = 2000
) -> Optional[str]:
    """
    Convenience function to generate a call tree section for a function.
    
    This is the main entry point for generating call tree context for LLM prompts.
    
    Args:
        call_graph_data: The merged_call_graph.json data
        function_name: The function to center the tree on
        max_ancestor_depth: How many levels of callers to include
        max_descendant_depth: How many levels of callees to include
        max_children_per_node: Max children per node
        max_tokens: Maximum tokens allowed for the section
        
    Returns:
        Formatted text string for inclusion in prompt, or None if over budget
    """
    if not call_graph_data or not function_name:
        return None
    
    try:
        generator = CallTreeSectionGenerator(
            call_graph_data,
            max_ancestor_depth=max_ancestor_depth,
            max_descendant_depth=max_descendant_depth,
            max_children_per_node=max_children_per_node
        )
        
        # Generate section
        section = generator.generate_section(function_name)
        estimated_tokens = generator.estimate_token_count(section)
        
        # Reduce depth if over budget
        while estimated_tokens > max_tokens and generator.can_reduce_depth():
            generator.reduce_depth()
            section = generator.generate_section(function_name)
            estimated_tokens = generator.estimate_token_count(section)
        
        if estimated_tokens > max_tokens:
            logger.warning(
                f"Call tree section for '{function_name}' too large "
                f"({estimated_tokens} tokens), skipping"
            )
            return None
        
        return generator.format_as_text(section)
        
    except Exception as e:
        logger.error(f"Error generating call tree section for '{function_name}': {e}")
        return None
