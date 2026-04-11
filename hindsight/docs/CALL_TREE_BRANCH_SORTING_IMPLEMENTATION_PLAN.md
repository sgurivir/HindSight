# Call Tree Branch Sorting Implementation Plan

## Overview

This document outlines the implementation plan for sorting branches in the call tree output files (`call_tree.txt` and `call_tree.json`) so that **longer branches appear at the top**. This feature will help users quickly identify the deepest call chains in their codebase.

## Design Principles

This implementation follows these software engineering best practices for handling large trees:

1. **Single-Pass Computation**: Compute depths in one traversal, not per-node
2. **Bottom-Up Dynamic Programming**: Leverage DAG structure for O(N) complexity
3. **Memory Efficiency**: Use iterative approaches where possible to avoid stack overflow
4. **Lazy Evaluation**: Only compute what's needed
5. **Cache-Friendly Data Structures**: Use contiguous arrays for sorting operations

## Problem Statement

Currently, when the data flow analyzer generates call trees, the branches are sorted alphabetically by function name (see [`call_tree_util.py:216`](hindsight/core/lang_util/call_tree_util.py:216)):

```python
for child in sorted(children):  # Alphabetical sorting
    if child not in visited:
        child_node = build_call_tree_node(child, dag_edges, implementations, visited.copy())
        node["children"].append(child_node)
```

Similarly, root nodes are sorted alphabetically at [`call_tree_util.py:246`](hindsight/core/lang_util/call_tree_util.py:246):

```python
for root in sorted(root_nodes):  # Alphabetical sorting
    tree_node = build_call_tree_node(root, dag_edges, implementations, set())
    children.append(tree_node)
```

**Desired Behavior**: Sort branches by their maximum depth (longest branch first), so that the most complex call chains appear at the top of the output.

## Current Architecture Analysis

### Key Files

1. **[`hindsight/core/lang_util/call_tree_util.py`](hindsight/core/lang_util/call_tree_util.py)**: Contains the core call tree generation logic
   - [`build_call_tree_node()`](hindsight/core/lang_util/call_tree_util.py:186): Recursively builds call tree nodes
   - [`generate_call_tree()`](hindsight/core/lang_util/call_tree_util.py:224): Generates the complete call tree structure
   - [`write_tree_text_format()`](hindsight/core/lang_util/call_tree_util.py:297): Writes the text output
   - [`CallTreeGenerator`](hindsight/core/lang_util/call_tree_util.py:344): High-level API class

2. **[`hindsight/core/lang_util/call_graph_util.py`](hindsight/core/lang_util/call_graph_util.py)**: Contains graph algorithms
   - [`CallGraph`](hindsight/core/lang_util/call_graph_util.py:21): Graph data structure
   - [`compute_levels_from_bottom()`](hindsight/core/lang_util/call_graph_util.py:95): Computes node levels (useful for depth calculation)

3. **[`hindsight/analyzers/data_flow_analyzer.py`](hindsight/analyzers/data_flow_analyzer.py)**: The analyzer that uses the call tree utilities

### Current Call Tree Generation Flow

```
data_flow_analyzer.py
    └── CallTreeGenerator.generate_call_tree()
        └── call_tree_util.generate_call_tree()
            └── build_call_tree_node() [recursive]
                └── sorted(children) [alphabetical]
```

## Implementation Plan

### Phase 1: Efficient Depth Computation (Bottom-Up Dynamic Programming)

The key insight is that we already have a DAG (Directed Acyclic Graph) after cycle breaking. We can leverage this structure to compute all depths in a **single bottom-up pass** using topological order, which is much more efficient than recursive memoization.

#### 1.1 Add `compute_all_subtree_depths()` Function (Iterative, O(N+E))

Add a new function to [`call_tree_util.py`](hindsight/core/lang_util/call_tree_util.py) that computes depths for ALL nodes in a single pass:

```python
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
```

**Why this is faster than recursive memoization:**

| Aspect | Recursive Memoization | Bottom-Up DP |
|--------|----------------------|--------------|
| Function calls | O(N) recursive calls | 0 recursive calls |
| Stack usage | O(depth) stack frames | O(1) stack |
| Cache locality | Poor (random access) | Good (sequential) |
| Overhead | Dict lookups per call | Single pass |
| Risk | Stack overflow on deep trees | None |

### Phase 2: Modify Tree Building to Sort by Depth

#### 2.1 Update `build_call_tree_node()` Function

Modify the [`build_call_tree_node()`](hindsight/core/lang_util/call_tree_util.py:186) function to use pre-computed depths:

```python
def build_call_tree_node(
    func: str,
    dag_edges: Dict[str, Set[str]],
    implementations: Dict[str, List[Dict[str, Any]]],
    visited: Set[str],
    depths: Dict[str, int] = None,
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
    
    # Mark as visited
    visited = visited | {func}  # Create new set to avoid mutation
    
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
```

#### 2.2 Update `generate_call_tree()` Function

