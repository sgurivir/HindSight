# Function-Level Diff Analysis Implementation Plan

## Overview

This document outlines the implementation plan for enhancing `git_simple_diff_analyzer` to perform function-level analysis of git diffs.

### Key Change: From Chunk-Based to Function-Based Analysis

| Aspect | Current Approach | New Approach |
|--------|------------------|--------------|
| **Unit of Analysis** | Diff chunk (portion of total diff) | Single function |
| **Context Provided** | Raw diff text with file boundaries | Function code with +/- markers + call context |
| **Iteration** | Iterate through diff chunks | Iterate through affected functions |
| **PR Context** | Chunk mentions "this is chunk X of Y" | Function analysis mentions "this is part of a PR changing N files" |

**The fundamental shift**: Instead of splitting the diff by size/file limits and analyzing each chunk, we now:
1. Build a list of files changed by the commit
2. Generate AST for changed files including dependencies
3. Identify "affected" functions (modified or transitively impacted)
4. **Analyze each affected function individually** - similar to how `code_analyzer` works, but with PR/diff context

## Current State Analysis

### Existing Components

| Component | Location | Purpose | Reusability |
|-----------|----------|---------|-------------|
| [`GitSimpleCommitAnalyzer`](../diff_analyzers/git_simple_diff_analyzer.py:51) | `hindsight/diff_analyzers/git_simple_diff_analyzer.py` | Main diff analyzer class | **Modify** - Core orchestration |
| [`CommitExtendedContextProvider`](../diff_analyzers/commit_additional_context_provider.py:44) | `hindsight/diff_analyzers/commit_additional_context_provider.py` | AST generation for changed files | **Reuse** - Already builds scoped AST |
| [`ScopedASTUtil`](../core/lang_util/scoped_ast_util.py:60) | `hindsight/core/lang_util/scoped_ast_util.py` | Scoped AST analysis with dependencies | **Reuse** - Handles dependency discovery |
| [`ASTMerger._add_invoked_by_attributes()`](../core/lang_util/ast_merger.py:130) | `hindsight/core/lang_util/ast_merger.py` | Builds `invoked_by` relationships | **Reuse** - Already computes call graph |
| [`CodeAnalysisRunner._run_code_analysis()`](../analyzers/code_analyzer.py:999) | `hindsight/analyzers/code_analyzer.py` | Function-level LLM analysis | **Reference** - Pattern for function analysis |
| [`DiffAnalysis`](../core/llm/diff_analysis.py:40) | `hindsight/core/llm/diff_analysis.py` | LLM diff analysis with tools | **Modify** - Add function-level mode |
| [`diffAnalysisPrompt.md`](../core/prompts/diffAnalysisPrompt.md:1) | `hindsight/core/prompts/diffAnalysisPrompt.md` | Diff analysis system prompt | **Modify** - Add function context |

### Current Diff Analysis Flow

```
GitSimpleCommitAnalyzer.run_analysis()
    ├── generate_diff()                    # Get unified diff
    ├── _create_diff_chunks()              # Split by file/size limits
    └── analyze_diff_with_llm()
        ├── _analyze_single_chunk()        # Single chunk analysis
        └── _analyze_multiple_chunks()     # Multi-chunk analysis
            └── _analyze_chunk_independently()
                └── DiffAnalysis.analyze_diff()  # LLM call
```

### Existing AST Capabilities

The codebase already has robust AST capabilities:

1. **Scoped AST Generation** ([`ScopedASTUtil.run_scoped_analysis()`](../core/lang_util/scoped_ast_util.py:375)):
   - Analyzes target files + dependencies up to configurable depth
   - Supports C/C++, Swift, Kotlin, Java, Go
   - Generates: `merged_functions.json`, `merged_call_graph.json`, `merged_data_types.json`

2. **Call Graph with Bidirectional Relationships**:
   - `functions_invoked`: List of functions called by each function
   - `invoked_by`: List of functions that call each function (computed by [`ASTMerger._add_invoked_by_attributes()`](../core/lang_util/ast_merger.py:130))

