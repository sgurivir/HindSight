# Context Generation Profiler Implementation Plan

## Overview

This document outlines the plan to create a new script `dev/ast_inspection/context_generation_profiler.py` that analyzes call trees to compute cumulative line count statistics at each level. This helps understand how much code context would be needed when traversing the call tree from leaves upward.

## Objective

Given a merged call graph JSON file, the script will:
1. Generate a call tree (reusing existing functionality)
2. Compute **multiple sets** of line count statistics at each level:
   - **Full Subtree**: Self + all descendants (children, grandchildren, etc.)
   - **Depth-Limited (2 levels)**: Self + children + grandchildren only
   - **Depth-Limited (3 levels)**: Self + up to 3 levels of descendants
   - **Depth-Limited (4 levels)**: Self + up to 4 levels of descendants
   - **Depth-Limited (5 levels)**: Self + up to 5 levels of descendants

This allows understanding how context size grows as you include more levels of the call hierarchy.

## Current Code Analysis

### Existing Reusable Components

The following modules already provide most of the needed functionality:

| Module | Location | Reusable Functions |
|--------|----------|-------------------|
| [`call_graph_util.py`](hindsight/core/lang_util/call_graph_util.py) | `hindsight/core/lang_util/` | `CallGraph`, `load_call_graph_from_json()`, `compute_levels_from_bottom()` |
| [`call_tree_util.py`](hindsight/core/lang_util/call_tree_util.py) | `hindsight/core/lang_util/` | `CallTreeGenerator`, `extract_implementations()`, `create_dag()`, `build_call_tree_node()` |

### Key Data Structures

1. **Implementation Locations** (from [`extract_implementations()`](hindsight/core/lang_util/call_tree_util.py:25)):
   ```python
   {
       "function_name": [
           {"file_path": str, "start_line": int, "end_line": int},
           ...
       ]
   }
   ```

2. **Call Tree Node** (from [`build_call_tree_node()`](hindsight/core/lang_util/call_tree_util.py:255)):
   ```python
   {
       "function": str,
       "location": [{"file_path": str, "start_line": int, "end_line": int}],
       "children": [...]
   }
   ```

3. **Levels** (from [`compute_levels_from_bottom()`](hindsight/core/lang_util/call_graph_util.py:95)):
   ```python
   {
       0: {"leaf_func1", "leaf_func2", ...},  # Leaves
       1: {"func_a", "func_b", ...},          # Call only level 0
       2: {"func_x", "func_y", ...},          # Call at least one level 1
       ...
   }
   ```

## Implementation Plan

### Phase 1: Create Helper Module (Optional Refactoring)

**File**: `dev/ast_inspection/call_graph_helper.py`

Extract common CLI patterns and utilities from existing scripts:

```python
# Proposed structure
class CallGraphHelper:
    """Helper class for common call graph operations."""
    
    @staticmethod
    def load_call_graph(json_path: str) -> Tuple[CallGraph, Dict, Any]:
        """Load call graph, implementations, and raw data from JSON file."""
        pass
    
    @staticmethod
    def compute_function_line_count(implementations: Dict, func_name: str) -> int:
        """Compute line count for a single function."""
        pass
    
    @staticmethod
    def validate_json_path(json_path: str) -> None:
        """Validate that JSON file exists and is readable."""
        pass
```

**Reusable code from existing scripts**:

| Source | Code to Extract |
|--------|-----------------|
| [`call_graph_stats.py:82-99`](dev/ast_inspection/call_graph_stats.py:82) | JSON file loading and validation |
| [`call_tree_generator.py:89-94`](dev/ast_inspection/call_tree_generator.py:89) | File existence check pattern |
| [`call_graph_stats.py:37-78`](dev/ast_inspection/call_graph_stats.py:37) | Argument parser setup pattern |

### Phase 2: Create Context Generation Profiler

**File**: `dev/ast_inspection/context_generation_profiler.py`

#### Core Algorithm

