# Performance Analyzer — Implementation Plan

## Overview

A new analyzer (`perf_analyzer`) that identifies in-place performance optimization opportunities to reduce **power**, **CPU**, and **memory** consumption. It operates on the existing AST data and call graph, analyzing one call path at a time using a two-stage LLM process (context collection → analysis).

Unlike `code_analyzer` which analyzes individual functions in isolation, the perf analyzer walks call paths through the graph — because performance issues often emerge from how functions interact (e.g., repeated allocations across a call chain, redundant computations propagated through layers, unnecessary serialization/deserialization at boundaries).

---

## Architecture

```
                    ┌─────────────────────────┐
                    │  PerfAnalysisRunner      │
                    │  (CLI entry point)       │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  CallPathEnumerator      │
                    │  (graph traversal)       │
                    └────────────┬────────────┘
                                 │  yields call paths
                    ┌────────────▼────────────┐
                    │  PerfAnalyzer            │
                    │  (orchestrator)          │
                    └────────────┬────────────┘
                                 │
               ┌─────────────────┼─────────────────┐
               │                                   │
   ┌───────────▼───────────┐          ┌───────────▼───────────┐
   │  Stage A:             │          │  Stage B:             │
   │  Context Collection   │          │  Performance Analysis │
   │  (gather code along   │          │  (identify perf       │
   │   the call path)      │          │   issues + suggest    │
   └───────────────────────┘          │   optimizations)      │
                                      └───────────────────────┘
```

---

## Key Design Decisions

### A) AST Consumption

Reuses the existing `RepoAstIndex` singleton:
- `merged_functions.json` — function bodies, signatures, locations
- `merged_call_graph.json` — caller→callee edges
- `merged_defined_classes.json` — type definitions

The call graph is loaded into a `CallGraph` instance (from `call_graph_util.py`) for traversal.

### B) One Call Path at a Time

Each analysis unit is a **call path** — an ordered sequence of functions from a root (or entry point) down to a leaf (or up to the depth limit).

**Path Selection Strategy:**

1. Build the `CallGraph` from `merged_call_graph.json`
2. Compute levels from bottom (`compute_levels_from_bottom()`)
3. Start from **root nodes** (nodes with no incoming edges) or user-specified entry points
4. Enumerate paths via DFS with depth limit (configurable, default = 6)
5. Each path = `[root, child1, child2, ..., leaf]` with minimum depth of 3 hops

**Minimum depth filter (reduces shallow/trivial analysis):**
- `MIN_PATH_DEPTH = 3` — skip paths shorter than 3 hops (too shallow for cross-function perf patterns)
- `MAX_PATH_DEPTH = 8` — cap to prevent combinatorial explosion
- Skip paths composed entirely of trivial functions (< N lines)

**Path prioritization** (analyze highest-value paths first):
- Prefer paths containing loops, allocations, or I/O operations (detected via simple AST heuristics)
- Prefer deeper paths (more optimization surface)
- Prefer paths through hot modules (user-configurable list)

### C) Edge Coloring & Duplicate Detection

#### Edge Coloring — Prevent Repeated Analysis

The core problem: if paths `[A→B→C→D]` and `[A→B→C→E]` are both analyzed, the shared sub-path `A→B→C` gets full LLM treatment twice. Edge coloring eliminates this waste.

**Mechanism:**

A global `analyzed_edges: Set[Tuple[str, str]]` tracks which caller→callee relationships have been fully analyzed. A path only qualifies for analysis if it contains at least one **uncolored edge**.

```python
class EdgeColoringTracker:
    """Tracks analyzed edges to prevent redundant path analysis."""

    def __init__(self):
        self.analyzed_edges: Set[Tuple[str, str]] = set()

    def has_novel_edges(self, path: List[str]) -> bool:
        """Returns True if the path contains at least one unanalyzed edge."""
        for i in range(len(path) - 1):
            edge = (path[i], path[i + 1])
            if edge not in self.analyzed_edges:
                return True
        return False

    def get_novel_edges(self, path: List[str]) -> List[Tuple[str, str]]:
        """Returns the unanalyzed edges in this path."""
        return [
            (path[i], path[i + 1])
            for i in range(len(path) - 1)
            if (path[i], path[i + 1]) not in self.analyzed_edges
        ]

    def mark_analyzed(self, path: List[str]) -> None:
        """Color all edges in the path after successful analysis."""
        for i in range(len(path) - 1):
            self.analyzed_edges.add((path[i], path[i + 1]))

    def get_coverage_stats(self) -> Dict[str, int]:
        """Report how many edges have been analyzed."""
        return {
            "analyzed_edges": len(self.analyzed_edges),
        }
```