Modify the [`generate_call_tree()`](hindsight/core/lang_util/call_tree_util.py:224) function to compute depths once and reuse:

```python
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
        sort_by_depth: If True, sort branches by depth (longest first)
        
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
    if sort_by_depth:
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
```

### Phase 3: Add Configuration Option

#### 3.1 Update `CallTreeGenerator` Class

Add a configuration option to the [`CallTreeGenerator`](hindsight/core/lang_util/call_tree_util.py:344) class:

```python
class CallTreeGenerator:
    """
    High-level class for generating call trees from call graphs.
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
    
    def generate_call_tree(self) -> Dict[str, Any]:
        """
        Generate the call tree structure.
        """
        if self.graph is None:
            raise RuntimeError("No call graph loaded.")
        
        # Create DAG by breaking cycles
        self.dag_edges = create_dag(self.graph, self.max_depth)
        
        # Generate the call tree with sorting option
        self.call_tree = generate_call_tree(
            self.graph, self.dag_edges, self.implementations,
            sort_by_depth=self.sort_by_depth
        )
        
        return self.call_tree
```

#### 3.2 Update `DataFlowAnalysisRunner`

Add command-line option to [`data_flow_analyzer.py`](hindsight/analyzers/data_flow_analyzer.py):

```python
# In _generate_call_tree method
def _generate_call_tree(self, config: dict) -> Dict[str, Any]:
    # ...
    max_depth = config.get('max_call_depth', 20)
    sort_by_depth = config.get('sort_by_depth', True)  # Default: True
    self.call_tree_generator = CallTreeGenerator(
        max_depth=max_depth,
        sort_by_depth=sort_by_depth
    )
    # ...

# In argparse section
parser.add_argument(
    "--sort-by-depth",
    action="store_true",
    default=True,
    help="Sort branches by depth (longest first, default: True)"
)
parser.add_argument(
    "--no-sort-by-depth",
    action="store_false",
    dest="sort_by_depth",
    help="Sort branches alphabetically instead of by depth"
)
```

### Phase 4: Add Depth Information to Output (Optional Enhancement)

#### 4.1 Include Depth in JSON Output

Optionally include the subtree depth in the JSON output for each node:

```python
def build_call_tree_node(..., include_depth: bool = False) -> Dict[str, Any]:
    node = {
        "function": func,
        "location": implementations.get(func, []),
        "children": []
    }
    
    if include_depth:
        node["subtree_depth"] = compute_subtree_depth(func, dag_edges, set(), depth_memo)
    
    # ... rest of function
```

#### 4.2 Include Depth in Text Output

Optionally show depth in the text output:

```python
def write_tree_text_format(..., show_depth: bool = False) -> None:
    def write_node(f, node, prefix="", is_last=True, is_root=False):
        func_name = node.get("function", "")
        depth = node.get("subtree_depth", "")
        
        if show_depth and depth:
            node_text = f"{func_name} [depth: {depth}]"
        else:
            node_text = func_name
        
        # ... rest of function
```

## Implementation Steps

### Step 1: Modify `call_tree_util.py`

1. Add `from collections import deque` import at the top of the file
2. Add [`compute_all_subtree_depths()`](hindsight/core/lang_util/call_tree_util.py) function (iterative, O(N+E))
3. Update [`build_call_tree_node()`](hindsight/core/lang_util/call_tree_util.py:186) signature to accept `depths` dict and `sort_by_depth` flag
4. Update [`generate_call_tree()`](hindsight/core/lang_util/call_tree_util.py:224) to:
   - Call `compute_all_subtree_depths()` once at the start
   - Pass pre-computed depths to `build_call_tree_node()`
   - Add `max_depth` to metadata
5. Update [`CallTreeGenerator.__init__()`](hindsight/core/lang_util/call_tree_util.py:358) to accept `sort_by_depth` parameter
6. Update [`CallTreeGenerator.generate_call_tree()`](hindsight/core/lang_util/call_tree_util.py:404) to pass the parameter

### Step 2: Modify `data_flow_analyzer.py`

1. Add `--sort-by-depth` / `--no-sort-by-depth` command-line arguments
2. Update [`_generate_call_tree()`](hindsight/analyzers/data_flow_analyzer.py:228) to pass `sort_by_depth` to `CallTreeGenerator`
3. Update [`run()`](hindsight/analyzers/data_flow_analyzer.py:330) method signature to accept `sort_by_depth` parameter

### Step 3: Testing

1. **Unit tests**: Test depth computation on small graphs (empty, single node, linear, diamond)
2. **Performance tests**: Verify O(N+E) behavior on large synthetic graphs
3. **Integration tests**: Run on real repositories, verify output ordering
4. **Regression tests**: Ensure `--no-sort-by-depth` produces identical output to current behavior

## Example Output

### Before (Alphabetical Sorting)

```
ROOT
├── alpha_function
│   └── helper_a
├── beta_function
│   ├── helper_b1
│   │   └── deep_helper
│   │       └── deepest_helper
│   └── helper_b2
└── gamma_function
```

