# Two-Stage LLM Analysis — Implementation Plan

## Overview

This plan splits the single-stage LLM analysis step into two sequential sub-stages across **two analysis pipelines**:

1. **`CodeAnalysis` pipeline** (`code_analyzer.py`) — Stage 4 Step 2: "LLM analysis — new functions only"
2. **`DiffAnalysis` pipeline** (`git_simple_diff_analyzer.py`) — the per-function analysis inside `_analyze_affected_functions()`

In both pipelines the monolithic LLM call is replaced with:

- **Stage A — Context Collection**: Gathers all relevant code context without performing analysis. Outputs a self-contained JSON context bundle.
- **Stage B — Analysis**: Receives the context bundle as input and performs the actual bug/performance analysis. Outputs the existing result JSON schema unchanged.

Both stages in both pipelines have access to knowledge-base tools backed by a per-repo SQLite database stored at:

```
llm_artifacts/<repo_name>/knowledge_base/<first-16-hex-chars-of-sha256(abs_repo_path)>.db
```

The chunk-based analysis path in `git_simple_diff_analyzer.py` (`analyze_diff_with_llm`, `_analyze_single_chunk`, `_analyze_chunk_independently`) is **removed**. The diff pipeline uses function-level analysis exclusively.

---

## Motivation

Combining data collection and analysis in one LLM call creates several problems:

1. **Token pressure at decision time** — the LLM is still mid-exploration when it must also produce a final JSON verdict, causing rushed or shallow analysis.
2. **No reusable context** — every function re-reads the same class hierarchies and related files from scratch; the Janus knowledge pattern shows this is expensive and avoidable.
3. **Difficult to improve** — a single monolithic prompt is hard to tune independently for "gather more context" vs. "reason more carefully".

---

## Affected Components

### Shared (both pipelines)

| Component | Location |
|-----------|----------|
| **New** — Knowledge store | `hindsight/core/knowledge/knowledge_store.py` |
| **New** — Knowledge tool wrappers | `hindsight/core/llm/tools/knowledge_tools.py` |
| Tools registry | `hindsight/core/llm/tools/tools.py` |

### CodeAnalysis pipeline (`code_analyzer.py`)

| Component | Location |
|-----------|----------|
| Code Analyzer (orchestrator) | `hindsight/analyzers/code_analyzer.py` |
| Analysis orchestrator | `hindsight/core/llm/code_analysis.py` |
| Prompt builder | `hindsight/core/prompts/prompt_builder.py` |
| Analysis prompt | `hindsight/core/prompts/detailedAnalysisProcess.md` |
| Output schema | `hindsight/core/prompts/outputSchema.json` |
| **New** — Stage 4a prompt | `hindsight/core/prompts/contextCollectionProcess.md` |
| **New** — Stage 4b prompt | `hindsight/core/prompts/analysisProcess.md` |

### DiffAnalysis pipeline (`git_simple_diff_analyzer.py`)

| Component | Location |
|-----------|----------|
| Diff analyzer (orchestrator) | `hindsight/diff_analyzers/git_simple_diff_analyzer.py` |
| Diff analysis orchestrator | `hindsight/core/llm/diff_analysis.py` |
| **Deleted** — Chunk-based analysis prompt | `hindsight/core/prompts/diffAnalysisPrompt.md` |
| **Deleted** — Function diff analysis prompt | `hindsight/core/prompts/functionDiffAnalysisPrompt.md` |
| **New** — Diff context collection prompt | `hindsight/core/prompts/diffContextCollectionProcess.md` |
| **New** — Diff analysis prompt (stage B) | `hindsight/core/prompts/diffAnalysisProcess.md` |

---

## Knowledge Base

### Database Location

```
llm_artifacts/<repo_name>/knowledge_base/<first-16-hex-chars-of-sha256(abs_repo_path)>.db
```

The `llm_artifacts/<repo_name>/` directory is already created by the output directory provider. The `knowledge_base/` sub-directory is created on first access.

### Schema

Mirrors the Janus schema but extended with a `stage` column to distinguish context-collection knowledge from analysis knowledge:

