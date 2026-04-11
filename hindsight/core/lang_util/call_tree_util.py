"""
call_tree_util.py - Utility module for generating call trees from call graphs.

This module provides functionality to:
1. Extract implementation locations from call graph data
2. Break cycles to create a DAG (Directed Acyclic Graph)
3. Generate hierarchical call trees
4. Output call trees in JSON and text formats

This module was refactored from dev/call_graph_util/call_tree_generator.py to be part of the
core lang_util package for reuse by data_flow_analyzer and other components.
"""

import json
import logging
import os
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple, Any, Optional

from .call_graph_util import CallGraph, load_call_graph_from_json

logger = logging.getLogger(__name__)


def extract_implementations(data: Any) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract implementation locations (file_path, start_line, end_line) for each function.
    
    Args:
        data: The raw JSON data from merged_call_graph.json
        
    Returns:
        Dictionary mapping function name to list of implementation locations.
        Each location is: {"file_path": str, "start_line": int, "end_line": int}
    """
    implementations: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    
    # Handle both direct list format and wrapped dict format
    if isinstance(data, list):
        call_graph_data = data
    elif isinstance(data, dict):
        call_graph_data = data.get('call_graph', data.get('files', []))
        if isinstance(call_graph_data, dict):
            call_graph_data = []
    else:
        call_graph_data = []
    
    def process_function_entry(func_entry: Dict, file_path: str) -> None:
        """Process a function entry and extract its implementation info."""
        func_name = func_entry.get('function', '')
        if not func_name:
            return
        
        # Extract line information - check context object first, then top-level fields
        context = func_entry.get('context', {})
        if context:
            start_line = context.get('start', context.get('start_line', 0))
            end_line = context.get('end', context.get('end_line', 0))
            # Use file from context if available
            context_file = context.get('file', '')
            if context_file:
                file_path = context_file
        else:
            start_line = func_entry.get('start_line', func_entry.get('line_start', 0))
            end_line = func_entry.get('end_line', func_entry.get('line_end', 0))
        
        impl_info = {
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line
        }
        
        # Avoid duplicate implementations
        if impl_info not in implementations[func_name]:
            implementations[func_name].append(impl_info)
        
        # Process nested functions_invoked to extract their implementations too
        functions_invoked = func_entry.get('functions_invoked', [])
        process_invoked_functions(functions_invoked, file_path)
    
    def process_invoked_functions(functions_invoked: List, file_path: str) -> None:
        """Recursively process invoked functions to extract implementation info."""
        for invoked in functions_invoked:
            if isinstance(invoked, dict):
                func_name = invoked.get('function', '')
                if func_name:
                    # Extract line information - check context object first
                    context = invoked.get('context', {})
                    invoked_file = file_path
                    if context:
                        start_line = context.get('start', context.get('start_line', 0))
                        end_line = context.get('end', context.get('end_line', 0))
                        context_file = context.get('file', '')
                        if context_file:
                            invoked_file = context_file
                    else:
                        start_line = invoked.get('start_line', invoked.get('line_start', 0))
                        end_line = invoked.get('end_line', invoked.get('line_end', 0))
                    
                    # Only add if we have line information
                    if start_line or end_line:
                        impl_info = {
                            "file_path": invoked_file,
                            "start_line": start_line,
                            "end_line": end_line
                        }
                        if impl_info not in implementations[func_name]:
                            implementations[func_name].append(impl_info)
                    
                    # Recursively process nested invocations
                    nested_invoked = invoked.get('functions_invoked', [])
                    if nested_invoked:
                        process_invoked_functions(nested_invoked, invoked_file)
    
    for file_entry in call_graph_data:
        file_path = file_entry.get('file', file_entry.get('file_path', ''))
        functions = file_entry.get('functions', [])
        
        for func_entry in functions:
            process_function_entry(func_entry, file_path)
    
    return dict(implementations)


def create_dag(graph: CallGraph, max_depth: int = 20) -> Dict[str, Set[str]]:
    """
    Break cycles in the graph to create a DAG.
    
    Uses level-based cycle breaking: only keeps edges that go from
    higher level nodes to strictly lower level nodes.
    
    Args:
        graph: The CallGraph instance
        max_depth: Maximum depth for level computation
        
    Returns:
        Dictionary mapping each node to its set of children in the DAG
    """
    # Compute levels from bottom up
    levels = graph.compute_levels_from_bottom(max_depth)
    
    # Create node -> level mapping
    node_levels: Dict[str, int] = {}
    for level, nodes in levels.items():
        for node in nodes:
            node_levels[node] = level
    
    # Create DAG by only keeping edges from higher to strictly lower levels
    dag_edges: Dict[str, Set[str]] = defaultdict(set)
    
    for node in graph.nodes:
        node_level = node_levels.get(node, 0)
        for callee in graph.get_outgoing_edges(node):
            # Skip self-loops
            if callee == node:
                continue
            callee_level = node_levels.get(callee, 0)
            # Only keep edge if it goes to a strictly lower level
            if callee_level < node_level:
                dag_edges[node].add(callee)
    
    return dict(dag_edges)


def get_dag_root_nodes(graph: CallGraph, dag_edges: Dict[str, Set[str]]) -> Set[str]:
    """
    Find root nodes in the DAG (nodes with no incoming edges in the DAG).
    
    Args:
        graph: The original CallGraph
        dag_edges: The DAG edges after cycle breaking
        
    Returns:
        Set of root node names
    """
    # Build reverse edges for the DAG
    has_incoming: Set[str] = set()
    for caller, callees in dag_edges.items():
        for callee in callees:
            has_incoming.add(callee)
    
    # Root nodes are those with no incoming edges
    return graph.nodes - has_incoming


def compute_all_subtree_depths(
    dag_edges: Dict[str, Set[str]],
    all_nodes: Set[str]
) -> Dict[str, int]:
    """
    Compute the maximum subtree depth for ALL nodes in a single bottom-up pass.
    
    This is an O(N + E) algorithm that leverages the DAG structure:
    1. Build reverse edges (child -> parents)
    2. Find all leaf nodes (nodes with no outgoing edges in DAG)
    3. Process nodes in topological order (leaves first, then their parents)
    4. Each node's depth = max(children depths) + 1
    
    This is significantly faster than recursive memoization for large graphs
    because it:
    - Avoids function call overhead
    - Processes each node exactly once
    - Uses cache-friendly iteration patterns
    - Avoids stack overflow for deep trees
    
    Args:
        dag_edges: The DAG edges (node -> set of children)
        all_nodes: Set of all nodes in the graph
        
    Returns:
        Dictionary mapping each node to its maximum subtree depth
        (0 for leaf nodes, 1 for nodes with only leaf children, etc.)
    """
    # Initialize depths - all nodes start at 0 (leaf depth)
    depths: Dict[str, int] = {node: 0 for node in all_nodes}
    
    # Build reverse edges: child -> set of parents
    # This allows us to propagate depth information upward
    reverse_edges: Dict[str, Set[str]] = defaultdict(set)
    out_degree: Dict[str, int] = defaultdict(int)
    
    for parent, children in dag_edges.items():
        out_degree[parent] = len(children)
        for child in children:
            reverse_edges[child].add(parent)
    
    # Find leaf nodes (nodes with no children in DAG)
    # These are our starting points for bottom-up processing
    leaf_nodes = [node for node in all_nodes if out_degree.get(node, 0) == 0]
    
    # Process nodes in topological order using Kahn's algorithm
    # This ensures we process children before parents
    queue = deque(leaf_nodes)
    processed_children: Dict[str, int] = defaultdict(int)
    
    while queue:
        node = queue.popleft()
        node_depth = depths[node]
        
        # Update all parents of this node
        for parent in reverse_edges.get(node, set()):
            # Parent's depth is max of all children's depths + 1
            depths[parent] = max(depths[parent], node_depth + 1)
            
            # Track how many children we've processed for this parent
            processed_children[parent] += 1
            
            # If all children of parent are processed, parent is ready
            if processed_children[parent] == out_degree[parent]:
                queue.append(parent)
    
    return depths


def build_call_tree_node(
    func: str,
    dag_edges: Dict[str, Set[str]],
    implementations: Dict[str, List[Dict[str, Any]]],
    visited: Set[str],
    depths: Optional[Dict[str, int]] = None,
    sort_by_depth: bool = True
) -> Dict[str, Any]:
    """
    Recursively build a call tree node with its children.
    
    Performance optimizations:
    - Uses pre-computed depths (O(1) lookup) instead of computing on-demand
    - Sorts using tuple keys for efficient comparison
    - Uses set operations for visited tracking
    
    Args:
        func: The function name for this node
        dag_edges: The DAG edges
        implementations: Implementation locations for each function
        visited: Set of already visited nodes (to prevent infinite recursion)
        depths: Pre-computed subtree depths for all nodes (from compute_all_subtree_depths)
        sort_by_depth: If True, sort children by subtree depth (longest first)
        
    Returns:
        Dictionary representing the call tree node
    """
    node = {
        "function": func,
        "location": implementations.get(func, []),
        "children": []
    }
    
    # Mark as visited - create new set to avoid mutation
    visited = visited | {func}
    
    # Get children from DAG
    children = dag_edges.get(func, set())
    
    # Filter out already visited children
    unvisited_children = [c for c in children if c not in visited]
    
    # Sort children
    if sort_by_depth and depths:
        # Sort by depth (descending), then alphabetically for ties
        # Using tuple comparison is faster than lambda with multiple keys
        unvisited_children.sort(key=lambda c: (-depths.get(c, 0), c))
    else:
        unvisited_children.sort()
    
    # Build child nodes
    for child in unvisited_children:
        child_node = build_call_tree_node(
            child, dag_edges, implementations, visited,
            depths, sort_by_depth
        )
        node["children"].append(child_node)
    
    return node


def generate_call_tree(
    graph: CallGraph,
    dag_edges: Dict[str, Set[str]],
    implementations: Dict[str, List[Dict[str, Any]]],
    sort_by_depth: bool = True
) -> Dict[str, Any]:
    """
    Generate the complete call tree JSON structure with a synthetic ROOT node.
    
    Performance characteristics:
    - O(N + E) for depth computation (single pass)
    - O(N log N) for sorting (using Timsort)
    - O(N) for tree construction
    - Total: O(N + E + N log N) = O(N log N + E) for typical graphs
    
    Args:
        graph: The CallGraph instance
        dag_edges: The DAG edges after cycle breaking
        implementations: Implementation locations for each function
        sort_by_depth: If True, sort branches by depth (longest first, default: True)
        
    Returns:
        Dictionary containing the call tree structure with ROOT at top
    """
    # Find root nodes in the DAG
    root_nodes = get_dag_root_nodes(graph, dag_edges)
    
    # Pre-compute ALL depths in a single O(N+E) pass
    # This is the key optimization - we compute once and reuse everywhere
    depths: Dict[str, int] = {}
    if sort_by_depth:
        depths = compute_all_subtree_depths(dag_edges, graph.nodes)
    
    # Sort root nodes
    if sort_by_depth and depths:
        # Sort by depth (descending), then alphabetically for ties
        sorted_roots = sorted(root_nodes, key=lambda r: (-depths.get(r, 0), r))
    else:
        sorted_roots = sorted(root_nodes)
    
    # Build call tree starting from each root
    children = []
    for root in sorted_roots:
        tree_node = build_call_tree_node(
            root, dag_edges, implementations, set(),
            depths, sort_by_depth
        )
        children.append(tree_node)
    
    # Create synthetic ROOT node with all entry points as children
    call_tree = {
        "function": "ROOT",
        "location": [],
        "children": children
    }
    
    return {
        "call_tree": call_tree,
        "metadata": {
            "total_functions": len(graph.nodes),
            "total_root_nodes": len(root_nodes),
            "dag_edges_count": sum(len(c) for c in dag_edges.values()),
            "max_depth": max(depths.values()) if depths else 0
        }
    }


def format_location(location: List[Dict[str, Any]]) -> str:
    """
    Format location information as a string.
    
    Args:
        location: List of implementation locations
        
    Returns:
        Formatted string with all locations
    """
    if not location:
        return ""
    
    parts = []
    for loc in location:
        file_path = loc.get("file_path", "")
        start_line = loc.get("start_line", 0)
        end_line = loc.get("end_line", 0)
        if file_path:
            # Always show file path, add line numbers if available (non-zero)
            if start_line > 0 and end_line > 0:
                parts.append(f"{file_path}:{start_line}-{end_line}")
            elif start_line > 0:
                parts.append(f"{file_path}:{start_line}")
            else:
                parts.append(file_path)
    
    return " | ".join(parts) if parts else ""


def write_tree_text_format(
    call_tree: Dict[str, Any],
    output_path: str,
    show_location: bool = False
) -> None:
    """
    Write the call tree in text format with tree-style indentation.
    The tree has a synthetic ROOT node at the top with all entry points as children.
    
    Args:
        call_tree: The call tree dictionary
        output_path: Path to write the text output
        show_location: If True, append implementation location details to each node
    """
    def write_node(f, node: Dict[str, Any], prefix: str = "", is_last: bool = True, is_root: bool = False) -> None:
        """Recursively write a node and its children with tree-style formatting."""
        func_name = node.get("function", "")
        children = node.get("children", [])
        location = node.get("location", [])
        
        # Build the node line with optional location info
        if show_location and location:
            location_str = format_location(location)
            node_text = f"{func_name}  {{{location_str}}}"
        else:
            node_text = func_name
        
        if is_root:
            # ROOT node - no prefix
            f.write(f"{node_text}\n")
            child_prefix = ""
        else:
            # Regular node with tree branch
            branch = "└── " if is_last else "├── "
            f.write(f"{prefix}{branch}{node_text}\n")
            child_prefix = prefix + ("    " if is_last else "│   ")
        
        # Write children
        for i, child in enumerate(children):
            is_last_child = (i == len(children) - 1)
            write_node(f, child, child_prefix, is_last_child, is_root=False)
    
    with open(output_path, 'w') as f:
        root_node = call_tree.get("call_tree", {})
        write_node(f, root_node, "", True, is_root=True)


class CallTreeGenerator:
    """
    High-level class for generating call trees from call graphs.
    
    This class provides a clean API for the data_flow_analyzer to use.
    
    Example usage:
        generator = CallTreeGenerator(max_depth=20, sort_by_depth=True)
        generator.load_from_json("/path/to/merged_call_graph.json")
        call_tree = generator.generate_call_tree()
        generator.write_json("/path/to/output.json", pretty=True)
        generator.write_text("/path/to/output.txt", show_location=True)
    """
    
    def __init__(self, max_depth: int = 20, sort_by_depth: bool = True):
        """
        Initialize the CallTreeGenerator.
        
        Args:
            max_depth: Maximum depth for cycle breaking (default: 20)
            sort_by_depth: If True, sort branches by depth (longest first, default: True)
        """
        self.max_depth = max_depth
        self.sort_by_depth = sort_by_depth
        self.graph: Optional[CallGraph] = None
        self.implementations: Dict[str, List[Dict[str, Any]]] = {}
        self.dag_edges: Dict[str, Set[str]] = {}
        self.call_tree: Optional[Dict[str, Any]] = None
        self._raw_data: Any = None
    
    def load_from_json(self, json_path: str) -> None:
        """
        Load call graph from JSON file.
        
        Args:
            json_path: Path to the merged_call_graph.json file
            
        Raises:
            FileNotFoundError: If the JSON file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
        """
        if not os.path.exists(json_path):
            raise FileNotFoundError(f"Call graph file not found: {json_path}")
        
        with open(json_path, 'r') as f:
            self._raw_data = json.load(f)
        
        self.load_from_data(self._raw_data)
        logger.info(f"Loaded call graph from: {json_path}")
    
    def load_from_data(self, data: Any) -> None:
        """
        Load call graph from parsed JSON data.
        
        Args:
            data: Parsed JSON data (list or dict)
        """
        self._raw_data = data
        self.graph = load_call_graph_from_json(data)
        self.implementations = extract_implementations(data)
        logger.info(f"Loaded {self.graph.get_num_nodes()} nodes and {self.graph.get_num_edges()} edges")
    
    def generate_call_tree(self) -> Dict[str, Any]:
        """
        Generate the call tree structure.
        
        Returns:
            Dictionary containing the call tree and metadata
            
        Raises:
            RuntimeError: If no call graph has been loaded
        """
        if self.graph is None:
            raise RuntimeError("No call graph loaded. Call load_from_json() or load_from_data() first.")
        
        # Create DAG by breaking cycles
        self.dag_edges = create_dag(self.graph, self.max_depth)
        logger.info(f"Created DAG with {sum(len(c) for c in self.dag_edges.values())} edges")
        
        # Generate the call tree with sorting option
        self.call_tree = generate_call_tree(
            self.graph, self.dag_edges, self.implementations,
            sort_by_depth=self.sort_by_depth
        )
        
        metadata = self.call_tree.get('metadata', {})
        logger.info(f"Generated call tree with {metadata.get('total_root_nodes', 0)} root nodes")
        if self.sort_by_depth:
            logger.info(f"Branches sorted by depth (longest first), max_depth: {metadata.get('max_depth', 0)}")
        else:
            logger.info("Branches sorted alphabetically")
        
        return self.call_tree
    
    def write_json(self, output_path: str, pretty: bool = False) -> None:
        """
        Write call tree to JSON file.
        
        Args:
            output_path: Path to write the JSON output
            pretty: If True, format with indentation (default: False)
            
        Raises:
            RuntimeError: If no call tree has been generated
        """
        if self.call_tree is None:
            raise RuntimeError("No call tree generated. Call generate_call_tree() first.")
        
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        
        indent = 2 if pretty else None
        with open(output_path, 'w') as f:
            json.dump(self.call_tree, f, indent=indent)
        
        logger.info(f"Call tree JSON written to: {output_path}")
    
    def write_text(self, output_path: str, show_location: bool = False) -> None:
        """
        Write call tree to text file.
        
        Args:
            output_path: Path to write the text output
            show_location: If True, show file locations (default: False)
            
        Raises:
            RuntimeError: If no call tree has been generated
        """
        if self.call_tree is None:
            raise RuntimeError("No call tree generated. Call generate_call_tree() first.")
        
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        
        write_tree_text_format(self.call_tree, output_path, show_location=show_location)
        logger.info(f"Call tree text written to: {output_path}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the call graph.
        
        Returns:
            Dictionary containing graph statistics
            
        Raises:
            RuntimeError: If no call graph has been loaded
        """
        if self.graph is None:
            raise RuntimeError("No call graph loaded. Call load_from_json() or load_from_data() first.")
        
        return self.graph.get_statistics(self.max_depth)
    
    def get_call_tree(self) -> Optional[Dict[str, Any]]:
        """
        Get the generated call tree.
        
        Returns:
            The call tree dictionary, or None if not generated
        """
        return self.call_tree
    
    def get_graph(self) -> Optional[CallGraph]:
        """
        Get the underlying CallGraph instance.
        
        Returns:
            The CallGraph instance, or None if not loaded
        """
        return self.graph
