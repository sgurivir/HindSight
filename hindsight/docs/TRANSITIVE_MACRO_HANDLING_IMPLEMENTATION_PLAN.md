# Transitive Macro Handling Implementation Plan

## Status: ✅ IMPLEMENTED

**Implementation Date:** 2026-03-07

## Summary

This document outlines the plan to implement "ambivalent to macros" mode in Hindsight's AST generation, similar to how StaticIntelligence's `LangUtil` handles transitive macro definitions.

## Current State Analysis

### StaticIntelligence's Approach (LangUtil)

StaticIntelligence handles transitive macro definitions through a **dual-pass "ambivalent to macros" mode**:

1. **Pass 1**: Build AST **without** macro expansion
2. **Pass 2**: Build AST **with** macro expansion (auto-detected or user-provided)
3. **Merge**: Combine results from both passes, deduplicating by function/file/line

Key files:
- [`LangUtil.py`](/Users/sgurivireddy/src/StaticIntelligence/staticintelligence/framework/core/lib/LangUtil.py) - Orchestrates dual-pass analysis
- [`ClangUtil.py`](/Users/sgurivireddy/src/StaticIntelligence/staticintelligence/framework/core/lib/ClangUtil.py) - Handles macro detection and flag creation

Key features:
- `detect_preprocessor_macros_with_derived()` - Detects derived macros like `#define MACRO !OTHER_MACRO`
- `create_macro_flags_excluding_derived()` - Excludes derived macros from explicit definition (they're computed from base macros)
- `expand_macros` parameter - Enables dual-pass mode (default: True)

### Hindsight's Current State (CASTUtil)

Hindsight already has the **building blocks** but lacks the **dual-pass orchestration**:

**Already Implemented:**
- ✅ `detect_preprocessor_macros()` - Detects macros in `#if`, `#ifdef`, `#ifndef` directives
- ✅ `detect_preprocessor_macros_with_derived()` - Detects derived macros and their dependencies
- ✅ `create_macro_flags_excluding_derived()` - Creates clang flags excluding derived macros
- ✅ `create_macro_flags()` - Creates clang -D flags from macro set
- ✅ `get_cached_preprocessor_macros_with_derived()` - Caches macro detection results
- ✅ Merge functions for registries and call graphs

**NOT Implemented:**
- ❌ Dual-pass AST building (with and without macros)
- ❌ `expand_macros` parameter in analysis functions
- ❌ Automatic merging of dual-pass results

## Implementation Plan

### Phase 1: Add Dual-Pass Support to CASTUtil

**File:** `hindsight/core/lang_util/cast_util.py`

#### 1.1 Add `build_function_registry_ambivalent()` method

```python
@staticmethod
def build_function_registry_ambivalent(repo_root, source_files, clang_args, out_path, 
                                        macros: List[str] = None,
                                        expand_macros: bool = True):
    """
    Build function registry with optional dual-pass macro handling.
    
    Args:
        repo_root: Path to repository root
        source_files: List of source files to analyze
        clang_args: Base clang arguments
        out_path: Output path for registry JSON
        macros: Optional list of macros to expand (auto-detect if empty list, skip if None)
        expand_macros: If True, build AST twice and merge results (default: True)
    """
    if expand_macros:
        # PASS 1: Without macro expansion
        registry1 = CASTUtil._build_function_registry_single_pass(
            repo_root, source_files, clang_args, []
        )
        
        # PASS 2: With macro expansion
        if macros is not None:
            if macros:
                macro_flags = create_macro_flags(set(macros))
            else:
                # Auto-detect macros, excluding derived ones
                detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
        else:
            macro_flags = []
        
        registry2 = CASTUtil._build_function_registry_single_pass(
            repo_root, source_files, clang_args, macro_flags
        )
        
        # MERGE: Combine results
        registry = CASTUtil._merge_function_registries(registry1, registry2)
    else:
        # Single pass without macros
        registry = CASTUtil._build_function_registry_single_pass(
            repo_root, source_files, clang_args, []
        )
    
    # Write output and return
    # ... (existing output logic)
```

#### 1.2 Add similar methods for other registry types

- `build_data_types_registry_ambivalent()`
- `build_constants_registry_ambivalent()`
- `build_forward_call_graph_ambivalent()`
- `build_data_type_use_ambivalent()`
- `build_constants_usage_ambivalent()`

### Phase 2: Update ASTUtil to Support Ambivalent Mode

**File:** `hindsight/core/lang_util/ast_util.py`

#### 2.1 Add `expand_macros` parameter to `run_full_analysis()`

```python
@staticmethod
def run_full_analysis(repo: Path,
                      include_dirs: Set[str],
                      ignore_dirs: Set[str],
                      clang_args: List[str],
                      out_dir: Path,
                      merged_symbols_out: Path,
                      merged_graph_out: Path,
                      merged_data_types_out: Path,
                      use_subprocess: bool = False,
                      max_dependency_depth: int = 3,
                      enable_preprocessor_macros: List[str] = None,
                      expand_macros: bool = True) -> None:  # NEW PARAMETER
```

#### 2.2 Update `ClangAnalysisHelper` to support ambivalent mode

**File:** `hindsight/core/lang_util/ast_util_language_helper.py`

Add `expand_macros` parameter to `run_clang_analysis()` and propagate to CASTUtil methods.

### Phase 3: Add CLI Support

**File:** `hindsight/core/lang_util/ast_util.py` (main function)

Add command-line argument:
```python
parser.add_argument("--expand-macros", type=lambda x: x.lower() in ('true', '1', 'yes'),
                    default=True, nargs='?', const=True,
                    help="Build AST twice (with and without macros) and merge results. "
                         "Captures all code paths regardless of macro state. "
                         "Enabled by default. Pass 'false' to disable.")
```

### Phase 4: Configuration File Support

Update configuration schema to support:
```json
{
  "expand_macros": true,
  "preprocessor_macros": ["TARGET_OS_IPHONE=1", "DEBUG"]
}
```

## Files to Modify

1. **`hindsight/core/lang_util/cast_util.py`**
   - Add `*_ambivalent()` methods for all registry/graph building functions
   - Update existing methods to call ambivalent versions

2. **`hindsight/core/lang_util/ast_util.py`**
   - Add `expand_macros` parameter to `run_full_analysis()`
   - Add CLI argument for ambivalent mode
   - Update main() to pass parameter

3. **`hindsight/core/lang_util/ast_util_language_helper.py`**
   - Update `ClangAnalysisHelper.run_clang_analysis()` to support ambivalent mode

4. **`hindsight/core/lang_util/ast_worker.py`**
   - Update worker to handle ambivalent mode in subprocess execution

## Testing Plan

1. **Unit Tests:**
   - Test `detect_preprocessor_macros_with_derived()` with various macro patterns
   - Test `create_macro_flags_excluding_derived()` excludes derived macros correctly
   - Test merge functions properly deduplicate entries

2. **Integration Tests:**
   - Test dual-pass analysis on a codebase with conditional compilation
   - Verify functions in both `#if` and `#else` branches are captured
   - Compare results with StaticIntelligence output

3. **Performance Tests:**
   - Measure overhead of dual-pass vs single-pass
   - Ensure caching prevents redundant macro detection

## Estimated Effort

- Phase 1: 4-6 hours (CASTUtil changes)
- Phase 2: 2-3 hours (ASTUtil integration)
- Phase 3: 1 hour (CLI support)
- Phase 4: 1 hour (Config support)
- Testing: 2-3 hours

**Total: ~10-14 hours**

## Risks and Mitigations

1. **Performance Impact**: Dual-pass doubles AST parsing time
   - Mitigation: Make ambivalent mode optional, default to True for completeness

2. **Memory Usage**: Two registries in memory during merge
   - Mitigation: Process files in batches if memory becomes an issue

3. **Merge Conflicts**: Same function with different signatures in different passes
   - Mitigation: Use (function_name, file, start_line) as deduplication key

## Implementation Summary

### Completed Changes

The following files were modified to implement the "ambivalent to macros" mode:

#### 1. `hindsight/core/lang_util/cast_util.py`
- Added `expand_macros` parameter to all public build methods:
  - [`build_function_registry()`](hindsight/core/lang_util/cast_util.py)
  - [`build_data_types_registry()`](hindsight/core/lang_util/cast_util.py)
  - [`build_constants_registry()`](hindsight/core/lang_util/cast_util.py)
  - [`build_forward_call_graph()`](hindsight/core/lang_util/cast_util.py)
  - [`build_data_type_use()`](hindsight/core/lang_util/cast_util.py)
  - [`build_constants_usage()`](hindsight/core/lang_util/cast_util.py)
- Added private `_build_*_ambivalent()` methods for dual-pass processing

#### 2. `hindsight/core/lang_util/ast_util.py`
- Added `expand_macros` parameter to [`run_full_analysis()`](hindsight/core/lang_util/ast_util.py:276)
- Added `expand_macros` parameter to [`_run_full_analysis_in_process()`](hindsight/core/lang_util/ast_util.py:425)
- Added `--expand-macros` CLI argument in [`main()`](hindsight/core/lang_util/ast_util.py:616)
- Propagated parameter through all code paths

#### 3. `hindsight/core/lang_util/ast_util_language_helper.py`
- Added `expand_macros` parameter to [`ClangAnalysisHelper.run_clang_analysis()`](hindsight/core/lang_util/ast_util_language_helper.py:39)
- Propagated parameter to all CASTUtil method calls

#### 4. `hindsight/core/lang_util/ast_process_manager.py`
- Added `expand_macros` parameter to [`run_full_analysis()`](hindsight/core/lang_util/ast_process_manager.py:42)
- Added parameter to subprocess configuration

#### 5. `hindsight/core/lang_util/ast_worker.py`
- Added `expand_macros` parameter to [`_run_full_analysis()`](hindsight/core/lang_util/ast_worker.py:105)
- Added `expand_macros` parameter to [`_run_clang_analysis()`](hindsight/core/lang_util/ast_worker.py:396)
- Propagated parameter to ClangAnalysisHelper

### Usage

#### CLI Usage
```bash
# Default behavior: expand_macros=True (captures all code paths)
python -m hindsight.core.lang_util.ast_util --repo /path/to/project

# Explicitly enable expand_macros mode
python -m hindsight.core.lang_util.ast_util --repo /path/to/project --expand-macros

# Disable expand_macros mode (single-pass analysis)
python -m hindsight.core.lang_util.ast_util --repo /path/to/project --expand-macros false
```

#### Programmatic Usage
```python
from hindsight.core.lang_util.ast_util import ASTUtil

# Default: expand_macros=True (recommended)
ASTUtil.run_full_analysis(
    repo=repo_path,
    include_dirs=set(),
    ignore_dirs={".git", "build"},
    clang_args=[],
    out_dir=out_dir,
    merged_symbols_out=symbols_out,
    merged_graph_out=graph_out,
    merged_data_types_out=data_types_out
    # expand_macros defaults to True
)

# Explicitly disable expand_macros mode
ASTUtil.run_full_analysis(
    repo=repo_path,
    include_dirs=set(),
    ignore_dirs={".git", "build"},
    clang_args=[],
    out_dir=out_dir,
    merged_symbols_out=symbols_out,
    merged_graph_out=graph_out,
    merged_data_types_out=data_types_out,
    expand_macros=False  # Single-pass analysis only
)
```

### Analyzer Integration

Both `code_analyzer.py` and `trace_analyzer.py` automatically use `expand_macros=True` when building AST through the `_generate_ast_call_graph()` method in `analysis_runner.py`. This ensures all code paths are captured regardless of macro state.

### How It Works

When `expand_macros=True`:

1. **Pass 1 (Without Macros)**: Build AST without any macro definitions
   - Captures code in `#else` branches
   - Captures code when macros are undefined

2. **Pass 2 (With Macros)**: Build AST with auto-detected macros (excluding derived macros)
   - Captures code in `#if` branches
   - Captures code when macros are defined

3. **Merge**: Combine results from both passes
   - Deduplicate by (function_name, file, start_line)
   - Union of all discovered functions, data types, and call relationships

### Transitive Macro Detection

The implementation uses `detect_preprocessor_macros_with_derived()` to identify:
- **Base macros**: Directly used in `#if`, `#ifdef`, `#ifndef` directives
- **Derived macros**: Defined in terms of other macros (e.g., `#define MACRO !OTHER_MACRO`)

Derived macros are excluded from explicit definition because they are computed from base macros.

## Conclusion

The infrastructure for transitive macro handling already exists in Hindsight. The main work is adding the dual-pass orchestration layer that StaticIntelligence's `LangUtil` provides. This will ensure that code in both branches of conditional compilation (`#if`/`#else`) is captured in the AST.

**Implementation Status: ✅ Complete**
