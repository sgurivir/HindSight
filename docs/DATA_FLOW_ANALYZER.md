# Data Flow Analyzer

The Data Flow Analyzer generates hierarchical call trees from AST-based call graphs and uses LLM-powered analysis to identify which functions accept external (untrusted) input. It combines static analysis (Steps 1-3) with LLM-driven security classification (Step 4).

## Usage

```bash
python -m hindsight.analyzers.data_flow_analyzer \
    --config config.json \
    --repo /path/to/repo \
    --out-dir ~/llm_artifacts

# Force AST regeneration
python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo --force-recreate-ast

# Custom depth and directory filters
python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo \
    --max-call-depth 30 \
    --include-directories src lib \
    --exclude-directories test vendor

# Custom parallel workers for external input analysis
python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo --workers 8
```

## Pipeline

`DataFlowAnalysisRunner.run()` executes four steps:

### Step 1: Directory Classification

Uses `DirectoryClassifier` (static rules + LLM-based classification) to identify directories to exclude from analysis (vendor code, generated files, tests, etc.). Results are merged with user-provided `--include-directories` and `--exclude-directories`.

### Step 2: AST Call Graph Generation

Generates (or reuses cached) AST-based call graphs for the repository. Output is a `nested_call_graph.json` file mapping each function to its callees with source location metadata (file path, start/end lines).

Skipped if existing AST files are detected (unless `--force-recreate-ast` is set).

### Step 3: Call Tree Generation

Handled by `CallTreeGenerator` (`hindsight/core/lang_util/call_tree_util.py`):

1. **Load** — Parses `nested_call_graph.json` into a `CallGraph` (nodes + directed edges).
2. **Extract implementations** — Builds a map from function names to their source locations (file, line range).
3. **Break cycles** (`create_dag()`) — Computes bottom-up levels for all nodes, then only keeps edges that go from higher-level to strictly lower-level nodes. This produces a DAG while preserving the most semantically meaningful relationships.
4. **Compute subtree depths** — Single O(N+E) bottom-up pass using Kahn's algorithm (topological sort). Each node's depth = max(children depths) + 1.
5. **Build tree** — Starting from DAG root nodes (no incoming edges), recursively constructs tree nodes. Children are sorted by subtree depth (deepest first) so the most complex call chains appear at the top.
6. **Output** — Writes `call_tree.json`, `call_tree.txt` (tree-style indentation), and `call_graph_statistics.json`.

### Step 4: External Input Analysis (LLM — Batched)

Uses an LLM to classify each function as accepting external input or not. This step identifies entry points where untrusted data enters the application.

**How it works (batched mode):**

1. A `CodeNavigationServer` is initialized with the call graph data.
2. All function bodies are **pre-fetched** from the MCP server upfront.
3. Functions are grouped into **batches of up to 8** (configurable via `EXTERNAL_INPUT_BATCH_SIZE`), with each batch sized to fit within the model's context window token budget.
4. Each function in a batch is assigned a **short UUID** (8-char hex) so the LLM's structured response can be reliably correlated back to the correct function.
5. The LLM receives all function bodies in a single prompt and responds with a **JSON array** containing one `{id, ext_input, reason}` object per function.
6. Any functions missing from the batch response are **retried individually** using the single-function fallback (iterative tool-calling mode).
7. Results are merged back into the call tree as `ext_input: true/false` on each node.

**Why batching:**

- Reduces total LLM API requests by ~8x (e.g., 152 functions → ~19 requests instead of 152)
- Avoids hitting rate limits (160 requests per 4-minute window)
- Each batch is a single-turn request (no multi-turn tool calling needed since bodies are pre-fetched)

**Batch sizing — context window safety:**

