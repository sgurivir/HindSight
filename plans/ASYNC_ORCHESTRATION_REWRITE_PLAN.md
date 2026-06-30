# Async Orchestration Rewrite Plan

**Status:** ✅ COMPLETE — Steps 1–7 done, legacy stack deleted, all 5 CLIs green
**Scope:** Full rewrite of LLM orchestration layer for code/diff/trace/perf/security analyzers
**Out of scope:** AST generation, reports, dedupers, results store, issue filters' non-LLM levels, prompt `.md` files
**Last updated:** 2026-06-29

---

## Quick resume guide

If you're returning to this work cold, the fastest way to get oriented:

1. **What's working now:** All 5 analyzer CLIs (code_analyzer, trace_analyzer, git_simple_diff_analyzer, perf_analyzer, data_flow_analyzer) run on the new async stack. 154 new tests in `hindsight/tests/llm/` + `hindsight/tests/orchestration/` pass; full test suite green at 1108 passed, 3 skipped.
2. **What's done:** ALL 7 steps. The legacy stack (`hindsight/core/llm/`, `hindsight/core/async_infra/`, `hindsight/core/mcp_tools/`, plus the legacy `trace_code_analysis.py` + `trace_prompt_builder.py`) is deleted. See "Progress log" below.
3. **Where the new code lives:** `hindsight/llm/`, `hindsight/llm/tools/`, `hindsight/orchestration/`.
4. **Where the new tests live:** `hindsight/tests/llm/`, `hindsight/tests/orchestration/`.
5. **Where the diff-only changed-lines filter lives:** `hindsight/orchestration/pipeline_diff.py` (`_filter_issues_to_changed_lines` + helpers).
6. **Trace pipeline:** `hindsight/orchestration/pipeline_trace.py` — Ta → Tb → Tc with publish/cache callbacks supplied by `TraceAnalysisRunner`.
7. **Security pipeline:** `hindsight/orchestration/pipeline_security.py` — exposes `open_security_llm()` async context manager. `SinkAnalyzer`/`ExternalInputAnalyzer`/`FlowVulnerabilityAnalyzer` are unchanged structurally; they now consume an `AsyncLLMClient`-backed callable.
8. **What's next:** FastAPI integration on top of `AnalysisSession`.

---

## Motivation

1. The current `hindsight/core/llm/` stack is synchronous (`requests` + `time.sleep`) and uses class-statics on `Claude` for conversation logging. That blocks a thread per in-flight LLM call and breaks under concurrent FastAPI requests.
2. The next step after this rewrite is putting a **FastAPI client on top of these analyzers** (onboard repo → trigger AST → setup results listener → analyze function). The new stack must expose awaitable, dependency-injected functions with per-session state.
3. Cross-function fan-out is already async via `RateLimiter` + `run_worker_pool`, but **within** each LLM iteration tool calls are sequential and HTTP is blocking — the biggest wall-clock wins are here.

---

## Locked decisions

| Decision | Choice |
|---|---|
| Trace pipeline | Migrate to new async stack alongside code+diff |
| Other LLM-using analyzers (perf, directory_classifier, sink, data_flow, external_input, flow_vulnerability) + LLM-using issue filters | Migrate them all — full sweep |
| HTTP library | `httpx.AsyncClient` with manual SigV4 |
| MCP layer | Dropped; tools dispatch via `ToolRegistry` directly (`AnalysisMCPServer` is not a real MCP server) |
| CLI surface | Preserved byte-for-byte — every flag, default, required/optional, exit code, mutual exclusion |
| `~/llm_artifacts/{repo}/...` layout | Preserved byte-for-byte — directory names, file names, contents, clear-on-run vs append semantics |
| AST gen, dedupers, report gen, results store, issue filters' non-LLM levels | Untouched; called from new orchestration |
| FastAPI readiness | First-class — `AnalysisSession` is the FastAPI handle |

---

## Current pipeline shape (what the rewrite must reproduce)

Both analyzers run the same two-LLM-stage pattern, with a third "call-tree-at-once" variant gated by `CALL_TREE_ANALYSIS_ENABLED`:

| Pipeline | Stage A (context) | Stage B (analysis) | Unit of parallelism |
|---|---|---|---|
| `code_analyzer` (per-function legacy) | 4a — `ContextCollectionAnalyzer` → context bundle dict | 4b — `CodeAnalysisAnalyzer` → issues array | one function |
| `code_analyzer` (call-tree) | — (deterministic tree build, no LLM) | one `CodeAnalysisAnalyzer` per root with whole subtree | one root |
| `git_simple_diff_analyzer` (per-function) | Da — `DiffContextAnalyzer` | Db — `DiffAnalysisAnalyzer` | one affected function |
| `git_simple_diff_analyzer` (call-tree) | — (deterministic tree build) | one `DiffAnalysisAnalyzer` per root | one root |
| `trace_analyzer` | Ta — `TraceContextAnalyzer` | Tb — `TraceAnalysisAnalyzer` → challenge → validate | one callstack |
| `perf_analyzer` | Perf context | Perf analysis | one function |

Stage analyzers extract & validate stage-specific JSON shapes (dict-with-`primary_function`, array-of-issue-dicts, etc.) — the JSON-extraction logic is preserved verbatim, only the surrounding ceremony goes away.

