# Plan: Add `recursive` Parameter Support to `list_files` Tool

## Problem Statement

The `list_files` tool is failing with the following error:

```
2026-03-12 10:52:00,972 - hindsight.core.llm.tools.tools - ERROR - tools.py:346 - [TOOL ORCHESTRATOR] Error executing tool 'list_files': Tools._handle_list_files() got an unexpected keyword argument 'recursive'
```

The LLM is passing a `recursive` parameter to the `list_files` tool, but the current implementation does not support this parameter.

---

## Current Implementation Analysis

### 1. Tool Definition ([`tool_definitions.py`](hindsight/core/llm/tools/tool_definitions.py:94))

The current `list_files` tool definition only has two parameters:

```python
"list_files": {
    "description": "List directory contents with file sizes. Use this to explore the repository structure.",
    "parameters": {
        "path": {
            "type": "string",
            "required": True,
            "description": "Path to the directory to list (relative to repository root)"
        },
        "reason": {
            "type": "string",
            "required": False,
            "description": "Reason for listing this directory"
        }
    },
    "aliases": {
        "directory": "path"
    }
}
```

### 2. Tool Handler ([`tools.py`](hindsight/core/llm/tools/tools.py:136))

The handler method signature:

```python
def _handle_list_files(self, path: str, reason: str = None) -> str:
    """Handler for list_files tool."""
    return self.execute_list_files_tool(path, reason)
```

### 3. Tool Implementation ([`directory_tools.py`](hindsight/core/llm/tools/directory_tools.py:31))

The actual implementation:

```python
def execute_list_files_tool(self: ToolsBase, path: str, reason: str = None) -> str:
    """
    Execute list_files tool to retrieve directory tree structure using DirectoryTreeUtil.
    """
    # ... uses DirectoryTreeUtil.get_directory_listing() which only lists one level
```

### 4. DirectoryTreeUtil ([`directory_tree_util.py`](hindsight/utils/directory_tree_util.py:70))

Current implementation only lists one level:

```python
@staticmethod
def get_directory_listing(repo_path: str, relative_path: str) -> str:
    """
    Print one-level ASCII tree of files and subdirectories.
    """
    # Only iterates through immediate children
    for entry in sorted(base_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.is_dir():
            lines.append(f"|-- {entry.name}/")
        elif entry.is_file() and entry.suffix.lower() in exts:
            # ...
```

---

## Example Tool Invocations

### Current Behavior (Non-Recursive)

**Tool Call:**
```json
{
    "type": "tool_use",
    "id": "call_123",
    "name": "list_files",
    "input": {
        "path": "src",
        "reason": "Exploring source directory structure"
    }
}
```

**Expected Result:**
```
Directory listing for 'src' (use this to understand file sizes before reading files):
src/
|-- components/
|-- utils/
|-- main.py Size : (1234 chars)
|-- config.py Size : (567 chars)
```

### Desired Behavior (Recursive)

**Tool Call:**
```json
{
    "type": "tool_use",
    "id": "call_456",
    "name": "list_files",
    "input": {
        "path": "src",
        "recursive": true,
        "reason": "Getting full directory tree"
    }
}
```

**Expected Result:**
```
Directory listing for 'src' (recursive):
src/
|-- components/
|   |-- Button.py Size : (890 chars)
|   |-- Header.py Size : (1200 chars)
|-- utils/
|   |-- helpers.py Size : (456 chars)
|-- main.py Size : (1234 chars)
|-- config.py Size : (567 chars)
```

---

## Implementation Plan

### Design Principle: Extend DirectoryTreeUtil

We will add recursive capability directly to `DirectoryTreeUtil` to:
1. Keep all directory listing logic in one place
2. Maintain backward compatibility with existing callers
3. Reuse the existing path resolution and file filtering logic

---

## Phase 1: Update DirectoryTreeUtil

