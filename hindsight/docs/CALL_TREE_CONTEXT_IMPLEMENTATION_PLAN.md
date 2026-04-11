# Call Tree Context Enhancement Implementation Plan

## Executive Summary

This document outlines the plan to enhance the code_analyzer's prompt context by including a hierarchical call tree section. Currently, the analyzer sends the function being analyzed along with its invoking functions (callers) and invoked functions (callees). This enhancement will add a tree-format representation of the call hierarchy that includes grandchildren and deeper descendants, giving the LLM better context about how the function fits within the broader codebase.

## Current State Analysis

### What We Currently Send to LLM

Based on analysis of [`prompt_builder.py`](hindsight/core/prompts/prompt_builder.py), the current prompt includes:

1. **Primary Function**: The function being analyzed with its code body
2. **Invoked Functions** (`functions_invoked`): Direct callees - functions called by the primary function
3. **Invoking Functions** (`invoked_by`): Direct callers - functions that call the primary function
4. **Data Types Used**: Classes/structs used by the function
5. **Constants Used**: Constants referenced by the function

### Available Data Structures

From [`call_tree_util.py`](hindsight/core/lang_util/call_tree_util.py) and [`call_graph_util.py`](hindsight/core/lang_util/call_graph_util.py):

1. **merged_call_graph.json**: Contains the full call graph with:
   - Function name
   - **Relative file path** (relative to repo root) - stored in `context.file` or `file_name` fields
   - Start/end line numbers
   - `functions_invoked`: List of function names called
   - `invoked_by`: List of function names that call this function
   - Checksum for caching

2. **CallTreeGenerator**: Already generates hierarchical call trees with:
   - Function name
   - Location (file_path, start_line, end_line) - **uses relative paths**
   - Children (recursive structure)
   - Supports depth limiting and cycle detection

3. **CallGraph**: Provides graph operations including:
   - `get_outgoing_edges()`: Get callees
   - `get_incoming_edges()`: Get callers
   - Level computation for DAG creation

### Available LLM Tools for Code Inspection

The LLM has access to the **`getFileContentByLines`** tool (defined in [`file_tools.py`](hindsight/core/llm/tools/file_tools.py:142)) which allows reading specific line ranges from files:

```python
def execute_get_file_content_by_lines(self, path: str, start_line: int, end_line: int, reason: str = None) -> str:
    """
    Execute getFileContentByLines tool to retrieve content between specific line numbers from a file.
    
    Args:
        path: Relative path to the file
        start_line: Starting line number (1-based)
        end_line: Ending line number (1-based)
        reason: Optional reason for the request
    """
```

This tool is critical because:
- The call tree will show **relative file paths and line numbers**
- The LLM can use this tool to inspect any function in the tree if needed
- It enables the LLM to "drill down" into grandchildren or deeper descendants

## Feasibility Assessment

### ✅ The Idea Makes Sense

**Benefits:**
1. **Deeper Context**: LLM can understand not just immediate relationships but the broader call chain
2. **Pattern Recognition**: Helps identify issues that span multiple levels (e.g., resource leaks propagating through call chains)
3. **Architectural Understanding**: Shows how the function fits in the overall system architecture
4. **Compact Representation**: Tree format is more token-efficient than sending full code for all descendants
5. **Actionable References**: LLM can use `getFileContentByLines` to inspect any function in the tree

**Existing Infrastructure:**
- `CallTreeGenerator` already builds hierarchical trees from call graphs
- `extract_implementations()` extracts file/line info for each function
- `format_location()` formats location info compactly
- DAG creation handles cycles properly
- **`getFileContentByLines` tool** allows LLM to read code at specific line ranges

### ✅ We Have Enough Information

The `merged_call_graph.json` contains all necessary data:
- Function names ✓
- **Relative file paths** (relative to repo root) ✓
- Line numbers (start/end) ✓
- Call relationships (both directions) ✓

**File Path Format Confirmation:**
The AST utilities (e.g., [`cast_util.py`](hindsight/core/lang_util/cast_util.py:1251), [`java_ast_util.py`](hindsight/core/lang_util/java_ast_util.py:281)) consistently store **relative paths**:
```python
try:
    rel_path = str(Path(file_path).relative_to(repo_root))
except ValueError:
    rel_path = file_path
```

### ⚠️ Token Budget Considerations

Current limits from [`constants.py`](hindsight/core/constants.py):
- `DEFAULT_MAX_TOKENS = 64000`
- Large functions (>300 lines) already use reference-only format

The tree format is inherently compact and uses **relative paths**:
```
function_name  {src/analyzer/processor.c:100-150}
├── child1  {src/utils/validator.c:200-220}
│   ├── grandchild1  {src/utils/bounds.c:50-60}
│   └── grandchild2  {src/utils/normalizer.c:70-80}
└── child2  {src/storage/writer.c:300-350}
```

Estimated token usage per tree node: ~20-35 tokens (function name + relative path + line numbers)

## Proposed Implementation

### Phase 1: Call Tree Section Generator