```sql
CREATE TABLE function_knowledge (
    function_name   TEXT NOT NULL,
    file_name       TEXT NOT NULL,    -- basename of the file
    summary         TEXT NOT NULL,    -- LLM-written description / analysis result
    related_context TEXT,             -- JSON: callers, callees, relevant types, constants
    confidence      REAL NOT NULL DEFAULT 0.8,
    stage           TEXT NOT NULL DEFAULT 'context',  -- 'context' | 'analysis'
    analyzed_at     REAL NOT NULL,
    fts_rowid       INTEGER,
    PRIMARY KEY (function_name, file_name)
);

CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    entity_type,
    entity_key,       -- "function_name@file_name"
    search_text,      -- concatenation of name, file, summary, related_context
    tokenize='porter unicode61'
);
```

WAL mode is used for safe concurrent access. Falls back to LIKE-based search when FTS5 is unavailable.

### New Python Class: `KnowledgeStore`

**File**: `hindsight/core/knowledge/knowledge_store.py`

```
KnowledgeStore(db_path: str)
  ├── store(entity_key, summary, related_context=None, confidence=0.8, stage='context')
  ├── lookup(query: str, limit=5) → List[dict]
  ├── get_exact(function_name: str, file_name: str) → Optional[dict]
  └── _get_db_path(repo_path: str) → str  [classmethod]
```

The `db_path` is resolved by `_get_db_path` using the SHA-256 pattern above.

---

## New Tools: `lookup_knowledge` and `store_knowledge`

**File**: `hindsight/core/llm/tools/knowledge_tools.py`

These are registered on the existing `Tools` object when a `KnowledgeStore` instance is injected. They follow the same API tool-call pattern already used by the rest of the tool set.

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `lookup_knowledge` | `query: str` | FTS5 or LIKE search; returns ≤5 ranked results |
| `store_knowledge` | `entity_key: str`, `summary: str`, `related_context: str \| null`, `confidence: float`, `stage: str` | Upsert into SQLite + FTS5 |

**`entity_key` format** (same as Janus): `"function_name@file_name"`, e.g. `MyClass.fetchData@MyClass.swift`

---

## Stage 4a — Context Collection

### Goal

Gather *all* code artefacts needed to understand the function under analysis. Produce a structured JSON context bundle. Do **not** reason about bugs.

### Input to LLM

The same content currently sent in the single-stage call:

- Primary function source code with **line numbers preserved** (1-indexed, matching source file line numbers, not relative to function start)
- Invoking functions (callers) from the call tree with their **original line numbers**
- Called functions (callees) with their **original line numbers**
- Relevant data types (structs/enums/classes) referenced by the function
- Constants and globals used
- File summaries for related files (from `ProjectSummaryGenerator`)
- Directory structure excerpt

**Line number preservation rule**: all code snippets in the input JSON must carry their original source-file line numbers (e.g. `"start_line": 142`). The context bundle output must repeat these unchanged.

### Tools Available (Stage 4a)

Priority order (LLM is instructed to follow this strictly):

1. **`lookup_knowledge`** — **always first**; check what is already known before reading files
2. `getDirectoryListing` — check file sizes before reading
3. `getImplementation` — preferred for classes/structs/enums
4. `getSummaryOfFile` — quick context on large files
5. `readFile` — small non-class files only
6. `findSpecificFilesWithSearchString` — locate files by content
7. `runTerminalCmd` — last resort
8. **`store_knowledge`** — called **after** understanding any new function/type; persists for future runs

### Stage 4a Prompt Strategy

**New prompt file**: `hindsight/core/prompts/contextCollectionProcess.md`

Key instructions:

```
ROLE: You are a context-gathering agent. Your ONLY job is to collect
every piece of code needed to reason about the primary function.
Do NOT identify bugs. Do NOT offer suggestions. Do NOT draw conclusions.

KNOWLEDGE TOOL PRIORITY (MANDATORY):
Before calling any file-reading tool, always call lookup_knowledge
with the function name or type name you need. If the knowledge base
returns a relevant result with confidence ≥ 0.8, use it instead of
reading the file. Only read the file if lookup_knowledge returns
nothing useful.

LINE NUMBER RULE:
Every code snippet you include in the output MUST carry the original
source-file line numbers (start_line, end_line). Never use relative
line numbers.

AFTER GATHERING:
For every function or type you read that is NOT already in the
knowledge base, call store_knowledge with stage='context' to cache
your understanding for future runs.

OUTPUT: Return ONLY a JSON context bundle (schema below). No analysis,
no issue descriptions, no markdown.
```

### Stage 4a Output — Context Bundle Schema