**File:** [`hindsight/utils/directory_tree_util.py`](hindsight/utils/directory_tree_util.py)

### 1.1 Add New Recursive Method

Add a new static method for recursive directory listing while keeping the existing method unchanged:

```python
@staticmethod
def get_directory_listing(repo_path: str, relative_path: str, recursive: bool = False, max_depth: int = 6) -> str:
    """
    Print ASCII tree of files and subdirectories.
    If a file is given, show its character size.
    This is used by LLM Tools.
    
    Args:
        repo_path: Base repository path
        relative_path: Relative path to list (or file to inspect)
        recursive: If True, list files recursively in tree format. Default is False (single level).
        max_depth: Maximum depth for recursive listing. Default is 6.
        
    Returns:
        str: Formatted directory listing or file info
    """
    repo_root = Path(repo_path).expanduser().resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        return f"Path not found: {repo_path}"

    base_path = DirectoryTreeUtil._resolve_anywhere(repo_root, relative_path)
    if base_path is None:
        return f"Path not found: {relative_path}"

    # Determine relative display path to repo
    try:
        relative_display = str(base_path.relative_to(repo_root))
    except ValueError:
        relative_display = str(base_path.name)

    # --- Handle file case ---
    if base_path.is_file():
        try:
            size = base_path.stat().st_size
            return f"{relative_display} ({size} chars)"
        except OSError:
            return f"{relative_display} (size unavailable)"

    # --- Handle directory case ---
    if recursive:
        return DirectoryTreeUtil._get_recursive_listing(base_path, relative_display, max_depth)
    else:
        return DirectoryTreeUtil._get_single_level_listing(base_path, relative_display)

@staticmethod
def _get_single_level_listing(base_path: Path, relative_display: str) -> str:
    """
    Get single-level directory listing (existing behavior).
    
    Args:
        base_path: Resolved path to the directory
        relative_display: Display name for the directory
        
    Returns:
        str: Formatted single-level directory listing
    """
    exts = {e.lower() for e in DirectoryTreeUtil.DEFAULT_EXTS}
    lines = [f"{relative_display}/"]

    for entry in sorted(base_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.is_dir():
            lines.append(f"|-- {entry.name}/")
        elif entry.is_file() and entry.suffix.lower() in exts:
            try:
                size = entry.stat().st_size
                lines.append(f"|-- {entry.name} Size : ({size} chars)")
            except OSError:
                lines.append(f"|-- {entry.name} (size unavailable)")

    if len(lines) == 1:
        lines.append(f"|-- (no supported files or subdirectories)")

    return "\n".join(lines)

@staticmethod
def _get_recursive_listing(base_path: Path, relative_display: str, max_depth: int = 6) -> str:
    """
    Get recursive directory listing in tree format.
    
    Args:
        base_path: Resolved path to the directory
        relative_display: Display name for the directory
        max_depth: Maximum depth to traverse
        
    Returns:
        str: Formatted recursive directory tree
    """
    exts = {e.lower() for e in DirectoryTreeUtil.DEFAULT_EXTS}
    lines = [f"{relative_display}/"]
    
    def _format_tree_recursive(current_path: Path, prefix: str, depth: int) -> None:
        """Recursively format directory tree."""
        if depth > max_depth:
            return
            
        try:
            entries = sorted(current_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError):
            return
            
        # Filter entries: directories and files with supported extensions
        filtered_entries = []
        for entry in entries:
            if entry.is_dir():
                filtered_entries.append(entry)
            elif entry.is_file() and entry.suffix.lower() in exts:
                filtered_entries.append(entry)
        
        for i, entry in enumerate(filtered_entries):
            is_last = (i == len(filtered_entries) - 1)
            connector = "|-- " if not is_last else "`-- "
            
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                # Recurse into subdirectory
                new_prefix = prefix + ("|   " if not is_last else "    ")
                _format_tree_recursive(entry, new_prefix, depth + 1)
            else:
                try:
                    size = entry.stat().st_size
                    lines.append(f"{prefix}{connector}{entry.name} Size : ({size} chars)")
                except OSError:
                    lines.append(f"{prefix}{connector}{entry.name} (size unavailable)")
    
    _format_tree_recursive(base_path, "", 0)
    
    if len(lines) == 1:
        lines.append("|-- (no supported files or subdirectories)")
    
    return "\n".join(lines)
