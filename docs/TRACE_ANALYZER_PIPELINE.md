# Trace Analyzer Pipeline

## Overview

The trace analyzer (`hindsight/analyzers/trace_analyzer.py`) implements a two-stage LLM pipeline that transforms raw callstack traces (from profiling tools like Instruments) into structured performance issue reports. It separates **context collection** (Stage A) from **performance analysis** (Stage B), each running in isolated LLM contexts with different tool access and prompts.

---

## Pipeline Stages

The full pipeline (when invoked via `TraceAnalysisRunner.run()`) has five stages:

```
Stage 1: Initialization & Configuration
Stage 2: Directory Structure Index + Classification
Stage 3: AST Call Graph Generation / Loading
Stage 4: Trace Analysis (Two-Stage LLM per callstack, parallel)
Stage 5: Report Generation (Dedup, Filtering, HTML)
```

### Stage 1: Initialization & Configuration

| Component | Notes |
|-----------|-------|
| Configuration loading from JSON | api_key, model, repo_path, etc. |
| Output directory setup | `llm_artifacts/<repo_name>/trace_analysis/` |
| File content provider initialization | For file resolution across the repo |
| Publisher-subscriber setup | Result distribution system |
| AnalyzedRecordsRegistry | Tracks which callstacks are already done |

### Stage 2: Directory Structure Index + Classification

| Sub-stage | Notes |
|-----------|-------|
| Directory structure index building | Pre-step, always runs |
| Static directory analysis | Finds directories to exclude |
| LLM-based directory classification | Single LLM call to classify directories |
| Enhanced directory analysis | Combines static + LLM results |

### Stage 3: AST Call Graph Generation / Loading

- If existing AST files are present, they are reused.
- Otherwise AST call graphs are generated from source.
- The merged call graph is loaded into memory for `CodeNavigationServer`.
- `AnalysisMCPServer` is initialized with call graph data (enables code navigation tools).

### Stage 4: Trace Analysis (Two-Stage LLM, Parallel)

For each callstack (up to `num_traces_to_analyze`):

1. **Registry check** — skip if already analyzed (via `AnalyzedRecordsRegistry`).
2. **Prompt generation** — `TraceAnalysisPromptBuilder.create_context_for()` builds the prompt.
3. **Stage A: Context Collection** — iterative LLM loop gathers source code for all callstack functions.
4. **Stage B: Performance Analysis** — fresh LLM context analyzes the collected context bundle.
5. **Publish result** — write to disk via publisher-subscriber.

Callstacks are analyzed in parallel using a thread pool with rate limiting.

### Stage 5: Report Generation

| Sub-stage | Notes |
|-----------|-------|
| Issue deduplication (`IssueDeduper`) | Optional semantic dedup via embeddings |
| Unified issue filtering | Category filter + LLM-based filter + Response Challenger |
| HTML report generation | Organized by directory, filterable |
| Tool usage summary | Logs all tool calls made during analysis |

---

## Stage 4 Deep Dive: Two-Stage LLM Analysis

### Architecture

```
Input: Callstack trace + initial code context
            │
            ▼
┌──────────────────────────────────┐
│  Stage A: Context Collection     │
│  ─────────────────────────────   │
│  Prompt: traceContextCollection  │
│  Process.md                      │
│  Tools: Full set (15 tools)      │
│  Goal: Gather all source code    │
│  Output: context_bundle (JSON)   │
│    { call_path: [...],           │
│      functions: {...} }          │
└──────────────────────────────────┘
            │ context_bundle
            ▼
┌──────────────────────────────────┐
│  Stage B: Performance Analysis   │
│  (Fresh LLM context window)      │
│  ─────────────────────────────   │
│  Prompt: traceAnalysisProcess.md │
│  Tools: Reduced set (7 tools)    │
│  Goal: Identify perf issues      │
│  Output: issues[] (JSON array)   │
└──────────────────────────────────┘
            │ issues[]
            ▼
    Save to JSON output file
```

### Stage A: Context Collection

**Goal**: Gather all code artifacts needed to understand the callstack trace. Do NOT reason about performance issues.

**Tool Access (Stage A)**:

