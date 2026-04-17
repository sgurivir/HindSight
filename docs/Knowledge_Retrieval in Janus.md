# Knowledge Retrieval in Janus

## a) Tools Provided to the LLM

The `AnalyzerTools` class (`janus/llm_reasoning/llm/tools.py`) exposes six tools. These tools are not invoked through provider-specific APIs. Instead, they are described in the system prompt and the LLM returns JSON-embedded requests in its response text:

```json
{"tool": "read_file", "file_path": "Foo.m", "reason": "..."}
```

The host Python code regex-extracts these, executes the tool, and injects the result back as a user message. This mechanism is provider-agnostic and works identically for Claude, AWS Bedrock, and other providers.

### Available Tools

| Tool | Purpose |
|------|---------|
| `run_terminal_command` | Runs safe shell commands (`ls`, `find`, `grep`, `wc`, `head`, `tail`, `cat`, `tree`, `file`) in the project directory |
| `read_file` | Reads a file with optional `start_line`/`end_line` range; returns numbered lines |
| `find_file_path` | Finds a file by name via `FileContentProvider` (fast index) or `os.walk` fallback |
| `batch_find_files` | Batch version of `find_file_path` — returns which files are in-repo vs. system libraries in one call |
| `lookup_knowledge` | Searches the SQLite knowledge base for previously-learned function summaries |
| `store_knowledge` | Persists new LLM learnings about a function into the SQLite knowledge base |

The knowledge tools (`lookup_knowledge` / `store_knowledge`) are only registered when a `KnowledgeStore` instance is provided to `AnalyzerTools`. When present, the LLM is instructed to always call `lookup_knowledge` before `read_file`, and to always call `store_knowledge` after analyzing a function for the first time.

---

## d) How SQLite is Used to Store and Retrieve Knowledge

### Database Location

Each repository gets its own SQLite database, addressed by the SHA-256 hash of the resolved repo path:

```
~/.janus/knowledge/<first-16-hex-chars-of-sha256(abs_repo_path)>.db
```

This means the same repo always maps to the same database file, and different repos never share a database.

### Schema

The database has two tables:

```sql
-- Main knowledge table
CREATE TABLE function_knowledge (
    function_name   TEXT NOT NULL,   -- exact mangled name from callstack frame
    file_name       TEXT NOT NULL,   -- basename as it appears in the callstack
    summary         TEXT NOT NULL,   -- LLM-written description of the function
    memory_behavior TEXT,            -- observed alloc/dealloc patterns
    confidence      REAL NOT NULL DEFAULT 0.0,
    analyzed_at     REAL NOT NULL,   -- Unix timestamp
    fts_rowid       INTEGER,         -- back-reference into the FTS5 table
    PRIMARY KEY (function_name, file_name)
);

-- FTS5 virtual table for full-text search
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    entity_type,
    entity_key,     -- "function_name@file_name"
    search_text,    -- concatenation of name, file, summary, memory_behavior
    tokenize='porter unicode61'
);
```

The database is opened in WAL mode for safe concurrent access. FTS5 is optional — if the SQLite build does not include it, the store falls back silently to `LIKE`-based search.

### Storing Knowledge

When the LLM calls `store_knowledge`, it provides:

- `entity_key` — in the format `"function_name@file_name"`, e.g. `-[CLDispatchSilo prepareAndRunBlock:]@CLDispatchSilo.m`
- `summary` — a plain-text description of what the function does (max ~500 words)
- `memory_behavior` — optional description of allocation/deallocation patterns observed
- `confidence` — a score from 0.0 to 1.0 (default 0.8)

The store upserts the row (INSERT OR UPDATE) and keeps the FTS5 index in sync by deleting the stale FTS entry and inserting a fresh one. The FTS `search_text` field is built by concatenating the function name, filename, summary, and memory behavior into a single searchable string.

### Retrieving Knowledge

There are two retrieval paths:

**1. Exact lookup** (`get_function`) — used before LLM analysis begins.
At the start of each callstack analysis, `_gather_prior_knowledge` iterates every frame in the alloc and dealloc stacks and performs an exact `(function_name, file_name)` lookup. Any hits are formatted and injected into the prompt as a "Prior Knowledge" block, allowing the LLM to understand those functions without re-reading their source files.

**2. Keyword search** (`lookup`) — used by the LLM mid-analysis.
The LLM calls `lookup_knowledge(query=...)` with a function name fragment, filename fragment, or keyword. The store runs an FTS5 phrase query (`"quoted for safety"`) and returns up to 5 ranked results. If FTS5 is unavailable, it falls back to a four-column `LIKE` search across `function_name`, `file_name`, `summary`, and `memory_behavior`.

### Prioritising Lookups Over File Reads

The hierarchical analyzer detects when the LLM requests both `lookup_knowledge` and `read_file` in the same response batch. In that case, it executes only the lookup calls first, returns those results to the LLM, and defers the file reads. This gives the LLM a chance to skip the file read entirely if the knowledge base already has a fresh answer — avoiding unnecessary source reads and keeping token usage low.

### Effect Across Runs

Because the database persists on disk, knowledge accumulates across analysis runs. A function analyzed once is never re-read in future runs unless the stored entry is stale or the LLM's confidence is low. For large codebases with recurring callstack patterns, this significantly reduces both LLM token consumption and wall-clock time.

---

## Summary: SQLite as a Persistent Learning Cache

SQLite serves as the long-term memory of the Janus analyzer. Each time the LLM learns something about a function — what it does, how it allocates memory — that knowledge is stored by key (`function_name@file_name`) in a local database tied to the repository. On subsequent runs, that knowledge is injected automatically into every analysis that touches the same functions. The combination of exact-key lookup (for known frames) and FTS5 full-text search (for exploratory queries) means the LLM can retrieve relevant context with a single tool call, rather than reading and re-parsing source files it has already studied.
