# Constrained Call Tree Context in LLM Prompts - Implementation Plan

## Executive Summary

This document outlines the plan to enhance the LLM prompt with a **constrained call tree context** that provides exactly:
- **1 level above** (callers) the function being analyzed
- **3 levels below** (callees) the function being analyzed

The call tree will be rendered as **indented tree syntax text** (not JSON) and will appear **after the code blocks** in the prompt. Each frame in the call tree will include the **file name** and **line range** to enable the LLM to use the `getFileContentByLines` tool for deeper inspection when needed.

## Current State Analysis

### Existing Implementation

Based on analysis of the codebase, we have the following components:

1. **[`CallTreeSectionGenerator`](../core/lang_util/call_tree_section_generator.py)**: A fully implemented class that generates focused call tree sections with configurable ancestor and descendant depths. It already outputs **indented tree syntax text**.

2. **[`prompt_builder.py`](../core/prompts/prompt_builder.py:586-601)**: Has code to integrate call tree context into prompts via the [`_convert_json_to_comment_format()`](../core/prompts/prompt_builder.py:286) method.

3. **[`constants.py`](../core/constants.py:87-92)**: Configuration constants for call tree context:
   ```python
   CALL_TREE_MAX_ANCESTOR_DEPTH = 2      # Currently 2 levels of callers (needs to be 1)
   CALL_TREE_MAX_DESCENDANT_DEPTH = 3    # 3 levels of callees
   CALL_TREE_MAX_CHILDREN_PER_NODE = 5   # Max children per node
   CALL_TREE_MAX_TOKENS = 2000           # Max tokens for call tree section
   ```

4. **[`merged_call_graph.json`]**: Generated during AST analysis, contains call graph data with function relationships.

### Critical Finding: Call Tree Section NOT Being Generated

**Analysis of actual prompts sent** (from `/Users/sgurivireddy/llm_artifacts/almanacapps/prompts_sent/conversation_5.md`) reveals that the call tree section is **NOT appearing in the prompts**, despite the code being present.

#### Root Cause Analysis

The call tree generation code exists at [`prompt_builder.py:586-601`](../core/prompts/prompt_builder.py:586):

```python
# Add call tree context section if call graph data is available
if merged_call_graph_data and function_name != 'Unknown':
    call_tree_section = generate_call_tree_section_for_function(
        call_graph_data=merged_call_graph_data,
        function_name=function_name,
        max_ancestor_depth=CALL_TREE_MAX_ANCESTOR_DEPTH,
        max_descendant_depth=CALL_TREE_MAX_DESCENDANT_DEPTH,
        max_children_per_node=CALL_TREE_MAX_CHILDREN_PER_NODE,
        max_tokens=CALL_TREE_MAX_TOKENS
    )
```

**Potential Issues to Investigate:**

1. **`merged_call_graph_data` may be `None`**: The call graph data might not be loaded or passed correctly to the prompt builder.

2. **Function name mismatch**: The function name in the JSON (e.g., `AppDelegate::application:didFinishLaunchingWithOptions:`) may not match the function names in the call graph.

3. **Silent failure in `generate_call_tree_section_for_function`**: The function may be returning `None` due to:
   - Function not found in call graph
   - Empty ancestors/descendants
   - Token budget exceeded

## Requirements

### Functional Requirements

1. **Tree Depth Constraints**:
   - **1 level above**: Show only direct callers of the function
   - **3 levels below**: Show callees up to 3 levels deep

2. **Output Format**: Indented tree syntax text (NOT JSON), for example:
   ```
   // === CALL TREE CONTEXT ===
   // This shows the function's position in the call hierarchy
   // Use getFileContentByLines tool to read code at any location shown below
   //
   // CALLERS (who calls this function):
   //     └── process_data()  {src/processor/processor.c:100-200}
   //         └── [TARGET] analyze_item()
   //
   // CALLEES (what this function calls):
   // [TARGET] analyze_item()  {src/analyzer/analyzer.c:50-80}
   // ├── validate_input()  {src/utils/validator.c:20-40}
   // │   └── check_bounds()  {src/utils/validator.c:60-70}
   // │       └── validate_range()  {src/utils/bounds.c:10-25}
   // ├── transform_data()  {src/transform/transformer.c:100-150}
   // │   ├── apply_filter()  {src/transform/filter.c:30-50}
   // │   │   └── filter_impl()  {src/transform/filter_impl.c:5-20}
   // │   └── normalize()  {src/transform/normalizer.c:10-25}
   // │       └── scale_values()  {src/transform/scale.c:15-30}
   // └── store_result()  {src/storage/storage.c:200-250}
   //     └── write_to_disk()  {src/io/disk_writer.c:100-120}
   //         └── flush_buffer()  {src/io/buffer.c:50-65}
   ```