```json
{
  "primary_function": {
    "name": "functionName",
    "file_path": "path/to/file.ext",
    "start_line": 142,
    "end_line": 189,
    "source": "  142: func fetchData(...) {\n  143:   ...\n  189: }"
  },
  "callers": [
    {
      "name": "callerFunctionName",
      "file_path": "path/to/caller.ext",
      "start_line": 55,
      "end_line": 72,
      "source": "  55: func callerFunctionName() {\n  ...  72: }"
    }
  ],
  "callees": [
    {
      "name": "calleeFunctionName",
      "file_path": "path/to/callee.ext",
      "start_line": 210,
      "end_line": 230,
      "source": "  210: func calleeFunctionName(...) { ... }"
    }
  ],
  "data_types": [
    {
      "name": "SomeStruct",
      "file_path": "path/to/SomeStruct.swift",
      "start_line": 10,
      "end_line": 45,
      "source": "  10: struct SomeStruct { ... }"
    }
  ],
  "constants_and_globals": [
    {
      "name": "kMaxRetries",
      "value": "3",
      "file_path": "path/to/constants.swift",
      "line_number": 7
    }
  ],
  "file_summaries": {
    "path/to/file.ext": "This file implements the data fetching layer ...",
    "path/to/SomeStruct.swift": "Defines SomeStruct used for ..."
  },
  "knowledge_hits": [
    {
      "entity_key": "helperFunc@Helper.swift",
      "summary": "Cached from previous run: ...",
      "stage": "context"
    }
  ],
  "collection_notes": "Brief notes on what was found / not found."
}
```

All `source` fields use the format `"  <line_number>: <code>"` to make line numbers unambiguous.

---

## Stage 4b — Analysis

### Goal

Perform the actual bug and performance analysis using the context bundle from Stage 4a. Output the existing result JSON schema unchanged.

### Input to LLM

- The **full context bundle** from Stage 4a (injected as part of the user prompt)
- Any prior analysis results retrieved from the knowledge base for this function

### Tools Available (Stage 4b — Smaller Set)

1. **`lookup_knowledge`** — **always first**; check for prior analysis of this or similar functions
2. `getImplementation` — only if the context bundle is missing something critical
3. `readFile` — targeted reads for small files not already covered by the context bundle
4. `runTerminalCmd` — for searching or cross-file exploration when the context bundle is insufficient
5. **`store_knowledge`** — called at the end with `stage='analysis'` to persist the final verdict

`getFileContentByLines`, `checkFileSize`, `list_files`, `inspectDirectoryHierarchy`, and `getSummaryOfFile` are intentionally omitted — the context bundle should already cover directory structure and large-file summaries. If the LLM needs to reach for `readFile` or `runTerminalCmd` frequently in Stage 4b, it signals that Stage 4a needs to collect more context.

### Stage 4b Prompt Strategy

**New prompt file**: `hindsight/core/prompts/analysisProcess.md`

Key instructions:

```
ROLE: You are a senior software engineer performing a deep code review.
The context bundle below contains all the code you need.

KNOWLEDGE TOOL PRIORITY (MANDATORY):
Before analysing, call lookup_knowledge with the primary function name
and file. If a prior analysis result is found (stage='analysis',
confidence ≥ 0.8), consider it carefully — it may still be valid.

ANALYSIS RULES:
- Analyse ONLY the primary_function.
- Use callers, callees, data_types, and constants_and_globals as
  supporting context.
- Confidence threshold: report only issues with confidence ≥ 0.8.
- Include exact line numbers from the context bundle. These are
  original source-file line numbers — use them directly.
- Categories: logicBug, performance only.
- Do not report speculative or stylistic issues.

ADDITIONAL TOOLS (use only if context bundle is insufficient):
- readFile: targeted read of a small file not already in the bundle
- runTerminalCmd: cross-file search or exploration as a last resort
  Use these sparingly — frequent use indicates Stage 4a under-collected.

AFTER ANALYSIS:
Call store_knowledge with:
  entity_key = "<function_name>@<file_name>"
  summary    = one-paragraph plain-text description of what this
               function does and any confirmed issues
  stage      = 'analysis'
  confidence = your overall confidence in the analysis

OUTPUT: Respond ONLY with valid JSON matching the output schema below.
```

The existing `outputRequirements.md` and `outputSchema.json` are reused unchanged for the final output.

### Stage 4b Output

Identical to the current single-stage output:

```json
[
  {
    "file_path": "path/to/file.ext",
    "file_name": "file.ext",
    "function_name": "functionName",
    "line_number": "142",
    "severity": "high",
    "issue": "Brief description",
    "description": "Detailed explanation",
    "suggestion": "How to fix",
    "category": "logicBug",
    "issueType": "logicBug"
  }
]
```

Line numbers in `line_number` come directly from the context bundle's `start_line`/`end_line` fields — no guessing.

---

## Updated Pipeline Flow

### CodeAnalysis pipeline — Before (single-stage)

```
temp_file (func + call tree JSON)
  └──► LLM (collect + analyse) ──► JSON issues array
```

### CodeAnalysis pipeline — After (two-stage)

```
temp_file (func + call tree JSON)
  └──► Stage 4a LLM (collect, no analysis)
         ├── lookup_knowledge (before each file read)
         ├── getImplementation / readFile / ...
         └── store_knowledge (after each new function/type)
         └──► context_bundle.json   (saved to llm_artifacts/<repo>/context_bundles/)
  └──► Stage 4b LLM (analyse only)
         ├── lookup_knowledge (check for prior analysis)
         ├── [getImplementation / readFile / runTerminalCmd — fallback only]
         └── store_knowledge stage='analysis' (persist verdict)
         └──► JSON issues array   (same schema as today)
```

### DiffAnalysis pipeline — Before (single-stage, per affected function)

```
_build_function_diff_prompt() → prompt_data dict
  └──► DiffAnalysis.analyze_function_diff(prompt_data)
         └──► LLM (collect + analyse) ──► JSON issues array
```

### DiffAnalysis pipeline — After (two-stage, per affected function)

```
_build_function_diff_prompt() → prompt_data dict
  └──► Stage Da LLM (collect, no analysis)
         ├── lookup_knowledge (before each file read)
         ├── getImplementation / readFile / ...
         └── store_knowledge (after each new function/type)
         └──► diff_context_bundle.json   (saved to llm_artifacts/<repo>/diff_context_bundles/)
  └──► Stage Db LLM (analyse only)
         ├── lookup_knowledge (check for prior analysis)
         ├── [getImplementation / readFile / runTerminalCmd — fallback only]
         └── store_knowledge stage='diff_analysis' (persist verdict)
         └──► JSON issues array   (same schema as today)
```

### Caching

- The existing **checksum cache** (Stage 4, Step 1) in the CodeAnalysis pipeline remains unchanged. If a checksum hit is found, both Stage 4a and Stage 4b are skipped.
- The **context bundles** are saved to disk so Stage B can be retried independently if it fails:
  - CodeAnalysis: `llm_artifacts/<repo_name>/context_bundles/<checksum>.json`
  - DiffAnalysis: `llm_artifacts/<repo_name>/diff_context_bundles/<func_name_hash>.json`
- The DiffAnalysis pipeline has no checksum cache today; context bundle persistence is the only retry optimisation.

---

## Code Changes Summary

### Shared Changes (both pipelines)

#### 1. New: `hindsight/core/knowledge/knowledge_store.py`

- `KnowledgeStore` class with SQLite + FTS5 backend
- `_get_db_path(repo_path)` class method computing `llm_artifacts/<repo_name>/knowledge_base/<sha256[:16]>.db`
- `store()`, `lookup()`, `get_exact()` methods

#### 2. New: `hindsight/core/llm/tools/knowledge_tools.py`

- `lookup_knowledge` tool definition and executor
- `store_knowledge` tool definition and executor
- Both tools integrated into the existing `Tools` class when `KnowledgeStore` is injected

#### 3. Modified: `hindsight/core/llm/tools/tools.py`

- Accept optional `knowledge_store: KnowledgeStore` parameter
- Register knowledge tools when the store is present
- Expose a `with_stage_b_tools` method that returns a copy of the tool set containing only Stage B tools: `lookup_knowledge`, `store_knowledge`, `getImplementation`, `readFile`, `runTerminalCmd` (drops `getSummaryOfFile`, `list_files`, `inspectDirectoryHierarchy`, `checkFileSize`, `getFileContentByLines`)

---

### CodeAnalysis Pipeline Changes

#### 4. Modified: `hindsight/core/llm/code_analysis.py`

