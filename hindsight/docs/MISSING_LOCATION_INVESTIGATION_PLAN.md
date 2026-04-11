# Investigation Plan: Missing Location Information in Call Tree

## Problem Statement

Many functions in the generated call tree (`call_tree.txt`) are missing their implementation file name and line numbers. For example:

```
│   ├── -[CLIndoorPrefetchRegion initFromLOI:]
│   ├── -[CLIndoorPrefetchRegion mergeLocationOfInterest:clusterRadius:]
│   ├── -[CLIndoorPrefetchRegion relevancy:]
```

These should display location information like:
```
│   ├── -[CLIndoorPrefetchRegion initFromLOI:]  {SomeFile.mm:100-150}
```

## Initial Observations

### 1. Source AST Data Structure

From examining `/Users/sgurivireddy/llm_artifacts/corelocation/code_insights/merged_call_graph.json`:

**Functions with location info (top-level function definitions):**
```json
{
  "file": "Daemon/Core/CSI/CLThreadSupport.h",
  "functions": [
    {
      "checksum": "9e8e7376bd403cc10979dee212e6d9ec",
      "context": {
        "end": 276,
        "file": "Daemon/Core/CSI/CLThreadSupport.h",
        "start": 272
      },
      "function": "AutoLocker::~AutoLocker()",
      "functions_invoked": [
        "clv::util::SpinLock::unlock()"   // <-- STRING ONLY, no context!
      ]
    }
  ]
}
```

**Key Finding:** The `functions_invoked` array contains **only function name strings**, not objects with context/location information.

### 2. Functions Missing Location

Functions like `-[CLIndoorPrefetchRegion initFromLOI:]`:
- Appear **only** in `functions_invoked` arrays as strings
- Are **never** defined as top-level function entries with their own `context` object
- Therefore have no location information available in the source data

### 3. Statistics from Log

```
Loaded 5587 nodes and 7381 edges
Generated call tree with 2628 root nodes
```

This suggests many functions are referenced but not defined in the call graph.

## Root Cause Hypotheses

### Hypothesis A: AST Generation Issue (Source Problem)

The AST parser (`ast_util.py` or related) may not be extracting all function definitions:

1. **Objective-C method definitions not captured**: Methods like `-[CLIndoorPrefetchRegion initFromLOI:]` may be defined in `.m` or `.mm` files but not being parsed correctly
2. **Header vs Implementation mismatch**: Functions declared in headers but implemented elsewhere may not have their implementations captured
3. **File exclusion**: The implementation files may be in excluded directories
4. **Language-specific parsing gaps**: Certain Objective-C/C++ constructs may not be handled

### Hypothesis B: Call Graph Merging Issue

The call graph merging process may be losing location information:

1. **Deduplication logic**: When merging call graphs from multiple files, location info may be dropped
2. **String-only invocations**: The `functions_invoked` field stores only strings, losing any context that might have been available

### Hypothesis C: Call Tree Generation Issue (Processing Problem)

The `call_tree_util.py` implementation extraction may have gaps:

1. **`extract_implementations()` function**: May not be finding all function definitions
2. **Lookup by function name**: Functions may have slightly different names in definition vs invocation

## Investigation Steps

### Step 1: Verify Source Data Completeness

**Goal:** Determine if missing functions exist as top-level definitions in the AST

```bash
# Count total top-level function definitions
grep -c '"function":' merged_call_graph.json

# Search for specific missing function
grep -i "CLIndoorPrefetchRegion" merged_call_graph.json

# Check if it exists as a top-level function definition
grep -B 5 '"function": ".*CLIndoorPrefetchRegion.*"' merged_call_graph.json
```

**Expected Outcome:** If the function is not found as a top-level definition, the problem is in AST generation.

### Step 2: Trace AST Generation for Missing Functions

**Goal:** Understand why certain functions are not being captured

1. **Find the source file:**
   ```bash
   # In the repository, find where CLIndoorPrefetchRegion is defined
   grep -r "CLIndoorPrefetchRegion" ~/src/corelocation/ --include="*.m" --include="*.mm" --include="*.h"
   ```

2. **Check if file is excluded:**
   - Review the exclude_directories list in the config
   - Verify the file path is not in an excluded directory

3. **Check AST parsing:**
   - Review `ast_util.py` for Objective-C method parsing
   - Check if `@implementation` blocks are being parsed correctly

### Step 3: Analyze Call Graph Structure

**Goal:** Understand the relationship between callers and callees

1. **Examine a function that HAS location:**
   ```bash
   grep -A 30 '"function": "CLIndoorLogic::loadAvailabilityTiles' merged_call_graph.json
   ```

2. **Compare with a function that LACKS location:**
   - Trace where it's referenced
   - Check if it appears anywhere as a top-level definition

### Step 4: Review Implementation Extraction Logic

**Goal:** Verify `extract_implementations()` in `call_tree_util.py` is working correctly

