# Code Analyzer Pipeline

## Overview

The code analyzer (`hindsight/analyzers/code_analyzer.py`) implements a multi-stage pipeline that takes a repository from raw source code to a filtered, deduplicated HTML report of real bugs and performance issues. This document covers the full pipeline: execution stages, the LLM analysis process, the three-level filtering architecture, results publishing, JSON file management, execution modes, known bugs, and performance characteristics.

---

## Pipeline Stages

The full pipeline (when invoked via `runner.run()` + report generation) has five stages:

```
Stage 1: Initialization & Configuration
Stage 2: Directory Structure Index + Classification & File Count
Stage 3: AST Call Graph Generation / Loading
Stage 4: Code Analysis (LLM + Filtering per function)
Stage 5: Report Generation (Dedup, FP CSV filter, Writeback, HTML)
```

### Stage 1: Initialization & Configuration (~2 seconds)

| Component | Time |
|-----------|------|
| Configuration loading from JSON | ~0.2s |
| Token tracker setup | ~0.1s |
| File system results cache initialization | ~0.7s |
| API key retrieval | ~1.0s |

### Stage 2: Directory Structure Index + Classification & File Count (~19 seconds)

> In code this is two separate steps: **Step 0** (`_ensure_directory_structure_index`) always runs first, then **Step 1.5** (`_run_directory_classification_and_file_count_check`) runs LLM-based classification and the file count check.

| Sub-stage | Duration | Notes |
|-----------|----------|-------|
| Directory structure index building (Step 0) | ~0.5s | Pre-step, always runs |
| Static directory analysis | ~3s | Finds directories to exclude |
| **LLM-based directory classification** | **~12s** | Single LLM call |
| File count check | ~0.5s | Exits early if count exceeds limit |

### Stage 3: AST Call Graph Generation / Loading (~1 second when reusing)

- If existing AST files are present and `--force-recreate-ast` is not set, they are reused.
- Otherwise AST call graphs are generated from source (Swift/ObjC/C++).
- The merged call graph is loaded into memory; functions are sorted by length (longest first) for prioritized analysis.

### Stage 4: Code Analysis Phase (~18 minutes typical, dominated by LLM calls)

For each function (up to `num_functions_to_analyze`, default 300):