- Replace `run_analysis()` with two new methods called in sequence:
  - `run_context_collection(json_data, checksum: str) -> dict`
    - Builds Stage 4a prompt via `PromptBuilder`
    - Calls `claude.run_iterative_analysis()` with full tool set
    - Parses and validates the context bundle JSON
    - Saves context bundle to `llm_artifacts/<repo_name>/context_bundles/<checksum>.json`
  - `run_analysis_from_context(context_bundle: dict) -> list`
    - Builds Stage 4b prompt, injecting context bundle
    - Calls `claude.run_iterative_analysis()` with Stage B tool set
    - Returns the issues list

#### 5. New: `hindsight/core/prompts/contextCollectionProcess.md`

Stage 4a system prompt covering:
- Role: pure context collector
- Knowledge tool priority (check `lookup_knowledge` before any file read)
- Line number preservation rule
- `store_knowledge` after each new function/type read
- Context bundle output schema and format requirements

#### 6. New: `hindsight/core/prompts/analysisProcess.md`

Stage 4b system prompt covering:
- Role: senior code reviewer
- Knowledge tool priority (check prior analysis via `lookup_knowledge`)
- Stage B tool set reminder (`readFile`, `runTerminalCmd` available as fallback)
- Analysis rules and confidence threshold
- `store_knowledge` after analysis
- Defers to existing `outputRequirements.md` for JSON output schema

#### 7. Modified: `hindsight/core/prompts/prompt_builder.py`

- Add `build_context_collection_prompt(json_content, ...) -> Tuple[str, str]`
- Add `build_analysis_from_context_prompt(context_bundle: dict, ...) -> Tuple[str, str]`
- Remove `build_complete_prompt()` — replaced by the two new methods above

#### 8. Modified: `hindsight/analyzers/code_analyzer.py`

- Initialize `KnowledgeStore` for the current repo during `_initialize_publisher_subscriber()`
- Pass `KnowledgeStore` instance to `CodeAnalysis` (via `AnalysisConfig`)
- Update the analysis loop (lines 1264–1380) to call `run_context_collection()` then `run_analysis_from_context()` instead of the current single `analyzer.analyze_function(json_data)` call
- Log Stage 4a and Stage 4b separately in timing/token metrics

---

### DiffAnalysis Pipeline Changes

#### 9. Modified: `hindsight/core/llm/diff_analysis.py`

- **Remove** all chunk-based methods: `run_analysis()`, `analyze_diff_with_llm()`, `_analyze_single_chunk()`, `_analyze_chunk_independently()`, `_build_diff_analysis_user_message()`
- **Remove** `analyze_function_diff()` — replaced by the two stage methods below
- Add `run_diff_context_collection(prompt_data: dict) -> dict` method
  - Accepts the same `prompt_data` dict previously passed to `analyze_function_diff()`
  - Builds Stage Da prompt from `diffContextCollectionProcess.md`
  - Full tool set + knowledge tools
  - Saves diff context bundle to `llm_artifacts/<repo_name>/diff_context_bundles/<func_name_hash>.json`
- Add `run_diff_analysis_from_context(diff_context_bundle: dict) -> list` method
  - Accepts the diff context bundle produced by Stage Da
  - Builds Stage Db prompt from `diffAnalysisProcess.md`, injecting the bundle
  - Stage B tool set + knowledge tools
  - Returns the issues list (same schema as the deleted `analyze_function_diff()`)
- Accept `knowledge_store: KnowledgeStore` parameter in `DiffAnalysis.__init__()` and pass it to `Tools`
- `DiffAnalysisConfig` no longer needs to support chunk-based fields (`num_blocks_to_analyze`, `max_characters_per_diff_analysis`)

#### 10. New: `hindsight/core/prompts/diffContextCollectionProcess.md`

Stage Da system prompt, analogous to `contextCollectionProcess.md` but diff-aware:
- Role: pure context collector for diff analysis
- Knowledge tool priority (check `lookup_knowledge` before any file read)
- **Diff-specific instructions**: preserve `+`/`-` line markers and original source-file line numbers in all collected code snippets; include the `is_modified` flag on related functions
- `store_knowledge` after each new function/type read
- Diff context bundle output schema (see below)

#### 11. New: `hindsight/core/prompts/diffAnalysisProcess.md`

Stage Db system prompt:
- Role: senior code reviewer focused on diff-introduced regressions
- Knowledge tool priority (check prior analysis via `lookup_knowledge`)
- **Diff-specific instructions**: focus analysis on lines marked `+`; treat `[MODIFIED]` related functions as higher-risk context; prefer reporting issues on changed lines for PR comment accuracy
- Stage B tool set reminder
- `store_knowledge` with `stage='diff_analysis'` after analysis
- Defers to existing JSON output schema