**How it integrates with path enumeration:**

1. Paths are sorted by priority score (descending)
2. Iterate through sorted paths in order
3. For each path, check `has_novel_edges()`
4. If yes → submit for analysis; after completion → `mark_analyzed()`
5. If no → skip entirely (all edges already covered by previous paths)

This naturally favors longer, higher-priority paths (analyzed first) and prunes shorter overlapping paths that would be redundant.

**Concurrency note:** Since multiple paths analyze concurrently, edge coloring is checked at dispatch time (before submitting to worker pool) and updated at completion. A path dispatched concurrently with an overlapping path may still run — this is acceptable because the context cache (below) ensures Stage A work isn't duplicated at the function level.

---

#### Per-Function Context Cache — Reuse Across Paths

Even when a path has novel edges, many of its constituent functions may have already been context-collected during earlier path analyses. The **function context cache** stores the Stage A output per function so it can be injected directly into subsequent paths without re-running LLM context collection.

**Storage:**

```
llm_artifacts/<repo_name>/perf_context_cache/<function_checksum>.json
```

Each cached entry contains:

```json
{
    "function_name": "ClassName.methodName",
    "file_path": "relative/path.swift",
    "checksum": "abc123...",
    "context": {
        "body": "func doThing() { ... }",
        "data_types_used": ["MyStruct", "NetworkConfig"],
        "resource_patterns": {
            "allocations": ["creates NSMutableArray in loop"],
            "io_operations": [],
            "synchronization": ["acquires self.lock"],
            "loops": ["for-in over items (line 34)"]
        },
        "threading_context": "called on main queue",
        "constants_and_globals": ["MAX_RETRY_COUNT = 5"]
    },
    "collected_at": "2026-05-07T10:30:00Z"
}
```

**How it works in Stage A:**

```python
class PerfContextCollector:
    """Stage A with per-function caching."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def get_cached_context(self, func_name: str, checksum: str) -> Optional[Dict]:
        """Load cached context if checksum matches."""
        cache_file = self.cache_dir / f"{checksum}.json"
        if cache_file.exists():
            cached = json.loads(cache_file.read_text())
            if cached.get("checksum") == checksum:
                return cached["context"]
        return None

    def save_context(self, func_name: str, checksum: str, context: Dict) -> None:
        """Persist function context for reuse."""
        cache_file = self.cache_dir / f"{checksum}.json"
        cache_file.write_text(json.dumps({
            "function_name": func_name,
            "checksum": checksum,
            "context": context,
            "collected_at": datetime.utcnow().isoformat() + "Z",
        }))

    async def collect_path_context(self, path: List[str], ...) -> Dict:
        """
        Collect context for an entire path.
        Uses cache for already-collected functions, runs LLM only for novel ones.
        """
        cached_functions = {}
        novel_functions = []

        for func_name in path:
            checksum = get_function_checksum(func_name)
            cached = self.get_cached_context(func_name, checksum)
            if cached:
                cached_functions[func_name] = cached
            else:
                novel_functions.append(func_name)

        if novel_functions:
            # Run Stage A LLM only for uncached functions
            # Provide cached context as "already known" in the prompt
            new_context = await self._run_llm_context_collection(
                path=path,
                novel_functions=novel_functions,
                pre_collected=cached_functions,
            )
            # Cache the newly collected function contexts
            for func_name, ctx in new_context.items():
                checksum = get_function_checksum(func_name)
                self.save_context(func_name, checksum, ctx)
                cached_functions[func_name] = ctx

        return self._build_path_context_bundle(path, cached_functions)
```

**Key insight:** The LLM in Stage A is told which functions already have cached context (injected as pre-collected data in the prompt). It only needs to explore and collect context for the novel functions — plus gather any **edge-specific context** (how data flows between functions, threading transitions at call boundaries) that is path-dependent and not cacheable per-function.

