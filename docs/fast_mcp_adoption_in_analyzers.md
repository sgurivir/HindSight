# FastMCP Adoption & Cooperative Multitasking for All Analyzers

## Overview

Adopt FastMCP as the unified tool-serving interface and cooperative multitasking (async worker pools) for parallel analysis in all three analyzers: `code_analyzer`, `git_simple_diff_analyzer`, and `trace_analyzer`.

**Reference implementation:** `data_flow_analyzer` already has both — `CodeNavigationServer` (in-process FastMCP) + asyncio workers with rate limiting.

---

## Design Decisions

| Decision | Resolution |
|----------|-----------|
| Knowledge tools in MCP? | No |
| Worker defaults | Per-analyzer: code_analyzer=4, diff_analyzer=2, trace_analyzer=3 |
| Batching? | No — 1 function/callstack per LLM request, parallel workers only |
| Prompt changes? | No removal. Add call-graph tool docs to Stage A prompts only |
| MCP mode | In-process only (no network service) |
| LLM client | Keep synchronous; wrap at analyzer level with `run_in_executor` |
| Graceful degradation | `max_workers=1` produces identical behavior to current sequential |

---

## Phase 1: Shared Async Infrastructure

**Create `hindsight/core/async_infra/`:**

| Module | Purpose |
|--------|---------|
| `rate_limiter.py` | Extract from `external_input_analyzer.py` — token-bucket, `asyncio.Semaphore` |
| `worker_pool.py` | Generic `async_worker_pool(queue, worker_fn, max_workers, rate_limiter)` |
| `llm_async_wrapper.py` | Wraps sync `provider.make_request()` via `loop.run_in_executor()` |

**Tests:**
- `tests/core/async_infra/test_rate_limiter.py` — extract existing RateLimiter tests from `test_data_flow_analyzer.py` and `test_sink_analyzer.py` into shared tests
- `tests/core/async_infra/test_worker_pool.py` — new: tests for pool completion, cancellation, error propagation, max_workers enforcement
- Add `pytest-asyncio` to test deps and configure `asyncio_mode = "auto"` in `conftest.py`

---

## Phase 2: Unified `AnalysisMCPServer`

**Create `hindsight/core/mcp_tools/analysis_server.py`:**

- Wraps existing `Tools` instance + optionally `CodeNavigationServer`
- Exposes all tools via FastMCP decorators (same names as today)
- `execute_tool(name, params) -> str` dispatch method
- `get_tool_descriptions(allowed_tools) -> str` for prompt injection
- `allowed_tools: set` constructor param for stage filtering (replaces `with_stage_b_tools()`)

### Tool Set

| Tool | Source | Available in Stage A | Available in Stage B |
|------|--------|---------------------|---------------------|
| `readFile` | FileToolsMixin | Yes | Yes |
| `getFileContentByLines` | FileToolsMixin | Yes | Yes |
| `checkFileSize` | FileToolsMixin | Yes | No |
| `getSummaryOfFile` | ImplementationToolsMixin | Yes | No |
| `list_files` | DirectoryToolsMixin | Yes | No |
| `inspectDirectoryHierarchy` | DirectoryToolsMixin | Yes | No |
| `runTerminalCmd` | TerminalToolsMixin | Yes | Yes |
| `getImplementation` | ImplementationToolsMixin | Yes | No |
| `search_symbol` | CodeNavigationServer | Yes (when AST loaded) | No |
| `get_function_body` | CodeNavigationServer | Yes (when AST loaded) | No |
| `get_callers` | CodeNavigationServer | Yes (when AST loaded) | No |
| `get_callees` | CodeNavigationServer | Yes (when AST loaded) | No |
| `find_references` | CodeNavigationServer | Yes (when AST loaded) | No |

**Tests:**
- `tests/core/mcp_tools/test_analysis_server.py` — new:
  - Initialization with/without call graph data
  - `execute_tool()` dispatch for all 13 tools
  - `allowed_tools` filtering (Stage B blocks forbidden tools)
  - `get_tool_descriptions()` returns correct subset
  - Unknown tool name returns error
  - Delegates to underlying `Tools` and `CodeNavigationServer` correctly

---

## Phase 3: Migrate Analyzers to MCP + Parallel Workers

### 3A. code_analyzer (biggest win — 100+ functions)

**Changes:**
- Replace `Tools(...)` with `AnalysisMCPServer(repo_path, ..., call_graph=graph)`
- Wrap sequential `for function in functions: stage_4a -> stage_4b` into async worker pool
- Default workers: **4**
- Add call-graph tools to Stage A (context collection can use `get_callers`/`get_callees`)