1. **Cache check** — compute checksum; if a result exists on disk for the same checksum, skip LLM call.
2. **LLM analysis** (new functions only) — send function + call tree context to the analysis LLM.
3. **Three-level filtering** — applied immediately after each result before publishing (see [Filtering Architecture](#filtering-architecture)).
4. **Publish result** — write to disk via publisher-subscriber.

**Cache performance (example run, Safari repo):**

| Type | Count | Time |
|------|-------|------|
| Cache hits (skipped) | 525 | ~0.3s total |
| New LLM analyses | 34 | ~425s total |

**Cache hit rate: 93.9%**

**Per-function LLM analysis time:**

| Metric | Value |
|--------|-------|
| Total LLM analysis time | 425.39 seconds |
| Average per function | 12.89 seconds |
| Min / Max | 2.26s / 34.36s |

### Stage 5: Report Generation

Called after `runner.run()` returns via `runner._generate_report(report_config, writeback_final_issues=True)`.

| Sub-stage | Notes |
|-----------|-------|
| Issue deduplication (`IssueDeduper`) | ChromaDB embeddings; removes exact + semantic duplicates |
| FP CSV filter (`FpCsvFilter`) | Only runs when `--false-positives-csv` is provided |
| Final JSON writeback (`_writeback_final_issues_to_json`) | Removes post-filter dropped issues from per-function JSON files; archives to `dropped_issues/final_filter/` |
| HTML report generation | Produces the final HTML report |

> **Not called** during `--generate-report-from-existing-issues` mode — `_writeback_final_issues_to_json` is skipped (`writeback_final_issues=False`).

### Stage Summary: Time Distribution

Based on a profiled Safari repository run (March 24, 2026):

| Stage | Duration | % of Total |
|-------|----------|------------|
| **1. Initialization** | ~2s | 0.2% |
| **2. Directory Index + Classification** | ~19s | 1.8% |
| └─ Directory structure index (Step 0) | ~0.5s | <0.1% |
| └─ LLM directory analysis | ~12s | 1.1% |
| **3. AST Loading** | ~1s | 0.1% |
| **4. Code Analysis** | ~18 min | 97.9% |
| └─ Cache lookups (525) | ~0.3s | <0.1% |
| └─ LLM primary analysis (34) | ~425s | 39% |
| └─ Level 2 LLM filtering | ~30s | 2.8% |
| └─ Level 3 Response Challenger | ~120s | 11% |
| └─ Client-side processing | ~5s | 0.5% |
| **5. Report Generation** | variable | — |
| └─ Issue deduplication | variable | depends on issue count |
| └─ FP CSV filter (optional) | variable | only with --false-positives-csv |
| └─ Final JSON writeback | ~seconds | removes post-filter dropped issues |
| └─ HTML generation | ~seconds | — |

---

## Stage 4 Deep Dive: LLM Analysis Process

During Stage 4, each function is analyzed by an LLM that has access to contextual tools. The LLM follows this process and tool priority order:

### Tool Priority

1. `getDirectoryListing` → Always first; check file sizes before reading.
2. `getImplementation` → Retrieve full class/struct/enum implementation.
3. `getSummaryOfFile` → Quick understanding of large files.
4. `readFile` → For specific small files when other tools fail.
5. `runTerminalCmd` → Last resort, for searching or exploration.

### Tool Decision Flow

```
Need context? → getDirectoryListing (check size)
├── Is it a class/struct/enum? → getImplementation
├── Need quick context only? → getSummaryOfFile
├── Is it a small standalone file? → readFile
└── Need to search/explore? → runTerminalCmd
```

**File Size Guidelines:**

| Size | Approach |
|------|----------|
| < 5,000 chars | Safe to read with `readFile` |
| 5,000–20,000 chars | Read with caution; consider `getSummaryOfFile` first |
| 20,000–80,000 chars | Use `getSummaryOfFile` instead of `readFile` |
| > 80,000 chars | Always use `getSummaryOfFile`; never `readFile` |

### Analysis Process Steps

1. Parse JSON input → identify **primary function**.
2. Review file summaries and directory context.
3. Use tools efficiently to confirm relevant class or file structure.
4. Analyze **only** primary function logic.
5. Skip contextual or speculative issues.
6. Report structured findings (with line numbers and confidence ≥ 0.8).

### Reliable `grep` Usage

| Do | Don't |
|----|-------|
| `grep -rn 'functionName' --include='*.swift' .` | `grep 'class MyClass'` (multi-word) |
| Use exact single-word patterns | `grep 'enum.*Type'` (regex) |
| Run two separate greps for OR patterns | `grep 'word1\|word2'` |
| `grep -r 'pattern' --include='*.swift' dir/` | `grep 'pattern' dir/*.swift` |

> Always wrap search patterns in single quotes to prevent shell interpretation.

---

## Filtering Architecture

All filtering is orchestrated by [`UnifiedIssueFilter`](hindsight/issue_filter/unified_issue_filter.py).

### Three-Level Filtering System

#### Level 1: Category-Based Filter (ALLOWLIST)

- **Implementation**: [`CategoryBasedFilter`](hindsight/issue_filter/category_filter.py)
- **Type**: ALLOWLIST — only specified categories are kept
- **Allowed Categories**: `logicBug`, `performance`
- **Behavior**: Issues with categories NOT in the allowlist are dropped instantly (no LLM call)
- **Time**: Instant (pure Python)
- **Dropped Issues**: Saved to `dropped_issues/level1_category_filter/`

#### Level 2: LLM-Based Trivial Issue Filter

- **Implementation**: [`LLMBasedFilter`](hindsight/issue_filter/llm_filter.py)
- **Purpose**: Uses LLM to identify and filter out trivial/obvious issues
- **Time**: ~2–3 seconds per batch
- **Triggered**: Only when issues pass Level 1
- **Dropped Issues**: Saved to `dropped_issues/level2_trivial_filter/`

#### Level 3: Response Challenger (Senior Engineer Verification)

- **Implementation**: [`LLMResponseChallenger`](hindsight/issue_filter/response_challenger.py)
- **Purpose**: Final LLM-based validation using tool calls to verify issues against source code
- **Time**: ~15–40 seconds per issue batch
- **Dropped Issues**: Saved to `dropped_issues/level3_response_challenger/`

### When Filtering Is Applied

1. **During the Analysis Loop** ([code_analyzer.py:1335–1361](hindsight/analyzers/code_analyzer.py#L1335)):
   - **New analysis results**: Full 3-level filtering applied before publishing.
   - **Cached results**: Only Level 1 (Category) filtering applied to avoid unnecessary LLM API calls.

2. **During Report Generation** (`_generate_report()`):
   - Deduplication applied.
   - FP CSV Filter applied (if `--false-positives-csv` provided).
   - Final writeback to JSON files (full runs only).

### Partial Filtering Logic

When a function has multiple issues and only some are filtered out, the system handles this correctly:

1. Each function's JSON file contains a `results` array with multiple issues.
2. When filtering is applied, only issues that pass survive in the array.
3. `_writeback_final_issues_to_json()` ([code_analyzer.py:1526–1681](hindsight/analyzers/code_analyzer.py#L1526)):
   - Builds a set of surviving `(checksum, issue_title)` pairs.
   - For each JSON file, keeps only issues in the surviving set.
   - Archives dropped issues individually to `dropped_issues/final_filter/`.
   - Writes back the JSON file with only surviving issues.

```python
# code_analyzer.py lines 1613-1624
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

---

## Results Publishing System

### Publisher-Subscriber Pattern

- **Publisher**: [`CodeAnalysisResultsPublisher`](hindsight/results_store/code_analysis_publisher.py)
- **Subscriber**: [`CodeAnalysysResultsLocalFSSubscriber`](hindsight/results_store/code_analysys_results_local_fs_subscriber.py)

### Key Publisher Methods

#### `add_result()`
- Publishes a new result to all subscribers.
- Triggers `on_result_added()` which writes JSON to disk.
- Used for both new analysis results and republished cached results.

#### `index_existing_result()`
- Indexes existing results for cache lookup **only**.
- Does NOT add to the publisher's results collection.
- Used during normal analysis to enable checksum-based caching.

#### `load_existing_result_for_report()`
- Loads results directly into the publisher's results collection.
- Used by `--generate-report-from-existing-issues` mode.
- Makes results available via `get_results()` for report generation.

---

## Two Execution Modes

### 1. Full Analysis Run (`runner.run()`)

**Flow:**
1. Load existing results for cache lookup (`load_existing_results()` — indexes only, line 993).
2. For each function:
   - Check cache by checksum.
   - If cached: Apply Level 1 filter, republish.
   - If new: Analyze, apply full 3-level filter, publish.
3. Generate report with `writeback_final_issues=True` (line 3032).
4. `_writeback_final_issues_to_json()` syncs disk with final filtered results.

**JSON File State After:** Contains only issues that survived ALL filtering stages.

### 2. Report Regeneration (`--generate-report-from-existing-issues`)

**Flow:**
1. Load existing results directly into publisher (`load_existing_results_for_report()`, line 1030).
2. Apply Level 1 (Category) filter only — no LLM calls.
3. Update JSON files in `code_analysis/` to remove dropped issues.
4. Generate report with `writeback_final_issues=False`.
5. `_writeback_final_issues_to_json()` is NOT called.

**JSON File State After:** Contains issues that survived Level 1 filter only.

---

## Known Bug: Report Regeneration Can Show MORE Issues

### Scenario
`--generate-report-from-existing-issues` can show more issues than the original full run.

### Root Cause: `num_functions_to_analyze` Limit Not Applied During Report Regeneration

**Full Analysis Run:**
1. `_initialize_publisher_subscriber()` calls `load_existing_results()` (line 993) — indexes only, does NOT add to `_results`.
2. Analysis loop (lines 1162–1251) processes functions from the call graph, **limited by `num_functions_to_analyze`** (default: 300).
3. Report generated from `_results` (at most `num_functions_to_analyze` entries).

**Report Regeneration:**
1. `_initialize_publisher_subscriber_for_report()` calls `load_existing_results_for_report()` (line 1030).
2. **ALL JSON files** in `code_analysis/` are loaded directly into `_results` — **no limit applied**.
3. Report generated from `_results` (may contain more than `num_functions_to_analyze` entries).

### Why This Causes More Issues

If multiple analysis sessions have run over time:
- Session 1: Analyzed functions A, B, C (300 functions)
- Session 2: Analyzed functions D, E, F (300 different functions)
- JSON files on disk: A–F (600 files total)

**Full run** → reports from 300 functions. **Report regeneration** → reports from all 600 functions.

### Additional Contributing Factors

1. **JSON files accumulate** — old results are not removed between runs.
2. **`_writeback_final_issues_to_json()` removes issues, not files** — JSON files for functions not in the current run stay on disk.
3. **Different function selection between runs** — functions sorted by length (line 1094); code changes may yield a different set of 300 functions.

### Fix Recommendations

1. Apply `num_functions_to_analyze` limit in `load_existing_results_for_report()` or in `generate_report_from_existing_issues()`.
2. During full analysis, remove JSON files for functions not in the current run.
3. Add a `--clean-old-results` flag to let users explicitly clean up stale results.

---

## Performance Data

### LLM Token Usage (example Safari run)

| Metric | Value |
|--------|-------|
| Total input tokens | 472,536 |
| Total output tokens | 24,921 |
| **Total tokens** | **497,457** |
| Functions analyzed | 33 |
| Avg tokens per function | ~15,074 |

### LLM Request Rate (example Safari run)

| Metric | Value |
|--------|-------|
| Mean | 4.11 requests/minute |
| Median | 4.00 requests/minute |
| Std Dev | 1.70 requests/minute |
| Min / Max | 1 / 7 requests/minute |
| Mean inter-request interval | 13.77 seconds |

### Client-Side Python Overhead

| Component | Estimated Time |
|-----------|----------------|
| Configuration loading & validation | ~0.5s |
| File system cache indexing | ~0.7s |
| Directory tree building | ~0.3s |
| File count enumeration | ~0.4s |
| AST call graph loading | ~0.01s |
| Function filtering & sorting | ~0.3s |
| Cache lookups | ~0.3s |
| Result publishing & file I/O | ~2s |
| **Total client-side overhead** | **~5 seconds** |

### Key Optimization Insights

1. **LLM calls dominate (~95% of time)** — primary analysis ~425s, L2+L3 filtering ~150s, directory classification ~12s.
2. **Caching is highly effective** — 93.9% hit rate in the example run; ~6,700 seconds saved.
3. **Issue filtering adds ~150s** but significantly reduces false positives — the trade-off is acceptable.
4. **Parallelization opportunity** — overlapping primary analysis with L2/L3 filtering could reduce total time by ~55% (from 17.7 to ~8 min), but doubles peak request rate (7 → 11 req/min).

---

## Parallelization Analysis

### Current Sequential Flow

```
Primary(N) → L2(N) → L3(N) → Primary(N+1) → L2(N+1) → L3(N+1) → ...
```

### Proposed Parallel Flow

```
Primary(N) → Primary(N+1) → Primary(N+2) → ...
              ↓
           L2(N) → L3(N) → L2(N+1) → L3(N+1) → ...
```

Analysis of function N+1 starts as soon as L2+L3 filtering of function N begins.

| Metric | Sequential | Parallel | Change |
|--------|------------|----------|--------|
| **Total Duration** | 17.7 min | 8.0 min | **2.21x faster** |
| **Mean req/min** | 3.67 | 7.56 | +106% |
| **Peak req/min** | 7 | 11 | +57% |

**Implementation requirements:** `asyncio` or thread pools, a filtering queue, rate limiting, and graceful partial-failure handling.

---

## File Locations

| Component | Location |
|-----------|----------|
| Code Analyzer | `hindsight/analyzers/code_analyzer.py` |
| Unified Issue Filter | `hindsight/issue_filter/unified_issue_filter.py` |
| Category Filter | `hindsight/issue_filter/category_filter.py` |
| LLM-Based Filter | `hindsight/issue_filter/llm_filter.py` |
| Response Challenger | `hindsight/issue_filter/response_challenger.py` |
| Publisher | `hindsight/results_store/code_analysis_publisher.py` |
| FS Subscriber | `hindsight/results_store/code_analysys_results_local_fs_subscriber.py` |
| LLM Analysis Prompt | `hindsight/core/prompts/detailedAnalysisProcess.md` *(runtime LLM prompt — do not delete)* |

## Key Code References

| Functionality | File | Lines |
|--------------|------|-------|
| Unified filter application (new results) | code_analyzer.py | 1335–1361 |
| Level 1 filter on cached results | code_analyzer.py | 1226–1235 |
| Writeback final issues to JSON | code_analyzer.py | 1526–1681 |
| Report generation | code_analyzer.py | 1687–1925 |
| Report from existing issues | code_analyzer.py | 2047–2218 |
| Full run calls writeback | code_analyzer.py | 3032 |
| Load results for cache | code_analysys_results_local_fs_subscriber.py | 70–124 |
| Load results for report | code_analysys_results_local_fs_subscriber.py | 126–180 |

---

## Appendix: Log Analysis Commands

```bash
# Count LLM analysis times
grep "Total time taken" log.txt | awk -F': ' '{print $NF}'

# Count cache hits vs misses
grep "ANALYSIS SKIPPED" log.txt | wc -l
grep "NO existing result found" log.txt | wc -l

# Extract token usage
grep "TOKEN USAGE SUMMARY" log.txt

# Calculate requests per minute
grep "Analysis iteration 1/" log.txt | awk -F'[ ,]' '{print $1, $2}'
```