```python
def compute_function_line_count(implementations: Dict, func_name: str) -> int:
    """
    Compute line count for a function from its implementations.
    
    Handles multiple implementations by summing all.
    """
    locations = implementations.get(func_name, [])
    total_lines = 0
    for loc in locations:
        start = loc.get("start_line", 0)
        end = loc.get("end_line", 0)
        if start > 0 and end > 0:
            total_lines += (end - start + 1)
    return total_lines


def compute_depth_limited_line_count(
    func_name: str,
    dag_edges: Dict[str, Set[str]],
    implementations: Dict[str, List[Dict]],
    max_levels: int,  # None means unlimited (full subtree)
    current_depth: int = 0,
    visited: Set[str] = None
) -> int:
    """
    Recursively compute line count with depth limit.
    
    Args:
        func_name: Function to compute for
        dag_edges: DAG edges (parent -> children)
        implementations: Function implementation locations
        max_levels: Maximum levels to include (None = unlimited)
                   - 1 = self only
                   - 2 = self + children
                   - 3 = self + children + grandchildren
                   - etc.
        current_depth: Current recursion depth (0 = root)
        visited: Set of visited nodes to avoid cycles
    
    Returns:
        Total line count within the depth limit
    """
    if visited is None:
        visited = set()
    
    # Avoid cycles
    if func_name in visited:
        return 0
    visited = visited | {func_name}
    
    # Compute self line count
    self_lines = compute_function_line_count(implementations, func_name)
    
    # Check depth limit (max_levels=1 means self only, no children)
    if max_levels is not None and current_depth >= max_levels - 1:
        return self_lines
    
    # Recursively compute children's counts
    children_total = 0
    for child in dag_edges.get(func_name, set()):
        children_total += compute_depth_limited_line_count(
            child, dag_edges, implementations, max_levels,
            current_depth + 1, visited
        )
    
    return self_lines + children_total
```

#### Multi-Depth Statistics Computation

```python
# Depth limits to compute statistics for
DEPTH_LIMITS = [
    None,  # Full subtree (unlimited)
    2,     # Self + children
    3,     # Self + children + grandchildren
    4,     # Self + up to 3 levels of descendants
    5,     # Self + up to 4 levels of descendants
]


def compute_multi_depth_level_statistics(
    graph: CallGraph,
    dag_edges: Dict[str, Set[str]],
    implementations: Dict[str, List[Dict]],
    depth_limits: List[Optional[int]] = DEPTH_LIMITS,
    max_graph_depth: int = 20
) -> Dict[str, Dict[int, Dict[str, float]]]:
    """
    Compute line count statistics for each level at multiple depth limits.
    
    Returns:
        {
            "full_subtree": {
                0: {"mean": X, "min": Y, "max": Z, "median": W, "count": N},
                1: {...},
                ...
            },
            "depth_2": {
                0: {"mean": X, "min": Y, "max": Z, "median": W, "count": N},
                1: {...},
                ...
            },
            "depth_3": {...},
            "depth_4": {...},
            "depth_5": {...},
        }
    """
    # Step 1: Compute levels from bottom
    levels = graph.compute_levels_from_bottom(max_graph_depth)
    
    # Step 2: Compute line counts for each depth limit
    all_stats = {}
    
    for depth_limit in depth_limits:
        # Compute cumulative counts for this depth limit
        cumulative_counts = {}
        for node in graph.nodes:
            cumulative_counts[node] = compute_depth_limited_line_count(
                node, dag_edges, implementations, depth_limit
            )
        
        # Compute statistics per level
        depth_key = "full_subtree" if depth_limit is None else f"depth_{depth_limit}"
        level_stats = {}
        
        for level, nodes in sorted(levels.items()):
            counts = [cumulative_counts.get(node, 0) for node in nodes]
            if counts:
                level_stats[level] = {
                    "mean": statistics.mean(counts),
                    "min": min(counts),
                    "max": max(counts),
                    "median": statistics.median(counts),
                    "count": len(counts)
                }
        
        all_stats[depth_key] = level_stats
    
    return all_stats
```

#### Optimized Computation with Memoization

For better performance, we can use memoization per depth limit:

```python
def compute_all_depth_limited_counts(
    dag_edges: Dict[str, Set[str]],
    all_nodes: Set[str],
    implementations: Dict[str, List[Dict]],
    depth_limits: List[Optional[int]] = DEPTH_LIMITS
) -> Dict[str, Dict[str, int]]:
    """
    Compute line counts for all nodes at all depth limits efficiently.
    
    Uses memoization to avoid redundant computation.
    
    Returns:
        {
            "full_subtree": {"func1": 100, "func2": 200, ...},
            "depth_2": {"func1": 50, "func2": 80, ...},
            ...
        }
    """
    # Pre-compute self line counts (used by all depth limits)
    self_counts = {
        node: compute_function_line_count(implementations, node)
        for node in all_nodes
    }
    
    results = {}
    
    for depth_limit in depth_limits:
        depth_key = "full_subtree" if depth_limit is None else f"depth_{depth_limit}"
        
        # Use memoization for this depth limit
        memo: Dict[Tuple[str, int], int] = {}
        
        def compute_with_memo(func: str, remaining_depth: int) -> int:
            """Compute with memoization on (func, remaining_depth)."""
            if remaining_depth == 0:
                return self_counts.get(func, 0)
            
            key = (func, remaining_depth)
            if key in memo:
                return memo[key]
            
            total = self_counts.get(func, 0)
            for child in dag_edges.get(func, set()):
                total += compute_with_memo(child, remaining_depth - 1)
            
            memo[key] = total
            return total
        
        # Compute for all nodes
        node_counts = {}
        for node in all_nodes:
            if depth_limit is None:
                # Full subtree - use large number as "unlimited"
                node_counts[node] = compute_with_memo(node, 1000)
            else:
                node_counts[node] = compute_with_memo(node, depth_limit - 1)
        
        results[depth_key] = node_counts
    
    return results
```