The **JSON-embedded tool protocol** stays unchanged: tools requested via `{"tool": "readFile", ...}` regex-extracted from response text; tool results inserted as plain-text user messages with `[TOOL_RESULT: id]` prefix. **Not** native API tool-use.

---

## What gets preserved exactly

### CLI surface

**`python -m hindsight.analyzers.code_analyzer`** — every flag at [code_analyzer.py:3561+](../hindsight/analyzers/code_analyzer.py#L3561):
`--config/-c`, `--repo/-r`, `--out-dir/-o`, `--force-recreate-ast`, `--force-llm-analysis`, `--generate-report-from-existing-issues`, `--generate-from-text-file`, `--text-file-project-name`, `--text-file-output`, `--issue-dedupe`, `--false-positives-csv`, `--file-filter`, `--exclude-directories`, `--include-directories`, `--exclude-files`, `--min-function-body-length`, `--analysys_type`, `--function-filter`, `--num-functions-to-analyze`, `--user-prompt`, `--force-in-process-ast`, `--no-parallel`, `--max-workers`

**`python -m hindsight.analyzers.trace_analyzer`** — `--config/-c`, `--repo/-r`, `--hotspot/-t`, `--num-traces-to-analyze/-n`, `--batch-index`, `--out-dir/-o`, `--generate-report-from-existing-issues`, `--issue-dedupe`

**`python -m hindsight.diff_analyzers.git_simple_diff_analyzer`** — `--repo`, `--config`, `--out_dir`, `--c1`, `--c2`, `--branch1`, `--branch2`, `--branch`, `-v/--verbose`, `--num-chunks-to-analyze`, `--generate-report-from-existing-issues`

Mutual exclusions, required combinations, and exit codes preserved. The existing module entry points (`hindsight.analyzers.code_analyzer:main` etc.) become **thin shims** that delegate to `hindsight/orchestration/cli_*.py`, so external scripts keep working.

### Output layout under `~/llm_artifacts/{repo_name}/`

| Path | Writer | Naming | Clear-on-run? |
|---|---|---|---|
| `code_insights/` | AST gen (untouched) | `merged_symbols.json`, `nested_call_graph.json`, `merged_defined_classes.json` | No |
| `prompts_sent/{code\|diff\|trace}_analysis/{N}/step{N}_{stage}.md` | `ConversationLogger` (new) | per Claude class today | Cleared at start of run |
| `results/code_analysis/{func}_{file}_{checksum8}_analysis.json` | `CodeAnalysysResultsLocalFSSubscriber` (unchanged) | unchanged | No (checksum cache) |
| `results/trace_analysis/trace_{idx:04d}_analysis.json` | trace subscriber (unchanged) | unchanged | No |
| `results/diff_analysis/*_analysis.json` | diff subscriber (unchanged) | unchanged | No |
| `results/html_reports/repo_analysis_{project}_{timestamp}.html` | existing report gen | unchanged | No |
| `results/errors/too_large_context_error_{timestamp}.txt` | `ConversationLogger` (new) | unchanged | No |
| `results/dropped_issues/{level1_category\|level2_llm\|level3_challenge\|final_filter}/*.json` | existing issue filters | unchanged | No |
| `context_bundles/{checksum8}.json` | `pipeline_code` (new) — Stage 4a cache | unchanged | No |
| `diff_context_bundles/{hash8}.json` | `pipeline_diff` (new) — Stage Da cache | unchanged | No |
| `logs/hindsight_{timestamp}.log` | `LogUtil` (unchanged) | unchanged | No |
| `currentFullPrompt.txt` | per-call debug dump | unchanged | Overwritten per call |
| `trace_analysis/analyzed_records/` | `AnalyzedRecordsRegistry` (unchanged) | unchanged | No |
| `analysis_input/code_analysis/` | transient per-function input JSONs | deleted after analysis | Per file |
| `analysis/diff_{c1:8}_to_{c2:8}.diff` (and `_original.diff`) | `git_simple_diff_analyzer` | unchanged | Yes (per run) |

`OutputDirectoryProvider` singleton stays — the contract is already correct. The only wrinkle: it needs a session-scoped wrapper for FastAPI so concurrent sessions don't write into each other's directories.

---

## Target architecture

### File layout

```
hindsight/llm/                         # async LLM stack, replaces hindsight/core/llm/
  __init__.py
  client.py                # AsyncLLMClient — httpx.AsyncClient + SigV4, async retry/backoff
  bedrock.py               # Bedrock payload builder + signing
  errors.py                # LLMTokenLimitExceeded, LLMTransientError, LLMFatalError
  conversation.py          # ConversationState — instance, no statics
  logger.py                # ConversationLogger — instance, writes prompts_sent/.../stepN_stage.md
  prompts.py               # PromptCache — reads .md files at startup, holds in memory
  tool_protocol.py         # JSON-embedded tool-request regex extraction
  json_extract.py          # _find_all_json_objects/_arrays
  rate_limit.py            # AsyncRateLimiter (sliding window)
  iterate.py               # IterativeRunner — async loop, per-iteration tool gather()
  stages.py                # StageSpec dataclass + 9 factories:
                           #   Stage4aContext, Stage4bAnalysis,
                           #   StageDaDiffContext, StageDbDiffAnalysis,
                           #   StageTaTraceContext, StageTbTraceAnalysis,
                           #   StageResponseChallenger, StageTraceValidator, StageTrivialFilter
  callsite.py              # one-shot helpers: directory_classify(), summarize_file(), llm_filter_issue()
                           #   — used by directory_classifier + issue_filter + file_summary_generator

hindsight/llm/tools/                   # async tool registry, replaces hindsight/core/llm/tools/
  __init__.py
  registry.py              # ToolRegistry (decorator-based, async dispatch, allowed-set per call)
  schemas.py               # JSON schemas (moved from tool_definitions.py)
  fs.py                    # readFile, getFileContentByLines, checkFileSize
  summary.py               # getSummaryOfFile (uses callsite.summarize_file)
  dir.py                   # list_files, inspectDirectoryHierarchy
  shell.py                 # runTerminalCmd (asyncio.create_subprocess_exec)
  symbols.py               # getImplementation (reads existing AST index)

hindsight/orchestration/               # pipelines, replaces orchestration in analyzers/ + diff_analyzers/
  __init__.py
  context.py               # AnalysisContext frozen dataclass
  session.py               # AnalysisSession — long-lived, FastAPI-facing
  worker.py                # bounded_gather(items, fn, max_concurrency, rate_limiter, on_error)
  function_selector.py     # pure-function file/dir/length/verified filter logic
  affected_selector.py     # wraps existing AffectedFunctionDetector
  result_sink.py           # async bridge to existing publishers (asyncio.to_thread + asyncio.Lock)
  events.py                # AnalysisEvent dataclasses + session.subscribe() for FastAPI streaming
  pipeline_code.py         # CodePipeline: analyze_function / analyze_repo / analyze_call_tree
  pipeline_diff.py         # DiffPipeline: analyze_function / analyze_diff / analyze_call_tree
  pipeline_trace.py        # TracePipeline: analyze_trace (Ta → Tb → challenge → validate)
  pipeline_perf.py         # PerfPipeline: same shape as code, perf prompts
  pipeline_security.py     # shared analyze_for_security() used by sink/data_flow/external_input/flow_vulnerability
  cli_code.py              # entry point — argparse + AST gen + AnalysisSession + asyncio.run
  cli_trace.py
  cli_diff.py
```

### Deleted at end of migration

- `hindsight/core/llm/` (entire package: `llm.py`, `code_analysis.py`, `diff_analysis.py`, `perf_analysis.py`, `perf_context_cache.py`, `command_validator.py`, `summary_service.py`, `ttl_manager.py`, `iterative/`, `tools/`, `providers/`)
- `hindsight/core/async_infra/`
- `hindsight/core/mcp_tools/`
- Orchestration halves of `hindsight/analyzers/code_analyzer.py`, `trace_analyzer.py`, `perf_analyzer.py`, security analyzers, and `hindsight/diff_analyzers/git_simple_diff_analyzer.py` (CLI/setup halves stay or become thin shims)

---

## Async wins

| Place | Today | After |
|---|---|---|
| LLM HTTP | sync `requests.post` blocks a thread | async `httpx.AsyncClient` |
| Cross-function fan-out | threads via `run_in_executor` | `asyncio.TaskGroup` + `Semaphore` |
| Within-iteration tool calls | sequential | `asyncio.gather` per iteration |
| File I/O in tools | blocking | `asyncio.to_thread` |
| Subprocess (`runTerminalCmd`) | `subprocess.run` blocks | `asyncio.create_subprocess_exec` |
| Retry/backoff | `time.sleep` | `asyncio.sleep` |
| Conversation logging | class-statics (broken under concurrency) | per-session instance — concurrency-safe |

The Bedrock HTTP switch is the **biggest single win** — it removes the "one thread per in-flight LLM call" ceiling that's hidden in today's design.

---

## FastAPI handshake

The session is the integration point:

```python
session = await AnalysisSession.create(repo, config, out_dir, api_key)
await session.ensure_ast()                              # idempotent; reuses existing artifacts
async for event in session.analyze_repo():              # yields function_started, function_complete, run_complete
    await ws.send_json(event.to_dict())                 # SSE / WebSocket fan-out is trivial
```

Key properties:
- **No class-statics anywhere** — `Claude._prompts_dir`, `Claude._conversation_counter`, `Claude._current_issue_dir`, `Claude._errors_dir` all become instance state on `ConversationLogger`.
- **Cancellation-aware** — every long `await` (LLM call, tool call, gather) is cancellable; FastAPI's request-cancellation propagates naturally.
- **No `asyncio.run` in library code** — only inside CLI scripts. FastAPI handlers don't get nested-loop errors.
- **Connection pooling** — one `httpx.AsyncClient` per session, reused across all LLM calls.
- **Per-session output directories** — `OutputDirectoryProvider` gets a session-scoped wrapper.
- **Result streaming** — both callback API (`subscribe(cb)`) and async iterator API (`async for event in session.analyze_repo()`).

---

## Migration order (7 steps, CLI green at every step)

### Step 1 — Land `hindsight/llm/` core — ✅ COMPLETE
**Modules:** `client.py`, `bedrock.py`, `errors.py`, `conversation.py`, `logger.py`, `prompts.py`, `tool_protocol.py`, `json_extract.py`, `rate_limit.py`, `iterate.py`, `stages.py`, `callsite.py`
**Tests:** 38 tests covering JSON extraction, tool protocol, conversation state, rate limiter, every `StageSpec` factory + IterativeRunner with fake client. `hindsight/tests/llm/test_unit.py` + `test_iterative_runner.py`.
**Result:** Package imports cleanly; CLIs unchanged. Circular import with legacy `hindsight/core/prompts/` worked around by lazy `PromptBuilder` imports.

### Step 2 — Land `hindsight/llm/tools/` — ✅ COMPLETE
**Modules:** `registry.py`, `schemas.py`, `fs.py`, `summary.py`, `dir.py`, `shell.py`, `symbols.py`
**Tests:** 21 tests covering registry dispatch + allowed-set, parameter normalization/validation, fs tools against a Swift fixture, shell with subprocess + timeout, symbols with a minimal registry + repo-content fallback search. `hindsight/tests/llm/test_tools.py`.
**Known straggler:** `shell.py` still imports `CommandValidator` from `hindsight/core/llm/command_validator.py`. Move that file (or its 1 class) into `hindsight/llm/` before Step 7.

### Step 3 — Land `hindsight/orchestration/` skeletons — ✅ COMPLETE
**Modules:** `context.py`, `session.py`, `worker.py`, `result_sink.py`, `events.py`, `function_selector.py`, `affected_selector.py`. Pipeline modules were empty stubs (filled in steps 4–5).
**Notable additions:** `RunFailedEvent` for partial-stream-after-crash, `session.events()` async iterator for FastAPI WebSocket/SSE handlers, `subscribe()` push API, write-through `AsyncResultSink`. Per-consumer `asyncio.Queue` fan-out, lock-protected emit ordering.
**Tests:** 36 tests in `test_context.py` + `test_core.py` + `test_session.py` covering defaults, `bounded_gather` (order/isolation/concurrency/rate/cancellation/empty), `AsyncResultSink` (sanitization + soft-fail), `select_functions` precedence rules, full event fan-out including partial-stream + multi-consumer.

### Step 4 — Implement `pipeline_code.py` end-to-end — ✅ COMPLETE
- `analyze_function(work_item)`: 4a → 4b, with `context_bundles/{md5(file:fn)[:8]}.json` cache
- `analyze_repo(call_graph_data, filters, num_to_analyze, call_tree_builder)`: dispatches per-function vs call-tree, fans out via `bounded_gather`, emits events
- `analyze_call_tree(root, builder)`: single LLM run + groups issues by `defect_function` with out-of-tree re-pinning
- Rewired `hindsight/analyzers/code_analyzer.py:_run_code_analysis` to drive the new pipeline
- **Legacy code deleted** (was post-Step-4 cleanup at user request): `_run_code_analysis_legacy`, `_run_call_tree_code_analysis`, `_publish_call_tree_issues`, all `_should_analyze_function*` helpers, `_extract_file_path_from_json`, `_get_function_line_count`, `_process_function_entry`, `_generate_temp_function_file`, the `HINDSIGHT_USE_LEGACY_ORCHESTRATION` flag, and the inner `CodeAnalyzer(LLMBasedAnalyzer)` class collapsed to a 30-line `BaseAnalyzer` subclass that only reads results from disk. **code_analyzer.py: 3870 → 2532 lines (−34%)**.
**Tests:** 12 integration tests in `test_pipeline_code.py` (happy path, event sequence, cache hit, failure isolation, empty input, bundle persistence, sanitization, issue filter hook, token callback, call-tree grouping).

### Step 5 — Implement `pipeline_diff.py` — ✅ COMPLETE
- `analyze_function(prompt_data)`: Stage Da → Db with `diff_context_bundles/{md5(fn@file)[:8]}.json` cache
- `analyze_call_tree(tree_dict, diff_context, root)`: single LLM run over diff-marked subtree
- `analyze_diff_per_function(work_items)` / `analyze_diff_call_tree(work_items)`: top-level fan-out methods
- `DiffFunctionWork` / `DiffCallTreeWork` / `DiffRunSummary` typed work items
- Reads `diffContextCollectionProcess.md` + `diffAnalysisProcess.md` directly; uses `PromptBuilder.build_diff_call_tree_prompt` for call-tree mode
- Rewired `git_simple_diff_analyzer.py:_analyze_affected_functions` + added `_build_function_work_items` / `_build_call_tree_work_items` helpers
- **Legacy code deleted:** `_analyze_affected_functions_legacy`, `_run_single_function_analysis`, `_analyze_affected_functions_as_call_trees`, `_run_single_call_tree_analysis`. **git_simple_diff_analyzer.py: 1989 → 1782 lines (−207)**.
**API extensions to support diff's unusual `{out_dir}/{repo}_diff_analysis/analysis/` layout:**
- `AnalysisContext.from_config(..., artifacts_dir: Optional[str] = None)` explicit override
- `AnalysisSession.create(..., analyzer_name: str = "code_analysis")` selects `prompts_sent/{analyzer}/`
**Tests:** 12 integration tests in `test_pipeline_diff.py` mirroring `test_pipeline_code.py`.

### Step 6 — Implement `pipeline_trace.py`, `pipeline_perf.py`, `pipeline_security.py` — ✅ COMPLETE

**Bridge infrastructure (prior turn):**
- New `hindsight/llm/sync_bridge.py`: `SyncStageRunner` runs `StageSpec`s from sync code (spins up short-lived `AsyncLLMClient` + event loop, safe under `asyncio.to_thread`). `make_client_config_from_dict` builder.
- 4 tests in `test_sync_bridge.py`.

**Quick-wins + bridge extensions (prior turn):**
- `one_shot_text_sync` + `one_shot_json_sync` added to `sync_bridge.py` (mirror the async helpers). 3 new tests.
- New `stage_file_summary` factory in `stages.py` (returns `{"summary": "..."}`).
- `command_validator.py` moved to `hindsight/llm/`; legacy path becomes a re-export shim. `core/llm/__init__.py` emptied of eager re-exports (had a latent circular import with `core/prompts/prompt_builder` exposed by the move; no public consumers used the package surface).
- `analysis_runner.py` stale `Claude` import removed.
- `directory_classifier.py` LLM portion migrated to `one_shot_json_sync`.
- `file_or_directory_summary_generator.py` rewritten on top of `SyncStageRunner` + `stage_file_summary` + `build_default_registry` (preserves the sync `get_summary_of_file(root, relative_path)` API).
- `git_simple_diff_analyzer.py`: removed the now-dead `Claude.setup_prompts_logging("diff_analysis")` block at line ~1427 (no longer needed once DirectoryClassifier stopped using Claude statics).

**Diff changed-lines filter (prior turn):**
- New helpers in `pipeline_diff.py`: `_parse_line_number`, `_filter_issues_to_changed_lines`, `_build_changed_lines_map`, `_build_changed_lines_map_from_diff_context`.
- Per-function: filters issues to the function's `changed_lines` set ±`DIFF_LINE_NEIGHBORHOOD` (=2). Drops issues outside changed lines OR on files not in the diff.
- Call-tree: filters using `diff_context.changed_lines_per_file["added"]` per file.
- Both run BEFORE `_apply_issue_filter` (UnifiedIssueFilter) — programmatic, deterministic, fast.
- Defensive: keeps issues whose `line_number` can't be parsed (LLMs occasionally emit variable names).
- 9 new tests in `test_pipeline_diff.py` covering parser, neighborhood, ranges, unparseable lines, file-not-in-diff, no-op-when-no-info, end-to-end drop in per-function path.

**Perf pipeline (prior turn):**
- New `hindsight/orchestration/pipeline_perf.py` (path-based two-stage analysis with in-memory per-function context cache `_InProcessPerfContextCache`).
- `PerfPathWork`, `PerfRunSummary`, `PerfPipeline.analyze_path` / `analyze_paths`, `perf_function_checksum` exports.
- Reuses event types (`RunStartedEvent`, `FunctionStartEvent` etc.) with `function_name` slot repurposed for path IDs like `"root→...→leaf"`.
- 7 integration tests in `test_pipeline_perf.py`: happy path, event sequence, per-path failure isolation (with path-keyed fake LLM), cache reuse across paths, empty input, token callback, checksum determinism.
- `perf_analyzer.py` rewired: drops `PerfAnalysis`/`AnalysisMCPServer`/`RateLimiter`/`run_worker_pool` imports; new `PerfAnalyzer.build_path_work` materializes work items from the AST index; `PerfAnalysisRunner._run_pipeline` drives `PerfPipeline.analyze_paths` via `AnalysisSession`. CLI surface preserved byte-for-byte.

**Trace pipeline (this turn):**
- New `hindsight/orchestration/pipeline_trace.py`: `TracePipeline` with `analyze_trace` (Ta → Tb → Tc per callstack) and `analyze_traces` (bounded_gather fan-out).
- `TraceWork` dataclass (callstack_index, callstack, prompt_content, callstack_data, extracted_file_paths, callstack_text, trace_id).
- `TraceRunSummary` reports successful + failed + cached.
- Publish & cache callbacks (`publish_callback`, `cache_check_callback`) supplied by the runner — keeps the pipeline decoupled from the sync `TraceAnalysisResultsPublisher` + `AnalyzedRecordsRegistry`.
- Tc validator: confident rejection drops; low_confidence keeps with `validation` annotation; runner/parser errors keep with `validation.low_confidence=True` (conservative).
- Issue annotation: every kept issue gets `trace_id`, `Callstack` (text form), `original_callstack` (if available).
- 12 integration tests in `test_pipeline_trace.py`: happy path, event sequence, cache short-circuit, Tc rejection/low-confidence/unparseable, per-trace failure isolation, publish callback invoked, publish failure, token callback, empty work, annotation.

**Trace rewire (this turn):**
- `analyzers/trace_analyzer.py`: deleted `_is_callstack_cached`, `_analyze_single_callstack`, `Claude.setup_prompts_logging` block; replaced legacy `_run_trace_analysis` body with sync prep + `asyncio.run(self._run_trace_pipeline_async(...))`.
- New helpers: `_cache_check_sync` (registry + publisher lookups in to_thread), `_publish_trace_result_sync` (unified filter + publisher add + registry add).
- Dropped imports: `Claude`, `AnalysisMCPServer`, `RateLimiter`, `run_worker_pool`, `TraceAnalysisConfig`, `TraceCodeAnalysis`. `TraceAnalyzer.analyze_function` now raises `NotImplementedError` (interface-only; no callers).
- **trace_analyzer.py: 1517 → 1350 lines (−167)**.

**Response challengers (this turn):**
- `issue_filter/response_challenger.py`: replaced `Claude` + `Tools` + `ResponseChallengerAnalyzer` with `SyncStageRunner` + `stage_response_challenger` + `build_default_registry` (file-reading tools only). Per-issue prompts batched via `runner.run_many` so the httpx pool amortizes. Removed `_setup_response_challenger_prompts_logging` and the `Claude._prompts_dir` static manipulation. Dummy mode + dropped-issue persistence preserved verbatim. **759 → 641 lines (−118)**.
- `issue_filter/trace_response_challenger.py`: same pattern; no tools, just `run_many`. **443 → 416 lines (−27)**.

**Security pipeline (this turn):**
- New `hindsight/orchestration/pipeline_security.py`: `open_security_llm(api_key, config, ...)` async context manager that yields an `(system_prompt, messages) -> str` callable backed by `AsyncLLMClient`. LLM errors swallowed as empty string (matches legacy behavior).
- Moved `CodeNavigationServer` from `hindsight/core/mcp_tools/code_navigation_server.py` → `hindsight/core/lang_util/code_navigation.py` (dropped the unused `FastMCP` decorators — callers already bypassed FastMCP and called `execute_tool` directly).
- `sink_analyzer.py`, `external_input_analyzer.py`, `flow_vulnerability_analyzer.py`: swapped `from ..core.async_infra import RateLimiter` → `from ..llm.rate_limit import AsyncRateLimiter as RateLimiter`; swapped `CodeNavigationServer` import to new location. No other changes — these analyzers were already self-orchestrating around `llm_request_fn`.
- `data_flow_analyzer.py`: removed `Claude/ClaudeConfig/create_llm_provider` from all 3 LLM-using methods; wrapped analyzer construction + `asyncio.run(...)` + retry block in inner async functions using `open_security_llm`. `time.sleep(60)` → `await asyncio.sleep(60)`.

### Step 7 — Delete old stack — ✅ COMPLETE

**Deleted:**
- `hindsight/core/llm/` (entire package: `llm.py`, `code_analysis.py`, `diff_analysis.py`, `perf_analysis.py`, `perf_context_cache.py`, `command_validator.py`, `summary_service.py`, `ttl_manager.py`, `iterative/`, `tools/`, `providers/`)
- `hindsight/core/async_infra/`
- `hindsight/core/mcp_tools/`
- `hindsight/core/trace_util/trace_code_analysis.py` (legacy `TraceCodeAnalysis`)
- `hindsight/core/trace_util/trace_prompt_builder.py` (only used by legacy trace stack)
- Legacy tests: `tests/core/llm/`, `tests/core/mcp/`, `tests/core/async_infra/`, `tests/analyzers/test_perf_analyzer.py`, `tests/analyzers/test_sink_analyzer.py`, `tests/analyzers/test_data_flow_analyzer.py`, `tests/integration/test_list_files_recursive.py`, `tests/token_management/test_token_selection.py`, `tests/token_management/test_apple_connect_refresh.py`

**Stragglers fixed during deletion:**
- `hindsight/core/trace_util/file_name_extractor_from_trace.py` — migrated from `Claude` + `send_message_with_system` to `one_shot_text_sync`. The constructor stores an `LLMClientConfig` instead of a `Claude` instance.
- `hindsight/core/prompts/prompt_builder.py` — removed orphan `from ..llm.llm import Claude, ClaudeConfig, create_llm_provider` import (was unused).

**Verification:**
- All 5 CLIs launch: `code_analyzer`, `trace_analyzer`, `git_simple_diff_analyzer`, `perf_analyzer`, `data_flow_analyzer`.
- Full test suite: **1108 passed, 3 skipped** (full `hindsight/tests/` run).
- New-stack subset: **154 passed** in `hindsight/tests/llm/` + `hindsight/tests/orchestration/`.
- `grep -rn "from hindsight\.core\.llm\|from \.\.core\.llm\|from \.\.\.core\.llm\|from \.\.core\.async_infra\|from hindsight\.core\.async_infra\|from \.\.core\.mcp_tools" hindsight --include="*.py" | grep -v __pycache__` — zero matches.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Behavioral drift in JSON extraction | Preserve `extract_json`/`validate_json`/`fallback_guidance` logic verbatim from existing iterative analyzers |
| Output directory races under concurrent FastAPI requests | Session-scoped wrapper around `OutputDirectoryProvider` from step 3 |
| Sync-only callers of `Claude` (issue_filter, directory_classifier, file_summary_generator) | Migrated in step 6 with injected `AsyncLLMClient` |
| `httpx` SigV4 signing edge cases | Port the existing signing code verbatim from `aws_bedrock_provider.py`; just async-ify the transport |
| Existing tests assume sync interfaces | Step 7 swaps in fresh tests; intermediate steps keep old tests passing |
| CLI flag drift | Audit recorded in this doc; each `cli_*.py` re-implements argparse from the same recorded shape |
| `~/llm_artifacts/` path drift | Audit recorded in this doc; each writer uses the same `OutputDirectoryProvider` calls as today |

---

## Resume points

If work stops mid-migration:

- **After step 1:** New `hindsight/llm/` package committed but unused. Resume by starting step 2.
- **After step 2:** Tools committed. Resume by starting step 3.
- **After step 3:** Orchestration skeleton committed. Resume by implementing `pipeline_code.py`.
- **After step 4:** `code_analyzer` on new stack, all other analyzers on old stack. Both work. Resume with `pipeline_diff.py`.
- **After step 5:** `code_analyzer` + `git_simple_diff_analyzer` on new stack. Resume with trace/perf/security migration. *(We are here, partially through Step 6.)*
- **After step 6:** All analyzers on new stack; old stack still exists but unreferenced. Resume with deletion (step 7).
- **After step 7:** Done. FastAPI integration is the next project.

Each step is a self-contained PR. CLI behavior + `~/llm_artifacts/` layout must verify after every step.

---

## Progress log

| Date | Step | What landed | Tests | Notes |
|---|---|---|---|---|
| 2026-06-29 | Step 1 | `hindsight/llm/` core (13 modules) | 38 | Package imports cleanly; lazy `PromptBuilder` import works around legacy circular import |
| 2026-06-29 | Step 2 | `hindsight/llm/tools/` (8 modules, 9 tools) | +21 (59 total) | `CommandValidator` import still points at `core/llm/` — relocate before Step 7 |
| 2026-06-29 | Step 3 | `hindsight/orchestration/` (9 modules + 3 pipeline stubs) | +36 (95 total) | FastAPI streaming primitives: `session.events()` async iterator, `subscribe()` push API, `RunFailedEvent` |
| 2026-06-29 | Step 4 | `pipeline_code.py` + rewire | +12 (107 total) | code_analyzer.py 3870 → 2532 lines after legacy delete |
| 2026-06-29 | Step 5 | `pipeline_diff.py` + rewire | +12 (119 total) | git_simple_diff_analyzer.py 1989 → 1782 lines after legacy delete; `AnalysisContext.artifacts_dir` override + `AnalysisSession.analyzer_name` added for diff's unusual layout |
| 2026-06-29 | Step 6 partial | `hindsight/llm/sync_bridge.py` (SyncStageRunner); migrated `issue_filter/llm_filter.py` + `issue_filter/trace_relevance_filter.py` | +4 (123 total) | 14 files still pending — see Step 6 table |
| 2026-06-29 | Step 6 quick wins | `command_validator.py` relocated; `analysis_runner.py` stale `Claude` import removed; `directory_classifier.py` LLM portion migrated to `one_shot_json_sync`; `file_or_directory_summary_generator.py` rewritten on top of `SyncStageRunner` + `stage_file_summary` + `build_default_registry`; dead `Claude.setup_prompts_logging` block in `git_simple_diff_analyzer.py` removed; new `one_shot_text_sync` + `one_shot_json_sync` + `stage_file_summary` exported from `hindsight.llm` | +3 (126 total) | Latent `core/llm/__init__.py` circular import surfaced and fixed (no external consumers of the package surface) |
| 2026-06-29 | Step 6 diff filter | New `_filter_issues_to_changed_lines` + helpers in `pipeline_diff.py`; per-function uses `prompt_data.changed_lines`, call-tree uses `diff_context.changed_lines_per_file["added"]`; runs before `_apply_issue_filter` | +9 (135 total) | Programmatic enforcement of the prompt's "issues on changed lines only" rule, with ±2-line neighborhood; defensively keeps issues whose `line_number` can't be parsed |
| 2026-06-29 | Step 6 perf | New `hindsight/orchestration/pipeline_perf.py` (path-based two-stage with in-memory per-function context cache); `PerfPathWork`/`PerfRunSummary`/`perf_function_checksum` exports; `perf_analyzer.py` rewired to drive `PerfPipeline.analyze_paths` via `AnalysisSession`; legacy `PerfAnalysis`/`AnalysisMCPServer`/`RateLimiter`/`run_worker_pool` imports dropped | +7 (142 total) | CLI surface preserved; legacy `core/llm/perf_analysis.py` + `perf_context_cache.py` still on disk but no longer referenced by `perf_analyzer.py` |
| 2026-06-29 | Step 6 trace | New `pipeline_trace.py` with `TracePipeline`/`TraceWork`/`TraceRunSummary` — Ta → Tb → Tc with publish + cache callbacks; `trace_analyzer.py` rewired to drive it via `AnalysisSession`. Legacy `Claude.setup_prompts_logging` block + `_is_callstack_cached` + `_analyze_single_callstack` removed | +12 (154 total) | trace_analyzer.py 1517 → 1350 lines after legacy delete |
| 2026-06-29 | Step 6 challengers | `issue_filter/response_challenger.py` + `issue_filter/trace_response_challenger.py` migrated to `SyncStageRunner` + `stage_response_challenger` + `build_default_registry`. Per-issue prompts batched via `runner.run_many` | — (still 154) | response_challenger.py 759 → 641 (-118); trace_response_challenger.py 443 → 416 (-27). Legacy `Claude._prompts_dir` static manipulation removed (was broken under concurrency) |
| 2026-06-29 | Step 6 security | Relocated `CodeNavigationServer` to `core/lang_util/code_navigation.py` (dropped unused FastMCP decorators). New `orchestration/pipeline_security.py` with `open_security_llm` async-context-manager wrapping `AsyncLLMClient`. `sink_analyzer.py` + `external_input_analyzer.py` + `flow_vulnerability_analyzer.py` imports updated. `data_flow_analyzer.py` 3 LLM-using methods rewired to use `open_security_llm` (also: `time.sleep(60)` → `asyncio.sleep(60)`) | — (still 154) | Production code only — security analyzers self-orchestrate around `llm_request_fn` so no pipeline class needed |
| 2026-06-29 | Step 7 | `rm -rf hindsight/core/llm hindsight/core/async_infra hindsight/core/mcp_tools`; removed `core/trace_util/trace_code_analysis.py` + `trace_prompt_builder.py`. Migrated `core/trace_util/file_name_extractor_from_trace.py` from `Claude.send_message_with_system` to `one_shot_text_sync`. Removed orphan import in `core/prompts/prompt_builder.py`. Deleted obsolete test directories | — (still 154 in new stack; 1108 + 3 skipped in full suite) | All 5 CLIs (code, trace, diff, perf, data_flow) launch unchanged |

**Snapshot at last save:** 154 new-stack tests pass; 1108 passed + 3 skipped in the full `hindsight/tests/` suite; all 5 CLIs (`code_analyzer`, `trace_analyzer`, `git_simple_diff_analyzer`, `perf_analyzer`, `data_flow_analyzer`) launch unchanged. The legacy stack (`hindsight/core/llm/`, `hindsight/core/async_infra/`, `hindsight/core/mcp_tools/`, plus `trace_code_analysis.py` + `trace_prompt_builder.py`) is deleted.

---

## How to resume Step 6 (concrete, in order)

(Steps 6 and 7 are complete — section retained for archival reference only.)

1. **Sanity check first:** `/Users/sgurivireddy/Hindsight/.venv/bin/python -m pytest hindsight/tests/llm/ hindsight/tests/orchestration/` — must show 142 passing. `python -m hindsight.analyzers.code_analyzer --help` etc. should print argparse usage for all 4 CLIs.
2. **Trace pipeline (1 turn):**
   - Implement `hindsight/orchestration/pipeline_trace.py`: 5 stages (Ta context → Tb analysis → response_challenger → Tc validator → trivial filter). Stage factories all exist.
   - Rewire `hindsight/analyzers/trace_analyzer.py`. Delete legacy trace methods.
   - Tests in `hindsight/tests/orchestration/test_pipeline_trace.py`.
3. **Response challengers (1 turn):**
   - Migrate `hindsight/issue_filter/response_challenger.py` (Level 3, 759 LOC, uses tools).
   - Migrate `hindsight/issue_filter/trace_response_challenger.py` (443 LOC).
4. **Security pipeline (1–2 turns, biggest chunk):**
   - Implement `hindsight/orchestration/pipeline_security.py` with shared `analyze_for_security()`.
   - Rewire 4 analyzers: `sink`, `data_flow`, `external_input`, `flow_vulnerability`. ~3.4K LOC total — each has its own quirks, audit before changing.
5. **Step 7 (final, 1 turn):**
   - `grep -rn "from hindsight\.core\.llm\|from \.\.core\.llm\|from \.\.\.core\.llm" hindsight --include="*.py" | grep -v core/llm/ | grep -v __pycache__` — must return zero matches outside `hindsight/core/llm/` itself.
   - Delete the three legacy packages.
   - Run full test suite; verify CLIs.

---

## Key references

- Current pipelines: [hindsight/analyzers/code_analyzer.py](../hindsight/analyzers/code_analyzer.py), [hindsight/analyzers/trace_analyzer.py](../hindsight/analyzers/trace_analyzer.py), [hindsight/diff_analyzers/git_simple_diff_analyzer.py](../hindsight/diff_analyzers/git_simple_diff_analyzer.py)
- Current LLM core (to be replaced): [hindsight/core/llm/](../hindsight/core/llm/)
- Current async infra (to be replaced): [hindsight/core/async_infra/](../hindsight/core/async_infra/)
- Current iterative analyzers (logic preserved into `stages.py`): [hindsight/core/llm/iterative/](../hindsight/core/llm/iterative/)
- Existing stage-A on-disk caches: `~/llm_artifacts/{repo}/context_bundles/`, `~/llm_artifacts/{repo}/diff_context_bundles/`
- Prompt templates (untouched): [hindsight/core/prompts/](../hindsight/core/prompts/)