**What's cached per-function vs. what's path-specific:**

| Cached per-function | Computed per-path (not cached) |
|---------------------|-------------------------------|
| Function body & signature | Data flow between functions |
| Data types used | Threading transitions at boundaries |
| Resource patterns (alloc, I/O, locks, loops) | Cumulative allocation pressure |
| Constants & globals referenced | Call frequency / hot-path indicators |
| Threading context (where it's typically called) | Edge-specific coupling patterns |

---

#### Additional Dedup Layers

4. **Issue-level dedup** — After analysis, deduplicate findings by `(file_path, function_name, issue_type, line_range_overlap)`. If two paths produce the same finding for the same location, keep only the one with richer context.

5. **Cross-run dedup** — Use the publisher-subscriber caching pattern (same as `code_analyzer`). Cache key = `sha256(path_functions_checksums)`. If a path's constituent functions haven't changed, skip re-analysis.

### D) HTML Report

Reuse `hindsight/report/report_generator.py` with a new `analysis_type="Performance Analysis"`. The output schema per finding:

```json
{
    "file_path": "relative/path.swift",
    "file_name": "file.swift",
    "function_name": "ClassName.methodName",
    "line_number": "45-52",
    "severity": "high|medium|low",
    "issue": "One-sentence summary of the performance issue",
    "description": "Detailed explanation of why this is a performance problem",
    "suggestion": "Concrete optimization recommendation with code sketch",
    "category": "performance",
    "issueType": "cpu|memory|power|io",
    "call_path": ["FuncA", "FuncB", "FuncC"],
    "estimated_impact": "Reduces allocations by ~N per call"
}
```

Additional report enhancements (perf-specific):
- Group findings by `issueType` (CPU / Memory / Power / I/O)
- Show the call path visually in each finding card
- Summary statistics: findings by severity × issue type matrix

### E) Two-Stage Process (Context Collection → Analysis)

**Stage A — Context Collection (perfContextCollectionProcess.md)**

The LLM gathers all code context along the call path without performing analysis:
- Read each function body in the path (already provided in prompt)
- Explore adjacent code: data types, constants, class hierarchies used along the path
- Identify resource patterns: allocations, locks, I/O, caches, loops, dispatch queues
- Output: a self-contained context bundle

```json
{
    "call_path": ["ModuleA.entryPoint", "ServiceB.process", "HelperC.transform"],
    "functions": {
        "ModuleA.entryPoint": { "body": "...", "file": "...", "line": 42 },
        ...
    },
    "data_types_used": { ... },
    "resource_patterns": {
        "allocations": [...],
        "io_operations": [...],
        "synchronization": [...],
        "loops": [...]
    },
    "additional_context": { ... }
}
```

**Stage B — Performance Analysis (perfAnalysisProcess.md)**

Receives the context bundle. Identifies performance issues:
- Redundant computations across the path
- Unnecessary allocations (object churn, autorelease pressure)
- Excessive copying / serialization-deserialization at boundaries
- Lock contention or unnecessary synchronization
- Main-thread work that could be offloaded
- Cache misses or missing caching opportunities
- Inefficient data structures for the access pattern
- Power-wasteful patterns (wake locks, polling, unnecessary timers)

Output: JSON array of issue objects (schema above).

### F) Tools

**Stage A tools** (full set — for gathering context):

| Tool | Source | Purpose |
|------|--------|---------|
| `readFile` | FileToolsMixin | Read source files |
| `getFileContentByLines` | FileToolsMixin | Read specific line ranges |
| `checkFileSize` | FileToolsMixin | Size check before reading |
| `runTerminalCmd` | TerminalToolsMixin | grep/find for patterns |
| `list_files` | DirectoryToolsMixin | Directory listing |
| `inspectDirectoryHierarchy` | DirectoryToolsMixin | Directory tree |
| `getImplementation` | ImplementationToolsMixin | Lookup class/function impl from registry |
| `getSummaryOfFile` | ImplementationToolsMixin | File summary |

**Stage B tools** (restricted — analysis only):

| Tool | Purpose |
|------|---------|
| `readFile` | Re-read specific code if needed |
| `runTerminalCmd` | Targeted grep for related patterns |
| `getFileContentByLines` | Focused line ranges |

This follows the same pattern as `code_analyzer` (`with_stage_b_tools()`).

### G) Code Provided via MCP Server

Use `AnalysisMCPServer` for unified tool dispatch:
- Inject `call_graph_data` + `implementations` into the MCP server
- The MCP server routes tool calls to the appropriate handler
- Stage B gets a restricted `with_stage_b_tools()` instance

The MCP server also hosts `CodeNavigationServer` which provides:
- `getCallees` — who does this function call?
- `getCallers` — who calls this function?
- `getImplementation` — get the full body of a function/class

This is critical for the perf analyzer since the LLM needs to navigate the call graph during context collection.

### H) Async Concurrency

Use the existing `run_worker_pool()` infrastructure:

```python
async def analyze_all_paths(paths: List[CallPath], config: PerfConfig) -> List[Dict]:
    rate_limiter = RateLimiter(max_requests_per_minute=config.rpm_limit)
    
    results = await run_worker_pool(
        items=paths,
        worker_fn=analyze_single_path,
        max_workers=config.max_concurrent_analyses,  # default: 4
        rate_limiter=rate_limiter,
        on_result=on_path_complete,
        on_error=on_path_error,
    )
    return results
```

Each path analysis (Stage A + Stage B) runs as an independent async task. The rate limiter prevents exceeding LLM API quotas.

---

## New Files

| File | Purpose |
|------|---------|
| `hindsight/analyzers/perf_analyzer.py` | Orchestrator: `PerfAnalyzer` + `PerfAnalysisRunner` |
| `hindsight/analyzers/call_path_enumerator.py` | Graph traversal, path enumeration, prioritization |
| `hindsight/analyzers/edge_coloring_tracker.py` | Edge coloring state + novel-edge filtering |
| `hindsight/core/llm/perf_analysis.py` | `PerfAnalysis` class — two-stage LLM orchestration |
| `hindsight/core/llm/perf_context_cache.py` | Per-function context cache (read/write/invalidate) |
| `hindsight/core/llm/iterative/perf_context_analyzer.py` | Stage A iterative analyzer (JSON validation) |
| `hindsight/core/llm/iterative/perf_analysis_analyzer.py` | Stage B iterative analyzer (JSON validation) |
| `hindsight/core/prompts/perfContextCollectionProcess.md` | Stage A system prompt |
| `hindsight/core/prompts/perfAnalysisProcess.md` | Stage B system prompt |
| `tests/analyzers/test_perf_analyzer.py` | Unit tests |
| `tests/analyzers/test_call_path_enumerator.py` | Path enumeration tests |
| `tests/analyzers/test_edge_coloring_tracker.py` | Edge coloring tests |
| `tests/core/llm/test_perf_analysis.py` | LLM orchestration tests |
| `tests/core/llm/test_perf_context_cache.py` | Context cache tests |

## Modified Files

| File | Change |
|------|--------|
| `hindsight/analyzers/analysis_runner.py` | Register `perf_analyzer` as available analyzer type (additive only — new elif branch) |

## Backward Compatibility — Existing Analyzers Unaffected

The perf analyzer is a **new, parallel code path**. It does NOT modify any shared base classes, existing analyzer logic, or report generation behavior.

**Guarantees:**

1. **No base class changes** — `BaseAnalyzer`, `LLMBasedAnalyzer`, `AnalysisRunner` are not modified. `PerfAnalyzer` subclasses `LLMBasedAnalyzer` and overrides only its own methods.
2. **No shared tool changes** — `Tools`, `ToolsBase`, and all mixins remain unchanged. The perf analyzer uses them as-is.
3. **No prompt changes** — Existing prompts (`contextCollectionProcess.md`, `analysisProcess.md`, `diffContextCollectionProcess.md`, `diffAnalysisProcess.md`) are untouched. New perf-specific prompts live in separate files.
4. **No LLM client changes** — `Claude`, `ConversationState`, and iterative analyzers remain unchanged. New iterative analyzer subclasses are added in new files.
5. **No report generation changes** — `generate_html_report()` already accepts an `analysis_type` parameter. The perf analyzer passes `"Performance Analysis"` — no modifications to the function itself.
6. **Additive-only registration** — `analysis_runner.py` gets a new `elif` branch for `"perf_analyzer"` that imports and instantiates `PerfAnalyzer`. All other branches are untouched.
7. **New modules only** — `call_path_enumerator.py`, `edge_coloring_tracker.py`, `perf_context_cache.py`, and `perf_analysis.py` are entirely new files. No existing imports or modules are reorganized.
8. **Shared singletons are read-only** — `RepoAstIndex` is consumed (read-only) by the perf analyzer, same as by `code_analyzer`. No writes, no cache invalidation of shared state.
9. **Independent output directory** — Perf results are written to `llm_artifacts/<repo>/perf_analysis/` and `llm_artifacts/<repo>/perf_context_cache/`, separate from existing `code_analysis/` and `context_bundles/` directories.

---

## Call Path Enumerator — Detail

```python
class CallPathEnumerator:
    """Enumerates analysis-worthy call paths from the repo call graph."""

    def __init__(
        self,
        call_graph: CallGraph,
        merged_functions: Dict,
        min_path_depth: int = 3,
        max_path_depth: int = 8,
        max_paths: int = 500,
        entry_points: Optional[Set[str]] = None,
        min_function_lines: int = 5,
    ):
        ...

    def enumerate_paths(self) -> List[List[str]]:
        """
        Returns deduplicated, prioritized list of call paths.
        Each path is a list of function names [root, ..., leaf].
        """
        ...

    def _is_subset_of_existing(self, path: List[str], existing: Set[FrozenSet]) -> bool:
        """Skip paths that are strict subsets of already-selected paths."""
        ...

    def _compute_path_priority(self, path: List[str]) -> float:
        """Score a path by estimated optimization potential."""
        ...

    def _get_path_checksum(self, path: List[str]) -> str:
        """Deterministic hash of the functions in the path (for caching)."""
        ...
```

**Priority scoring heuristics:**
- +1 per function containing a loop keyword (`for`, `while`, `repeat`)
- +2 if path crosses module/framework boundaries (different directories)
- +1 per function > 30 lines
- +3 if any function name suggests I/O (`fetch`, `load`, `read`, `write`, `download`, `upload`)
- +2 if any function name suggests allocation-heavy patterns (`create`, `build`, `copy`, `serialize`)

---

## Perf Analyzer — Detail

```python
class PerfAnalyzer(LLMBasedAnalyzer):
    """Analyzes call paths for performance optimization opportunities."""

    def name(self) -> str:
        return "perf_analyzer"

    def initialize(self, config: Mapping[str, Any]) -> None:
        ...

    async def analyze_path(self, path: List[str], mcp_server=None) -> Optional[List[Dict]]:
        """Analyze a single call path. Returns list of perf findings."""
        ...

    def finalize(self) -> None:
        """Deduplicate findings, generate HTML report."""
        ...


class PerfAnalysisRunner(UnifiedIssueFilterMixin, ReportGeneratorMixin, AnalysisRunner):
    """CLI runner for the performance analyzer."""

    async def run(self) -> None:
        # 1. Load AST + call graph
        # 2. Enumerate paths via CallPathEnumerator
        # 3. Run analyze_all_paths (async worker pool)
        # 4. Deduplicate findings
        # 5. Generate HTML report
        ...
```

---

## Prompt Design (Highlights)

### perfContextCollectionProcess.md — Key Instructions

```
You are a performance context collector. Your job is to gather all code
context along a call path WITHOUT performing analysis.

Given a call path [A → B → C → D], you will:
1. Read the body of each function in the path (provided below)
2. Use tools to explore: data types, class hierarchies, constants
3. Identify resource patterns: allocations, locks, I/O, loops, caches
4. Note data flow: what data passes between functions in the path
5. Identify threading/dispatch context: which queue/thread each runs on

Output a JSON context bundle. Do NOT analyze or suggest optimizations.
```

### perfAnalysisProcess.md — Key Instructions

```
You are a performance engineer reviewing a call path for optimization
opportunities. You are looking for issues that cause unnecessary:
- CPU consumption (redundant computation, inefficient algorithms)
- Memory consumption (excessive allocations, leaks, unnecessary copies)
- Power consumption (polling, wake locks, unnecessary timers, main-thread blocking)
- I/O waste (redundant reads/writes, missing caches, over-fetching)

For each issue found, rate severity:
- high: Measurable impact on user experience (jank, battery drain, OOM risk)
- medium: Waste that accumulates over time or under load
- low: Minor inefficiency, good practice improvement

Only report issues that can be fixed IN PLACE (no architectural redesigns).
Each suggestion must be a concrete code change, not a vague recommendation.
```

---

## Configuration

```json
{
    "analyzer_type": "perf_analyzer",
    "path_to_repo": "/path/to/repo",
    "min_path_depth": 3,
    "max_path_depth": 8,
    "max_paths": 500,
    "max_concurrent_analyses": 4,
    "rpm_limit": 30,
    "entry_points": [],
    "hot_modules": [],
    "min_function_lines": 5,
    "exclude_directories": ["Pods", "vendor", "ThirdParty"],
    "include_directories": [],
    "model": "anthropic.claude-sonnet-4-20250514",
    "llm_provider_type": "aws_bedrock"
}
```

- `entry_points`: Optional list of function names to start path enumeration from. If empty, uses all root nodes.
- `hot_modules`: Directories whose functions get priority in path scoring.
- `max_paths`: Total cap on paths to analyze (budget control).

---

## Execution Flow

```
1. CLI invocation
   └─ PerfAnalysisRunner.run()

2. Load AST
   └─ RepoAstIndex (lazy singleton)
       ├─ merged_functions.json
       ├─ merged_call_graph.json
       └─ merged_defined_classes.json

3. Enumerate call paths
   └─ CallPathEnumerator.enumerate_paths()
       ├─ Build CallGraph from merged_call_graph
       ├─ Find root nodes (or use entry_points)
       ├─ DFS enumeration with depth limits
       ├─ Filter: min depth, min function size
       ├─ Prioritize by heuristic score
       └─ Return sorted candidate paths (up to max_paths)

4. Edge coloring filter
   └─ EdgeColoringTracker
       ├─ Iterate paths in priority order
       ├─ Skip paths with no novel (uncolored) edges
       └─ Yield paths that have ≥1 novel edge

5. Check cross-run cache (publisher-subscriber)
   └─ For each surviving path, compute checksum from function checksums
       └─ Skip paths whose checksum matches a cached result

6. Async worker pool (concurrent path analysis)
   └─ For each path to analyze:
       │
       ├─ Stage A: Context Collection (with per-function cache)
       │   ├─ Check function context cache for each node in path
       │   ├─ Inject cached contexts as pre-collected data
       │   ├─ Run LLM only for novel (uncached) functions
       │   ├─ LLM also collects edge-specific context (data flow, threading transitions)
       │   ├─ Save newly collected function contexts to cache
       │   └─ Output: complete path context bundle
       │
       └─ Stage B: Performance Analysis
           ├─ Build prompt from context bundle
           ├─ LLM iterative loop with restricted tools
           └─ Output: array of perf findings
       │
       └─ Post-path: mark all edges as analyzed (color the path)

7. Post-processing
   ├─ Issue-level deduplication (by location + issue type)
   ├─ Severity validation
   ├─ Merge findings from all paths
   └─ Log coverage stats (edges analyzed / total edges)

8. Report generation
   └─ generate_html_report(findings, analysis_type="Performance Analysis")
```

---

## Open Questions / Future Work

1. **Entry point detection** — Should we auto-detect entry points (e.g., `@IBAction`, `viewDidLoad`, API handlers) or require user configuration? Recommendation: auto-detect common patterns per language, allow override.

2. **Incremental analysis** — When a function changes, which paths need re-analysis? Could use the call graph to find all paths containing the changed function.

3. **Benchmark integration** — Could correlate findings with actual profiling data (Instruments traces, perf counters) to validate severity estimates.

4. **Language-specific heuristics** — The path prioritization scoring could be tuned per language (e.g., Swift ARC overhead patterns vs. Go GC patterns).

5. **Path merging** — Multiple overlapping paths may produce complementary findings about the same bottleneck. A post-processing pass could merge related findings into a coherent narrative.