3. **Function Checksums** ([`ASTFunctionSignatureGenerator`](../core/lang_util/ast_function_signature_util.py)):
   - Content-based checksums for change detection
   - Already used for caching in code analysis

---

## Implementation Plan

### Phase 0: Understand Existing code_analyzer (Reference Only)

**Purpose**: Before implementing function-level diff analysis, understand how [`code_analyzer.py`](../analyzers/code_analyzer.py:1) provides context to the LLM. This serves as a reference for the context we should provide in diff analysis.

**CRITICAL**: The `code_analyzer.py` functionality must NOT be affected by these changes. The diff analyzer is a separate analysis path.

**Key Context Provided by code_analyzer** (from [`_process_function_entry()`](../analyzers/code_analyzer.py:672)):

| Context Field | Description | Source |
|---------------|-------------|--------|
| `data_types_used` | List of data types used by the function | Call graph entry |
| `constants_used` | Constants referenced by the function | Call graph entry |
| `functions_invoked` | Functions called by this function | Call graph entry |
| `invoked_by` | Functions that call this function | Call graph entry |
| `function_context` | Full function source code | File content provider |

**What We Learn**:
1. The LLM benefits from knowing what data types a function uses
2. Constants provide important context for understanding function behavior
3. Call relationships (both directions) help understand function's role in the codebase

**How This Applies to Diff Analysis**:
- We should provide similar context for each affected function
- Data types and constants help the LLM understand the function's dependencies
- Call relationships help identify potential ripple effects of changes

**Files to Study (Read-Only)**:
- [`hindsight/analyzers/code_analyzer.py`](../analyzers/code_analyzer.py:672) - See `_process_function_entry()` for context extraction
- [`hindsight/core/llm/code_analysis.py`](../core/llm/code_analysis.py) - See how context is used in prompts

**Verification**: After implementation, run existing code_analyzer tests to ensure no regressions.

---

### Phase 1: Build Changed Files List

**Location**: Modify [`GitSimpleCommitAnalyzer.generate_diff()`](../diff_analyzers/git_simple_diff_analyzer.py:111)

**Current Implementation**: Already extracts `self.changed_files` from git diff.

**Enhancement**: Add method to parse diff and extract per-file line changes.

```python
# New method in GitSimpleCommitAnalyzer
def _extract_changed_lines_per_file(self, diff_content: str) -> Dict[str, Dict[str, List[int]]]:
    """
    Parse diff to extract changed line numbers per file.
    
    Returns:
        Dict mapping file_path -> {
            'added': [line_numbers],      # Lines with + prefix
            'removed': [line_numbers],    # Lines with - prefix  
            'modified_ranges': [(start, end), ...]  # Hunk ranges
        }
    """
```

**Existing Code to Reuse**: [`_split_diff_by_files()`](../diff_analyzers/git_simple_diff_analyzer.py:627) already splits diff by file.

---

### Phase 2: Build AST for Changed Files with Dependencies

**Location**: Enhance [`CommitExtendedContextProvider`](../diff_analyzers/commit_additional_context_provider.py:44)

**Current Implementation**: Already generates scoped AST via [`_generate_scoped_ast_artifacts()`](../diff_analyzers/commit_additional_context_provider.py:361).

**Key Existing Features**:
- Uses [`ScopedASTUtil.run_scoped_analysis()`](../core/lang_util/scoped_ast_util.py:375) with `max_dependency_depth=3`
- Generates `functions`, `call_graph`, `data_types` artifacts
- Caches results per commit via `_COMMIT_AST_CACHE`

**Enhancement**: Ensure AST includes all dependencies even if not directly changed.

```python
# Modify _generate_scoped_ast_artifacts to return structured data
def _generate_scoped_ast_artifacts(self, target_files, clang_args, out_dir, use_subprocess=False) -> Dict[str, Any]:
    """
    Returns:
        {
            'functions': {...},           # function_to_location mapping
            'call_graph': {...},          # Nested call graph with invoked_by
            'data_types': {...},          # Data type definitions
            'merged_functions_file': str, # Path to merged_functions.json
            'merged_call_graph_file': str # Path to merged_call_graph.json
        }
    """
```