#### 12. Modified: `hindsight/diff_analyzers/git_simple_diff_analyzer.py`

- **Remove** chunk-based entry points and helpers: `analyze_diff_with_llm()`, `_analyze_single_chunk()`, `_analyze_multiple_chunks()`, `_analyze_chunk_independently()`, `_create_diff_chunks()`, `_analyze_diff_stats_per_file()`, `_split_diff_by_files()`
- **Remove** `run_function_level_analysis()` alternative entry point — `run_analysis()` becomes the single entry point and calls `_analyze_affected_functions()` directly
- `run_analysis()` flow: generate diff → extract changed lines → build AST → detect affected functions → call `_analyze_affected_functions()`
- Initialize `KnowledgeStore` once in `run_analysis()` and pass it down to `_analyze_affected_functions()`
- Pass `KnowledgeStore` to each `DiffAnalysis` instance created per function (previously line 1272)
- No changes to `_build_function_diff_prompt()` or the prompt_data structure — Stage Da accepts it unchanged
- Log Stage Da and Stage Db separately in timing/token metrics

---

## Diff Context Bundle Schema

The Stage Da output mirrors the CodeAnalysis context bundle schema but adds diff-specific fields:

```json
{
  "primary_function": {
    "name": "functionName",
    "file_path": "path/to/file.ext",
    "start_line": 142,
    "end_line": 189,
    "affected_reason": "modified",
    "changed_lines": [145, 148, 152],
    "source": "  142: func fetchData(...) {\n  145:+    newCall()\n  189: }"
  },
  "callers": [
    {
      "name": "callerFunctionName",
      "file_path": "path/to/caller.ext",
      "start_line": 55,
      "end_line": 72,
      "is_modified": false,
      "source": "  55:   func callerFunctionName() { ... }"
    }
  ],
  "callees": [
    {
      "name": "calleeFunctionName",
      "file_path": "path/to/callee.ext",
      "start_line": 210,
      "end_line": 230,
      "is_modified": true,
      "source": "  210:+  func calleeFunctionName(...) { ... }"
    }
  ],
  "data_types": [ ... ],
  "constants_and_globals": [ ... ],
  "file_summaries": { ... },
  "diff_context": {
    "all_changed_files": ["path/to/file.ext", "other/file.ext"],
    "total_files_changed": 2,
    "is_part_of_wider_change": true
  },
  "knowledge_hits": [ ... ],
  "collection_notes": "..."
}
```

The `source` field format is `"  <line_number>: [+/-/ ] <code>"` — identical to `_get_function_code_with_diff_markers()` output, preserving both original line numbers and diff markers.

---

## Line Number Preservation — Detailed Rules

1. **Input to Stage 4a / Stage Da**: Both pipelines already have `start_line` / `end_line` in their input data. The code passed to the collection stage uses the `"  <N>: [+/-/ ] <code>"` format with original source-file line numbers.

2. **Stage A output**: Context bundle `source` fields carry original source-file line numbers unchanged.

3. **Stage B input**: The context bundle is injected as-is. The prompt instructs: "line numbers in the `source` fields are original source-file line numbers — use them directly in your output".

4. **Stage B output**: The `"line_number"` field in the issues JSON uses the line numbers from the context bundle, not re-derived numbers. For the diff pipeline, the prompt additionally instructs: "prefer reporting the `+`-prefixed lines, as these are the changed lines that will map to PR comments".

---

## File Artifacts Directory Structure

```
llm_artifacts/
└── <repo_name>/
    ├── code_analysis/              # existing — per-function JSON results
    ├── code_insights/              # existing — file summaries
    ├── context_bundles/            # NEW — Stage 4a outputs, keyed by function checksum
    │   ├── <checksum8>.json
    │   └── ...
    ├── diff_context_bundles/       # NEW — Stage Da outputs, keyed by func_name hash
    │   ├── <func_name_hash>.json
    │   └── ...
    └── knowledge_base/             # NEW — per-repo SQLite knowledge store (shared)
        └── <sha256[:16]>.db
```

The knowledge base is shared between both pipelines for the same repo — knowledge collected during code analysis is reused during diff analysis and vice versa.

---

## Token and Timing Impact