### After (Depth-Based Sorting)

```
ROOT
├── beta_function          [depth: 3]
│   ├── helper_b1
│   │   └── deep_helper
│   │       └── deepest_helper
│   └── helper_b2
├── alpha_function         [depth: 1]
│   └── helper_a
└── gamma_function         [depth: 0]
```

## Performance Analysis

### Time Complexity

| Operation | Complexity | Notes |
|-----------|------------|-------|
| Depth computation | O(N + E) | Single bottom-up pass using Kahn's algorithm |
| Root node sorting | O(R log R) | R = number of root nodes |
| Tree construction | O(N) | Each node visited once |
| Child sorting (per node) | O(C log C) | C = children count, amortized O(E log E) total |
| **Total** | **O(N + E log E)** | Dominated by sorting for dense graphs |

For sparse graphs (E ≈ N), this simplifies to **O(N log N)**.

### Space Complexity

| Data Structure | Space | Notes |
|----------------|-------|-------|
| Depths dictionary | O(N) | One entry per node |
| Reverse edges | O(E) | For bottom-up propagation |
| Processing queue | O(N) | Worst case for Kahn's algorithm |
| Visited sets | O(D) | D = max depth (reused per branch) |
| **Total** | **O(N + E)** | Linear in graph size |

### Benchmarks (Expected)

Based on similar graph algorithms, expected performance for typical codebases:

| Graph Size | Nodes | Edges | Expected Time |
|------------|-------|-------|---------------|
| Small | 1,000 | 5,000 | < 10ms |
| Medium | 10,000 | 50,000 | < 100ms |
| Large | 100,000 | 500,000 | < 1s |
| Very Large | 1,000,000 | 5,000,000 | < 10s |

### Memory Efficiency Tips

For extremely large graphs (>1M nodes), consider these additional optimizations:

1. **Streaming output**: Write tree nodes to file as they're generated instead of building full tree in memory
2. **Depth-limited processing**: Only compute depths up to a configurable maximum
3. **Lazy child loading**: For JSON output, use generators to avoid materializing all children at once

## Comparison with Alternative Approaches

### Approach 1: Recursive Memoization (Naive)
```
Pros: Simple to implement
Cons: Stack overflow risk, poor cache locality, function call overhead
Complexity: O(N) time, O(N + depth) stack space
```

### Approach 2: Bottom-Up DP (This Plan) ✓
```
Pros: No recursion for depth calc, cache-friendly, predictable memory usage
Cons: Slightly more complex implementation
Complexity: O(N + E) time, O(N + E) space
```

### Approach 3: On-Demand Computation
```
Pros: Minimal upfront cost
Cons: Repeated computation, unpredictable performance
Complexity: O(N * depth) worst case
```

**Conclusion**: Bottom-Up DP is the best choice for production use with large codebases.

## Backward Compatibility

- The default behavior will be `sort_by_depth=True` (new behavior)
- Users can opt-out using `--no-sort-by-depth` flag
- The JSON and text output formats remain unchanged (only ordering differs)
- New `max_depth` field added to metadata (additive, non-breaking)

## Testing Strategy

### Unit Tests

1. **Empty graph**: Verify handling of graphs with no nodes
2. **Single node**: Verify leaf node has depth 0
3. **Linear chain**: Verify depths are computed correctly (0, 1, 2, ...)
4. **Diamond pattern**: Verify max depth is taken at merge points
5. **Wide tree**: Verify sorting works with many siblings
6. **Deep tree**: Verify no stack overflow with depth > 1000

### Performance Tests

```python
def test_large_graph_performance():
    """Verify depth computation completes in reasonable time for large graphs."""
    # Generate synthetic graph with 100K nodes
    dag_edges = generate_random_dag(num_nodes=100_000, avg_edges_per_node=5)
    
    start = time.time()
    depths = compute_all_subtree_depths(dag_edges, set(dag_edges.keys()))
    elapsed = time.time() - start
    
    assert elapsed < 5.0, f"Depth computation took {elapsed}s, expected < 5s"
```

### Integration Tests

1. Run on real repositories of varying sizes
2. Compare output with alphabetically sorted version (same content, different order)
3. Verify JSON and text outputs are consistent

## Summary

This implementation plan provides a high-performance solution for sorting call tree branches by depth:

1. **Optimal algorithm**: Bottom-up DP with O(N + E) depth computation
2. **Production-ready**: Handles large graphs without stack overflow
3. **Cache-friendly**: Sequential memory access patterns
4. **Minimal code changes**: Only modifies [`call_tree_util.py`](hindsight/core/lang_util/call_tree_util.py) and [`data_flow_analyzer.py`](hindsight/analyzers/data_flow_analyzer.py)
5. **Configurable**: Users can choose between depth-based or alphabetical sorting
6. **Backward compatible**: Existing output formats are preserved
7. **Well-tested**: Comprehensive unit and performance test strategy
