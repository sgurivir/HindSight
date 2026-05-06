# Plan: Reduce getFileContentByLines Line Range Errors

## Problem Statement

The LLM frequently calls `getFileContentByLines` with line ranges that exceed the actual file length, resulting in warnings and errors:

```
[TOOL] getFileContentByLines - endLine (250) exceeds file length (243 lines), adjusting to file end
[TOOL] getFileContentByLines - Error: startLine (250) exceeds file length (243 lines)
[TOOL] getFileContentByLines - Error: startLine (350) exceeds file length (243 lines)
```

### Root Cause Analysis

1. **LLM lacks file length information**: When the LLM calls `getFileContentByLines`, it doesn't know the total number of lines in the file beforehand
2. **Sequential scanning pattern**: The LLM often tries to read files in chunks (e.g., lines 1-100, 100-200, 200-300...) without knowing when to stop
3. **checkFileSize underutilized**: While `checkFileSize` returns `line_count`, the LLM doesn't always call it first, or doesn't use the information effectively
4. **Error messages don't guide recovery**: When `startLine exceeds file length` error occurs, the LLM sometimes continues trying higher line ranges

### Evidence from Logs

From the log file, we see patterns like:
- Lines 860-904: LLM makes 10+ sequential calls with incrementing ranges (300-400, 400-500, ..., 1000-1100) on a 243-line file
- The LLM receives `checkFileSize` result showing 243 lines but still requests lines 300+

---

## Proposed Solutions

### Solution 1: Enhanced Tool Response with File Metadata (Recommended)

**Approach**: Include total line count in every `getFileContentByLines` response

**Changes to `file_tools.py`**:
```python
# In execute_get_file_content_by_lines_tool, modify the header:
header = f"File: {path} (lines {start_line}-{min(end_line, total_lines)} of {total_lines} total)\n"
header += f"Note: File has {total_lines} lines total. Do not request lines beyond {total_lines}.\n"
header += "=" * 50 + "\n"
```

**Pros**:
- Minimal code change
- LLM receives line count information with every response
- No changes to tool schema or orchestration logic

**Cons**:
- Relies on LLM to read and use the information
- Adds slight overhead to every response

---

### Solution 2: Smarter Error Response with Guidance

**Approach**: When line range errors occur, return helpful guidance instead of just an error

**Changes to `file_tools.py`**:
```python
if start_line > total_lines:
    # Instead of just returning an error, provide helpful context
    error_msg = (
        f"Error: startLine ({start_line}) exceeds file length.\n"
        f"File '{path}' has only {total_lines} lines.\n"
        f"Valid line range: 1-{total_lines}\n"
        f"Suggestion: Use checkFileSize first, or request lines 1-{total_lines}"
    )
    logger.warning(f"[TOOL] getFileContentByLines - {error_msg}")
    return error_msg
```

**Pros**:
- Provides actionable guidance when errors occur
- Helps LLM self-correct
- No schema changes needed

**Cons**:
- Error still occurs (just better handled)
- Doesn't prevent the initial bad request

---

### Solution 3: Auto-Clamp Line Ranges (with Warning)

**Approach**: Instead of erroring when `startLine > total_lines`, return the last portion of the file with a warning

**Changes to `file_tools.py`**:
```python
if start_line > total_lines:
    # Auto-clamp to last N lines instead of erroring
    requested_range = end_line - start_line + 1
    start_line = max(1, total_lines - requested_range + 1)
    end_line = total_lines
    logger.warning(
        f"[TOOL] getFileContentByLines - Requested startLine exceeded file length. "
        f"Auto-adjusted to lines {start_line}-{end_line} (file has {total_lines} lines)"
    )
    # Continue with adjusted range...
```

**Pros**:
- Prevents errors entirely
- LLM still gets useful content
- Graceful degradation

**Cons**:
- May return unexpected content
- Could mask programming errors in prompts
- LLM might not realize the adjustment happened

---

### Solution 4: Mandatory checkFileSize Before getFileContentByLines

**Approach**: Modify orchestration to require `checkFileSize` before `getFileContentByLines` for unknown files

**Implementation Options**:

**Option A - Tool-level enforcement**:
```python
# Track files that have been size-checked
self._size_checked_files = set()

def execute_check_file_size_tool(self, path, reason=None):
    # ... existing logic ...
    self._size_checked_files.add(resolved_path)
    return result

def execute_get_file_content_by_lines_tool(self, path, start_line, end_line, reason=None):
    resolved_path, _ = self._resolve_file_path(path)
    if resolved_path not in self._size_checked_files:
        # Auto-call checkFileSize first
        size_info = self.execute_check_file_size_tool(path, "Auto-check before getFileContentByLines")
        # Parse line_count from size_info and validate
```

**Option B - Prompt-level guidance**:
Update system prompts to strongly encourage calling `checkFileSize` first

**Pros**:
- Prevents errors at the source
- Ensures LLM has file metadata before reading

**Cons**:
- Adds complexity to orchestration
- May slow down analysis with extra tool calls
- Option A requires state management

---

### Solution 5: Combined getFileContentByLines with Auto-Discovery

**Approach**: Add optional parameter to auto-discover and return file metadata

**Changes to `tool_definitions.py`**:
```python
"getFileContentByLines": {
    "description": "Read specific line ranges from a file. Returns file metadata including total line count.",
    "parameters": {
        # ... existing parameters ...
        "includeMetadata": {
            "type": "boolean",
            "required": False,
            "description": "If true, include file metadata (total lines, size) in response header. Default: true"
        }
    }
}
```

**Pros**:
- Backward compatible
- LLM always gets metadata
- Single tool call provides all needed info

**Cons**:
- Schema change required
- Slightly larger responses

---

## Recommended Implementation Plan

Based on the analysis, I recommend implementing **Solutions 1 + 2** together as they provide the best balance of effectiveness and minimal disruption:

### Phase 1: Enhanced Response Headers (Solution 1)

**File**: `hindsight/core/llm/tools/file_tools.py`

**Changes**:
1. Modify the header in `execute_get_file_content_by_lines_tool` to always include total line count
2. Add a note about valid line ranges

### Phase 2: Improved Error Messages (Solution 2)

**File**: `hindsight/core/llm/tools/file_tools.py`

**Changes**:
1. Enhance error message when `startLine > total_lines` to include:
   - Actual file length
   - Valid line range
   - Suggestion to use `checkFileSize` or adjust range

### Phase 3: Update Tool Description (Optional Enhancement)

**File**: `hindsight/core/llm/tools/tool_definitions.py`

**Changes**:
1. Update `getFileContentByLines` description to mention that responses include total line count
2. Add guidance about using `checkFileSize` for large files

---

## Implementation Details

### Changes to file_tools.py

```python
# In execute_get_file_content_by_lines_tool method

# After reading file and getting total_lines:
total_lines = len(lines)

# Enhanced error handling for startLine exceeding file length
if start_line > total_lines:
    error_msg = (
        f"Error: startLine ({start_line}) exceeds file length.\n"
        f"File: {path}\n"
        f"Total lines: {total_lines}\n"
        f"Valid range: 1-{total_lines}\n"
        f"Suggestion: Request lines within the valid range, e.g., getFileContentByLines with startLine=1, endLine={total_lines}"
    )
    logger.warning(f"[TOOL] getFileContentByLines - startLine ({start_line}) exceeds file length ({total_lines} lines)")
    return error_msg

# Enhanced header with total line count
header = f"File: {path} (lines {start_line}-{min(end_line, total_lines)} of {total_lines} total)\n"
header += "=" * 50 + "\n"
```

### Changes to tool_definitions.py

```python
"getFileContentByLines": {
    "description": "Read specific line ranges from a file. Response includes total line count in header. Use checkFileSize first if you need to know the file length before requesting specific ranges.",
    # ... rest unchanged
}
```

---

## Success Metrics

After implementation, we should see:
1. Reduced frequency of `startLine exceeds file length` errors
2. LLM self-correcting after receiving enhanced error messages
3. More efficient file reading patterns (fewer wasted tool calls)

## Testing Plan

1. Run existing code analysis on test repositories
2. Monitor logs for `getFileContentByLines` warnings
3. Compare warning frequency before and after changes
4. Verify LLM behavior improves with enhanced error messages

---

## Alternative Consideration: Prompt Engineering

In addition to code changes, consider updating the system prompts used in context collection to:
1. Explicitly instruct the LLM to use `checkFileSize` before reading large files
2. Remind the LLM to check the total line count in response headers
3. Discourage sequential scanning patterns without knowing file length

This can be done in the prompt builder files without any tool changes.