| Metric | Expected Change |
|--------|----------------|
| Stage A tokens per function | ~60–70% of current single-stage tokens (no JSON output schema needed) |
| Stage B tokens per function | ~40–50% of current (smaller tool set, no exploration needed) |
| Total tokens per new function | ~100–120% of current (slight increase first run; reduced on repeated runs via knowledge base) |
| Cache hit rate improvement | Knowledge base eliminates redundant file reads across functions in the same codebase |
| After knowledge base is warm | Stage A and B token usage drops significantly; well-known classes may need zero file reads |
| Diff pipeline warm benefit | Code analysis runs warm the KB; diff analysis runs on the same repo benefit immediately |

---

## Error Handling

| Failure | Recovery |
|---------|----------|
| Stage 4a / Da fails (API error) | Log, skip Stage B, record as failed analysis |
| Stage 4a / Da returns invalid JSON | Retry once with a "fix your JSON" follow-up message; if still invalid, log and record as failed |
| Stage 4b / Db fails | Context bundle is already saved; Stage B can be retried independently on the next run |
| Knowledge DB unavailable | Both stages in both pipelines run without knowledge tools (graceful degradation)

---

## Testing Plan

### New Test Files

#### `hindsight/tests/core/knowledge/test_knowledge_store.py`

Tests for `KnowledgeStore` covering all persistence and retrieval behaviour.

| Test class | Cases |
|------------|-------|
| `TestKnowledgeStoreInit` | DB file is created under the correct path; WAL mode is set; FTS5 table is created (or graceful fallback when unavailable) |
| `TestKnowledgeStoreDbPath` | `_get_db_path` produces a deterministic path under `llm_artifacts/<repo_name>/knowledge_base/`; same repo path always yields the same DB filename; two different repo paths yield different filenames |
| `TestKnowledgeStoreStore` | New entry is inserted; duplicate entry is upserted (row count stays at 1); FTS5 index is updated on upsert; `stage` field is persisted correctly (`'context'` / `'analysis'`); `confidence` is stored as-is |
| `TestKnowledgeStoreLookup` | FTS5 query returns ranked results (≤5); fallback LIKE search returns results when FTS5 is disabled; empty DB returns empty list; query with no match returns empty list |
| `TestKnowledgeStoreGetExact` | Exact `(function_name, file_name)` hit returns the row; miss returns `None` |
| `TestKnowledgeStoreGracefulDegradation` | `KnowledgeStore` constructed with an unwritable path raises a clear error at init time, not at lookup/store time |

#### `hindsight/tests/core/llm/tools/test_knowledge_tools.py`

Tests for the `lookup_knowledge` and `store_knowledge` tool wrappers.

| Test class | Cases |
|------------|-------|
| `TestLookupKnowledgeTool` | Returns formatted results when KB has matches; returns "no results" message when KB is empty; query is passed through to `KnowledgeStore.lookup` |
| `TestStoreKnowledgeTool` | Calls `KnowledgeStore.store` with correct arguments; confirms success message is returned; `stage` parameter is forwarded correctly |
| `TestToolsWithKnowledge` | `Tools` registers knowledge tools when `KnowledgeStore` is injected; `Tools` does not expose knowledge tools when no store is provided; `execute_tool_use` routes `lookup_knowledge` and `store_knowledge` correctly |

### Modified Test Files

#### `hindsight/tests/core/llm/test_code_analysis.py`

| New test class | Cases |
|----------------|-------|
| `TestRunContextCollection` | Returns a valid context bundle dict on success; saves bundle to `llm_artifacts/<repo>/context_bundles/<checksum>.json`; all code snippets in the bundle carry `start_line` / `end_line`; raises (or returns `None`) if LLM returns invalid JSON after retry |
| `TestRunAnalysisFromContext` | Returns a list of issues matching the output schema; passes context bundle to the prompt builder; `store_knowledge` is called once after analysis; returns empty list `[]` when LLM finds no issues |
| `TestRunAnalysisOrchestration` | `run_context_collection()` then `run_analysis_from_context()` are called in order; skips both stages on checksum cache hit; if `run_context_collection` fails, `run_analysis_from_context` is not called; uses pre-saved context bundle from disk to skip Stage 4a on retry |
| `TestAnalysisConfigKnowledgeStore` | `AnalysisConfig` accepts optional `knowledge_store` field; `CodeAnalysis.__init__` passes the store through to `Tools` |

