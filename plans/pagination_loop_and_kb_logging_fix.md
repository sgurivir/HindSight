# Plan: Fix startLine Pagination Loop and Add Knowledge Base Hit/Miss Logging

## Overview

This plan addresses two feedback items from the log analysis:

1. **Issue 2d: startLine Pagination Loop** - When `startLine` exceeds file length, the LLM continues requesting `startLine + 80`, `+160`, etc. (12 occurrences observed on short files)
2. **Issue 2e: Knowledge Base Hit/Miss Logging** - No logging of what `lookup_knowledge` returns, making it impossible to tell if KB lookups are reducing redundant file reads

---

## Issue 2d: startLine Pagination Loop

### Problem Analysis

From the log file, we can see the pagination loop issue clearly:

```
2026-04-16 10:54:11,544 - [TOOL] getFileContentByLines - startLine (160) exceeds file length (115 lines)
2026-04-16 10:54:11,544 - [TOOL] getFileContentByLines - startLine (240) exceeds file length (115 lines)
2026-04-16 10:54:11,545 - [TOOL] getFileContentByLines - startLine (320) exceeds file length (115 lines)
2026-04-16 10:54:11,545 - [TOOL] getFileContentByLines - startLine (400) exceeds file length (115 lines)
2026-04-16 10:54:11,545 - [TOOL] getFileContentByLines - startLine (480) exceeds file length (115 lines)
```

The current error message (lines 217-227 in `file_tools.py`) says:
```
Error: startLine ({start_line}) exceeds file length.
File: {path}
Total lines in file: {total_lines}
Valid line range: 1-{total_lines}
Suggestion: Request lines within the valid range...
```

**Root Cause**: The error message doesn't include an explicit `end_of_file: true` signal that the LLM can recognize to stop pagination.

### Solution

Modify the error response in [`hindsight/core/llm/tools/file_tools.py`](hindsight/core/llm/tools/file_tools.py:215-227) to include:
1. An explicit `"end_of_file": true` JSON field
2. A clear message stating "There is no more content to read"
3. Return the response as JSON for machine-parseable signals

### Implementation

**File: `hindsight/core/llm/tools/file_tools.py`**

Change lines 215-227 from:
```python
if start_line > total_lines:
    error_msg = (
        f"Error: startLine ({start_line}) exceeds file length.\n"
        f"File: {path}\n"
        f"Total lines in file: {total_lines}\n"
        f"Valid line range: 1-{total_lines}\n"
        f"Suggestion: Request lines within the valid range. "
        f"For example, use startLine=1 and endLine={total_lines} to read the entire file, "
        f"or use checkFileSize tool first to get the file's line count."
    )
    logger.warning(f"[TOOL] getFileContentByLines - startLine ({start_line}) exceeds file length ({total_lines} lines)")
    return error_msg
```

To:
```python
if start_line > total_lines:
    # Return JSON response with explicit end_of_file signal to prevent pagination loops
    import json
    error_response = {
        "end_of_file": True,
        "error": f"startLine ({start_line}) exceeds file length",
        "file": path,
        "total_lines": total_lines,
        "valid_range": f"1-{total_lines}",
        "message": f"File has only {total_lines} lines. There is no more content to read.",
        "suggestion": f"The entire file content is available in lines 1-{total_lines}. Do not request lines beyond {total_lines}."
    }
    logger.warning(f"[TOOL] getFileContentByLines - startLine ({start_line}) exceeds file length ({total_lines} lines) - returning end_of_file signal")
    return json.dumps(error_response, indent=2)
```

---

## Issue 2e: Knowledge Base Hit/Miss Logging

### Problem Analysis

From the log file, we see `lookup_knowledge` being called but no indication of results:

```
2026-04-16 10:50:59,202 - [TOOL ORCHESTRATOR] Executing tool 'lookup_knowledge' (id: json_lookup_knowledge_1776361859)
2026-04-16 10:50:59,203 - [ContextCollectionAnalyzer] Added JSON tool result for lookup_knowledge (id: json_tool_1_0)
```

There's no logging of whether the lookup returned results (hit) or not (miss), making it impossible to evaluate KB effectiveness.

### Solution