**No Changes Needed**: Current implementation already handles this correctly.

---

### Phase 3: Identify Affected Functions

**Location**: New module `hindsight/diff_analyzers/affected_function_detector.py`

**Purpose**: Determine which functions are "affected" by the git change.

**Definition of "Affected"**:
1. **Directly Modified**: Function body contains changed lines
2. **Transitively Affected**: Function invokes or is invoked by a modified function

```python
class AffectedFunctionDetector:
    """
    Detects functions affected by git changes using AST and call graph.
    """
    
    def __init__(self, 
                 call_graph: Dict[str, Any],
                 functions: Dict[str, Any],
                 changed_lines_per_file: Dict[str, Dict[str, List[int]]]):
        """
        Args:
            call_graph: Merged call graph with functions_invoked and invoked_by
            functions: Function to location mapping
            changed_lines_per_file: Output from _extract_changed_lines_per_file()
        """
        self.call_graph = call_graph
        self.functions = functions
        self.changed_lines = changed_lines_per_file
        
    def is_function_modified(self, function_name: str, file_path: str) -> bool:
        """
        Check if function body overlaps with changed lines.
        
        Uses function start/end from functions mapping and compares
        against changed line numbers from diff.
        """
        
    def get_directly_modified_functions(self) -> List[Dict[str, Any]]:
        """
        Return list of functions whose bodies contain changed lines.
        
        Returns:
            List of {
                'function': str,
                'file': str,
                'start': int,
                'end': int,
                'changed_lines': List[int]  # Lines within function that changed
            }
        """
        
    def get_affected_functions(self, include_callers: bool = True, 
                               include_callees: bool = True,
                               max_depth: int = 1) -> List[Dict[str, Any]]:
        """
        Return all affected functions (modified + transitively affected).
        
        Args:
            include_callers: Include functions that call modified functions
            include_callees: Include functions called by modified functions
            max_depth: How many levels of transitive relationships to include
            
        Returns:
            List of {
                'function': str,
                'file': str,
                'start': int,
                'end': int,
                'affected_reason': 'modified' | 'calls_modified' | 'called_by_modified',
                'related_functions': List[str]  # Which modified functions relate to this
            }
        """
        
    def is_function_affected(self, function_name: str) -> bool:
        """
        Helper to check if a specific function is affected.
        Used for filtering during analysis.
        """
```

**Algorithm for `get_affected_functions()`**:

```python
def get_affected_functions(self, include_callers=True, include_callees=True, max_depth=1):
    affected = {}
    
    # Step 1: Find directly modified functions
    modified = self.get_directly_modified_functions()
    for func in modified:
        affected[func['function']] = {
            **func,
            'affected_reason': 'modified',
            'related_functions': []
        }
    
    # Step 2: Find transitively affected (BFS up to max_depth)
    current_level = set(f['function'] for f in modified)
    
    for depth in range(max_depth):
        next_level = set()
        
        for func_name in current_level:
            func_entry = self._find_function_in_call_graph(func_name)
            if not func_entry:
                continue
                
            # Add callers (functions that invoke this one)
            if include_callers:
                for caller in func_entry.get('invoked_by', []):
                    if caller not in affected:
                        affected[caller] = {
                            'function': caller,
                            **self._get_function_location(caller),
                            'affected_reason': 'calls_modified',
                            'related_functions': [func_name]
                        }
                        next_level.add(caller)
                    else:
                        affected[caller]['related_functions'].append(func_name)
            
            # Add callees (functions this one invokes)
            if include_callees:
                for callee in func_entry.get('functions_invoked', []):
                    if callee not in affected:
                        affected[callee] = {
                            'function': callee,
                            **self._get_function_location(callee),
                            'affected_reason': 'called_by_modified',
                            'related_functions': [func_name]
                        }
                        next_level.add(callee)
                    else:
                        affected[callee]['related_functions'].append(func_name)
        
        current_level = next_level
    
    return list(affected.values())
```