Existing test classes (`TestAnalysisConfig`, `TestCodeAnalysisInitialization`, `TestCodeAnalysisFileFiltering`, `TestCodeAnalysisTokenTracking`, `TestCodeAnalysisResultProcessing`, `TestCodeAnalysisCacheManagement`) require no changes — the new methods do not alter non-LLM behaviour.

#### `hindsight/tests/core/prompts/test_prompt_builder.py`

| New test cases (added to existing file) | Description |
|-----------------------------------------|-------------|
| `test_build_context_collection_prompt_returns_tuple` | `build_context_collection_prompt()` returns a non-empty `(system_prompt, user_prompt)` tuple |
| `test_build_context_collection_prompt_includes_function_name` | The function name from the input JSON appears in the built prompt |
| `test_build_analysis_from_context_prompt_includes_bundle` | The serialised context bundle is present in the user prompt |
| `test_build_analysis_from_context_prompt_references_output_schema` | The output schema section from `outputRequirements.md` is included |

#### `hindsight/tests/core/llm/tools/test_tools.py` *(new file — currently no dedicated Tools integration test)*

| Test class | Cases |
|------------|-------|
| `TestToolsKnowledgeRegistration` | Knowledge tools appear in `get_available_tools()` when store is injected; knowledge tools are absent when no store is provided |
| `TestToolsStageBSet` | `with_stage_b_tools()` returns a tool set containing only `lookup_knowledge`, `store_knowledge`, `getImplementation`, `readFile`, `runTerminalCmd`; the Stage B set does not contain `getSummaryOfFile`, `list_files`, `inspectDirectoryHierarchy`, `checkFileSize`, `getFileContentByLines` |

---

### DiffAnalysis Pipeline Tests

#### `hindsight/tests/core/llm/test_diff_analysis.py` *(new test class — add to existing file or create)*

| New test class | Cases |
|----------------|-------|
| `TestRunDiffContextCollection` | Returns a valid diff context bundle dict on success; saves bundle to `llm_artifacts/<repo>/diff_context_bundles/<hash>.json`; `+`/`-` markers are preserved in all `source` fields; `is_modified` flag is present on callers/callees; raises (or returns `None`) if LLM returns invalid JSON after retry |
| `TestRunDiffAnalysisFromContext` | Returns a list of issues matching the output schema; passes diff context bundle to the prompt builder; `store_knowledge(stage='diff_analysis')` is called once after analysis; returns `[]` when LLM finds no issues |
| `TestAnalyzeFunctionDiffOrchestration` | `run_diff_context_collection()` then `run_diff_analysis_from_context()` are called in order; if `run_diff_context_collection()` fails, `run_diff_analysis_from_context()` is not called; uses pre-saved diff context bundle from disk to skip Stage Da on retry |
| `TestDiffAnalysisConfigKnowledgeStore` | `DiffAnalysisConfig` accepts optional `knowledge_store` field; `DiffAnalysis.__init__` passes the store through to `Tools` |

#### `hindsight/tests/diff_analyzers/test_git_simple_diff_analyzer.py` *(existing file)*

**Delete** the following test classes — they test the chunk-based path that is being removed:
- `TestCreateDiffChunks`
- `TestAnalyzeDiffWithLlmMetricConsistency`
- `TestSplitDiffByFiles`

**Add** the following new test class:

| New test class | Cases |
|----------------|-------|
| `TestAnalyzeAffectedFunctionsKnowledge` | `KnowledgeStore` is initialised once in `run_analysis()` and passed to `_analyze_affected_functions()`; the same `KnowledgeStore` instance is passed to every `DiffAnalysis` instance created in the loop; when no repo path is available, analysis proceeds without a knowledge store (graceful skip) |

---

### Test Conventions

Follow the patterns already established in the codebase:

- Use `pytest` with class-based test organisation.
- Use `tempfile.mkdtemp()` / `shutil.rmtree()` via `@pytest.fixture` for any file-system side effects; always clean up in `yield`-based teardown.
- Mock LLM calls via `unittest.mock.patch` on `Claude.run_iterative_analysis` — do not make real API calls in unit tests.
- For `KnowledgeStore` tests use an in-memory SQLite database (`":memory:"`) where possible; use a temp-dir path for tests that verify the file location itself.
- Each test method name describes the scenario: `test_<method>_<condition>_<expected_outcome>`.
