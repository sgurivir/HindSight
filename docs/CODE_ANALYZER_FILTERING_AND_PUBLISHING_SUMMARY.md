# Code Analyzer Filtering and Publishing Summary

## Overview

The code analyzer implements a multi-stage filtering system to reduce noise and improve the quality of reported issues. This document summarizes the filtering stages, how results are published, and how JSON files on disk are managed.

## Filtering Architecture

### Three-Level Filtering System

The filtering is orchestrated by [`UnifiedIssueFilter`](hindsight/issue_filter/unified_issue_filter.py) which applies three levels of filtering:

#### Level 1: Category-Based Filter (ALLOWLIST)
- **Implementation**: [`CategoryBasedFilter`](hindsight/issue_filter/category_filter.py)
- **Type**: ALLOWLIST (only specified categories are kept)
- **Allowed Categories**: `logicBug`, `performance`
- **Behavior**: Issues with categories NOT in the allowlist are dropped
- **Dropped Issues**: Saved to `dropped_issues/level1_category_filter/`

#### Level 2: LLM-Based Trivial Issue Filter
- **Implementation**: [`TrivialIssueFilter`](hindsight/issue_filter/trivial_issue_filter.py)
- **Purpose**: Uses LLM to identify and filter out trivial/obvious issues
- **Dropped Issues**: Saved to `dropped_issues/level2_trivial_filter/`

#### Level 3: Response Challenger (Senior Engineer Verification)
- **Implementation**: [`ResponseChallenger`](hindsight/issue_filter/response_challenger.py)
- **Purpose**: Final LLM-based validation to ensure issues are worth pursuing
- **Dropped Issues**: Saved to `dropped_issues/level3_response_challenger/`

## Results Publishing System

### Publisher-Subscriber Pattern

The system uses a publisher-subscriber pattern for managing analysis results:

- **Publisher**: [`CodeAnalysisResultsPublisher`](hindsight/results_store/code_analysis_publisher.py)
- **Subscriber**: [`CodeAnalysysResultsLocalFSSubscriber`](hindsight/results_store/code_analysys_results_local_fs_subscriber.py)

### Key Methods

#### `add_result()`
- Publishes a new result to all subscribers
- Triggers `on_result_added()` which writes JSON to disk
- Used for both new analysis results and republished cached results

#### `index_existing_result()`
- Indexes existing results for cache lookup ONLY
- Does NOT add to the publisher's results collection
- Used during normal analysis run to enable checksum-based caching

#### `load_existing_result_for_report()`
- Loads results directly into the publisher's results collection
- Used by `--generate-report-from-existing-issues` mode
- Makes results available via `get_results()` for report generation

## JSON File Management During Filtering

### When Filtering Happens

1. **During Analysis Loop** (lines 1335-1361 in code_analyzer.py):
   - New analysis results: Full 3-level filtering applied before publishing
   - Cached results: Only Level 1 (Category) filtering applied to avoid LLM API calls

2. **During Report Generation** (`_generate_report()` method):
   - Deduplication applied
   - FP CSV Filter applied (if `--false-positives-csv` provided)
   - Final writeback to JSON files (only for full analysis runs)

### The `_writeback_final_issues_to_json()` Method

**Location**: Lines 1526-1681 in [`code_analyzer.py`](hindsight/analyzers/code_analyzer.py:1526)

**Purpose**: Synchronizes JSON files on disk with the final filtered issue set after all filtering stages (dedup, FP CSV filter, category filter).

**Key Behavior**:
- Only called with `writeback_final_issues=True` during **full analysis runs** (line 3032)
- NOT called during `--generate-report-from-existing-issues` mode
- Removes issues from JSON files that didn't survive the full pipeline
- Archives removed issues to `dropped_issues/final_filter/`

### Partial Filtering Logic

**Question**: What happens when a function has multiple issues but only some are filtered out?

**Answer**: The system correctly handles partial filtering:

1. Each function's JSON file contains a `results` array with multiple issues
2. When filtering is applied, only the issues that pass the filter remain in the array
3. The `_writeback_final_issues_to_json()` method:
   - Builds a set of surviving `(checksum, issue_title)` pairs
   - For each JSON file, keeps only issues whose `(checksum, issue_title)` is in the surviving set
   - Archives dropped issues individually to `dropped_issues/final_filter/`
   - Writes back the JSON file with only the surviving issues

**Code Reference** (lines 1613-1624):
```python
kept = [
    r
    for r in original_results
    if (checksum, (r.get("issue") or "").strip()) in surviving
]
dropped_in_file = [
    r
    for r in original_results
    if (checksum, (r.get("issue") or "").strip()) not in surviving
]
```

## Two Execution Modes

### 1. Full Analysis Run (`runner.run()`)

**Flow**:
1. Load existing results for cache lookup (`load_existing_results()` - indexes only)
2. For each function:
   - Check cache by checksum
   - If cached: Apply Level 1 filter, republish
   - If new: Analyze, apply full 3-level filter, publish