### Phase 3: CLI Interface

```python
def main():
    parser = argparse.ArgumentParser(
        description="Profile context generation by computing cumulative line counts per call tree level."
    )
    parser.add_argument(
        "-f", metavar="<path>", required=True,
        help="Path to merged call graph JSON file"
    )
    parser.add_argument(
        "-o", "--output", metavar="<path>", default="/tmp/context_profile.txt",
        help="Output file path (default: /tmp/context_profile.txt)"
    )
    parser.add_argument(
        "--max-depth", type=int, default=20,
        help="Maximum depth for cycle breaking (default: 20)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output in JSON format"
    )
    args = parser.parse_args()
    
    # Load and process
    # ... implementation ...
```

### Phase 4: Output Format

#### Text Output (Default)

```
================================================================================
CONTEXT GENERATION PROFILE
================================================================================
Input: /path/to/merged_call_graph.json
Total Functions: 1234
Total Levels: 15
================================================================================

================================================================================
FULL SUBTREE STATISTICS (Self + All Descendants)
================================================================================
Level | Count |    Mean |     Min |     Max |  Median
--------------------------------------------------------------------------------
    0 |   450 |    25.3 |       3 |     150 |    18.0
    1 |   320 |   178.5 |      15 |    2450 |   125.0
    2 |   180 |   485.2 |      45 |    8200 |   320.0
    3 |   120 |  1025.8 |     100 |   15000 |   680.0
    ...
--------------------------------------------------------------------------------

================================================================================
DEPTH-LIMITED STATISTICS: 2 Levels (Self + Children)
================================================================================
Level | Count |    Mean |     Min |     Max |  Median
--------------------------------------------------------------------------------
    0 |   450 |    25.3 |       3 |     150 |    18.0
    1 |   320 |    78.5 |      15 |     450 |    55.0
    2 |   180 |   125.2 |      25 |     800 |    90.0
    3 |   120 |   185.8 |      40 |    1200 |   130.0
    ...
--------------------------------------------------------------------------------

================================================================================
DEPTH-LIMITED STATISTICS: 3 Levels (Self + Children + Grandchildren)
================================================================================
Level | Count |    Mean |     Min |     Max |  Median
--------------------------------------------------------------------------------
    0 |   450 |    25.3 |       3 |     150 |    18.0
    1 |   320 |   105.5 |      15 |     650 |    75.0
    2 |   180 |   195.2 |      35 |    1500 |   140.0
    3 |   120 |   325.8 |      60 |    2800 |   220.0
    ...
--------------------------------------------------------------------------------

================================================================================
DEPTH-LIMITED STATISTICS: 4 Levels
================================================================================
... (similar format)

================================================================================
DEPTH-LIMITED STATISTICS: 5 Levels
================================================================================
... (similar format)

================================================================================
COMPARISON SUMMARY (Mean Line Counts by Level)
================================================================================
Level |  Full Tree |  Depth 2 |  Depth 3 |  Depth 4 |  Depth 5
--------------------------------------------------------------------------------
    0 |       25.3 |     25.3 |     25.3 |     25.3 |     25.3
    1 |      178.5 |     78.5 |    105.5 |    125.5 |    145.5
    2 |      485.2 |    125.2 |    195.2 |    285.2 |    365.2
    3 |     1025.8 |    185.8 |    325.8 |    485.8 |    685.8
    ...
--------------------------------------------------------------------------------
Note: Values shown are MEAN line counts per level
================================================================================
```

#### JSON Output (with `--json` flag)