---

### Phase 4: Analyze Affected Functions

**Location**: Modify [`GitSimpleCommitAnalyzer`](../diff_analyzers/git_simple_diff_analyzer.py:51)

**New Method**: `_analyze_affected_functions()`

```python
def _analyze_affected_functions(self, affected_functions: List[Dict[str, Any]], 
                                 all_changed_files: List[str]) -> List[Dict[str, Any]]:
    """
    Analyze each affected function using LLM with diff context.
    
    Similar to CodeAnalysisRunner._run_code_analysis() but with diff-specific prompts.
    """
    all_issues = []
    
    for i, func_info in enumerate(affected_functions, 1):
        self.logger.info(f"Analyzing function {i}/{len(affected_functions)}: {func_info['function']}")
        
        # Build function-specific prompt with diff context
        prompt_data = self._build_function_diff_prompt(func_info, all_changed_files)
        
        # Run LLM analysis
        issues = self._analyze_single_function(prompt_data)
        
        if issues:
            all_issues.extend(issues)
    
    return all_issues
```

**New Method**: `_build_function_diff_prompt()`

```python
def _build_function_diff_prompt(self, func_info: Dict[str, Any],
                                  all_changed_files: List[str],
                                  changed_lines_per_file: Dict[str, Dict]) -> Dict[str, Any]:
    """
    Build prompt data for analyzing a single function in diff context.
    
    IMPORTANT: All invoked/invoking functions are included regardless of whether
    they were modified. If a related function was modified, its code will show
    +/- markers on changed lines. This gives the LLM full context of the
    function's call relationships.
    
    NOTE: Data type context is included to match the context provided by code_analyzer.
    This includes data_types_used and constants_used from the call graph, which helps
    the LLM understand the types and constants the function interacts with.
    
    Returns:
        {
            'function': str,
            'file_path': str,
            'code': str,                    # Full function code with +/- markers and line numbers
            'line_numbers': Dict,           # Line number mapping
            'changed_lines': List[int],     # Which lines in function changed
            'data_types_used': List[str],   # Data types used by this function (from call graph)
            'constants_used': Dict[str, Any], # Constants used by this function (from call graph)
            'invoked_functions': List[Dict], # ALL called functions with their code
                                            # Each has: {name, file, code, is_modified, changed_lines}
                                            # Code includes line numbers and +/- markers if modified
            'invoking_functions': List[Dict], # ALL caller functions with their code
                                             # Same structure as invoked_functions
            'diff_context': {
                'all_changed_files': List[str],  # Just file names
                'is_part_of_wider_change': bool,
                'related_changes_summary': str
            }
        }
    """
```

**New Helper Method**: `_format_function_code_with_diff_markers()`

```python
def _format_function_code_with_diff_markers(self,
                                             function_code: str,
                                             start_line: int,
                                             changed_lines: Dict[str, List[int]]) -> str:
    """
    Format function code with line numbers and +/- markers for changed lines.
    
    Args:
        function_code: Raw function source code
        start_line: Starting line number in the file
        changed_lines: {'added': [line_nums], 'removed': [line_nums]}
        
    Returns:
        Formatted code like:
        ```
         45 |     func processData(input: String) -> Result {
         46 | +       guard !input.isEmpty else {
         47 | +           return .failure(.emptyInput)
         48 | +       }
         49 |       let parsed = parser.parse(input)
         50 | -       return parsed
         51 | +       return .success(parsed)
         52 |     }
        ```
        
    Note: Line numbers are always shown. +/- markers only appear on changed lines.
    """
```

---

### Phase 5: Enhanced Prompt for Function-Level Diff Analysis

**Location**: New prompt file `hindsight/core/prompts/functionDiffAnalysisPrompt.md`

**Key Differences from Current Diff Prompt**:

1. **Function-Centric**: Analyze one function at a time
2. **Diff Markers**: Show +/- for changed lines within function
3. **Call Context**: Include invoked/invoking functions with their changes
4. **Wider Change Context**: Mention other files changed (names only)