3. Generate report with `writeback_final_issues=True`
4. `_writeback_final_issues_to_json()` syncs disk with final filtered results

**JSON File State After**: Contains only issues that survived ALL filtering stages

### 2. Report Regeneration (`--generate-report-from-existing-issues`)

**Flow**:
1. Load existing results directly into publisher (`load_existing_results_for_report()`)
2. Apply Level 1 (Category) filter only - no LLM calls
3. Update JSON files in `code_analysis/` to remove dropped issues
4. Generate report with `writeback_final_issues=False`
5. `_writeback_final_issues_to_json()` is NOT called

**JSON File State After**: Contains issues that survived Level 1 filter only

## Bug: Report Regeneration Can Show MORE Issues

### Scenario
User reported that `--generate-report-from-existing-issues` shows MORE issues than the original run.

### Root Cause: `num_functions_to_analyze` Limit Not Applied During Report Regeneration

**This is a confirmed bug in the code.**

#### Full Analysis Run Flow:
1. `_initialize_publisher_subscriber()` calls `load_existing_results()` (line 993)
2. `load_existing_results()` only **indexes** results for cache lookup - does NOT add to `_results`
3. Analysis loop (lines 1162-1251) processes functions from call graph:
   - **Limited by `num_functions_to_analyze`** (default: 300)
   - Only functions passing `_should_analyze_function()` are processed
   - Results are added to `_results` via `add_result()`
4. Report generated from `_results` (which has at most `num_functions_to_analyze` entries)

#### Report Regeneration Flow:
1. `_initialize_publisher_subscriber_for_report()` calls `load_existing_results_for_report()` (line 1030)
2. `load_existing_results_for_report()` **directly adds ALL JSON files** to `_results`
3. **NO LIMIT** is applied - all JSON files in `code_analysis/` are loaded
4. Report generated from `_results` (which may have MORE than `num_functions_to_analyze` entries)

### Why This Causes More Issues

If you have run multiple analysis sessions over time:
- Session 1: Analyzed functions A, B, C (300 functions)
- Session 2: Analyzed functions D, E, F (300 different functions due to sorting/filtering changes)
- JSON files on disk: A, B, C, D, E, F (600 files total)

**Full analysis run**: Reports issues from 300 functions (limited by `num_functions_to_analyze`)
**Report regeneration**: Reports issues from ALL 600 functions (no limit)

### Additional Contributing Factors

1. **JSON files not cleaned up between runs**:
   - Old analysis results accumulate in `code_analysis/` directory
   - Each run adds new results but doesn't remove old ones

2. **`_writeback_final_issues_to_json()` only removes issues, not files**:
   - It removes filtered issues from existing JSON files
   - It does NOT delete JSON files for functions that weren't in the current run

3. **Different function selection between runs**:
   - Functions are sorted by length (longest first) at line 1094
   - If code changes, different functions may be selected
   - Old JSON files for previously-analyzed functions remain on disk

### Fix Recommendations

1. **Apply `num_functions_to_analyze` limit during report regeneration**:
   - In `load_existing_results_for_report()`, limit the number of results loaded
   - Or apply the limit after loading in `generate_report_from_existing_issues()`

2. **Clean up stale JSON files**:
   - During full analysis, remove JSON files for functions not in current analysis
   - Or track which functions were analyzed and only load those during report regeneration

3. **Add a `--clean-old-results` flag**:
   - Allow users to explicitly clean up old results before running analysis

## File Locations

| Component | Location |
|-----------|----------|
| Code Analyzer | `hindsight/analyzers/code_analyzer.py` |
| Unified Issue Filter | `hindsight/issue_filter/unified_issue_filter.py` |
| Category Filter | `hindsight/issue_filter/category_filter.py` |
| Trivial Issue Filter | `hindsight/issue_filter/trivial_issue_filter.py` |
| Response Challenger | `hindsight/issue_filter/response_challenger.py` |
| Publisher | `hindsight/results_store/code_analysis_publisher.py` |
| FS Subscriber | `hindsight/results_store/code_analysys_results_local_fs_subscriber.py` |

## Key Code References

| Functionality | File | Line Numbers |
|--------------|------|--------------|
| Unified filter application (new results) | code_analyzer.py | 1335-1361 |
| Level 1 filter on cached results | code_analyzer.py | 1226-1235 |
| Writeback final issues to JSON | code_analyzer.py | 1526-1681 |
| Report generation | code_analyzer.py | 1687-1925 |
| Report from existing issues | code_analyzer.py | 2047-2218 |
| Full run calls writeback | code_analyzer.py | 3032 |
| Load results for cache | code_analysys_results_local_fs_subscriber.py | 70-124 |
| Load results for report | code_analysys_results_local_fs_subscriber.py | 126-180 |