Batches respect both a hard cap (`batch_size=8`) and a dynamic token budget:
- Token budget = `context_window × 0.5` (leaves 50% for output and safety margin)
- Token estimation: ~4 chars per token
- If a single large function body would push the batch over budget, the batch is finalized early
- Configured via constants: `EXTERNAL_INPUT_BATCH_SIZE`, `EXTERNAL_INPUT_TOKEN_BUDGET_RATIO`, `EXTERNAL_INPUT_CHARS_PER_TOKEN`

**UUID correlation:**

Each function in a batch gets a unique 8-char hex ID (e.g., `a1b2c3d4`). The prompt includes this ID alongside the function name and body. The LLM is instructed to echo the ID back in its response array, enabling unambiguous correlation even if function names are similar or the LLM reorders results.

**Fallback to single-function mode:**

If the LLM's batch response is missing entries for some functions (e.g., it skipped one or the JSON was partially malformed), those functions are automatically retried using the original single-function iterative approach with MCP tool access.

**External input sources considered:**
- User input (UI fields, CLI args, stdin)
- Network data (HTTP requests, WebSocket, RPC, external API responses)
- File system data from user-controlled paths
- IPC from untrusted processes
- Environment variables that can be user-controlled
- Database fields storing user-provided data (indirect)

**Priority classification guidance:**

The analyzer uses priority-based guidance to focus on inputs that represent real attack surfaces and deprioritize framework-constrained inputs that carry no attacker-controlled content.

*High priority (classified as ext_input=true):*

| Category | Rationale |
|----------|-----------|
| Network response/request handlers | Attacker-controlled server or client data |
| URL scheme / deep link / universal link handlers | Attacker-crafted URLs |
| File/document import handlers | Attacker-crafted file content |
| Clipboard/pasteboard reads | Cross-app data injection |
| Deserialization of complex objects from persistence | Type confusion, code execution |
| IPC/XPC/RPC boundaries | Privilege escalation vectors |
| Push notification payload handlers | Server-controlled content |
| Free-form text input flowing into queries/URLs/commands/paths | Injection |
| WebView/JavaScript bridge callbacks | Web-to-native attacks |
| Voice assistant / intent handlers with parameters | Crafted invocations |
| Share extension / app extension input contexts | Untrusted app data |
| Activity continuation / handoff payloads | Crafted activity data |

*Low priority (classified as ext_input=false — deprioritized):*

| Category | Rationale |
|----------|-----------|
| Selection callbacks with framework-bounded index/position | Index constrained by data source, no content |
| Toggle/switch callbacks (boolean only) | No attacker-controlled content possible |
| Segment/slider/stepper callbacks (bounded numeric) | Value range enforced by framework |
| Lifecycle callbacks with no meaningful parameters | No data flows through |
| Internal notification/event observers (same process) | Trusted origin |
| Gesture recognizers carrying only position/state | No user-supplied content |
| Pure UI configuration/layout methods | Internal model data only |
| Simple reads of primitive types from app-managed local storage | App controls the persisted values |

*Key distinction:* The critical question is "Can an attacker control the CONTENT flowing through this input?" A bounded index selected from a list the app itself populates is not attacker-controlled. A URL opened from an external source IS attacker-controlled. The analyzer focuses on content, not on whether a human triggered the action.

**MCP Tools available to the LLM (single-function fallback only):**

| Tool | Description |
|------|-------------|
| `search_symbol` | Search functions by name substring (up to 20 results) |
| `get_symbol` | Get full info: locations, callers, callees |
| `get_function_body` | Read the source code of a function |
| `get_file_ast` | List all functions defined in a file |
| `get_callers` | Get all functions that call a given function |
| `get_callees` | Get all functions called by a given function |
| `find_references` | Combined view: implementations + callers + callees |

**Output:** `call_tree_with_sources.json` — same schema as `call_tree.json` with an added `ext_input` boolean field on each node.

## Output Files

All written to `<out-dir>/<repo-name>/data_flow_analysis/`:

| File | Description |
|------|-------------|
| `call_tree.json` | Full hierarchical tree with a synthetic ROOT node |
| `call_tree.txt` | Human-readable tree with tree-style indentation |
| `call_graph_statistics.json` | Node count, edge count, depth, leaf/root counts, mean edges/node |
| `call_tree_with_sources.json` | Call tree annotated with `ext_input` boolean per function |

## Key Options

| Flag | Default | Description |
|------|---------|-------------|
| `--max-call-depth` | 20 | Maximum depth for level computation during cycle breaking |
| `--sort-by-depth` | True | Sort branches by depth (longest first); use `--no-sort-by-depth` for alphabetical |
| `--show-location` | True | Include file:line info in text output; use `--no-show-location` to hide |
| `--force-recreate-ast` | False | Regenerate AST call graphs even if cached |
| `--workers` | 4 | Number of parallel async workers for external input analysis |

## Architecture

```
DataFlowAnalyzer (BaseAnalyzer)
    - Provides pull_results_from_directory() for downstream consumers

DataFlowAnalysisRunner (AnalysisRunner + UnifiedIssueFilterMixin + ReportGeneratorMixin)
    - Orchestrates the 4-step pipeline
    - Inherits AST generation, sleep prevention, directory structure indexing

CallTreeGenerator (hindsight/core/lang_util/call_tree_util.py)
    - Stateless utility: load -> generate -> write
    - All graph algorithms live here (DAG creation, depth computation, tree building)

CodeNavigationServer (hindsight/core/mcp_tools/code_navigation_server.py)
    - FastMCP-based in-process server
    - Exposes code navigation tools backed by call graph + file system
    - Used by ExternalInputAnalyzer to give the LLM codebase access

ExternalInputAnalyzer (hindsight/analyzers/external_input_analyzer.py)
    - Batched mode: sends up to 8 functions per LLM call with UUID correlation
    - Pre-fetches function bodies, builds batches respecting token budget
    - Falls back to single-function iterative tool-calling for missing results
    - Token-bucket rate limiter (40 req/min default, configurable in constants.py)
    - Annotates call tree with ext_input classification
```

## Async Execution Model

Step 4 uses Python's `asyncio` for cooperative multitasking:

- **Pre-fetch**: All function bodies are fetched synchronously before async processing begins
- **Batch creation**: Functions are grouped into batches (max 8) constrained by token budget
- **Workers**: N coroutines pull batches from a shared queue (each batch = 1 LLM call)
- **Rate limiting**: A token-bucket `RateLimiter` tracks timestamps in a 60-second sliding window; workers `await` when the bucket is full
- **LLM calls**: The synchronous `provider.make_request()` is wrapped via `loop.run_in_executor()` so it doesn't block the event loop
- **Fallback**: Functions missing from a batch response are retried individually with iterative tool calling
- **Tool execution** (fallback only): MCP tool calls are handled in-process (no network round-trip)

This allows processing ~8x more functions per API request while staying within rate limits.

## Cycle Breaking Strategy

The analyzer uses **level-based cycle breaking** rather than simple DFS back-edge removal:

1. Compute levels from the bottom of the graph (leaf nodes = level 0, their callers = level 1, etc.)
2. Only keep edges where the caller's level is strictly greater than the callee's level
3. Self-loops are always removed

This approach preserves call relationships that follow the natural layering of the codebase (higher-level code calling lower-level code) while breaking cycles that represent callbacks, mutual recursion, or circular dependencies.

## Complexity

- Depth computation: O(N + E) — single topological pass
- Sorting: O(N log N)
- Tree construction: O(N)
- **Steps 1-3 total: O(N log N + E)** for typical graphs
- **Step 4:** O(⌈F/B⌉) LLM requests in the common case, where F = number of functions and B = batch size (default 8). Falls back to O(F * K) in worst case if all batch responses fail (K = avg tool iterations per function, bounded by `EXTERNAL_INPUT_MAX_TOOL_ITERATIONS = 10`).
