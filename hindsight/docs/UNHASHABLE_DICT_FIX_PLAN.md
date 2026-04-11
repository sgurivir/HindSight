# Fix Plan: Unhashable Dict Error in Call Graph Checksum Generation

## Issue Summary

**Error Message:**
```
WARNING - ast_function_signature_util.py:614 - Cannot compute call graph function checksum for StepTestAnalyticsTests::biologicalSex_mapping(): unhashable type: 'dict'
```

**Affected Functions:**
- `StepTestAnalyticsTests::biologicalSex_mapping()`
- `StepTestAnalyticsTests::computations_bmiCalculation_bothInvalid()`
- `StepTestAnalyticsTests::computations_bmiCalculation_invalidHeight()`
- `StepTestAnalyticsTests::computations_bmiCalculation_invalidWeight()`

## Root Cause Analysis

### Location
The error occurs in [`create_call_graph_function_checksum()`](../core/lang_util/ast_function_signature_util.py:483) at line 614.

### Problem
The `sorted()` function is being called on lists (`data_types_used` and `functions_invoked`) that may contain dictionary objects instead of strings:

1. **Line 526**: `sorted_data_types = sorted(data_types_used)`
2. **Line 558**: `sorted_functions = sorted(functions_invoked)`

When Swift test functions are processed, the `data_types_used` or `functions_invoked` fields contain dictionaries (e.g., `{"name": "SomeType", "module": "SomeModule"}`) instead of simple strings. Python's `sorted()` function cannot compare dictionaries because they are unhashable, causing the error.

### Code Flow
```
add_checksums_to_call_graph() [line 620]
    └── create_call_graph_function_checksum() [line 674]
            └── sorted(data_types_used) [line 526] ← FAILS HERE
            └── sorted(functions_invoked) [line 558] ← OR HERE
```

## Solution Design

### Approach
Handle both string and dict entries in `data_types_used` and `functions_invoked` lists by:
1. Adding a helper method to normalize entries to strings for sorting
2. Updating the sorting logic to use a key function
3. Updating the checksum lookup logic to handle both formats

### Implementation Details

#### 1. Add Helper Method

Add a new static method after line 481 in `ast_function_signature_util.py`:

```python
@staticmethod
def _normalize_entry_to_string(entry: Any) -> str:
    """
    Convert an entry (string or dict) to a deterministic string for sorting and hashing.
    
    Args:
        entry: Either a string or a dictionary containing type/function information
        
    Returns:
        str: A deterministic string representation suitable for sorting
    """
    if isinstance(entry, str):
        return entry
    elif isinstance(entry, dict):
        # Use JSON serialization with sorted keys for deterministic output
        return json.dumps(entry, sort_keys=True)
    else:
        return str(entry)
```

#### 2. Update Data Types Sorting (Line 526)

**Before:**
```python
sorted_data_types = sorted(data_types_used)
```

**After:**
```python
sorted_data_types = sorted(data_types_used, key=ASTFunctionSignatureGenerator._normalize_entry_to_string)
```

#### 3. Update Functions Sorting (Line 558)

**Before:**
```python
sorted_functions = sorted(functions_invoked)
```

**After:**
```python
sorted_functions = sorted(functions_invoked, key=ASTFunctionSignatureGenerator._normalize_entry_to_string)
```

#### 4. Update Data Type Checksum Lookup (Lines 531-538)

**Before:**
```python
for data_type in sorted_data_types:
    # Get checksum from data_types_checksums, use "None" if not found
    data_type_checksum = data_types_checksums.get(data_type, None)
```

**After:**
```python
for data_type in sorted_data_types:
    # Extract the data type name/key for lookup
    if isinstance(data_type, str):
        data_type_key = data_type
    elif isinstance(data_type, dict):
        # Try common keys: "name", "type", or fall back to JSON representation
        data_type_key = data_type.get("name", data_type.get("type", json.dumps(data_type, sort_keys=True)))
    else:
        data_type_key = str(data_type)
    
    # Get checksum from data_types_checksums, use "None" if not found
    data_type_checksum = data_types_checksums.get(data_type_key, None)
```

#### 5. Update Function Checksum Lookup (Lines 563-588)

**Before:**
```python
for func_name in sorted_functions:
    # Try exact match first
    func_checksum = functions_checksums.get(func_name, None)
```

