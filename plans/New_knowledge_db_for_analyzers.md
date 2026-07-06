# New Knowledge DB for Analyzers

## Goal

Replace the existing per-analyzer knowledge stores with a single, unified `KnowledgeStore` shared across all analyzers (code, trace, diff). The store becomes a shared source of invariants across callstacks:

- A function analyzed in callstack A leaves invariants/summaries that callstack B can retrieve instead of re-deriving.
- Cross-stack invariants (threading, ownership, lifecycle) become visible to any single stack's analysis.
- The LLM can read from and write to the store via tool calls; searchable by function, file, and topic.

Clean break — no backward compatibility with the existing `TraceKnowledgeStore`. All DBs deleted; analyzers cut over to the new store.

## Why redundancy reduction matters

When two callstacks share a utility function `parseJSON`, both Stage 4a/4b runs today independently re-read its source, re-derive its callees, and re-reason about its invariants. The new store memoizes per-function analysis keyed by the function's source checksum, so the second callstack inherits the first's work.

## Schema

Single unified `learnings` table. `kind` discriminates between summary / invariant / finding / optimization.

```sql
CREATE TABLE learnings (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  subject       TEXT NOT NULL,           -- 'trace' | 'code' | 'diff'
  repo_name     TEXT NOT NULL,
  kind          TEXT NOT NULL,           -- 'summary' | 'invariant'
  entity_key    TEXT NOT NULL,           -- normalized: 'src/foo.swift::myFunc' or 'src/foo.swift' or free-form concept
  file_path     TEXT,
  function_name TEXT,
  checksum      TEXT,                    -- function source checksum; NULL for non-function entities
  summary       TEXT NOT NULL,
  details       TEXT,
  tags          TEXT,                    -- JSON array
  confidence    REAL NOT NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_learnings_func ON learnings(subject, repo_name, function_name, file_path);
CREATE INDEX ix_learnings_file ON learnings(subject, repo_name, file_path);
CREATE INDEX ix_learnings_chk  ON learnings(subject, repo_name, checksum);
CREATE INDEX ix_learnings_kind ON learnings(subject, repo_name, kind);

CREATE VIRTUAL TABLE learnings_fts USING fts5(summary, details, content='learnings', content_rowid='id');
-- INSERT/UPDATE/DELETE triggers keep FTS in sync
```

UPSERT uniqueness key: `(subject, repo_name, kind, entity_key, checksum, tags)`. Same learning re-asserted updates rather than duplicates.

The old `function_optimizations` table is gone. Findings/optimizations are **not stored** — issues belong in analysis output, not the knowledge store.

SQLite WAL mode + `check_same_thread=False` for concurrent async writers.

## Storage path

All subjects share a single per-repo DB: `~/llm_artifacts/<repo>/knowledge.db`.
The old `~/.hindsight/trace_knowledge.db` is abandoned.

## LLM tool surface (4 tools, used by all analyzers)

| Tool | Params | Returns |
|---|---|---|
| `read_knowledge_function` | `function_name, file_path?, checksum?, kind?` | `[Learning]` |
| `read_knowledge_file` | `file_path, kind?` | `[Learning]` |
| `read_knowledge_topic` | `query, kind?, tags?, max_results?` | `[Learning]` — FTS5 ranked |
| `store_knowledge` | `kind, entity_key, file_path?, function_name?, checksum?, summary, details?, tags?, confidence` | `{ok, id}` |

`subject` is **not** an LLM-facing param — each call site fixes it (`'code'` from code pipeline, `'trace'` from trace pipeline, `'diff'` from diff pipeline). The LLM never has to think about subjects.

**What gets stored**: general technical knowledge about the project (function summaries, file/module roles, cross-cutting invariants). **NOT** bug findings or defect reports — those go in analysis output. Valid `kind` values: `'summary' | 'invariant'`.

Two design principles:
- **Reads are cheap, writes are deliberate.** Reads tolerate misses (return `[]`). Writes require a confidence score to discourage low-confidence noise.
- **Idempotent UPSERT.** Re-asserting the same learning updates rather than duplicates.

## Cache aggression

Per-stage prompt design is **encourage, not mandate** for both reads and writes. On a cache hit during context-collection stages:
- Inject the prior learning(s) as a "Prior knowledge from previous analyses" block in the system prompt.
- The LLM may verify, expand, or trust the prior knowledge.

The prompts encourage `store_knowledge(kind='summary')` once a function is understood, and `store_knowledge(kind='invariant')` for cross-cutting rules. Neither is required — the store warms up over runs.

## Force-reset semantics

`--force-llm-analysis` calls `store.delete_subject('code')`. Trace and diff rows untouched. Users opting into force-analysis are saying "ignore caches," and the scope is bounded to the analyzer they're forcing.

## Concurrency

SQLite WAL handles N async writers. With `CODE_ANALYZER_DEFAULT_WORKERS = 3`, the practical contention is small.

Two concurrent callstacks analyzing the same function `F` will both miss the cache, both run, both UPSERT. **v1 accepts the collision** — keeps the code simple. A per-function `asyncio.Lock` can be added later if profiling shows real waste.

## Phase order

### Phase 0 — Inventory affected call sites