```

### 1.2 Backward Compatibility

The existing `get_directory_listing()` method signature changes from:
```python
def get_directory_listing(repo_path: str, relative_path: str) -> str:
```

To:
```python
def get_directory_listing(repo_path: str, relative_path: str, recursive: bool = False, max_depth: int = 6) -> str:
```

**This is backward compatible** because:
- `recursive` defaults to `False` (existing behavior)
- `max_depth` defaults to `6` (only used when recursive=True)
- All existing callers that don't pass these parameters will continue to work

---

## Phase 2: Update Tool Definition

**File:** [`hindsight/core/llm/tools/tool_definitions.py`](hindsight/core/llm/tools/tool_definitions.py)

Add the `recursive` parameter to the `list_files` tool definition:

```python
"list_files": {
    "description": "List directory contents with file sizes. Use this to explore the repository structure. Set recursive=true to get a full tree view of nested directories.",
    "parameters": {
        "path": {
            "type": "string",
            "required": True,
            "description": "Path to the directory to list (relative to repository root)"
        },
        "recursive": {
            "type": "boolean",
            "required": False,
            "description": "If true, list files recursively in a tree format showing nested directories. Default is false (single level only)."
        },
        "reason": {
            "type": "string",
            "required": False,
            "description": "Reason for listing this directory"
        }
    },
    "aliases": {
        "directory": "path"
    }
}
```

---

## Phase 3: Update Tool Handler

**File:** [`hindsight/core/llm/tools/tools.py`](hindsight/core/llm/tools/tools.py)

### 3.1 Update Handler Method Signature (line ~136)

```python
def _handle_list_files(self, path: str, recursive: bool = False, reason: str = None) -> str:
    """Handler for list_files tool."""
    return self.execute_list_files_tool(path, recursive, reason)
```

### 3.2 Update Fallback Logic (line ~311)

```python
elif tool_name == "list_files":
    path = tool_input.get("path", "")
    recursive = tool_input.get("recursive", False)
    reason = tool_input.get("reason", "")
    if not path:
        return "Error: list_files tool requires 'path' parameter"
    return self.execute_list_files_tool(path, recursive, reason)
