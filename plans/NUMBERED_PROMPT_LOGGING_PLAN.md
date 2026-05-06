# Numbered Directory Prompt Logging Implementation Plan

## Status: ✅ IMPLEMENTED

**Implementation Date:** 2026-04-16

## Problem Statement

Currently, prompts sent during code analysis are being **overwritten** because:

1. The `Claude` class uses a **global counter** (`_conversation_counter`) that resets when `clear_older_prompts()` is called
2. All prompts are saved to a **flat directory** (`prompts_sent/`) with names like `step1_context_collection.md`, `step2_analysis.md`
3. When analyzing multiple functions, each function's prompts **overwrite** the previous function's prompts

### Evidence from Log

```
Line 112: step1_directory_tree_analysis.md (function 1)
Line 349: step2_context_collection.md (function 1)
Line 383: step3_analysis.md (function 1)
...
Line 750: step4_context_collection.md (function 2 - OVERWRITES step2!)
Line 783: step5_analysis.md (function 2 - OVERWRITES step3!)
```

## Desired Behavior

Each analyzed function/issue should have its prompts saved in a **separate numbered directory**:

```
prompts_sent/
├── 1/                                    # First function analyzed
│   ├── step1_context_collection.md
│   ├── step2_analysis.md
│   └── step3_response_challenger.md
├── 2/                                    # Second function analyzed
│   ├── step1_context_collection.md
│   ├── step2_analysis.md
│   └── step3_response_challenger.md
├── 3/                                    # Third function analyzed
│   └── ...
└── directory_tree_analysis.md            # One-time analysis (not per-function)
```

## Affected Components

### 1. `hindsight/core/llm/llm.py` - Claude class

**Current State:**
- Class variables: `_conversation_counter`, `_prompts_dir`, `_errors_dir`
- `log_complete_conversation()` uses global counter for filenames

**Changes Needed:**
- Add class variable `_current_issue_dir` to track current issue's subdirectory
- Add class method `start_issue_logging(issue_number: int)` to create numbered subdirectory
- Modify `log_complete_conversation()` to use `_current_issue_dir` when set
- Add class method `end_issue_logging()` to reset `_current_issue_dir`

### 2. `hindsight/core/llm/code_analysis.py` - CodeAnalysis class

**Current State:**
- Calls `self.claude.log_complete_conversation()` after each stage
- No awareness of which function is being analyzed

**Changes Needed:**
- Accept `issue_number` parameter in `analyze_function()` or similar entry point
- Call `Claude.start_issue_logging(issue_number)` before analysis
- Call `Claude.end_issue_logging()` after analysis completes

### 3. `hindsight/analyzers/code_analyzer.py` - AnalysisRunner

**Current State:**
- Iterates through functions to analyze
- Calls `CodeAnalysis.analyze_function()` for each
- Calls `Claude.clear_older_prompts()` once at start

**Changes Needed:**
- Track function index during iteration
- Pass function index to `CodeAnalysis.analyze_function()`
- Or call `Claude.start_issue_logging(index)` before each function analysis

### 4. `hindsight/core/llm/diff_analysis.py` - DiffAnalysis class

**Current State:**
- Similar to CodeAnalysis, calls `log_complete_conversation()` after stages
- Used by `git_simple_diff_analyzer`

**Changes Needed:**
- Same pattern as CodeAnalysis - accept issue_number and manage logging scope

### 5. `hindsight/diff_analyzers/git_simple_diff_analyzer.py` - GitSimpleCommitAnalyzer

**Current State:**
- Calls `Claude.setup_prompts_logging()` and `Claude.clear_older_prompts()`
- Iterates through affected functions

**Changes Needed:**
- Track function index during iteration
- Call `Claude.start_issue_logging(index)` before each function analysis

## Implementation Details

### Phase 1: Modify Claude class in `llm.py`