```json
{
  "metadata": {
    "input_file": "/path/to/merged_call_graph.json",
    "total_functions": 1234,
    "total_levels": 15,
    "max_depth_setting": 20,
    "depth_limits_computed": ["full_subtree", "depth_2", "depth_3", "depth_4", "depth_5"]
  },
  "statistics": {
    "full_subtree": {
      "0": {"mean": 25.3, "min": 3, "max": 150, "median": 18.0, "count": 450},
      "1": {"mean": 178.5, "min": 15, "max": 2450, "median": 125.0, "count": 320},
      "2": {"mean": 485.2, "min": 45, "max": 8200, "median": 320.0, "count": 180}
    },
    "depth_2": {
      "0": {"mean": 25.3, "min": 3, "max": 150, "median": 18.0, "count": 450},
      "1": {"mean": 78.5, "min": 15, "max": 450, "median": 55.0, "count": 320},
      "2": {"mean": 125.2, "min": 25, "max": 800, "median": 90.0, "count": 180}
    },
    "depth_3": {
      "0": {"mean": 25.3, "min": 3, "max": 150, "median": 18.0, "count": 450},
      "1": {"mean": 105.5, "min": 15, "max": 650, "median": 75.0, "count": 320},
      "2": {"mean": 195.2, "min": 35, "max": 1500, "median": 140.0, "count": 180}
    },
    "depth_4": { "...": "..." },
    "depth_5": { "...": "..." }
  }
}
```

## File Structure After Implementation

```
dev/ast_inspection/
├── __init__.py                      # Existing
├── call_graph_stats.py              # Existing (no changes)
├── call_tree_generator.py           # Existing (no changes)
├── GraphUtil.py                     # Existing (no changes)
├── call_graph_helper.py             # NEW: Common utilities
├── context_generation_profiler.py   # NEW: Main profiler script
└── CONTEXT_GENERATION_PROFILER_PLAN.md  # This document
```

## Dependencies

The new scripts will depend on:

1. **Core modules** (already exist):
   - [`hindsight/core/lang_util/call_graph_util.py`](hindsight/core/lang_util/call_graph_util.py)
   - [`hindsight/core/lang_util/call_tree_util.py`](hindsight/core/lang_util/call_tree_util.py)

2. **Standard library**:
   - `argparse` - CLI argument parsing
   - `json` - JSON I/O
   - `statistics` - Mean, median calculations
   - `collections` - `defaultdict`, `deque`
   - `pathlib` - Path handling

## Testing Strategy

1. **Unit Tests**:
   - Test `compute_function_line_count()` with various edge cases
   - Test `compute_depth_limited_line_count()` with known graphs at different depth limits
   - Test `compute_multi_depth_level_statistics()` for all 5 statistics sets
   - Verify memoization correctness in `compute_all_depth_limited_counts()`

2. **Integration Tests**:
   - Run against sample `merged_call_graph.json` files
   - Verify output format matches specification for all depth limits
   - Verify JSON output structure contains all 5 statistics sets
   - Verify comparison summary table is correctly computed

3. **Manual Validation**:
   ```bash
   # Test with existing sample data (text output)
   python dev/ast_inspection/context_generation_profiler.py \
       -f /path/to/merged_call_graph.json \
       -o /tmp/profile.txt
   
   # Verify JSON output with all depth limits
   python dev/ast_inspection/context_generation_profiler.py \
       -f /path/to/merged_call_graph.json \
       --json \
       -o /tmp/profile.json
   ```

## Implementation Order

1. **Step 1**: Create `call_graph_helper.py` with common utilities
2. **Step 2**: Create `context_generation_profiler.py` with core algorithm
3. **Step 3**: Add CLI interface and output formatting
4. **Step 4**: Add JSON output option
5. **Step 5**: Test with real call graph data
6. **Step 6**: Update `__init__.py` to export new modules

## Estimated Effort

| Phase | Estimated Time |
|-------|---------------|
| Phase 1: Helper module | 1 hour |
| Phase 2: Core profiler | 2 hours |
| Phase 3: CLI interface | 30 minutes |
| Phase 4: Output formatting | 30 minutes |
| Testing & validation | 1 hour |
| **Total** | **~5 hours** |

## Example Usage

```bash
# Basic usage
python dev/ast_inspection/context_generation_profiler.py \
    -f ~/hindsight_artifacts/corelocation/code_insights/merged_call_graph.json

# With custom output path
python dev/ast_inspection/context_generation_profiler.py \
    -f ~/hindsight_artifacts/xnu/code_insights/merged_call_graph.json \
    -o ~/analysis/xnu_context_profile.txt

# JSON output for programmatic use
python dev/ast_inspection/context_generation_profiler.py \
    -f ~/hindsight_artifacts/opencv/code_insights/merged_call_graph.json \
    --json \
    -o ~/analysis/opencv_profile.json

# With custom max depth
python dev/ast_inspection/context_generation_profiler.py \
    -f /path/to/merged_call_graph.json \
    --max-depth 30
```