| Tool | Source | Purpose |
|------|--------|---------|
| `readFile` | AnalysisMCPServer/Tools | Read file contents |
| `getFileContentByLines` | AnalysisMCPServer/Tools | Read specific line ranges |
| `checkFileSize` | AnalysisMCPServer/Tools | Check file existence and size |
| `runTerminalCmd` | AnalysisMCPServer/Tools | Execute terminal commands (grep, find, etc.) |
| `list_files` | AnalysisMCPServer/Tools | List directory contents |
| `inspectDirectoryHierarchy` | AnalysisMCPServer/Tools | Get directory structure |
| `getSummaryOfFile` | AnalysisMCPServer/Tools | Summarize large files |
| `search_symbol` | CodeNavigationServer | Search functions by name |
| `get_symbol` | CodeNavigationServer | Get symbol details |
| `get_function_body` | CodeNavigationServer | Read function source |
| `get_file_ast` | CodeNavigationServer | List functions in a file |
| `get_callers` | CodeNavigationServer | Get callers of a function |
| `get_callees` | CodeNavigationServer | Get callees of a function |
| `find_references` | CodeNavigationServer | Find all references |
| `lookup_knowledge` | TraceKnowledgeStore | Search persistent learnings |
| `lookup_function_optimization` | TraceKnowledgeStore | Find cached optimizations |

**Collection Priority Order**:
1. Leaf function (FULL source code)
2. Intermediate functions (execution path to next function in stack)
3. Direct callees of leaf function
4. Data types referenced by leaf function
5. Constants/globals

**Output Format**:
```json
{
  "call_path": ["root_function", "intermediate_function", "leaf_function"],
  "functions": {
    "leaf_function": {
      "file": "path/to/file.swift",
      "source": "...",
      "start_line": 42,
      "end_line": 98
    }
  }
}
```

### Stage B: Performance Analysis

**Goal**: Analyze the pre-collected context bundle to identify performance bottlenecks. Runs in a **fresh context window** — no carryover from Stage A.

**Tool Access (Stage B — Reduced)**:

| Tool | Purpose |
|------|---------|
| `readFile` | Read additional files if needed |
| `runTerminalCmd` | Execute terminal commands |
| `getFileContentByLines` | Read specific line ranges |
| `lookup_knowledge` | Search persistent learnings |
| `store_learning` | Save learnings for future traces |
| `lookup_function_optimization` | Find cached optimizations |
| `store_function_optimization` | Cache optimization findings |

**Output Format**: JSON array of issue objects:
```json
[
  {
    "issue": "...",
    "severity": "high",
    "confidence": 0.85,
    "location": { "file": "...", "line": 42 },
    "recommendation": "..."
  }
]
```

### Fallback: Single-Stage Analysis

If Stage A fails to produce a valid context bundle (e.g., max iterations reached without valid JSON), the system falls back to a single-stage analysis that combines collection and analysis in one LLM call.

---

## Iterative LLM Loop

Both stages use `BaseIterativeAnalyzer` for the LLM conversation loop:

```
Iteration 1 → MAX_TOOL_ITERATIONS (20):
    │
    ├─ Send: system_prompt + conversation_history
    │
    ├─ Receive: LLM response
    │
    ├─ Extract JSON tool requests from response text
    │
    ├─ Tool requests found?
    │   ├─ YES → Execute tools, add results to history, continue
    │   └─ NO  → Extract final JSON output
    │              ├─ Valid? → Return result
    │              └─ Invalid? → Continue loop
    │
    └─ Iteration limits:
        ├─ Iteration 16 (80%): Soft reminder to start producing output
        ├─ Iteration 19: Strong pressure to produce output
        └─ Iteration 20: CRITICAL — forces output generation
```

**Stage-Specific Analyzers**:
- `TraceContextAnalyzer` (Stage A): Validates output has `call_path` key
- `TraceAnalysisAnalyzer` (Stage B): Validates output is a list of issue dicts

---

## MCP Server Integration

The `AnalysisMCPServer` (`hindsight/core/mcp_tools/analysis_server.py`) provides unified tool dispatch for both stages:

```
AnalysisMCPServer
    ├─ Tools (always available)
    │   ├─ readFile, getFileContentByLines, checkFileSize
    │   ├─ runTerminalCmd
    │   ├─ list_files, inspectDirectoryHierarchy
    │   ├─ getSummaryOfFile
    │   └─ getImplementation
    │
    └─ CodeNavigationServer (when call_graph_data provided)
        ├─ search_symbol, get_symbol, get_function_body
        ├─ get_file_ast
        └─ get_callers, get_callees, find_references
```