```

---

## Phase 4: Update Tool Implementation

**File:** [`hindsight/core/llm/tools/directory_tools.py`](hindsight/core/llm/tools/directory_tools.py)

Update the `execute_list_files_tool` method:

```python
def execute_list_files_tool(self: ToolsBase, path: str, recursive: bool = False, reason: str = None) -> str:
    """
    Execute list_files tool to retrieve directory tree structure using DirectoryTreeUtil.

    Args:
        path: Path to the directory or file to list (relative to repo root) or dict containing path info
        recursive: If True, list files recursively in tree format. Default is False.
        reason: Reason why this tool is being used (optional for backward compatibility)

    Returns:
        str: Directory tree structure or error message
    """
    start_time = time.time()
    self.tool_usage_stats['list_files']['count'] += 1

    # Validate path parameter
    if not isinstance(path, str):
        error_msg = f"Error: path parameter must be a string, got {type(path)}: {path}"
        logger.error(f"[TOOL] list_files - Invalid input type: {error_msg}")
        return error_msg
    
    path = path.strip() if path else self.repo_path

    logger.info(f"[TOOL] list_files called #{self.tool_usage_stats['list_files']['count']} - Path: {path}, Recursive: {recursive}")
    logger.info(f"[AI REASONING] {reason if reason else 'No reason provided'}")

    try:
        # Check if DirectoryTreeUtil is available
        if not self.directory_tree_util:
            error_msg = "Error: DirectoryTreeUtil not available. This tool requires DirectoryTreeUtil to be initialized."
            logger.error(f"[TOOL] list_files - {error_msg}")
            return error_msg

        # Use DirectoryTreeUtil with recursive parameter
        result = self.directory_tree_util.get_directory_listing(
            repo_path=self.repo_path,
            relative_path=path,
            recursive=recursive
        )

        # Add helpful context about the tool usage
        if result and not result.startswith("Path not found"):
            mode = "recursive" if recursive else "single level"
            context_msg = f"Directory listing for '{path}' ({mode}):\n{result}"
            result = context_msg

        # Update statistics
        self.tool_usage_stats['list_files']['total_chars'] += len(result)
        self.tool_usage_stats['list_files']['paths_accessed'].append(path)

        execution_time = time.time() - start_time
        logger.info(f"[TOOL] list_files completed - Path: {path}, Recursive: {recursive}, "
                   f"Content: {len(result)} chars, Time: {execution_time:.2f}s")

        return result

    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"Error getting directory listing for '{path}': {str(e)}"
        logger.error(f"[TOOL] list_files failed - Path: {path}, Error: {str(e)}, Time: {execution_time:.2f}s")
        return error_msg