Create a new utility class to generate focused call tree sections:

```python
# hindsight/core/lang_util/call_tree_section_generator.py

class CallTreeSectionGenerator:
    """
    Generates a focused call tree section for a specific function.
    
    Unlike CallTreeGenerator which builds the entire tree from roots,
    this generates a subtree centered on a specific function showing:
    - Ancestors (callers) up to a configurable depth
    - Descendants (callees) up to a configurable depth
    """
    
    def __init__(self, 
                 call_graph_data: Dict[str, Any],
                 max_ancestor_depth: int = 2,
                 max_descendant_depth: int = 3,
                 max_children_per_node: int = 5):
        """
        Args:
            call_graph_data: The merged_call_graph.json data
            max_ancestor_depth: How many levels of callers to include
            max_descendant_depth: How many levels of callees to include  
            max_children_per_node: Max children to show per node (prevents explosion)
        """
        pass
    
    def generate_section(self, function_name: str) -> Dict[str, Any]:
        """
        Generate a call tree section centered on the given function.
        
        Returns:
            {
                "ancestors": [...],  # Caller chain
                "function": {...},   # The target function
                "descendants": [...] # Callee tree
            }
        """
        pass
    
    def format_as_text(self, section: Dict[str, Any]) -> str:
        """
        Format the section as compact text for prompt inclusion.
        Uses relative file paths (relative to repo root) for all locations.
        
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
        pass
    
    def estimate_token_count(self, section: Dict[str, Any]) -> int:
        """Estimate tokens needed for this section."""
        pass
```

### Phase 2: Integration with PromptBuilder

Modify [`prompt_builder.py`](hindsight/core/prompts/prompt_builder.py:278) to include the call tree section:

```python
@staticmethod
def _convert_json_to_comment_format(
    json_content: str, 
    merged_functions_data: Optional[Dict[str, Any]] = None, 
    merged_data_types_data: Optional[Dict[str, Any]] = None, 
    merged_call_graph_data: Optional[Dict[str, Any]] = None
) -> str:
    """
    Convert JSON content to comment-based format.
    
    ENHANCEMENT: Add call tree section after existing context.
    """
    # ... existing code ...
    
    # NEW: Add call tree section if call graph data is available
    if merged_call_graph_data and function_name:
        call_tree_section = PromptBuilder._generate_call_tree_section(
            function_name, 
            merged_call_graph_data
        )
        if call_tree_section:
            result.append("")
            result.append("// === CALL TREE CONTEXT ===")
            result.append("// This shows the function's position in the call hierarchy")
            result.append(call_tree_section)
            result.append("")
    
    return '\n'.join(result)
```

### Phase 3: Token Budget Management

Add token budget tracking to ensure we don't exceed limits:

```python
# In prompt_builder.py

class PromptBuilder:
    # New constants for call tree section
    MAX_CALL_TREE_TOKENS = 2000  # Reserve ~2000 tokens for call tree
    TOKENS_PER_TREE_NODE = 20   # Estimated tokens per node
    
    @staticmethod
    def _generate_call_tree_section(
        function_name: str,
        call_graph_data: Dict[str, Any],
        max_tokens: int = MAX_CALL_TREE_TOKENS
    ) -> Optional[str]:
        """
        Generate call tree section with token budget awareness.
        
        Dynamically adjusts depth based on available token budget.
        """
        generator = CallTreeSectionGenerator(call_graph_data)
        
        # Start with default depths
        section = generator.generate_section(function_name)
        estimated_tokens = generator.estimate_token_count(section)
        
        # Reduce depth if over budget
        while estimated_tokens > max_tokens and generator.can_reduce_depth():
            generator.reduce_depth()
            section = generator.generate_section(function_name)
            estimated_tokens = generator.estimate_token_count(section)
        
        if estimated_tokens > max_tokens:
            # Still over budget, skip call tree section
            logger.warning(f"Call tree section too large ({estimated_tokens} tokens), skipping")
            return None
        
        return generator.format_as_text(section)
```

### Phase 4: Update Token Accounting

Modify [`token_tracker.py`](hindsight/analyzers/token_tracker.py) to track call tree section usage:

```python
class TokenTracker:
    def __init__(self, llm_provider_type: str = "claude"):
        # ... existing code ...
        
        # New: Track call tree section tokens
        self.call_tree_section_tokens = 0
    
    def add_call_tree_tokens(self, tokens: int) -> None:
        """Track tokens used by call tree sections."""
        self.call_tree_section_tokens += tokens
        self.logger.debug(f"Call tree section tokens: {tokens}")
    
    def log_summary(self) -> None:
        """Log a summary of token usage."""
        # ... existing code ...
        
        if self.call_tree_section_tokens > 0:
            self.logger.info(f"Call Tree Section Tokens: {self.call_tree_section_tokens:,}")
```

## Configuration Options

Add new configuration options to control the feature:

```python
# In constants.py
CALL_TREE_MAX_ANCESTOR_DEPTH = 2      # Levels of callers to show
CALL_TREE_MAX_DESCENDANT_DEPTH = 3    # Levels of callees to show
CALL_TREE_MAX_CHILDREN_PER_NODE = 5   # Max children per node
CALL_TREE_MAX_TOKENS = 2000           # Max tokens for call tree section
CALL_TREE_ENABLED = True              # Feature flag
```

Allow override via config JSON:
```json
{
    "call_tree_context": {
        "enabled": true,
        "max_ancestor_depth": 2,
        "max_descendant_depth": 3,
        "max_children_per_node": 5,
        "max_tokens": 2000
    }
}
```

## Example Output

For a function `analyze_item()`, the call tree section would look like:

```
// === CALL TREE CONTEXT ===
// This shows the function's position in the call hierarchy
// Use getFileContentByLines tool to read code at any location shown below

// CALLERS (who calls this function):
// main()  {src/main.c:10-50}
// └── process_data()  {src/processor/processor.c:100-200}
//     └── [TARGET] analyze_item()  {src/analyzer/analyzer.c:50-80}

// CALLEES (what this function calls):
// [TARGET] analyze_item()  {src/analyzer/analyzer.c:50-80}
// ├── validate_input()  {src/utils/validator.c:20-40}
// │   └── check_bounds()  {src/utils/validator.c:60-70}
// ├── transform_data()  {src/transform/transformer.c:100-150}
// │   ├── apply_filter()  {src/transform/filter.c:30-50}
// │   └── normalize()  {src/transform/normalizer.c:10-25}
// └── store_result()  {src/storage/storage.c:200-250}
//     └── write_to_disk()  {src/io/disk_writer.c:100-120}
```

**Key Features:**
1. **Relative Paths**: All file paths are relative to the repository root (e.g., `src/utils/validator.c`)
2. **Line Numbers**: Each function shows its implementation location (start-end lines)
3. **Tool Integration**: LLM can use `getFileContentByLines` to inspect any function:
   - Example: `getFileContentByLines(path="src/utils/validator.c", startLine=20, endLine=40)` to read `validate_input()`

## Implementation Steps

### Step 1: Create CallTreeSectionGenerator (2-3 hours)
- [ ] Create new file `hindsight/core/lang_util/call_tree_section_generator.py`
- [ ] Implement `generate_section()` method
- [ ] Implement `format_as_text()` method
- [ ] Implement `estimate_token_count()` method
- [ ] Add unit tests

### Step 2: Integrate with PromptBuilder (1-2 hours)
- [ ] Add `_generate_call_tree_section()` method to PromptBuilder
- [ ] Modify `_convert_json_to_comment_format()` to include call tree
- [ ] Add configuration options
- [ ] Add feature flag for easy enable/disable

### Step 3: Token Budget Management (1 hour)
- [ ] Add constants for call tree token limits
- [ ] Implement dynamic depth adjustment
- [ ] Add logging for token usage

### Step 4: Update Token Tracking (30 minutes)
- [ ] Add call tree token tracking to TokenTracker
- [ ] Update summary logging

### Step 5: Testing and Validation (2-3 hours)
- [ ] Test with various function types (leaf, root, middle)
- [ ] Test with cyclic call graphs
- [ ] Test token budget enforcement
- [ ] Validate output format
- [ ] Performance testing with large codebases

### Step 6: Documentation (1 hour)
- [ ] Update README with new feature
- [ ] Add configuration documentation
- [ ] Add examples

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Token budget exceeded | Analysis fails | Dynamic depth adjustment, hard limits |
| Cyclic call graphs | Infinite loops | Reuse existing DAG creation from CallTreeGenerator |
| Performance impact | Slow analysis | Cache call tree sections, lazy generation |
| Large codebases | Memory issues | Limit tree size, stream generation |

## Success Metrics

1. **Token Efficiency**: Call tree section uses <5% of total prompt tokens
2. **Coverage**: 95%+ of functions get meaningful call tree context
3. **Performance**: <100ms additional time per function analysis
4. **Quality**: LLM produces more contextually aware analysis results

## Conclusion

This enhancement is **feasible and valuable**. The existing infrastructure (CallTreeGenerator, CallGraph, merged_call_graph.json) provides all necessary data. The main implementation work is creating a focused section generator and integrating it with the prompt builder while respecting token budgets.

The tree format is compact and informative, giving the LLM the ability to understand:
- Where the function sits in the call hierarchy
- What functions it depends on (and their dependencies)
- What functions depend on it
- **Relative file paths** for cross-file understanding
- **Line numbers** that can be used with `getFileContentByLines` tool to inspect any function

**Key Integration Point**: The LLM already has access to the `getFileContentByLines` tool which accepts:
- `path`: Relative file path (exactly as shown in the call tree)
- `startLine`: Starting line number (1-based)
- `endLine`: Ending line number (1-based)

This means the call tree serves as a **navigation map** - the LLM can see the structure and then "drill down" into any function by using the tool with the exact path and line numbers shown in the tree.

This context will help the LLM identify issues that span multiple functions and understand the broader impact of any problems found.