**After:**
```python
for func in sorted_functions:
    # Extract the function name for lookup
    if isinstance(func, str):
        func_name = func
    elif isinstance(func, dict):
        # Try common keys: "name", "function", or fall back to JSON representation
        func_name = func.get("name", func.get("function", json.dumps(func, sort_keys=True)))
    else:
        func_name = str(func)
    
    # Try exact match first
    func_checksum = functions_checksums.get(func_name, None)
```

## Caller Impact Analysis

### Direct Callers
The only direct caller of `create_call_graph_function_checksum()` is:
- [`add_checksums_to_call_graph()`](../core/lang_util/ast_function_signature_util.py:620) at line 674

### Indirect Callers
`add_checksums_to_call_graph()` is called by:
- [`process_call_graph_file()`](../core/lang_util/ast_function_signature_util.py:698) at line 761

### Impact Assessment
| Caller | Impact | Action Required |
|--------|--------|-----------------|
| `add_checksums_to_call_graph()` | None | No changes needed - interface unchanged |
| `process_call_graph_file()` | None | No changes needed - interface unchanged |

**Conclusion:** The fix is internal to `create_call_graph_function_checksum()` and does not change its interface. All callers will continue to work without modification.

## Files to Modify

| File | Changes |
|------|---------|
| [`hindsight/core/lang_util/ast_function_signature_util.py`](../core/lang_util/ast_function_signature_util.py) | Add helper method, update sorting and lookup logic |

## Testing Plan

1. **Unit Test**: Create test cases with both string and dict entries in `data_types_used` and `functions_invoked`
2. **Integration Test**: Run the checksum generation on Swift test files that previously caused the error
3. **Regression Test**: Verify existing functionality with string-only entries still works correctly

### Test Cases

```python
# Test case 1: String entries (existing behavior)
function_entry_strings = {
    "function": "TestClass::testMethod()",
    "context": {"file": "test.swift", "start": 10, "end": 20},
    "data_types_used": ["TypeA", "TypeB"],
    "functions_invoked": ["funcA", "funcB"]
}

# Test case 2: Dict entries (new behavior)
function_entry_dicts = {
    "function": "StepTestAnalyticsTests::biologicalSex_mapping()",
    "context": {"file": "test.swift", "start": 10, "end": 20},
    "data_types_used": [
        {"name": "BiologicalSex", "module": "HealthKit"},
        {"name": "String", "module": "Swift"}
    ],
    "functions_invoked": [
        {"name": "XCTAssertEqual", "module": "XCTest"},
        {"name": "biologicalSex", "module": "Analytics"}
    ]
}

# Test case 3: Mixed entries
function_entry_mixed = {
    "function": "MixedTest::testMethod()",
    "context": {"file": "test.swift", "start": 10, "end": 20},
    "data_types_used": ["TypeA", {"name": "TypeB", "module": "ModuleB"}],
    "functions_invoked": ["funcA", {"name": "funcB", "module": "ModuleB"}]
}
```

## Rollback Plan

If issues arise after deployment:
1. Revert the changes to `ast_function_signature_util.py`
2. The system will fall back to the existing exception handler at line 613-617, which logs a warning and returns a fallback checksum

## Timeline

| Phase | Duration | Description |
|-------|----------|-------------|
| Implementation | 1 hour | Apply code changes |
| Testing | 2 hours | Run unit and integration tests |
| Code Review | 1 hour | Review changes |
| Deployment | 30 min | Deploy to production |

## Appendix: Full Code Changes

### Complete Modified Method

```python
@staticmethod
def _normalize_entry_to_string(entry: Any) -> str:
    """
    Convert an entry (string or dict) to a deterministic string for sorting and hashing.
    
    Args:
        entry: Either a string or a dictionary containing type/function information
        
    Returns:
        str: A deterministic string representation suitable for sorting
    """
    if isinstance(entry, str):
        return entry
    elif isinstance(entry, dict):
        return json.dumps(entry, sort_keys=True)
    else:
        return str(entry)

@staticmethod
def _extract_name_from_entry(entry: Any, name_keys: List[str] = None) -> str:
    """
    Extract a name/identifier from an entry for checksum lookup.
    
    Args:
        entry: Either a string or a dictionary containing type/function information
        name_keys: List of keys to try when extracting name from dict (default: ["name", "type", "function"])
        
    Returns:
        str: The extracted name or a JSON representation
    """
    if name_keys is None:
        name_keys = ["name", "type", "function"]
    
    if isinstance(entry, str):
        return entry
    elif isinstance(entry, dict):
        for key in name_keys:
            if key in entry:
                return entry[key]
        return json.dumps(entry, sort_keys=True)
    else:
        return str(entry)
```