1. **Add debug logging:**
   - Log all function names found as top-level definitions
   - Log all function names found in `functions_invoked`
   - Compare the two sets

2. **Check for name mismatches:**
   - Function names in definitions vs invocations may differ slightly
   - Objective-C method signatures may have variations

### Step 5: Quantify the Problem

**Goal:** Understand the scope of missing locations

```python
# Script to analyze call_tree.json
import json

with open('call_tree.json') as f:
    data = json.load(f)

def count_locations(node, with_loc=0, without_loc=0):
    if node.get('location'):
        with_loc += 1
    else:
        without_loc += 1
    for child in node.get('children', []):
        with_loc, without_loc = count_locations(child, with_loc, without_loc)
    return with_loc, without_loc

with_loc, without_loc = count_locations(data['call_tree'])
print(f"Functions with location: {with_loc}")
print(f"Functions without location: {without_loc}")
print(f"Percentage missing: {without_loc / (with_loc + without_loc) * 100:.1f}%")
```

## Files to Investigate

| File | Purpose | Investigation Focus |
|------|---------|---------------------|
| `hindsight/core/lang_util/ast_util.py` | AST parsing | Objective-C method extraction |
| `hindsight/core/lang_util/ast_util_language_helper.py` | Language-specific parsing | ObjC/C++ handling |
| `hindsight/core/lang_util/call_graph_util.py` | Call graph building | How `functions_invoked` is populated |
| `hindsight/core/lang_util/call_tree_util.py` | Call tree generation | `extract_implementations()` function |
| `hindsight/analyzers/code_analyzer.py` | Main analyzer | Call graph merging logic |

## Potential Solutions (To Be Validated)

### Solution A: Enhance AST Parsing

If the problem is in AST generation:
- Improve Objective-C method parsing in `ast_util.py`
- Ensure `@implementation` blocks are fully parsed
- Handle category methods and class extensions

### Solution B: Enrich `functions_invoked` Data

If the problem is in call graph structure:
- Change `functions_invoked` from string array to object array
- Include context/location for each invoked function when available
- This would require changes to AST generation

### Solution C: Cross-Reference Lookup

If the problem is in call tree generation:
- Build a comprehensive function-to-location index from all sources
- Look up locations for functions that appear only in `functions_invoked`
- Use fuzzy matching for function name variations

### Solution D: Post-Processing Enhancement

Add a post-processing step:
- After call tree generation, scan for functions without locations
- Search the original source files for their definitions
- Populate missing location information

## Investigation Results

### Root Cause Identified

After examining the codebase, the root cause has been identified:

**The issue is in `cast_util.py` at line 3262 in `build_nested_call_graph()`:**

```python
# Just add the function name, not the full node with context
functions_invoked.append(display_name(decorated))  # <-- STRING ONLY!
```

The `functions_invoked` array is **intentionally** populated with just function name strings, not objects with context/location information. This is a design decision, not a bug.

### Why Functions Are Missing Location

Functions appear without location information when:

1. **Function is invoked but not defined in the repository** - The function exists in `functions_invoked` as a string, but has no entry in `definitions_map` because:
   - The implementation file is in an excluded directory
   - It's a system/framework function (e.g., Objective-C runtime methods)
   - The file wasn't parsed due to compilation errors
   - The function is declared in a header but implemented elsewhere

2. **The validation check at line 3257-3258:**
   ```python
   # Validate that the function exists in the definitions_map before adding
   if b not in definitions_map:
       continue
   ```
   This check **skips** functions that don't have definitions, but they still appear in the call graph edges.

3. **In `call_tree_util.py`, `extract_implementations()` can only extract locations from:**
   - Top-level function definitions (lines 48-75)
   - Nested `functions_invoked` entries that are **dict objects** with context (lines 83-113)
   - String-only invocations are completely skipped (line 84: `if isinstance(invoked, dict)`)

### Data Flow Analysis

```
AST Parsing (cast_util.py)
    ↓
build_function_registry() → definitions_map (function → location)
    ↓
build_forward_call_graph() → adjacency (caller → [callees])
    ↓
build_nested_call_graph() → JSON with functions_invoked as STRINGS
    ↓
extract_implementations() → Can only get locations from definitions_map
    ↓
Call Tree → Functions in functions_invoked without definitions have NO location
```

## Proposed Solution

### Option A: Enrich `functions_invoked` with Context (Recommended)

Modify `build_nested_call_graph()` to include context information for invoked functions when available:

```python
# In cast_util.py, around line 3250-3266
functions_invoked = []
for decorated in sorted(unique_children.values()):
    b = base_function_name(decorated)
    if b in seen:
        continue
    
    seen.add(b)
    
    # Get context for the invoked function if available
    invoked_context = context_for(b)
    if invoked_context:
        # Include context when available
        functions_invoked.append({
            "function": display_name(decorated),
            "context": invoked_context
        })
    else:
        # Fall back to string-only for functions without definitions
        functions_invoked.append(display_name(decorated))
    
    seen.remove(b)
```

