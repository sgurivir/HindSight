# Function Block Checksum Implementation Plan

## Overview

This document outlines the plan to add a fast checksum generation feature to the AST generation logic. The checksum will be computed from the function code block only (ignoring invoked functions and line numbers), enabling efficient change detection for function content.

## Current Architecture Analysis

### Existing Checksum Logic

The current checksum implementation is located in:

1. **[`hash_util.py`](../utils/hash_util.py)** - Centralized hash utility class (`HashUtil`) providing various hash methods:
   - `hash_for_content_md5()` - MD5 hash for content-based hashing (used for AST functions)
   - `hash_for_dummy_checksum_md5()` - Fallback when content isn't available
   - Other specialized hash methods for different use cases

2. **[`ast_function_signature_util.py`](../core/lang_util/ast_function_signature_util.py)** - `ASTFunctionSignatureGenerator` class that:
   - Creates checksums for function entries from `merged_functions.json`
   - Creates checksums for data type entries from `merged_defined_classes.json`
   - Creates checksums for call graph entries (includes invoked functions and data types)

### Current Checksum Behavior

The current [`create_function_checksum()`](../core/lang_util/ast_function_signature_util.py:128) method:
- Reads the actual source code from files using line numbers (start/end)
- **Includes line numbers** in the checksum calculation (`line_info = f"start_line: {start_line} end_line: {end_line}"`)
- Combines content from multiple locations if a function has multiple definitions
- Uses MD5 hashing via `HashUtil.hash_for_content_md5()`

### Problem Statement

The current checksum:
1. **Includes line numbers** - Any line number change (e.g., adding a comment above the function) changes the checksum even if the function code is identical
2. **Call graph checksums include invoked functions** - The [`create_call_graph_function_checksum()`](../core/lang_util/ast_function_signature_util.py:483) method includes checksums of invoked functions and data types

## Proposed Solution

### New Checksum Type: Function Block Checksum

Add a new checksum field that captures **only the function body content**, ignoring:
- Line numbers (start/end positions)
- Invoked functions
- Data types used

### Implementation Approach

#### Option A: Add New Field (Recommended)
Add a new `block_checksum` field alongside the existing `checksum` field:
- `checksum` - Existing behavior (includes line numbers, useful for exact location tracking)
- `block_checksum` - New field (content-only, ignores line numbers)

**Pros:**
- Backward compatible
- Preserves existing functionality
- Clear separation of concerns

**Cons:**
- Slightly larger JSON output

#### Option B: Modify Existing Checksum
Change the existing `checksum` field to ignore line numbers.

**Pros:**
- Simpler implementation
- No schema changes

**Cons:**
- Breaking change for existing consumers
- Loses line-number-based change detection

## Detailed Implementation Plan

### Phase 1: Add New Hash Method to HashUtil

**File:** [`hindsight/utils/hash_util.py`](../utils/hash_util.py)

Add a new static method for function block checksums:

```python
@staticmethod
def hash_for_function_block_md5(content: str) -> str:
    """
    Generate MD5 hash for function block content only.
    Used for detecting changes in function body regardless of line position.
    
    This method normalizes the content by:
    - Stripping leading/trailing whitespace from each line
    - Removing empty lines
    - Joining with consistent newlines
    
    Args:
        content: Function body content
    
    Returns:
        str: MD5 hash as hexadecimal string
    """
    if not content:
        return "None"
    
    # Normalize content: strip whitespace, remove empty lines
    lines = content.split('\n')
    normalized_lines = [line.strip() for line in lines if line.strip()]
    normalized_content = '\n'.join(normalized_lines)
    
    return hashlib.md5(normalized_content.encode('utf-8')).hexdigest()
```

### Phase 2: Modify ASTFunctionSignatureGenerator

**File:** [`hindsight/core/lang_util/ast_function_signature_util.py`](../core/lang_util/ast_function_signature_util.py)

#### 2.1 Add New Method for Block Checksum

Add a new static method `create_function_block_checksum()`:

