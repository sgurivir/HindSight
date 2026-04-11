# Investigation Plan: Filter LLM Analysis to include_directories Only

## Problem Statement

When `include_directories` is specified in the config JSON:
1. **AST Generation**: Correctly includes files from `include_directories` AND their dependencies (imported code from outside those directories) for context
2. **LLM Analysis**: Currently analyzes ALL functions in the call graph, including those from dependency files outside `include_directories`

**Desired Behavior**: Only analyze functions that are physically located inside `include_directories`. Functions from dependency files should be included in the AST for context but NOT sent to the LLM for analysis.

## Investigation Findings

### Current Architecture

#### 1. AST Generation Flow (`ast_util.py`)
- When `include_directories` is specified, `ASTUtil.run_full_analysis()` calls `_find_files_in_include_dirs()` to find initial files
- Then `ScopedASTUtil.find_file_dependencies()` discovers dependencies (imports, includes) up to `max_dependency_depth`
- The merged call graph (`merged_call_graph.json`) contains ALL functions: both from `include_directories` AND their dependencies
- This is **correct behavior** - we want the full context for analysis

#### 2. LLM Analysis Flow (`code_analyzer.py`)
The function selection for LLM analysis happens in `CodeAnalysisRunner._run_code_analysis()` at lines 1033-1054:

```python
for file_entry in self.call_graph_data['call_graph']:
    functions = file_entry.get('functions', [])
    for func_entry in functions:
        # Apply filtering logic
        if self._should_analyze_function(func_entry, config):
            filtered_functions.append(...)
```

#### 3. Current Filtering Logic (`_should_analyze_function()` at line 410)
The filtering precedence is:
1. `verified_functions` (from `--function-filter`)
2. `file_filter` (from `--file-filter`)
3. Function length requirements (min/max)
4. **Directory filters** via `_should_analyze_function_by_directory_filters()`

#### 4. Directory Filter Implementation (`_should_analyze_function_by_directory_filters()` at line 525)
Currently uses `FilteredFileFinder.should_analyze_by_directory_filters()` which:
- Checks `exclude_files` first
- Checks `include_directories` - if provided, only analyze files in these directories
- Checks `exclude_directories`

**Key Finding**: The `include_directories` check IS already implemented in `FilteredFileFinder.should_analyze_by_directory_filters()` (lines 169-189 in `filtered_file_finder.py`).

### The Issue

Looking at the code flow:
1. In `_should_analyze_function_by_directory_filters()`, the `include_directories` is retrieved from config at line 547
2. The `FilteredFileFinder.should_analyze_by_directory_filters()` method correctly checks if a file is within `include_directories`

**However**, there's a potential issue: The `include_directories` might be empty or None when it reaches the filtering logic, even if it was specified in the original config.

Let me trace the config flow:
- Config is loaded in `run()` method
- `include_directories` is set from command line or config at lines 2037-2039
- Config is passed to `_run_code_analysis()` 
- In `_should_analyze_function_by_directory_filters()`, it retrieves `include_directories` from config at line 547

## Root Cause Analysis

After careful analysis, the filtering logic **should already work** based on the existing code. The issue might be:

1. **Config not being passed correctly**: The `include_directories` might not be in the config dict when `_should_analyze_function_by_directory_filters()` is called
2. **Empty vs None handling**: The check `if include_directories:` at line 170 in `filtered_file_finder.py` treats empty list `[]` the same as not specified

## Proposed Solution

### Option 1: Verify and Fix Config Propagation (Recommended)

Add explicit logging and verification to ensure `include_directories` is properly propagated:

**File: `hindsight/analyzers/code_analyzer.py`**

1. In `_should_analyze_function_by_directory_filters()` (around line 547), add debug logging:
```python
def _should_analyze_function_by_directory_filters(self, json_data: Dict[str, Any], config: dict) -> bool:
    # Extract file path from the JSON data
    file_path = self._extract_file_path_from_json(json_data)
    if not file_path:
        self.logger.debug(f"Could not extract file path from JSON data, including by default")
        return True

    # Normalize file path
    normalized_file_path = file_path.lstrip('./')

    # Get filtering parameters from config
    include_directories = config.get('include_directories', [])
    exclude_directories = config.get('exclude_directories', [])
    exclude_files = config.get('exclude_files', [])
    
    # ADD: Debug logging to verify include_directories is being used
    if include_directories:
        self.logger.debug(f"Filtering with include_directories: {include_directories}")
    
    # ... rest of method
```