```markdown
# Function-Level Diff Analysis

You are analyzing a function that is part of a git commit. This function is either:
- **Directly modified**: Contains changed lines
- **Transitively affected**: Calls or is called by modified functions

## Function Being Analyzed

```{language}
{function_code_with_diff_markers}
```

**File**: {file_path}
**Lines**: {start_line}-{end_line}
**Changed Lines**: {changed_line_numbers}

## Data Types Used

The following data types are used by this function (extracted from AST analysis):
{data_types_used_list}

## Constants Used

The following constants are used by this function:
{constants_used_list}

## Related Functions (Call Context)

**Note**: All invoked/invoking functions are included regardless of whether they changed.
If a related function was modified in this diff, its code will show +/- markers on changed lines.

### Functions This Function Calls:
{invoked_functions_with_code}

### Functions That Call This Function:
{invoking_functions_with_code}

## Wider Change Context

This function is part of a commit that modifies {num_files} files:
{list_of_changed_file_names}

## Analysis Instructions

1. Focus on the changed lines (marked with + or -)
2. Consider how changes affect the function's behavior
3. Check if changes are consistent with related functions
4. Report issues ONLY on changed lines when possible

## Line Number Requirements

**CRITICAL**:
- Line numbers shown in the code are from the **NEW file** (after changes)
- When reporting issues, use these line numbers exactly as shown
- Only report issues on lines that are actually changed (+ prefix) to ensure
  PR comments can be placed correctly
- GitHub can only place PR comments on changed lines, so reporting on
  actually changed lines ensures actionable PR comments

{standard_json_output_requirements}
```

---

### Phase 6: Orchestration Changes

**Location**: Modify [`GitSimpleCommitAnalyzer.run_analysis()`](../diff_analyzers/git_simple_diff_analyzer.py:1459)

**New Flow**:

```python
def run_analysis(self) -> str:
    """Enhanced run_analysis with function-level diff analysis."""
    
    # Step 1: Setup (existing)
    self._clear_diff_output_directory()
    self.setup_repository()
    self.determine_commit_order()
    
    # Step 2: Generate diff and extract changed files (existing)
    diff_file_path = self.analysis_dir / f"diff_{...}.diff"
    diff_content = self.generate_diff(str(diff_file_path))
    
    if not diff_content.strip():
        return self.save_results([])
    
    # Step 3: Extract changed lines per file (NEW)
    changed_lines_per_file = self._extract_changed_lines_per_file(diff_content)
    
    # Step 4: Build AST for changed files with dependencies (ENHANCED)
    ast_artifacts = self._build_ast_for_changed_files()
    
    # Step 5: Identify affected functions (NEW)
    detector = AffectedFunctionDetector(
        call_graph=ast_artifacts['call_graph'],
        functions=ast_artifacts['functions'],
        changed_lines_per_file=changed_lines_per_file
    )
    affected_functions = detector.get_affected_functions(
        include_callers=True,
        include_callees=True,
        max_depth=1
    )
    
    self.logger.info(f"Found {len(affected_functions)} affected functions")
    
    # Step 6: Analyze affected functions (NEW)
    issues = self._analyze_affected_functions(
        affected_functions=affected_functions,
        all_changed_files=self.changed_files
    )
    
    # Step 7: Save results and generate report (existing)
    report_path = self.save_results(issues)
    
    return report_path
```

---

## Implementation Checklist

### Phase 0: Understanding (No Code Changes)

- [ ] Study [`code_analyzer.py`](../analyzers/code_analyzer.py:672) `_process_function_entry()` to understand context extraction
- [ ] Study [`code_analysis.py`](../core/llm/code_analysis.py) to understand how context is used in prompts
- [ ] Document any additional context fields that should be included in diff analysis
- [ ] Verify understanding of how `data_types_used` and `constants_used` are extracted from call graph

### New Files to Create