3. **Placement**: The call tree section should appear **after the code blocks** in the prompt, specifically:
   - After main function code
   - After invoked functions code
   - After caller functions code
   - After data types used
   - After constants used
   - **Then the call tree context section**

4. **Frame Information**: Each frame must include:
   - Function name
   - File path (relative to repo root)
   - Line range (start-end)

## Implementation Plan

### Phase 1: Fix Call Tree Generation (Priority: HIGH)

#### Step 1.1: Add Debug Logging

Add logging to understand why call tree sections are not being generated.

**File**: [`hindsight/core/prompts/prompt_builder.py`](../core/prompts/prompt_builder.py)

```python
# At line 586, add debug logging:
logger.info(f"Call tree generation: "
            f"merged_call_graph_data={'present' if merged_call_graph_data else 'None'}, "
            f"function_name={function_name}")

if merged_call_graph_data and function_name != 'Unknown':
    try:
        call_tree_section = generate_call_tree_section_for_function(
            call_graph_data=merged_call_graph_data,
            function_name=function_name,
            max_ancestor_depth=CALL_TREE_MAX_ANCESTOR_DEPTH,
            max_descendant_depth=CALL_TREE_MAX_DESCENDANT_DEPTH,
            max_children_per_node=CALL_TREE_MAX_CHILDREN_PER_NODE,
            max_tokens=CALL_TREE_MAX_TOKENS
        )
        if call_tree_section:
            result.append("")
            result.append(call_tree_section)
            result.append("")
            logger.info(f"Added call tree context section for function: {function_name}")
        else:
            logger.warning(f"No call tree context generated for function: {function_name}")
    except Exception as e:
        logger.error(f"Error generating call tree section for {function_name}: {e}")
else:
    logger.debug(f"Skipping call tree generation: "
                 f"has_data={merged_call_graph_data is not None}, "
                 f"func_name={function_name}")
```

#### Step 1.2: Verify Call Graph Data Flow

Trace the data flow to ensure `merged_call_graph_data` is being passed correctly:

1. **[`code_analysis.py`](../core/llm/code_analysis.py:292-294)**: Verify `self.ast_index.merged_call_graph` is populated
2. **[`ast_index.py`](../core/ast_index.py:140-149)**: Verify `merged_call_graph.json` is being loaded

#### Step 1.3: Fix Function Name Matching

The [`CallTreeSectionGenerator`](../core/lang_util/call_tree_section_generator.py:91) checks if the function exists in the graph:

```python
if function_name not in self.graph.nodes:
    logger.warning(f"Function '{function_name}' not found in call graph")
    return {...}
```

**Potential Fix**: Add function name normalization to handle variations like:
- `AppDelegate::application:didFinishLaunchingWithOptions:` vs `AppDelegate::application(_:didFinishLaunchingWithOptions:)`

### Phase 2: Update Configuration (Priority: MEDIUM)

#### Step 2.1: Change Ancestor Depth

**File**: [`hindsight/core/constants.py`](../core/constants.py:88)

```python
# Change from:
CALL_TREE_MAX_ANCESTOR_DEPTH = 2      # Levels of callers to show

# To:
CALL_TREE_MAX_ANCESTOR_DEPTH = 1      # 1 level of callers (direct callers only)
```

### Phase 3: Verify Output Format (Priority: MEDIUM)

The existing [`CallTreeSectionGenerator.format_as_text()`](../core/lang_util/call_tree_section_generator.py:222-276) already produces the correct indented tree syntax format. Verify it matches the expected output:

```
// === CALL TREE CONTEXT ===
// This shows the function's position in the call hierarchy
// Use getFileContentByLines tool to read code at any location shown below
//
// CALLERS (who calls this function):
//     └── process_data()  {src/processor/processor.c:100-200}
//         └── [TARGET] analyze_item()
//
// CALLEES (what this function calls):
// [TARGET] analyze_item()  {src/analyzer/analyzer.c:50-80}
// ├── validate_input()  {src/utils/validator.c:20-40}
// │   └── check_bounds()  {src/utils/validator.c:60-70}
```

### Phase 4: Testing and Validation

#### Step 4.1: Unit Tests

Update tests in [`test_call_tree_section_generator.py`](../core/lang_util/tests/test_call_tree_section_generator.py) to verify:
- 1 ancestor level is generated
- 3 descendant levels are generated
- Output format is correct indented tree syntax

#### Step 4.2: Integration Test

