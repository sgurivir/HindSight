# Search Tool Removal Plan

## Problem Statement

The current `search_files` tool implementation in `search_tools.py` causes shell interpretation issues when search strings contain special characters like parentheses `(`, `[`, `$`, etc.

### Error Examples from Logs
```
2026-03-11 22:01:24,095 - hindsight.core.llm.tools.search_tools - ERROR - search_tools.py:191 - [TOOL] search_files - Error: grep command failed with exit code 2: /bin/sh: -c: line 0: syntax error near unexpected token `('
```

## Solution: Remove `search_files` Tool Entirely

Instead of fixing the escaping issues in `search_files`, we will **remove the tool entirely** and have the LLM use `runTerminalCmd` with grep commands directly.

### Why This Works

The LLM is already trained (via examples in prompts) to provide properly quoted commands for `runTerminalCmd`. By removing `search_files` and adding grep examples to the prompts, the LLM will handle escaping correctly.

| Aspect | `runTerminalCmd` | `search_files` (to be removed) |
|--------|------------------|--------------------------------|
| **Input** | Complete shell command | Raw parameters |
| **Quoting** | LLM provides quoted command | Tool must escape |
| **Responsibility** | LLM handles escaping | Tool handles escaping (broken) |

## Implementation Steps

### Step 1: Remove `search_files` from Tool Definitions

**File:** `hindsight/core/llm/tools/tool_definitions.py`

Remove the `search_files` entry from `TOOL_DEFINITIONS` (lines 76-112).

### Step 2: Remove `search_files` Tool Handler and Imports

**File:** `hindsight/core/llm/tools/tools.py`

Remove the following:

1. **Remove import** (line 15):
   ```python
   from .search_tools import SearchToolsMixin
   ```

2. **Remove from class inheritance** (line 29):
   ```python
   SearchToolsMixin,
   ```

3. **Remove handler registration** (search for `search_files` handler):
   ```python
   self.register_tool_handler("search_files", self._handle_search_files)
   ```

4. **Remove handler method**:
   ```python
   def _handle_search_files(self, search_string: str, extensions: List[str], reason: str = None) -> str:
       """Handler for search_files tool."""
       return self.execute_search_files_tool(search_string, extensions, reason)
   ```

### Step 3: Update Prompts with grep Examples

**Files to update:**
- `hindsight/core/prompts/systemPrompt.md`
- `hindsight/core/prompts/analysisTools.md`
- `hindsight/core/prompts/detailedAnalysisProcess.md`
- `hindsight/core/prompts/diffAnalysisPrompt.md`
- `hindsight/core/prompts/systemPromptTrace.md`
- `hindsight/core/prompts/responseChallenger.md`

**Changes:**
1. Remove all references to `search_files` tool
2. Add grep examples under `runTerminalCmd` section

### Step 4: Grep Examples to Add to Prompts

Add the following examples to the `runTerminalCmd` section in prompts:

```markdown
### runTerminalCmd Tool (Exploration & Search)
**Purpose**: Execute safe terminal commands to explore the codebase structure and search for patterns.
**Allowed commands**: ls, find, grep, wc, head, tail, cat, tree, file, sed

**Example 1: Search for a function name in specific file types**
```json
{
  "tool": "runTerminalCmd",
  "command": "grep -r -l 'functionName' --include='*.java' .",
  "reason": "Find all Java files containing the function functionName"
}
```

**Example 2: Search for a pattern with special characters (use single quotes)**
```json
{
  "tool": "runTerminalCmd",
  "command": "grep -r -l 'processData(' --include='*.py' .",
  "reason": "Find Python files containing the function call processData("
}
```

**Example 3: Search with context lines**
```json
{
  "tool": "runTerminalCmd",
  "command": "grep -r -A 5 -B 2 'class MyClass' --include='*.java' .",
  "reason": "Find class definition with surrounding context"
}
```

**Example 4: Case-insensitive search**
```json
{
  "tool": "runTerminalCmd",
  "command": "grep -r -i -l 'error' --include='*.log' .",
  "reason": "Find all log files containing 'error' (case-insensitive)"
}
```

**Grep flags reference:**
- `-r`: Recursive search
- `-l`: List only filenames (not matching lines)
- `-i`: Case-insensitive
- `-A N`: Show N lines after match
- `-B N`: Show N lines before match
- `-n`: Show line numbers
- `--include='*.ext'`: Filter by file extension
- `--exclude-dir=dirname`: Exclude directories

**IMPORTANT**: Always wrap search patterns in single quotes to prevent shell interpretation of special characters.
```

### Step 5: Delete `search_tools.py`

**File:** `hindsight/core/llm/tools/search_tools.py`

Delete the file entirely.

**Note:** The `_log_tool_failure()` method in this file is useful for logging tool failures. Before deleting, consider moving this method to `base.py` so it can be used by other tools (like `runTerminalCmd`).

### Step 6: Update `__init__.py` Documentation

**File:** `hindsight/core/llm/tools/__init__.py`

Update the docstring to remove reference to `search_tools.py` (line 16):

**Before:**
```python
- search_tools.py: Search tools (search_files)
```

**After:** (delete this line entirely)

### Step 7: Update Tests

Remove or update any tests that reference `search_files`:
- Check `hindsight/tests/` for test files

## Files to Modify

| File | Action |
|------|--------|
| `hindsight/core/llm/tools/tool_definitions.py` | Remove `search_files` from `TOOL_DEFINITIONS` |
| `hindsight/core/llm/tools/tools.py` | Remove handler and registration |
| `hindsight/core/llm/tools/search_tools.py` | Delete (move `_log_tool_failure` to `base.py` first) |
| `hindsight/core/llm/tools/__init__.py` | Remove `search_tools.py` from docstring (line 16) |
| `hindsight/core/prompts/systemPrompt.md` | Remove `search_files`, add grep examples |
| `hindsight/core/prompts/analysisTools.md` | Remove `search_files`, add grep examples |
| `hindsight/core/prompts/detailedAnalysisProcess.md` | Remove `search_files`, add grep examples |
| `hindsight/core/prompts/diffAnalysisPrompt.md` | Remove `search_files`, add grep examples |
| `hindsight/core/prompts/systemPromptTrace.md` | Remove `search_files`, add grep examples |
| `hindsight/core/prompts/responseChallenger.md` | Remove `search_files`, add grep examples |

## Benefits of This Approach

1. **No escaping issues** - LLM provides properly quoted commands
2. **Simpler codebase** - One less tool to maintain
3. **More flexible** - LLM can use full grep capabilities (context lines, regex, etc.)
4. **Consistent** - All terminal commands go through the same path
5. **No new dependencies** - Uses existing `runTerminalCmd` infrastructure

## Testing

After implementation, test with search patterns that previously failed:
- `functionName(` - parentheses
- `array[0]` - brackets
- `$variable` - dollar sign
- `it's` - single quote
- `say "hello"` - double quotes

## Rollback Plan

If issues arise, the `search_files` tool can be restored from git history.