Grep for: `TraceKnowledgeStore`, `lookup_knowledge`, `store_learning`, `lookup_function_optimization`, `store_function_optimization`, `trace_knowledge.db`. Expected hits:
- `hindsight/core/knowledge/trace_knowledge_store.py` (to delete)
- `hindsight/core/knowledge/__init__.py` (re-export)
- `FlowVulnerabilityAnalyzer`, `DataFlowAnalyzer` constructors + method calls
- Trace-analyzer prompts that reference the old tool names by string (grep `.md` too)
- Any tests under `hindsight/tests/`

Complete list before touching any code — paste back for review.

### Phase 1 — New `KnowledgeStore`

Create `hindsight/core/knowledge/knowledge_store.py` with the unified schema and Python API:

```python
class KnowledgeStore:
    def __init__(self, db_path, repo_name)
    def recall_by_function(self, subject, function_name, file_path=None, checksum=None, kind=None) -> list
    def recall_by_file(self, subject, file_path, kind=None) -> list
    def recall_by_topic(self, subject, query, kind=None, tags=None, max_results=10) -> list
    def record_learning(self, subject, kind, entity_key, summary, *, file_path=None,
                        function_name=None, checksum=None, details=None, tags=None,
                        severity=None, confidence) -> int
    def delete_subject(self, subject) -> int   # used by --force-llm-analysis
    def close(self)
```

Re-export from `hindsight/core/knowledge/__init__.py`. Delete `trace_knowledge_store.py`.

### Phase 2 — New tool module

`hindsight/llm/tools/knowledge_tools.py` implements the 4 tools. The tool factory binds a fixed `subject` per call site:

```python
register_knowledge_tools(registry, store, subject='code')   # called by code pipeline
register_knowledge_tools(registry, store, subject='trace')  # called by trace pipeline
```

Trace and code analyzers register under the same names but with different subjects baked in.

### Phase 3 — `AnalysisSession` owns the store

`hindsight/orchestration/session.py` constructs `KnowledgeStore(db_path=<output>/knowledge.db, repo_name=...)` and exposes it on the session. Pass it into `build_default_registry` along with `subject='code'`.

Lifetime tied to the session; close on `__aexit__`. On construction failure, log and set to `None` — pipeline must degrade gracefully everywhere.

### Phase 4 — Migrate trace analyzers

- `FlowVulnerabilityAnalyzer` and `DataFlowAnalyzer`: swap `TraceKnowledgeStore(...)` for `KnowledgeStore(db_path=..., repo_name=...)`. Swap method calls: `store_learning(...)` → `record_learning(subject='trace', kind='summary', ...)`, etc.
- Trace prompts: replace literal references to old tool names (`lookup_knowledge`, `store_learning`, `lookup_function_optimization`, `store_function_optimization`) with the new four.
- Add `register_knowledge_tools(registry, store, subject='trace')` wherever the trace LLM session is built.

**Acceptance gate**: trace flow must work end-to-end after this phase.

### Phase 5 — Code pipeline integration

- Add `register_knowledge_tools(...)` to `build_default_registry` with `subject='code'`.
- Add the 4 tools to `FULL_CONTEXT_TOOLS` and `ANALYSIS_TOOLS` in `hindsight/llm/stages.py`.
- In `hindsight/orchestration/pipeline_code.py::_run_stage_4a`, before invoking the LLM: query `recall_by_function` with checksum filter; if hits, format and prepend a "Prior knowledge" section to the system prompt.
- Update `hindsight/core/prompts/contextCollectionProcess.md`: instruct LLM to use `recallByFunction` for unknown callees and to call `recordLearning(kind='summary', ...)` before emitting the final bundle.
- Update `hindsight/core/prompts/analysisProcess.md`: encourage `recallByTopic` for cross-cutting patterns; `recordLearning(kind='finding', ...)` for cross-stack issues.

### Phase 6 — Force-analysis reset

Where `hindsight/analyzers/code_analyzer.py` honors `force_llm_analysis`, call `store.delete_subject('code')`.

### Phase 7 — Tests

- `hindsight/tests/core/knowledge/test_knowledge_store.py` — schema, UPSERT, kind/subject filtering, FTS5, checksum staleness, `delete_subject`.
- `hindsight/tests/llm/tools/test_knowledge_tools.py` — 4 tools via registry, subject binding, None-store degradation.
- `hindsight/tests/orchestration/test_pipeline_code.py` (extend) — two-callstack scenario: stack A records summary, stack B sees it in prompt; checksum mismatch ignored.
- Update or delete any existing trace-analyzer tests that referenced `TraceKnowledgeStore` directly.

## Risks specific to clean break

1. **Phase 0 must be exhaustive.** A missed grep hit becomes a runtime `AttributeError`. Worth doing the inventory before any code changes and reviewing the full list.
2. **Trace prompts that hardcode old tool names** are easy to miss — they're in markdown, not Python. Grep `*.md` too.
3. **No data migration.** Every user gets a fresh empty store. The first few runs after merge are slower (no cached learnings). Worth noting in the commit message.

## Suggested commit order

1. Phase 0 inventory (grep results — reviewed, not committed).
2. Phases 1+2 together: new store + new tools, no integration yet. Tests included.
3. Phase 3: session owns the store. No behavior change yet.
4. Phase 4: migrate trace analyzers. **Trace flow must still work end-to-end** — acceptance gate.
5. Phase 5: wire into code pipeline + update prompts.
6. Phase 6: force-reset hookup.
7. Phase 7: cross-callstack integration test (some sub-tests land in earlier commits).