- [ ] `hindsight/diff_analyzers/affected_function_detector.py` - Affected function detection logic
- [ ] `hindsight/core/prompts/functionDiffAnalysisPrompt.md` - Function-level diff prompt
- [ ] `hindsight/tests/diff_analyzers/test_affected_function_detector.py` - Unit tests

### Files to Modify

- [ ] `hindsight/diff_analyzers/git_simple_diff_analyzer.py`:
  - [ ] Add `_extract_changed_lines_per_file()` method
  - [ ] Add `_build_ast_for_changed_files()` method (wrapper around existing)
  - [ ] Add `_analyze_affected_functions()` method
  - [ ] Add `_build_function_diff_prompt()` method
  - [ ] Add `_format_function_code_with_diff_markers()` helper method
  - [ ] Add `_get_all_invoked_functions()` method to get ALL called functions (not just affected)
  - [ ] Add `_get_all_invoking_functions()` method to get ALL caller functions (not just affected)
  - [ ] Add `_extract_data_types_for_function()` method to get data_types_used from call graph (similar to code_analyzer)
  - [ ] Add `_extract_constants_for_function()` method to get constants_used from call graph (similar to code_analyzer)
  - [ ] Modify `run_analysis()` to use new flow

- [ ] `hindsight/core/llm/diff_analysis.py`:
  - [ ] Add `analyze_function_diff()` method for single function analysis

- [ ] `hindsight/diff_analyzers/commit_additional_context_provider.py`:
  - [ ] Ensure AST artifacts are properly structured for affected function detection

### Existing Code to Reuse (No Changes Needed)

- [x] [`ScopedASTUtil.run_scoped_analysis()`](../core/lang_util/scoped_ast_util.py:375) - AST generation with dependencies
- [x] [`ASTMerger._add_invoked_by_attributes()`](../core/lang_util/ast_merger.py:130) - Call graph relationships
- [x] [`CommitExtendedContextProvider._get_or_generate_ast_artifacts()`](../diff_analyzers/commit_additional_context_provider.py:134) - AST caching
- [x] [`PromptBuilder.convert_json_to_comment_format()`](../core/prompts/prompt_builder.py) - Code formatting

### Verification (Post-Implementation)