```python
class Claude:
    # Existing class variables
    _conversation_counter = 0
    _prompts_dir = None
    _errors_dir = None
    
    # NEW: Track current issue directory
    _current_issue_number = None
    _current_issue_dir = None
    
    @classmethod
    def start_issue_logging(cls, issue_number: int) -> None:
        """
        Start logging prompts for a specific issue in a numbered subdirectory.
        
        Args:
            issue_number: The issue/function number (1-based)
        """
        if not cls._prompts_dir:
            logger.warning("Prompts directory not set up, cannot start issue logging")
            return
            
        cls._current_issue_number = issue_number
        cls._current_issue_dir = os.path.join(cls._prompts_dir, str(issue_number))
        
        # Create the numbered subdirectory
        ensure_directory_exists(cls._current_issue_dir)
        
        # Reset conversation counter for this issue
        cls._conversation_counter = 0
        
        logger.info(f"Started issue logging in: {cls._current_issue_dir}")
    
    @classmethod
    def end_issue_logging(cls) -> None:
        """
        End logging for the current issue and reset to root prompts directory.
        """
        if cls._current_issue_number is not None:
            logger.info(f"Ended issue logging for issue {cls._current_issue_number}")
        cls._current_issue_number = None
        cls._current_issue_dir = None
    
    def log_complete_conversation(self, final_result: str = None, double_check_info: str = None) -> str:
        """
        Log the complete conversation to a markdown file.
        Uses numbered subdirectory if start_issue_logging() was called.
        """
        # Determine target directory
        target_dir = self._current_issue_dir if self._current_issue_dir else self._prompts_dir
        
        if not target_dir:
            logger.warning("Conversation logging not setup")
            return None
        
        self.__class__._conversation_counter += 1
        # ... rest of existing implementation, using target_dir instead of _prompts_dir
```

### Phase 2: Modify code_analyzer.py

In the function analysis loop (around line 1278):

```python
# Before analyzing each function
Claude.start_issue_logging(function_index + 1)  # 1-based numbering

try:
    # Existing analysis code
    result = self._analyze_function(...)
finally:
    # Always end issue logging, even on error
    Claude.end_issue_logging()
```

### Phase 3: Modify git_simple_diff_analyzer.py

Similar pattern in the affected functions analysis loop:

```python
# Before analyzing each affected function
Claude.start_issue_logging(function_index + 1)

try:
    # Existing analysis code
    result = self._analyze_affected_function(...)
finally:
    Claude.end_issue_logging()
```

### Phase 4: Handle Global Prompts

Some prompts are not per-function (e.g., directory tree analysis). These should:
- Be logged to the root `prompts_sent/` directory (when `_current_issue_dir` is None)
- Use a separate naming convention (e.g., `global_step1_directory_tree_analysis.md`)

## File Changes Summary

| File | Changes |
|------|---------|
| `hindsight/core/llm/llm.py` | Add `start_issue_logging()`, `end_issue_logging()`, modify `log_complete_conversation()` |
| `hindsight/analyzers/code_analyzer.py` | Call `start_issue_logging()`/`end_issue_logging()` around function analysis |
| `hindsight/diff_analyzers/git_simple_diff_analyzer.py` | Call `start_issue_logging()`/`end_issue_logging()` around function analysis |
| `hindsight/core/llm/code_analysis.py` | No changes needed (Claude class handles directory) |
| `hindsight/core/llm/diff_analysis.py` | No changes needed (Claude class handles directory) |

## Testing Plan

1. **Unit Tests:**
   - Test `start_issue_logging()` creates numbered directory
   - Test `end_issue_logging()` resets state
   - Test `log_complete_conversation()` uses correct directory

2. **Integration Tests:**
   - Run code_analyzer with 3+ functions
   - Verify each function has its own numbered directory
   - Verify all stages (context_collection, analysis, response_challenger) are in correct directory

3. **Manual Verification:**
   - Run the same command from the log file
   - Verify prompts are not overwritten
   - Verify directory structure matches expected format

## Rollback Plan

If issues arise:
1. Revert changes to `llm.py`
2. Revert changes to `code_analyzer.py`
3. Revert changes to `git_simple_diff_analyzer.py`

The changes are isolated to prompt logging and don't affect analysis logic.

## Timeline Estimate

- Phase 1 (llm.py): 30 minutes
- Phase 2 (code_analyzer.py): 20 minutes
- Phase 3 (git_simple_diff_analyzer.py): 20 minutes
- Phase 4 (global prompts): 15 minutes
- Testing: 30 minutes

**Total: ~2 hours**