```

---

## Files to Modify

| File | Changes | Impact |
|------|---------|--------|
| [`hindsight/utils/directory_tree_util.py`](hindsight/utils/directory_tree_util.py) | Add `recursive` and `max_depth` parameters to `get_directory_listing()`, add helper methods | **Backward compatible** - defaults preserve existing behavior |
| [`hindsight/core/llm/tools/tool_definitions.py`](hindsight/core/llm/tools/tool_definitions.py) | Add `recursive` parameter to `list_files` definition | **Additive** - new optional parameter |
| [`hindsight/core/llm/tools/tools.py`](hindsight/core/llm/tools/tools.py) | Update `_handle_list_files` signature and fallback logic | **Backward compatible** - new parameter has default |
| [`hindsight/core/llm/tools/directory_tools.py`](hindsight/core/llm/tools/directory_tools.py) | Update `execute_list_files_tool` to pass `recursive` parameter | **Backward compatible** - new parameter has default |

---

## Existing Clients Analysis

### Complete List of DirectoryTreeUtil Clients

Based on codebase search, here are ALL files that use `DirectoryTreeUtil`:

| File | Usage | Impact Assessment |
|------|-------|-------------------|
| [`hindsight/core/llm/tools/directory_tools.py`](hindsight/core/llm/tools/directory_tools.py:64) | Calls `get_directory_listing(repo_path, relative_path)` | **Will be updated** to pass `recursive` parameter |
| [`hindsight/utils/directory_tree_util.py`](hindsight/utils/directory_tree_util.py:145) | CLI main() calls `get_directory_listing(args.repo_path, args.relative_path)` | **No change needed** - defaults to non-recursive |
| [`hindsight/analyzers/analysis_runner.py`](hindsight/analyzers/analysis_runner.py:84) | Creates `DirectoryTreeUtil()` instance | **No change needed** - just instantiation |
| [`hindsight/core/trace_util/trace_code_analysis.py`](hindsight/core/trace_util/trace_code_analysis.py:110) | Creates `DirectoryTreeUtil()` instance | **No change needed** - just instantiation |
| [`hindsight/core/proj_util/file_or_directory_summary_generator.py`](hindsight/core/proj_util/file_or_directory_summary_generator.py:127) | Creates `DirectoryTreeUtil()` instance | **No change needed** - just instantiation |
| [`hindsight/core/llm/diff_analysis.py`](hindsight/core/llm/diff_analysis.py:94) | Creates `DirectoryTreeUtil()` instance | **No change needed** - just instantiation |
| [`hindsight/core/llm/code_analysis.py`](hindsight/core/llm/code_analysis.py:120) | Creates `DirectoryTreeUtil()` instance | **No change needed** - just instantiation |
| [`hindsight/issue_filter/response_challenger.py`](hindsight/issue_filter/response_challenger.py:42) | Accepts `directory_tree_util` parameter | **No change needed** - just parameter passing |
| [`hindsight/issue_filter/unified_issue_filter.py`](hindsight/issue_filter/unified_issue_filter.py:57) | Accepts `directory_tree_util` parameter | **No change needed** - just parameter passing |
| [`hindsight/core/llm/tools/base.py`](hindsight/core/llm/tools/base.py:77) | Accepts `directory_tree_util` parameter | **No change needed** - just parameter passing |
| [`hindsight/core/llm/tools/tools.py`](hindsight/core/llm/tools/tools.py:76) | Accepts `directory_tree_util` parameter | **No change needed** - just parameter passing |

### Clients That Call get_directory_listing()

Only **2 locations** actually call `get_directory_listing()`:

1. **[`directory_tools.py:64`](hindsight/core/llm/tools/directory_tools.py:64)** - The LLM tool implementation
   ```python
   result = self.directory_tree_util.get_directory_listing(
       repo_path=self.repo_path,
       relative_path=path
   )
   ```
   **Action:** Update to pass `recursive` parameter

2. **[`directory_tree_util.py:145`](hindsight/utils/directory_tree_util.py:145)** - CLI main function
   ```python
   tree = DirectoryTreeUtil.get_directory_listing(args.repo_path, args.relative_path)
   ```
   **Action:** No change needed - will use default `recursive=False`

### Backward Compatibility Guarantee

All existing callers will continue to work unchanged because:

1. **New parameters have defaults:**
   ```python
   def get_directory_listing(repo_path: str, relative_path: str, recursive: bool = False, max_depth: int = 6) -> str:
   ```

2. **Default behavior matches current behavior:**
   - `recursive=False` produces identical output to current implementation
   - `max_depth=6` is only used when `recursive=True`

3. **No breaking changes to method signature:**
   - Existing positional arguments remain in same position
   - New arguments are keyword-only with defaults

---

## Testing Plan

### Test File Location

Create new test file: **`hindsight/tests/utils/test_directory_tree_util.py`**

### Unit Tests for DirectoryTreeUtil

```python
"""
Tests for DirectoryTreeUtil recursive directory listing functionality.

File: hindsight/tests/utils/test_directory_tree_util.py
"""

import pytest
import tempfile
import os
from pathlib import Path

from hindsight.utils.directory_tree_util import DirectoryTreeUtil


@pytest.fixture
def temp_repo_structure():
    """Create a temporary directory structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create nested directory structure
        # repo/
        #   src/
        #     components/
        #       Button.py
        #       Header.py
        #     utils/
        #       helpers.py
        #     main.py
        #   tests/
        #     test_main.py
        #   README.md
        
        repo = Path(tmpdir)
        
        # Create directories
        (repo / "src" / "components").mkdir(parents=True)
        (repo / "src" / "utils").mkdir(parents=True)
        (repo / "tests").mkdir(parents=True)
        
        # Create files with content
        (repo / "src" / "components" / "Button.py").write_text("# Button component\nclass Button:\n    pass\n")
        (repo / "src" / "components" / "Header.py").write_text("# Header component\nclass Header:\n    pass\n")
        (repo / "src" / "utils" / "helpers.py").write_text("# Helper functions\ndef helper():\n    pass\n")
        (repo / "src" / "main.py").write_text("# Main entry point\nif __name__ == '__main__':\n    pass\n")
        (repo / "tests" / "test_main.py").write_text("# Tests\ndef test_main():\n    pass\n")
        (repo / "README.md").write_text("# Test Repository\n")
        
        yield repo


class TestDirectoryTreeUtilBackwardCompatibility:
    """Tests to ensure backward compatibility with existing callers."""
    
    def test_get_directory_listing_without_new_params(self, temp_repo_structure):
        """Test that calling without new parameters works (backward compatibility)."""
        # This is how existing code calls the method
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src"
        )
        
        assert result is not None
        assert "src/" in result
        assert "|-- " in result
    
    def test_get_directory_listing_default_is_non_recursive(self, temp_repo_structure):
        """Test that default behavior is non-recursive."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src"
        )
        
        # Should show immediate children
        assert "|-- components/" in result
        assert "|-- utils/" in result
        assert "|-- main.py" in result
        
        # Should NOT show nested content (files inside components/)
        assert "Button.py" not in result
        assert "Header.py" not in result
    
    def test_get_directory_listing_explicit_false_matches_default(self, temp_repo_structure):
        """Test that explicit recursive=False matches default behavior."""
        default_result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src"
        )
        
        explicit_result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=False
        )
        
        assert default_result == explicit_result