- [ ] Run existing `code_analyzer` tests to ensure no regressions
- [ ] Verify `code_analyzer.py` functionality is completely unaffected
- [ ] Test diff analysis with sample repositories
- [ ] Verify data type context appears correctly in diff analysis prompts

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         git_simple_diff_analyzer.py                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 1: generate_diff()                                                      │
│   - Get unified diff between commits                                         │
│   - Extract list of changed files                                            │
│   Output: diff_content, self.changed_files                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 2: _extract_changed_lines_per_file()                                    │
│   - Parse diff to get line-level changes                                     │
│   Output: {file: {added: [lines], removed: [lines]}}                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 3: CommitExtendedContextProvider._generate_scoped_ast_artifacts()       │
│   - Build AST for changed files + dependencies (depth=3)                     │
│   - Uses ScopedASTUtil.run_scoped_analysis()                                 │
│   Output: {functions, call_graph, data_types}                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 4: AffectedFunctionDetector.get_affected_functions()                    │
│   - Find functions with changed lines (directly modified)                    │
│   - Find callers/callees of modified functions (transitively affected)       │
│   Output: List[{function, file, affected_reason, related_functions}]         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 5: _analyze_affected_functions()                                        │
│   For each affected function:                                                │
│   ├── _build_function_diff_prompt()                                          │
│   │   - Get function code with +/- markers                                   │
│   │   - Get invoked/invoking function code                                   │
│   │   - Add wider change context (file names only)                           │
│   └── DiffAnalysis.analyze_function_diff()                                   │
│       - LLM analysis with function-level prompt                              │
│   Output: List[issues]                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Step 6: save_results()                                                       │
│   - Apply issue filtering                                                    │
│   - Generate HTML report                                                     │
│   Output: report_path                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Prompt Structure for Function Analysis

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ SYSTEM PROMPT (functionDiffAnalysisPrompt.md)                                │
│   - Analysis instructions                                                    │
│   - Severity guidelines                                                      │
│   - JSON output requirements                                                 │
│   - Tool definitions                                                         │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ USER MESSAGE                                                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ ## Function Being Analyzed                                                   │
│                                                                              │
│ ```swift                                                                     │
│  45 |   func processData(input: String) -> Result {                          │
│  46 | +     guard !input.isEmpty else {                                      │
│  47 | +         return .failure(.emptyInput)                                 │
│  48 | +     }                                                                │
│  49 |       let parsed = parser.parse(input)                                 │
│  50 | -     return parsed                                                    │
│  51 | +     return .success(parsed)                                          │
│  52 |   }                                                                    │
│ ```                                                                          │
│                                                                              │
│ **File**: Sources/DataProcessor.swift                                        │
│ **Changed Lines**: 46-48, 51                                                 │
│ **Affected Reason**: modified                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│ ## Data Types Used by processData                                            │
│ (Similar to context provided by code_analyzer)                               │
│                                                                              │
│ - **String**: Input parameter type                                           │
│ - **Result**: Return type (enum with success/failure cases)                  │
│ - **ParsedData**: Type returned by parser.parse()                            │
│                                                                              │
│ ## Constants Used by processData                                             │
│                                                                              │
│ - **ErrorType.emptyInput**: Error case for empty input validation            │
├─────────────────────────────────────────────────────────────────────────────┤
│ ## Functions Called by processData                                           │
│ (All invoked functions shown; +/- markers indicate changes in this diff)     │
│                                                                              │
│ ### parser.parse (Sources/Parser.swift:23-45) [UNCHANGED]                    │
│ ```swift                                                                     │
│  23 |     func parse(_ input: String) -> ParsedData {                        │
│  24 |         let tokens = tokenize(input)                                   │
│  25 |         let ast = buildAST(tokens)                                     │
│  26 |         return ParsedData(ast: ast)                                    │
│  45 |     }                                                                  │
│ ```                                                                          │
│                                                                              │
│ ### validator.validate (Sources/Validator.swift:12-28) [UNCHANGED]           │
│ ```swift                                                                     │
│  12 |     func validate(_ data: ParsedData) -> Bool {                        │
│  13 |         guard data.ast != nil else { return false }                    │
│  14 |         return checkConstraints(data)                                  │
│  28 |     }                                                                  │
│ ```                                                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ ## Functions That Call processData                                           │
│ (All invoking functions shown; +/- markers indicate changes in this diff)    │
│                                                                              │
│ ### handleRequest (Sources/RequestHandler.swift:78-95) [MODIFIED]            │
│ ```swift                                                                     │
│  78 |     func handleRequest(_ request: Request) {                           │
│  79 |         let result = processData(request.body)                         │
│  80 | +       switch result {                                                │
│  81 | +       case .success(let data): respond(with: data)                   │
│  82 | +       case .failure(let error): respond(with: error)                 │
│  83 | +       }                                                              │
│  84 | -       respond(with: result)                                          │
│  95 |     }                                                                  │
│ ```                                                                          │
│                                                                              │
│ ### batchProcess (Sources/BatchProcessor.swift:45-62) [UNCHANGED]            │
│ ```swift                                                                     │
│  45 |     func batchProcess(_ items: [String]) -> [Result] {                 │
│  46 |         return items.map { processData($0) }                           │
│  62 |     }                                                                  │
│ ```                                                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ ## Wider Change Context                                                      │
│                                                                              │
│ This commit modifies 5 files:                                                │
│ - Sources/DataProcessor.swift (this file)                                    │
│ - Sources/RequestHandler.swift                                               │
│ - Sources/Result.swift                                                       │
│ - Tests/DataProcessorTests.swift                                             │
│ - Tests/RequestHandlerTests.swift                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Backward Compatibility

**No backward compatibility required** per user request. The changes will:

1. Replace the current chunk-based diff analysis entirely
2. Not affect `code_analyzer.py` (separate analysis path)
3. Maintain the same result schema for issues

---

## Testing Strategy

### Unit Tests