The `_ToolsWithKnowledge` wrapper intercepts knowledge store tool calls (`lookup_knowledge`, `store_learning`, `lookup_function_optimization`, `store_function_optimization`) and routes them to `TraceKnowledgeStore`, delegating all other tool calls to the underlying `AnalysisMCPServer`.

---

## Knowledge Store

**Location**: `hindsight/core/knowledge/trace_knowledge_store.py`

SQLite + FTS5 database for persistent learnings across traces within the same repository.

**Tables**:

| Table | Purpose |
|-------|---------|
| `trace_learnings` | General learnings (entity_key, summary, confidence) |
| `function_optimizations` | Per-function optimization findings (file_path, function_name, summary, severity) |

**Tool Interface**:

| Tool | Stage | Purpose |
|------|-------|---------|
| `lookup_knowledge` | A, B | Full-text search across learnings |
| `store_learning` | B only | Save general learnings for future traces |
| `lookup_function_optimization` | A, B | Find cached optimizations (fuzzy LIKE match) |
| `store_function_optimization` | B only | Cache optimization findings |

---

## Parallel Execution Model

```
TraceAnalysisRunner._run_trace_analysis():
    │
    ├─ Load all callstacks from hotspot file
    │
    ├─ Filter already-analyzed via AnalyzedRecordsRegistry
    │
    ├─ Pre-generate prompts (SEQUENTIAL — prompt builder not thread-safe)
    │   └─ For each callstack: create_context_for() → (prompt_content, callstack_data)
    │
    ├─ Parallel worker pool
    │   ├─ Workers: TRACE_ANALYZER_DEFAULT_WORKERS (5)
    │   ├─ Rate limit: TRACE_ANALYZER_RATE_LIMIT (20 req/min)
    │   │
    │   └─ Per callstack:
    │       ├─ TraceCodeAnalysis(config, mcp_server=self._mcp_server)
    │       ├─ Stage A → context_bundle
    │       ├─ Stage B → issues[]
    │       ├─ Publish result
    │       └─ Update AnalyzedRecordsRegistry
    │
    └─ On completion: aggregate token usage, log summary
```

**Thread Safety**:

| Component | Protection |
|-----------|-----------|
| AnalyzedRecordsRegistry | `_registry_lock` (threading.Lock) |
| Token tracking | `_token_tracker_lock` (threading.Lock) |
| Publisher-subscriber | Internal synchronization |
| Prompt builder | Pre-generated sequentially before pool starts |
| AnalysisMCPServer.execute_tool() | Stateless reads — safe as-is |

---

## Prompt Architecture

### System Prompts (Cached)

| File | Role |
|------|------|
| `systemPromptTrace.md` | Persona: Senior performance engineer. Scope rules, caching prohibition, tool priority |
| `traceContextCollectionProcess.md` | Stage A instructions: collect source code, output format |
| `traceAnalysisProcess.md` | Stage B instructions: analyze context, output format |

### User Prompt

| File | Role |
|------|------|
| `analyzeTrace.md` | Template with `{json_content}` placeholder for callstack data |

### Key Prompt Constraints

- **CACHING PROHIBITION**: Never suggest caching/memoization as an optimization
- **Scope**: Only analyze functions present in the callstack trace
- **Tool priority**: `get_function_body` > `readFile` (more efficient)
- **Line numbers**: Must preserve original source-file line numbers (1-indexed)

---

## Callstack Data Flow

```
Hotspot JSON (nested tree)
    │
    ▼
HotSpotUtil.flatten()
    │
    ▼
TraceAnalysisPromptBuilder.process_hotspot_data()
    │ Supports batching (batch_index * num_traces)
    ▼
processed_hotspots.json
    │
    ▼
TraceAnalysisPromptBuilder.process_callstacks()
    │ Filters already-analyzed
    ▼
For each callstack:
    TraceAnalysisPromptBuilder.create_context_for()
        ├─ _convert_callstack_to_text_format()
        │   (preserves costs for registry, strips for LLM)
        ├─ _find_function_context() — lookup in merged_functions.json
        ├─ _find_caller_information() — gets caller context
        └─ Loads file content from FileContentProvider
            │
            ▼
        (prompt_content, callstack_data)
```

**Callstack entry format (for registry, with costs)**:
```
15% (2.05) LibraryName functionName (filename.swift)
```

**Callstack entry format (for LLM, costs stripped)**:
```
LibraryName functionName (filename.swift)
```

---

## Results Publishing System

### Publisher-Subscriber Pattern