class TestDirectoryTreeUtilRecursive:
    """Tests for new recursive directory listing functionality."""
    
    def test_get_directory_listing_recursive_shows_nested(self, temp_repo_structure):
        """Test recursive listing shows nested directories and files."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True
        )
        
        # Should show nested structure
        assert "src/" in result
        assert "components/" in result
        assert "Button.py" in result
        assert "Header.py" in result
        assert "utils/" in result
        assert "helpers.py" in result
        assert "main.py" in result
    
    def test_get_directory_listing_recursive_tree_formatting(self, temp_repo_structure):
        """Test recursive listing has proper tree formatting."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True
        )
        
        # Should have tree connectors for nested items
        assert "|   " in result or "    " in result  # Indentation for nested items
    
    def test_get_directory_listing_max_depth_limits_recursion(self, temp_repo_structure):
        """Test max_depth parameter limits recursion depth."""
        shallow_result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True,
            max_depth=0
        )
        
        deep_result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True,
            max_depth=10
        )
        
        # Shallow result should have less content
        assert len(shallow_result) <= len(deep_result)
        
        # Deep result should include nested files
        assert "Button.py" in deep_result
    
    def test_get_directory_listing_file_path_ignores_recursive(self, temp_repo_structure):
        """Test that file paths work correctly with recursive parameter."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src/main.py",
            recursive=True  # Should be ignored for files
        )
        
        # Should return file info, not directory listing
        assert "chars" in result
        assert "main.py" in result
    
    def test_get_directory_listing_invalid_path(self, temp_repo_structure):
        """Test handling of invalid paths."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="nonexistent",
            recursive=True
        )
        
        assert "not found" in result.lower()
    
    def test_get_directory_listing_root_directory(self, temp_repo_structure):
        """Test recursive listing from root directory."""
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path=".",
            recursive=True
        )
        
        # Should include all directories
        assert "src/" in result
        assert "tests/" in result


class TestDirectoryTreeUtilEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_empty_directory(self, temp_repo_structure):
        """Test handling of empty directories."""
        # Create empty directory
        empty_dir = temp_repo_structure / "empty"
        empty_dir.mkdir()
        
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="empty",
            recursive=True
        )
        
        assert "empty/" in result
        assert "no supported files" in result.lower() or len(result.split('\n')) <= 2
    
    def test_directory_with_unsupported_extensions(self, temp_repo_structure):
        """Test that unsupported file extensions are filtered."""
        # Create file with unsupported extension
        (temp_repo_structure / "src" / "data.json").write_text('{"key": "value"}')
        
        result = DirectoryTreeUtil.get_directory_listing(
            repo_path=str(temp_repo_structure),
            relative_path="src",
            recursive=True
        )
        
        # JSON files should not appear (not in DEFAULT_EXTS)
        assert "data.json" not in result