Add INFO-level logging in [`hindsight/core/llm/tools/knowledge_tools.py`](hindsight/core/llm/tools/knowledge_tools.py:27-64) to log:
- The query being searched
- Whether it was a hit or miss
- If hit: the number of results and top confidence score
- If miss: explicit "miss" indication

### Implementation

**File: `hindsight/core/llm/tools/knowledge_tools.py`**

Change the `execute_lookup_knowledge_tool` method (lines 27-64) to add logging:

```python
def execute_lookup_knowledge_tool(self, query: str, limit: int = 5) -> str:
    """
    Execute lookup_knowledge tool to search the knowledge base.

    Args:
        query: Search query string
        limit: Maximum number of results to return (default 5)

    Returns:
        str: Formatted search results or informative message
    """
    if self.knowledge_store is None:
        logger.info(f'[KB] lookup_knowledge("{query}") → unavailable (no knowledge store)')
        return "Knowledge base not available"

    try:
        results = self.knowledge_store.lookup(query, limit)
    except Exception as exc:
        logger.error("lookup_knowledge failed for query %r: %s", query, exc, exc_info=True)
        logger.info(f'[KB] lookup_knowledge("{query}") → error ({exc})')
        return f"Error querying knowledge base: {exc}"

    if not results:
        logger.info(f'[KB] lookup_knowledge("{query}") → miss')
        return f"No knowledge base results for query: '{query}'"

    # Log hit with confidence of top result
    top_confidence = results[0].get("confidence", 0.0) if results else 0.0
    logger.info(f'[KB] lookup_knowledge("{query}") → hit ({len(results)} results, top_confidence={top_confidence:.2f})')

    lines = []
    for idx, record in enumerate(results, start=1):
        entity_key = record.get("entity_key", "unknown")
        stage = record.get("stage", "unknown")
        confidence = record.get("confidence", 0.0)
        summary = record.get("summary", "")
        related_context = record.get("related_context") or ""

        lines.append(f"Result {idx}: {entity_key}")
        lines.append(f"  Stage: {stage}")
        lines.append(f"  Confidence: {confidence}")
        lines.append(f"  Summary: {summary}")
        lines.append(f"  Related context: {related_context}")

    return "\n".join(lines)
```

---

## Files to Modify

1. **`hindsight/core/llm/tools/file_tools.py`** (lines 215-227)
   - Add JSON response with `end_of_file: true` signal for out-of-bounds startLine

2. **`hindsight/core/llm/tools/knowledge_tools.py`** (lines 27-64)
   - Add INFO-level logging for KB hit/miss with confidence scores

---

## Expected Log Output After Changes

### For Issue 2d (Pagination Loop):
```
2026-04-16 10:54:11,544 - [TOOL] getFileContentByLines - startLine (160) exceeds file length (115 lines) - returning end_of_file signal
```

And the LLM will receive:
```json
{
  "end_of_file": true,
  "error": "startLine (160) exceeds file length",
  "file": "apps/Orange/Orange/DataCollector/DataCollector.swift",
  "total_lines": 115,
  "valid_range": "1-115",
  "message": "File has only 115 lines. There is no more content to read.",
  "suggestion": "The entire file content is available in lines 1-115. Do not request lines beyond 115."
}
```

### For Issue 2e (KB Logging):
```
2026-04-16 10:50:59,203 - [KB] lookup_knowledge("DataCollector") → hit (3 results, top_confidence=0.92)
2026-04-16 10:50:59,205 - [KB] lookup_knowledge("SRDataSensor") → miss
2026-04-16 10:50:59,207 - [KB] lookup_knowledge("toggleSensor") → hit (1 results, top_confidence=0.85)
```

---

## Testing

1. **Pagination Loop Test**: 
   - Create a test file with 35 lines
   - Call `getFileContentByLines` with `startLine=100`
   - Verify JSON response contains `"end_of_file": true`
   - Verify LLM stops pagination after receiving this signal

2. **KB Logging Test**:
   - Run analysis with knowledge store enabled
   - Verify log contains `[KB] lookup_knowledge(...)` entries
   - Verify hit/miss status is correctly logged
   - Verify confidence scores are included for hits

---

## Rollout

1. Implement changes in both files
2. Run existing test suite to ensure no regressions
3. Run a sample analysis and verify new log output
4. Monitor for reduction in pagination loop occurrences