- **Publisher**: `TraceAnalysisResultsPublisher`
- **Subscriber**: `TraceAnalysisResultsLocalFSSubscriber`

Results are saved as JSON files to `llm_artifacts/<repo_name>/trace_analysis/`.

### TraceAnalysisResultRepository

Singleton for result management:
- `save_trace_result()` — saves JSON with metadata
- File path correction — corrects LLM-provided paths against actual repo structure
- Groups issues by callstack for consistent folder assignment

---

## Error Handling & Resilience

| Failure Mode | Recovery |
|-------------|----------|
| Stage A produces invalid JSON | Falls back to single-stage analysis |
| Stage A max iterations reached | Falls back to single-stage analysis |
| Stage B produces invalid JSON | Returns empty issues list |
| File not found during tool use | Logs warning, continues without context |
| Tool execution error | Returns error message to LLM for self-correction |
| Token overflow | Pre-checked via `ModelLimits.get_context_window()` |
| Worker thread crash | Logged as failure, other workers continue |

---

## Configuration Parameters

```python
# Defaults (from constants or config)
TRACE_ANALYZER_DEFAULT_WORKERS = 5
TRACE_ANALYZER_RATE_LIMIT = 20  # requests per minute
MAX_TOOL_ITERATIONS = 20
DEFAULT_MAX_TOKENS = 3000

# Config file keys
{
  "api_key": "...",
  "api_end_point": "...",
  "model": "claude-3-5-sonnet-...",
  "llm_provider_type": "aws_bedrock",
  "exclude_directories": ["node_modules", "venv", ...],
  "num_traces_to_analyze": 50,
  "enable_issue_deduplication": true,
  "dedupe_threshold": 0.85
}
```

---

## File Locations

| Component | Location |
|-----------|----------|
| Trace Analyzer (orchestrator) | `hindsight/analyzers/trace_analyzer.py` |
| Two-Stage Analysis | `hindsight/core/trace_util/trace_code_analysis.py` |
| Context Analyzer (Stage A) | `hindsight/core/llm/iterative/trace_context_analyzer.py` |
| Analysis Analyzer (Stage B) | `hindsight/core/llm/iterative/trace_analysis_analyzer.py` |
| Base Iterative Analyzer | `hindsight/core/llm/iterative/base_iterative_analyzer.py` |
| Prompt Builder | `hindsight/core/trace_util/trace_analysis_prompt_builder.py` |
| Trace Prompt Builder | `hindsight/core/trace_util/trace_prompt_builder.py` |
| Knowledge Store | `hindsight/core/knowledge/trace_knowledge_store.py` |
| Result Repository | `hindsight/core/trace_util/trace_result_repository.py` |
| File Name Extractor | `hindsight/core/trace_util/file_name_extractor_from_trace.py` |
| MCP Server | `hindsight/core/mcp_tools/analysis_server.py` |
| Code Navigation Server | `hindsight/core/mcp_tools/code_navigation_server.py` |

### Prompt Files

| File | Role |
|------|------|
| `hindsight/core/prompts/systemPromptTrace.md` | System persona (cached) |
| `hindsight/core/prompts/traceContextCollectionProcess.md` | Stage A instructions |
| `hindsight/core/prompts/traceAnalysisProcess.md` | Stage B instructions |
| `hindsight/core/prompts/analyzeTrace.md` | User prompt template |
| `hindsight/core/prompts/traceRelevanceFilterPrompt.md` | Relevance filtering |
| `hindsight/core/prompts/traceResponseChallenger.md` | Response challenger |

---

## Comparison with Code Analyzer Pipeline

| Aspect | Code Analyzer | Trace Analyzer |
|--------|--------------|----------------|
| Input | Individual functions from call graph | Callstack traces from profiler |
| Stage A goal | Gather context for one function | Gather source for all callstack functions |
| Stage B goal | Find bugs + perf issues | Find performance bottlenecks only |
| Tool set | Same MCP server | Same MCP server + knowledge tools |
| Knowledge store | `KnowledgeStore` (generic) | `TraceKnowledgeStore` (trace-specific) |
| Parallelism | Thread pool (4 workers default) | Thread pool (5 workers default) |
| Caching | Checksum-based (skip if same source) | Registry-based (skip if same callstack) |
| Filtering | 3-level (Category + LLM + Challenger) | Unified issue filter (same system) |
| Output | Per-function JSON + HTML report | Per-callstack JSON + HTML report |
| Prompt constraint | None specific | CACHING PROHIBITION |