**Tests — update `tests/core/llm/test_code_analysis.py`:**
- Update `Tools` mock -> `AnalysisMCPServer` mock
- Add async test for `run_context_collection()` called through worker pool
- Test that multiple functions run concurrently (verify worker count respected)
- Test rate limiter integration (shared across workers)
- Test graceful degradation: `max_workers=1` behaves identically to current sequential
- Test that `TokenTracker` accumulates correctly across parallel workers (thread-safe)
- Test publisher-subscriber receives results from all workers (no lost results)

### 3B. git_simple_diff_analyzer (fewer functions, still benefits)

**Changes:**
- Replace `Tools` with `AnalysisMCPServer`
- Async worker pool for affected functions
- Default workers: **2** (typically fewer affected functions per commit)

**Tests — update `tests/core/llm/test_diff_analysis.py`:**
- Update mocks from `Tools` -> `AnalysisMCPServer`
- Add async test: 2 affected functions analyzed in parallel
- Test single-function case (no difference from sequential)
- Test that diff context is passed correctly to each worker

### 3C. trace_analyzer (natural parallelism on callstacks)

**Changes:**
- Replace `Tools` with `AnalysisMCPServer`
- Async worker pool for callstacks
- Default workers: **3**
- Call-graph tools useful here (callstacks reference many functions)

**Tests — new/update trace analysis tests:**
- Test parallel callstack analysis with `max_workers=3`
- Test `--num-traces-to-analyze` limit still respected
- Test `AnalyzedRecordsRegistry` deduplication is thread-safe across workers
- Test that each worker gets independent tool execution context

---

## Phase 4: Prompt Updates (Additions Only)

**Update `contextCollectionProcess.md` and `diffContextCollectionProcess.md`:**
- Add call-graph tools section (when available): `search_symbol`, `get_function_body`, `get_callers`, `get_callees`, `find_references`
- Add tool format examples
- Add to decision tree: "Need to understand callers/callees? -> use get_callers/get_callees"

**No changes to:**
- `analysisProcess.md` (Stage B — still restricted to `readFile` + `runTerminalCmd`)
- `analysisTools.md` (referenced by older prompts, keep for backward compat)

---

## Phase 5: Thread-Safety Hardening

Components that need thread-safe access when workers run in parallel:

| Component | Current State | Fix |
|-----------|--------------|-----|
| `PublisherSubscriber.add_result()` | No lock | Add `threading.Lock` |
| `TokenTracker.add_token_usage()` | No lock | Add `threading.Lock` |
| `Claude.start_issue_logging(i)` | Global state | Per-worker logging, merge at end |
| `AnalyzedRecordsRegistry` | No lock | Add `threading.Lock` |
| `AnalysisMCPServer.execute_tool()` | Stateless reads | Safe as-is (read-only) |

**Tests:**
- `tests/core/test_thread_safety.py` — new:
  - `TokenTracker` concurrent writes from N threads produce correct totals
  - `PublisherSubscriber` concurrent `add_result()` calls — no lost results
  - `AnalyzedRecordsRegistry` concurrent dedup checks are consistent

---

## Phase 6: Cleanup & Deprecation

- `Tools.with_stage_b_tools()` -> deprecated, delegates to `AnalysisMCPServer(allowed_tools=...)`
- `Tools.execute_tool_use()` -> kept as thin wrapper around `AnalysisMCPServer.execute_tool()`
- Update `test_tools.py` `with_stage_b_tools` test to verify it still works via delegation

---

## Test Summary

| Test File | Action | Reason |
|-----------|--------|--------|
| `tests/conftest.py` | Update | Add pytest-asyncio config, async fixtures |
| `tests/core/async_infra/test_rate_limiter.py` | New | Shared rate limiter |
| `tests/core/async_infra/test_worker_pool.py` | New | Worker pool, cancellation, errors |
| `tests/core/mcp_tools/test_analysis_server.py` | New | Unified MCP server dispatch |
| `tests/core/llm/test_code_analysis.py` | Update | Mock changes, parallel worker tests |
| `tests/core/llm/test_diff_analysis.py` | Update | Mock changes, parallel worker tests |
| Trace analysis test (new or update) | New/Update | Parallel callstack analysis |
| `tests/core/test_thread_safety.py` | New | Concurrent access to shared state |
| `tests/core/llm/tools/test_tools.py` | Update | Verify delegation to AnalysisMCPServer |
| `test_data_flow_analyzer.py` | Update | Extract RateLimiter tests to shared module |
| `test_sink_analyzer.py` | Update | Extract RateLimiter tests to shared module |

**Dependencies to add:** `pytest-asyncio`

---

## Implementation Order

```
Phase 1 (foundation)     -> Phase 2 (MCP server)     -> Phase 3A (code_analyzer)
                                                       -> Phase 3B (diff_analyzer)
                                                       -> Phase 3C (trace_analyzer)
                          -> Phase 4 (prompts)
Phase 5 (thread-safety)  -> Phase 6 (cleanup)
```

Phases 3A/3B/3C can proceed in parallel once Phase 1+2 are done. Phase 5 should be done before or alongside Phase 3.