Run analysis on a sample repository and verify:
1. Call tree section appears in prompts (check `prompts_sent/` directory)
2. Section appears after code blocks
3. File names and line ranges are present
4. Tree structure is correct (1 up, 3 down)

## Implementation Steps Summary

| Step | Description | File(s) | Priority |
|------|-------------|---------|----------|
| 1.1 | Add debug logging | `prompt_builder.py` | HIGH |
| 1.2 | Verify call graph data flow | `code_analysis.py`, `ast_index.py` | HIGH |
| 1.3 | Fix function name matching | `call_tree_section_generator.py` | HIGH |
| 2.1 | Change ancestor depth to 1 | `constants.py` | MEDIUM |
| 3.1 | Verify output format | `call_tree_section_generator.py` | MEDIUM |
| 4.1 | Update unit tests | `test_call_tree_section_generator.py` | LOW |
| 4.2 | Integration test | N/A | LOW |

## Expected Output Example

For a function `analyze_item()` with the constrained settings (1 up, 3 down):

```
// === CALL TREE CONTEXT ===
// This shows the function's position in the call hierarchy
// Use getFileContentByLines tool to read code at any location shown below
//
// CALLERS (who calls this function):
//     └── process_data()  {src/processor/processor.c:100-200}
//         └── [TARGET] analyze_item()
//
// CALLEES (what this function calls):
// [TARGET] analyze_item()  {src/analyzer/analyzer.c:50-80}
// ├── validate_input()  {src/utils/validator.c:20-40}
// │   └── check_bounds()  {src/utils/validator.c:60-70}
// │       └── validate_range()  {src/utils/bounds.c:10-25}
// ├── transform_data()  {src/transform/transformer.c:100-150}
// │   ├── apply_filter()  {src/transform/filter.c:30-50}
// │   │   └── filter_impl()  {src/transform/filter_impl.c:5-20}
// │   └── normalize()  {src/transform/normalizer.c:10-25}
// │       └── scale_values()  {src/transform/scale.c:15-30}
// └── store_result()  {src/storage/storage.c:200-250}
//     └── write_to_disk()  {src/io/disk_writer.c:100-120}
//         └── flush_buffer()  {src/io/buffer.c:50-65}
```

**Key Features**:
- **1 level above**: Only `process_data()` is shown as the direct caller
- **3 levels below**: 
  - Level 1: `validate_input()`, `transform_data()`, `store_result()`
  - Level 2: `check_bounds()`, `apply_filter()`, `normalize()`, `write_to_disk()`
  - Level 3: `validate_range()`, `filter_impl()`, `scale_values()`, `flush_buffer()`
- **File name and line range**: Each frame shows `{relative/path/file.c:start-end}`
- **Indented tree syntax**: Uses `├──`, `└──`, `│` for visual hierarchy

## LLM Tool Integration

The LLM can use the `getFileContentByLines` tool to inspect any function in the tree:

```json
{
  "tool": "getFileContentByLines",
  "path": "src/utils/validator.c",
  "startLine": 20,
  "endLine": 40,
  "reason": "Inspecting validate_input() to understand input validation logic"
}
```

This enables the LLM to:
1. See the call hierarchy at a glance
2. Identify functions of interest
3. Drill down into specific functions using the provided file paths and line numbers

## Token Budget Considerations

With the constrained settings:
- **1 ancestor level**: ~25-50 tokens (1 caller + target)
- **3 descendant levels**: ~200-500 tokens (depending on branching factor)
- **Headers and formatting**: ~50 tokens
- **Total estimated**: ~300-600 tokens (well within the 2000 token budget)

The existing token budget management in [`generate_call_tree_section_for_function()`](../core/lang_util/call_tree_section_generator.py:446-502) will automatically reduce depth if the section exceeds the budget.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Call graph data not loaded | No call tree generated | Add logging, verify data flow |
| Function name mismatch | Function not found in graph | Add name normalization |
| Token budget exceeded | Call tree section skipped | Existing dynamic depth reduction handles this |
| Missing file paths | LLM cannot use getFileContentByLines | Already handled - shows function name only if path unavailable |

## Conclusion

The call tree context feature requires **debugging and fixing** before it can work as intended. The main issues are:

1. **Call tree sections are not appearing in prompts** - needs investigation
2. **Configuration change needed** - reduce ancestor depth from 2 to 1

Once fixed, the feature will provide:
- Indented tree syntax text (not JSON)
- Placed after code blocks in the prompt
- 1 level of callers, 3 levels of callees
- File names and line ranges for each frame
- Integration with `getFileContentByLines` tool for deeper inspection