2. In `_run_code_analysis()` (around line 1020), verify config contains include_directories:
```python
def _run_code_analysis(self, config: dict, output_base_dir: str, api_key: str = None) -> tuple:
    # ADD: Log include_directories status
    include_directories = config.get('include_directories', [])
    if include_directories:
        self.logger.info(f"LLM analysis will be limited to functions in include_directories: {include_directories}")
    else:
        self.logger.info("No include_directories specified - all functions will be analyzed")
    
    # ... rest of method
```

### Option 2: Add Explicit include_directories Check (If Option 1 doesn't work)

If the filtering isn't working as expected, add an explicit check in the pre-filtering loop:

**File: `hindsight/analyzers/code_analyzer.py`**

In `_run_code_analysis()` around line 1033, add explicit include_directories check:

```python
# Pre-filter and sort all functions before processing
self.logger.info("Pre-filtering and sorting functions by length...")
filtered_functions = []

# Get include_directories for explicit filtering
include_directories = config.get('include_directories', [])
if include_directories:
    self.logger.info(f"Will only analyze functions in directories: {include_directories}")

for file_entry in self.call_graph_data['call_graph']:
    functions = file_entry.get('functions', [])
    file_path = file_entry.get('file', '')
    
    # ADD: Early check for include_directories
    if include_directories:
        # Check if this file is within include_directories
        normalized_file_path = file_path.lstrip('./')
        file_in_include_dir = False
        for include_dir in include_directories:
            normalized_include_dir = include_dir.lstrip('./')
            if normalized_file_path.startswith(normalized_include_dir + '/') or \
               normalized_file_path == normalized_include_dir or \
               include_dir in normalized_file_path.split('/'):
                file_in_include_dir = True
                break
        
        if not file_in_include_dir:
            self.logger.debug(f"Skipping file {file_path} - not in include_directories")
            continue  # Skip all functions in this file

    for func_entry in functions:
        # ... existing filtering logic
```

## Implementation Steps

1. **Step 1: Add Diagnostic Logging**
   - Add logging in `_should_analyze_function_by_directory_filters()` to verify `include_directories` is being used
   - Add logging in `_run_code_analysis()` to show include_directories status at start

2. **Step 2: Test Current Behavior**
   - Run analysis with `include_directories` specified
   - Check logs to see if filtering is being applied
   - Verify which functions are being analyzed

3. **Step 3: Fix if Needed**
   - If `include_directories` is not being passed correctly, trace the config flow
   - If filtering logic is incorrect, implement Option 2

4. **Step 4: Add Unit Tests**
   - Test that functions outside `include_directories` are skipped
   - Test that functions inside `include_directories` are analyzed
   - Test that dependency files (outside `include_directories`) are NOT analyzed

## Files to Modify

1. **`hindsight/analyzers/code_analyzer.py`**
   - `_should_analyze_function_by_directory_filters()` - Add logging
   - `_run_code_analysis()` - Add include_directories status logging and potentially explicit filtering

2. **`hindsight/utils/filtered_file_finder.py`** (if needed)
   - `should_analyze_by_directory_filters()` - Verify logic handles all edge cases

## Testing Strategy

1. Create a test repository with:
   - `src/` directory with main code
   - `lib/` directory with dependency code
   - Files in `src/` that import from `lib/`

2. Run analysis with `include_directories: ["src"]`

3. Verify:
   - AST contains functions from both `src/` and `lib/`
   - LLM analysis only processes functions from `src/`
   - Functions from `lib/` are available as context but not analyzed

## Expected Outcome

After implementation:
- When `include_directories` is empty or None: All functions are analyzed (current behavior)
- When `include_directories` is specified: Only functions physically located in those directories are sent to LLM
- Dependency code is still included in AST for context but not analyzed
- This reduces LLM API calls and focuses analysis on the code the user cares about