```

### Unit Tests for Tool Handler

Create or update: **`hindsight/tests/core/llm/tools/test_directory_tools.py`**

```python
"""
Tests for list_files tool with recursive parameter.

File: hindsight/tests/core/llm/tools/test_directory_tools.py
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from hindsight.core.llm.tools.tools import Tools
from hindsight.utils.directory_tree_util import DirectoryTreeUtil


@pytest.fixture
def temp_repo_with_structure():
    """Create a temporary repository with directory structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        
        # Create nested structure
        (repo / "src" / "components").mkdir(parents=True)
        (repo / "src" / "components" / "Button.py").write_text("class Button: pass")
        (repo / "src" / "main.py").write_text("if __name__ == '__main__': pass")
        
        yield repo


@pytest.fixture
def tools_instance(temp_repo_with_structure):
    """Create a Tools instance for testing."""
    directory_tree_util = DirectoryTreeUtil()
    
    tools = Tools(
        repo_path=str(temp_repo_with_structure),
        directory_tree_util=directory_tree_util
    )
    
    return tools


class TestListFilesToolRecursiveParameter:
    """Tests for list_files tool recursive parameter support."""
    
    def test_list_files_accepts_recursive_parameter(self, tools_instance):
        """Test that recursive parameter is accepted without error."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src", "recursive": True}
        })
        
        # Should not contain error about unexpected keyword argument
        assert "unexpected keyword argument" not in result.lower()
        assert "error" not in result.lower() or "path not found" in result.lower()
    
    def test_list_files_recursive_true_shows_nested(self, tools_instance):
        """Test that recursive=True shows nested content."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src", "recursive": True}
        })
        
        # Should show nested files
        assert "Button.py" in result
    
    def test_list_files_recursive_false_hides_nested(self, tools_instance):
        """Test that recursive=False hides nested content."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src", "recursive": False}
        })
        
        # Should NOT show nested files
        assert "Button.py" not in result
        # Should show immediate children
        assert "components/" in result
    
    def test_list_files_without_recursive_parameter(self, tools_instance):
        """Test backward compatibility without recursive parameter."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src"}
        })
        
        # Should work without error
        assert "unexpected keyword argument" not in result.lower()
        # Should default to non-recursive (no nested files)
        assert "Button.py" not in result
    
    def test_list_files_handler_signature(self, tools_instance):
        """Test that _handle_list_files accepts recursive parameter."""
        # Direct call to handler
        result = tools_instance._handle_list_files(
            path="src",
            recursive=True,
            reason="Testing recursive parameter"
        )
        
        assert "Button.py" in result


class TestListFilesToolBackwardCompatibility:
    """Tests to ensure backward compatibility with existing tool calls."""
    
    def test_existing_tool_call_format_works(self, tools_instance):
        """Test that existing tool call format continues to work."""
        # Simulate existing tool call without recursive parameter
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"path": "src", "reason": "Exploring directory"}
        })
        
        assert "src" in result
        assert "error" not in result.lower() or "path not found" in result.lower()
    
    def test_directory_alias_still_works(self, tools_instance):
        """Test that 'directory' alias for 'path' still works."""
        result = tools_instance.execute_tool_use({
            "name": "list_files",
            "input": {"directory": "src"}
        })
        
        assert "src" in result
```

### Integration Test

Add to existing integration tests or create: **`hindsight/tests/integration/test_list_files_recursive.py`**

```python
"""
Integration tests for list_files tool recursive functionality.

File: hindsight/tests/integration/test_list_files_recursive.py
"""

import pytest
import tempfile
from pathlib import Path

from hindsight.core.llm.tools.tools import Tools
from hindsight.core.llm.tools.tool_definitions import get_openai_function_schema
from hindsight.utils.directory_tree_util import DirectoryTreeUtil