1. **`test_affected_function_detector.py`**:
   - Test `is_function_modified()` with various line overlap scenarios
   - Test `get_directly_modified_functions()` with sample call graphs
   - Test `get_affected_functions()` with transitive relationships
   - Test edge cases: empty diffs, no call graph, circular dependencies

2. **`test_changed_lines_extraction.py`**:
   - Test `_extract_changed_lines_per_file()` with various diff formats
   - Test handling of renamed files, binary files, new files, deleted files

### Integration Tests

1. **End-to-end diff analysis**:
   - Run on sample repositories with known changes
   - Verify affected functions are correctly identified
   - Verify issues are reported on correct lines

---

## Performance Considerations

1. **AST Caching**: Already implemented via `_COMMIT_AST_CACHE` in [`CommitExtendedContextProvider`](../diff_analyzers/commit_additional_context_provider.py:26)

2. **Function Analysis Batching**: Consider analyzing multiple small functions in one LLM call if they're in the same file

3. **Dependency Depth**: Default `max_dependency_depth=3` may be excessive for large repos; consider making configurable

4. **Parallel Analysis**: Future enhancement - analyze independent functions in parallel

---

## Open Questions

1. **Depth of Transitive Analysis**: Should we include functions 2+ levels away from modified code?
   - **Recommendation**: Start with depth=1, make configurable

2. **Handling Large Functions**: What if a modified function is >500 lines?
   - **Recommendation**: Use existing `MAX_FUNCTION_BODY_LENGTH` limit, skip or truncate

3. **Test File Handling**: Should test files be analyzed differently?
   - **Recommendation**: Apply same analysis, but could add test-specific categories

4. **Binary/Generated Files**: How to handle changes to generated code?
   - **Recommendation**: Skip via existing extension filtering

---

## Timeline Estimate

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase 1: Changed Lines Extraction | 2 hours | None |
| Phase 2: AST Enhancement | 1 hour | Phase 1 |
| Phase 3: Affected Function Detector | 4 hours | Phase 2 |
| Phase 4: Function Analysis | 4 hours | Phase 3 |
| Phase 5: Prompt Development | 2 hours | Phase 4 |
| Phase 6: Orchestration | 3 hours | All above |
| Testing & Debugging | 4 hours | All above |
| **Total** | **~20 hours** | |

---

## Summary

This implementation plan leverages existing AST and call graph infrastructure to enable function-level diff analysis.

### The Core Paradigm Shift

**Current Approach (Chunk-Based)**:
```
Total Diff → Split into chunks by size/file limits → Analyze each chunk → Merge issues
```

**New Approach (Function-Based)**:
```
Total Diff → Build AST → Find affected functions → Analyze each function (with PR context) → Merge issues
```

The key insight is that we're moving from **"analyze portions of the diff"** to **"analyze functions that are affected by the diff"**. This is similar to how [`code_analyzer`](../analyzers/code_analyzer.py:1) works (one function at a time), but with the crucial addition of:

1. **PR/Diff Context**: Each function analysis knows it's part of a wider change
2. **Change Markers**: Function code shows +/- for modified lines
3. **Related Changes**: LLM sees which other files changed (names only to save context)

### Important: Related Function Context

When analyzing an affected function, the prompt includes **ALL** invoked and invoking functions from the call graph, not just those that were modified. This gives the LLM complete context of the function's relationships. For each related function:

- **Line numbers are always shown** for all code
- **+/- markers are shown** only on lines that were actually changed in the diff
- **[MODIFIED] or [UNCHANGED] label** indicates whether the function was touched by the diff

This approach ensures the LLM understands both the direct changes and how they might affect unchanged code paths.

### Key New Components

1. **[`AffectedFunctionDetector`](../diff_analyzers/affected_function_detector.py)**: Identifies which functions are impacted by changes
2. **Function-level prompts**: Provide focused context for each function while maintaining PR awareness
3. **Enhanced orchestration**: Iterates through affected functions instead of diff chunks

The approach maintains the existing result schema while providing more precise, context-aware analysis of code changes.