```python
@staticmethod
def create_function_block_checksum(repo_path: Path, function_entry: Dict[str, Any], function_name: str = None) -> str:
    """
    Create checksum for function block content only, ignoring line numbers.
    
    This checksum captures only the actual code content of the function,
    making it stable across line number changes (e.g., when code is added
    above the function).
    
    Args:
        repo_path: Path to repository root
        function_entry: Dictionary with locations or list of locations
        function_name: Optional function name for fallback
    
    Returns:
        MD5 hash of combined content (without line info) as hexadecimal string
    """
    try:
        # Handle both formats: list of locations or dict with locations
        if isinstance(function_entry, list):
            locations = function_entry
        elif isinstance(function_entry, dict):
            locations = function_entry.get("locations", function_entry.get("code", []))
        else:
            if function_name:
                return HashUtil.hash_for_dummy_checksum_md5(function_name)
            return "None"
        
        if not locations:
            if function_name:
                return HashUtil.hash_for_dummy_checksum_md5(function_name)
            return "None"
        
        # Sort locations for consistent ordering
        sorted_locations = sorted(locations, key=lambda x: (x.get("file_name", ""), x.get("start", 0)))
        
        combined_content = []
        
        for location in sorted_locations:
            file_name = location.get("file_name", "")
            start_line = location.get("start", 0)
            end_line = location.get("end", 0)
            
            if not file_name or start_line <= 0 or end_line <= 0 or start_line >= end_line:
                continue
            
            file_path = repo_path / file_name
            
            if not file_path.exists():
                continue
            
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                
                start_idx = max(0, start_line - 1)
                end_idx = min(len(lines) - 1, end_line - 1)
                
                if start_idx <= end_idx and start_idx < len(lines):
                    selected_lines = lines[start_idx:end_idx + 1]
                    content = ''.join(selected_lines)
                    # NO line info included - just the raw content
                    combined_content.append(content)
            except Exception:
                continue
        
        if not combined_content:
            if function_name:
                return HashUtil.hash_for_dummy_checksum_md5(function_name)
            return "None"
        
        full_content = ''.join(combined_content)
        return HashUtil.hash_for_function_block_md5(full_content)
    
    except Exception:
        if function_name:
            return HashUtil.hash_for_dummy_checksum_md5(function_name)
        return "None"
```

#### 2.2 Modify `add_checksums_to_functions()` Method

Update the method at line ~315 to include the new `block_checksum` field:

```python
@staticmethod
def add_checksums_to_functions(repo_path: Path, functions_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add checksums to all entries in a merged_functions.json structure.
    
    Returns:
        Dictionary with checksums added:
            {
                "function_to_location_and_checksum": {
                    "functionName": {
                        "checksum": "abc123...",        # Includes line numbers
                        "block_checksum": "def456...",  # Content only, no line numbers
                        "code": [{"file_name": "...", "start": 10, "end": 50}]
                    },
                    ...
                }
            }
    """
    function_entries = functions_json["function_to_location"]
    result = {}
    
    for function_name, locations in function_entries.items():
        if not function_name or not locations:
            continue
        
        # Compute existing checksum (includes line numbers)
        checksum = ASTFunctionSignatureGenerator.create_function_checksum(repo_path, locations, function_name)
        
        # Compute new block checksum (content only, no line numbers)
        block_checksum = ASTFunctionSignatureGenerator.create_function_block_checksum(repo_path, locations, function_name)
        
        result[function_name] = {
            "checksum": checksum,
            "block_checksum": block_checksum,
            "code": locations
        }
    
    return result
```

### Phase 3: Update Data Types Checksum (Optional)

If needed, apply the same pattern to data types in `add_checksums_to_data_types()`:

```python
result[data_type_name] = {
    "checksum": checksum,           # Existing (with line numbers)
    "block_checksum": block_checksum,  # New (content only)
    "code": files
}
```

### Phase 4: Update Call Graph Checksum

The call graph checksum should **NOT** include the new block_checksum since it's designed to track dependencies. However, we can add a separate `function_block_checksum` field that only captures the function's own code:

In `add_checksums_to_call_graph()`, add:

```python
# Add function's own block checksum (ignoring invoked functions)
function_block_checksum = ASTFunctionSignatureGenerator.create_function_block_checksum(
    repo_path, 
    {"locations": [{"file_name": context.get("file", ""), 
                    "start": context.get("start", 0), 
                    "end": context.get("end", 0)}]},
    function_name
)
result_function["block_checksum"] = function_block_checksum
```

## Files to Modify

| File | Changes |
|------|---------|
| [`hindsight/utils/hash_util.py`](../utils/hash_util.py) | Add `hash_for_function_block_md5()` method |
| [`hindsight/core/lang_util/ast_function_signature_util.py`](../core/lang_util/ast_function_signature_util.py) | Add `create_function_block_checksum()` method; Update `add_checksums_to_functions()`, `add_checksums_to_data_types()`, `add_checksums_to_call_graph()` |

## Output Schema Changes

### Before (merged_functions.json)
```json
{
  "functionName": {
    "checksum": "abc123...",
    "code": [{"file_name": "...", "start": 10, "end": 50}]
  }
}
```

### After (merged_functions.json)
```json
{
  "functionName": {
    "checksum": "abc123...",
    "block_checksum": "def456...",
    "code": [{"file_name": "...", "start": 10, "end": 50}]
  }
}
```

### Before (merged_call_graph.json)
```json
{
  "call_graph": [{
    "file": "path/to/file.java",
    "functions": [{
      "function": "functionName",
      "context": {"start": 10, "end": 50},
      "checksum": "abc123...",
      "functions_invoked": ["otherFunc"],
      "data_types_used": ["MyClass"]
    }]
  }]
}
```

### After (merged_call_graph.json)
```json
{
  "call_graph": [{
    "file": "path/to/file.java",
    "functions": [{
      "function": "functionName",
      "context": {"start": 10, "end": 50},
      "checksum": "abc123...",
      "block_checksum": "def456...",
      "functions_invoked": ["otherFunc"],
      "data_types_used": ["MyClass"]
    }]
  }]
}
```

## Testing Strategy

1. **Unit Tests for HashUtil**
   - Test `hash_for_function_block_md5()` with various inputs
   - Verify normalization works correctly
   - Test edge cases (empty content, whitespace-only)

2. **Unit Tests for ASTFunctionSignatureGenerator**
   - Test `create_function_block_checksum()` produces consistent results
   - Verify block_checksum differs from checksum when line numbers change
   - Test with multi-location functions

3. **Integration Tests**
   - Run full AST generation on a test repository
   - Verify JSON output contains both checksum fields
   - Verify block_checksum remains stable when line numbers change

## Migration Considerations

1. **Backward Compatibility**: The existing `checksum` field is preserved, so existing consumers continue to work
2. **Cache Invalidation**: Existing cached results may need to be regenerated to include the new field
3. **Documentation**: Update any documentation that references the JSON schema

## Performance Considerations

1. The new checksum computation reads the same file content as the existing checksum
2. Consider caching file content to avoid double reads
3. The normalization step in `hash_for_function_block_md5()` adds minimal overhead

## Implementation Order

1. Add `hash_for_function_block_md5()` to `HashUtil`
2. Add `create_function_block_checksum()` to `ASTFunctionSignatureGenerator`
3. Update `add_checksums_to_functions()` to include `block_checksum`
4. Update `add_checksums_to_data_types()` to include `block_checksum`
5. Update `add_checksums_to_call_graph()` to include `block_checksum`
6. Add unit tests
7. Run integration tests
8. Update documentation

## Estimated Effort

- **Phase 1 (HashUtil)**: 0.5 hours
- **Phase 2 (ASTFunctionSignatureGenerator)**: 2 hours
- **Phase 3 (Data Types)**: 0.5 hours
- **Phase 4 (Call Graph)**: 1 hour
- **Testing**: 2 hours
- **Documentation**: 0.5 hours

**Total**: ~6.5 hours

## Conclusion

This implementation plan provides a clean, backward-compatible approach to adding function block checksums to the AST generation logic. The new `block_checksum` field will enable efficient change detection based purely on function content, ignoring line number changes and invoked function dependencies.