class TestListFilesRecursiveIntegration:
    """Integration tests for recursive list_files functionality."""
    
    def test_openai_schema_includes_recursive_parameter(self):
        """Test that OpenAI function schema includes recursive parameter."""
        schema = get_openai_function_schema("list_files")
        
        assert schema is not None
        properties = schema["function"]["parameters"]["properties"]
        
        assert "recursive" in properties
        assert properties["recursive"]["type"] == "boolean"
    
    def test_full_tool_execution_flow(self):
        """Test complete tool execution flow with recursive parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            
            # Create structure
            (repo / "src" / "nested").mkdir(parents=True)
            (repo / "src" / "nested" / "deep.py").write_text("# Deep file")
            (repo / "src" / "top.py").write_text("# Top file")
            
            # Create tools instance
            tools = Tools(
                repo_path=str(repo),
                directory_tree_util=DirectoryTreeUtil()
            )
            
            # Execute with recursive=True
            result = tools.execute_tool_use({
                "name": "list_files",
                "input": {"path": "src", "recursive": True}
            })
            
            # Verify nested file is visible
            assert "deep.py" in result
            assert "top.py" in result
```

---

## Files to Create/Modify

### Files to Modify

| File | Changes | Impact |
|------|---------|--------|
| [`hindsight/utils/directory_tree_util.py`](hindsight/utils/directory_tree_util.py) | Add `recursive` and `max_depth` parameters, add helper methods | **Backward compatible** |
| [`hindsight/core/llm/tools/tool_definitions.py`](hindsight/core/llm/tools/tool_definitions.py) | Add `recursive` parameter to `list_files` definition | **Additive** |
| [`hindsight/core/llm/tools/tools.py`](hindsight/core/llm/tools/tools.py) | Update `_handle_list_files` signature and fallback logic | **Backward compatible** |
| [`hindsight/core/llm/tools/directory_tools.py`](hindsight/core/llm/tools/directory_tools.py) | Pass `recursive` parameter to `DirectoryTreeUtil` | **Backward compatible** |

### Files to Create

| File | Purpose |
|------|---------|
| `hindsight/tests/utils/test_directory_tree_util.py` | Unit tests for DirectoryTreeUtil recursive functionality |
| `hindsight/tests/core/llm/tools/test_directory_tools.py` | Unit tests for list_files tool recursive parameter |
| `hindsight/tests/integration/test_list_files_recursive.py` | Integration tests for end-to-end functionality |

---

## Prompt Updates

The tool description in prompts should be updated to reflect the new capability:

### Before:
```
list_files: List directory contents with file sizes. Use this to explore the repository structure.
  - path (required): Path to the directory to list (relative to repository root)
  - reason (optional): Reason for listing this directory
```

### After:
```
list_files: List directory contents with file sizes. Use this to explore the repository structure. Set recursive=true to get a full tree view.
  - path (required): Path to the directory to list (relative to repository root)
  - recursive (optional, default: false): If true, list files recursively in a tree format showing nested directories
  - reason (optional): Reason for listing this directory
```

---

## Summary

| Aspect | Details |
|--------|---------|
| **Root Cause** | `list_files` tool doesn't support `recursive` parameter that LLMs are trying to use |
| **Solution** | Add `recursive` parameter to `DirectoryTreeUtil.get_directory_listing()` and propagate through tool chain |
| **Approach** | Extend existing `DirectoryTreeUtil` class to keep all directory listing logic centralized |
| **Backward Compatibility** | ✅ All changes use default values that preserve existing behavior |
| **Existing Clients** | 11 files use DirectoryTreeUtil, but only 2 call `get_directory_listing()` - both will work unchanged |
| **Files to Modify** | 4 files (directory_tree_util.py, tool_definitions.py, tools.py, directory_tools.py) |
| **Files to Create** | 3 test files (test_directory_tree_util.py, test_directory_tools.py, test_list_files_recursive.py) |
| **Testing** | Comprehensive unit tests for backward compatibility, recursive functionality, and edge cases |