**Pros:**
- Preserves backward compatibility (strings still work)
- Provides location info when available
- No changes needed to `extract_implementations()` (already handles both formats)

**Cons:**
- Increases JSON file size
- Mixed format (strings and objects) in same array

### Option B: Build Comprehensive Function Index

Create a separate lookup step that builds a complete function-to-location index from all sources before generating the call tree:

```python
# In call_tree_util.py
def build_function_location_index(data: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Build a comprehensive index of all function locations."""
    index = extract_implementations(data)
    
    # Also index functions from functions_invoked that have context
    # (This is already done in extract_implementations)
    
    return index
```

**Pros:**
- Clean separation of concerns
- Single source of truth for locations

**Cons:**
- Doesn't help if the function truly has no definition in the data

### Option C: Post-Processing Source Scan

Add a post-processing step that scans source files for missing function definitions:

```python
def find_missing_function_locations(
    call_tree: Dict,
    repo_root: Path,
    source_files: List[Path]
) -> Dict[str, List[Dict[str, Any]]]:
    """Scan source files to find locations for functions missing from call graph."""
    # Use tree-sitter or regex to find function definitions
    # that weren't captured by the AST parser
    pass
```

**Pros:**
- Can find functions that AST parser missed
- Works for any language

**Cons:**
- Expensive (requires re-scanning files)
- May find false positives

## Recommended Implementation

**Implement Option A** as it:
1. Requires minimal code changes
2. Preserves backward compatibility
3. Leverages existing infrastructure in `extract_implementations()`
4. Provides immediate benefit for functions that have definitions

## Implementation Status: ✅ COMPLETED

### Changes Made

**File: `hindsight/core/lang_util/cast_util.py`**

Modified the `build_nested_call_graph()` function (lines 3249-3272) to include context information for invoked functions when available:

```python
functions_invoked = []
for decorated in sorted(unique_children.values()):
    b = base_function_name(decorated)
    if b in seen:
        continue
    
    seen.add(b)
    
    # Get context for the invoked function if available
    invoked_context = context_for(b)
    
    # Always use dict format for consistency
    # Context is included when available (function has implementation in repo)
    # Context is omitted when not available (system functions, excluded files)
    invoked_entry = {
        "function": display_name(decorated)
    }
    if invoked_context:
        invoked_entry["context"] = invoked_context
    
    functions_invoked.append(invoked_entry)
    
    seen.remove(b)
```

### Key Changes

1. **Removed the validation check** that was skipping functions not in `definitions_map` - this was preventing valid functions from being added to `functions_invoked`

2. **Added context lookup** for each invoked function using the existing `context_for()` helper function

3. **Consistent dict format** - `functions_invoked` now always contains dict objects with:
   - `function`: The function name (always present)
   - `context`: Location information with `file`, `start`, `end` (only present when implementation exists in repo)

### Data Format

All entries in `functions_invoked` are now dict objects:

**With context (function has implementation in repo):**
```json
{
  "function": "SomeClass::someMethod()",
  "context": {
    "file": "path/to/file.cpp",
    "start": 100,
    "end": 150
  }
}
```

**Without context (system function, excluded file, etc.):**
```json
{
  "function": "-[NSObject init]"
}
```

### Backward Compatibility

The fix maintains backward compatibility because:
- `extract_implementations()` in `call_tree_util.py` already handles dict format (lines 83-113)
- `load_call_graph_from_json()` in `call_graph_util.py` already handles dict format
- The consistent dict format simplifies consumer code - no need to check `isinstance(item, dict)`

### Expected Results

After this fix:
- All functions in `functions_invoked` use consistent dict format
- Functions that have definitions in the repository will include `context` with location information
- Functions without definitions (system functions, excluded files) will have dict with only `function` key (no `context`)
- The call tree generator will be able to extract locations for more functions

### Testing

To verify the fix:
1. Regenerate a call graph for a repository
2. Check that all `functions_invoked` entries are dict objects
3. Verify entries with implementations have `context`, entries without don't
4. Generate a call tree and verify more functions have location information

## Appendix: Sample Data

### Function WITH location (from call_tree.json):
```json
{
  "function": "CLIndoorLogic::loadAvailabilityTiles(std,std)",
  "location": [
    {
      "file_path": "Daemon/Positioning/Indoor/CLIndoorStateMachine.mm",
      "start_line": 1487,
      "end_line": 1501
    }
  ],
  "children": [...]
}
```

### Function WITHOUT location (from call_tree.json):
```json
{
  "function": "-[CLIndoorPrefetchRegion initFromLOI:]",
  "location": [],
  "children": []
}
```

### Source AST showing string-only invocations:
```json
{
  "function": "SomeFunction()",
  "functions_invoked": [
    "-[CLIndoorPrefetchRegion initFromLOI:]",  // String only!
    "-[CLIndoorPrefetchRegion mergeLocationOfInterest:clusterRadius:]"
  ]
}
```
