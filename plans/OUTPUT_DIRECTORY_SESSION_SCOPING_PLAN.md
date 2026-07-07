# Output Directory Session-Scoping Plan

**Status:** 📋 PROPOSED — no code written yet
**Blocking for:** [FASTAPI_INTEGRATION_PLAN.md](FASTAPI_INTEGRATION_PLAN.md) Phase 1 (concurrent API analyses)
**Depends on:** [ASYNC_ORCHESTRATION_REWRITE_PLAN.md](ASYNC_ORCHESTRATION_REWRITE_PLAN.md) (✅ complete)
**Last updated:** 2026-07-06

---

## Problem

`OutputDirectoryProvider` ([hindsight/utils/output_directory_provider.py](../hindsight/utils/output_directory_provider.py)) is a **process-global singleton** holding mutable state — `_repo_path` and `_custom_base_dir` — set via `.configure()`. Every artifact path in the system is derived from it.

- **~11 configure sites** (analyzer `run()` entrypoints) call `.configure(repo_path, base_dir)`.
- **~35 read sites** call `get_output_directory_provider().get_repo_artifacts_dir()` — spread across tools ([llm/tools/registry.py:113](../hindsight/llm/tools/registry.py#L113), [shell.py:144](../hindsight/llm/tools/shell.py#L144)), prompt builders ([prompt_builder.py](../hindsight/core/prompts/prompt_builder.py)), issue filters ([issue_filter/](../hindsight/issue_filter/)), AST ([core/ast_index.py:66](../hindsight/core/ast_index.py#L66)), report, and utils.

**Why it breaks under FastAPI:** two concurrent analyses in one process race on the single `_repo_path`/`_custom_base_dir`. Whichever calls `.configure()` last wins for *everyone* — Analysis A's tools, prompts, filters, and results start writing into Analysis B's directory. `get_repo_artifacts_dir()` doesn't even hold the lock on read, so it's a torn read on top of a logical clobber.

**Second, subtler failure:** the API drives `AnalysisSession` **directly** (`create()` → `ensure_ast()` → `analyze_repo()`) and never goes through an analyzer `run()`, so `.configure()` is *never called* on the API path. Consumers would read whatever a previous request left behind — or raise `ConfigurationError`. **The session does not currently establish provider state at all** — confirmed: `AnalysisContext`/`AnalysisSession` compute their own frozen paths but never touch the singleton.

**Cache-persistence tie-in (req #4):** the on-disk checksum cache (`results/code_analysis/*.json`, read by `check_existing_result`) only survives across runs if the artifacts root is *stable per repository*. Today it's keyed by repo basename under an ad-hoc base dir. Keying it by `repository_id` is what makes a re-analysis hit the cache instead of re-running the LLM.

---

## Root cause

There are **two** path systems that must agree but are wired independently:

1. **`AnalysisContext`** — session-scoped, correct: frozen fields (`artifacts_dir`, `code_insights_dir`, `results_dir`, `context_bundles_dir`, …) computed from `output_base_dir` + repo basename. Used by the session, tools' `ToolContext`, result sink.
2. **`OutputDirectoryProvider`** — process-global, hazardous: read by the ~35 legacy consumers the session *doesn't* thread paths into (prompt builders, filters, ast_index, report, some tools).

The fix must make system #2 **inherit its scope from #1, per session**, without a 35-site rewrite.

---

## Approach — `contextvars`-backed provider + session-established scope

**Chosen: back the provider's mutable state with a `contextvars.ContextVar` and have `AnalysisSession` set/reset it in `__aenter__`/`__aexit__`.**

Why this is correct and minimal:
- `contextvars` is the standard tool for *ambient state isolated per async task*. `asyncio` copies the current context when a task is created, so every `bounded_gather` child (and every `asyncio.to_thread` call) inherits the session's scope automatically.
- **The ~35 read sites and ~11 configure sites do not change.** They keep calling `get_output_directory_provider().get_repo_artifacts_dir()` / `.configure(...)`; only the *storage* behind those calls moves from a shared attribute to a ContextVar.
- Two concurrent sessions run in two separate asyncio tasks (the FastAPI background-job pattern), so each sees its own ContextVar value. No lock, no clobber, no torn reads.

**Rejected alternative — full dependency injection (Approach A):** thread an `OutputDirectoryProvider` instance from the session through every prompt builder, filter, tool, ast_index, and report helper. Architecturally cleaner (no ambient global) but touches all ~35 read sites plus their call chains — high churn, high regression risk, and much of it is deep sync code. Deferred as a possible future cleanup; not needed for correctness once the ContextVar isolates state.

### Verified propagation (why the ContextVar reaches every consumer)

| Path | Mechanism | Inherits scope? |
|---|---|---|
| Cross-function fan-out | `bounded_gather` → `asyncio.gather` over coroutines ([worker.py:120](../hindsight/orchestration/worker.py#L120)) — each wrapped in a Task that copies context at creation | ✅ |
| Blocking file I/O in tools / result sink | `asyncio.to_thread` copies `contextvars.copy_context()` into the worker thread | ✅ |
| AST orchestration | `ast_index.py` reads provider in the **main session task** before dispatching | ✅ |
| AST parallel parse | `ProcessPoolExecutor` in [cast_util.py](../hindsight/core/lang_util/cast_util.py) — **separate processes**, receive paths as pickled args, not the in-process ContextVar; isolated per process so no clobber | ✅ (by construction) |
| Prior-result store lookup | `ThreadPoolExecutor` in [code_analysis_publisher.py:299](../hindsight/results_store/code_analysis_publisher.py#L299) / [trace_analysis_publisher.py:157](../hindsight/results_store/trace_analysis_publisher.py#L157) — **does NOT auto-copy contextvars** | ⚠️ audit (see Risks) |

---

## Design sketch

### 1. `output_directory_provider.py` — state → ContextVar (API preserved)

```python
import contextvars
from dataclasses import dataclass
from contextlib import contextmanager
from typing import Optional

@dataclass(frozen=True)
class _OutputScope:
    repo_path: str
    custom_base_dir: Optional[str]

_CURRENT_SCOPE: contextvars.ContextVar[Optional[_OutputScope]] = \
    contextvars.ContextVar("hindsight_output_scope", default=None)

class OutputDirectoryProvider:
    """Facade preserved for the ~46 existing call sites. State lives in a
    ContextVar so concurrent asyncio tasks are isolated."""
    _instance = None
    _lock = Lock()
    def __new__(cls): ...          # unchanged singleton facade (stateless now)

    def configure(self, repo_path: str, custom_base_dir: Optional[str] = None) -> None:
        _CURRENT_SCOPE.set(_OutputScope(repo_path, custom_base_dir))

    def get_repo_artifacts_dir(self, repo_path: Optional[str] = None) -> str:
        scope = _CURRENT_SCOPE.get()
        effective = repo_path or (scope.repo_path if scope else None)
        if not effective:
            raise ConfigurationError("OutputDirectoryProvider not configured and no repo_path provided")
        base = scope.custom_base_dir if scope else None
        return get_repo_artifacts_dir(effective, base)   # existing free function, unchanged

    def get_custom_base_dir(self) -> Optional[str]:
        s = _CURRENT_SCOPE.get(); return s.custom_base_dir if s else None
    def is_configured(self) -> bool:
        return _CURRENT_SCOPE.get() is not None
    def reset(self) -> None:            # conftest autouse fixture keeps working
        _CURRENT_SCOPE.set(None)

@contextmanager
def output_scope(repo_path: str, custom_base_dir: Optional[str] = None):
    """Explicit scope for standalone provider use outside a session
    (e.g. onboarding-time AST warmup)."""
    token = _CURRENT_SCOPE.set(_OutputScope(repo_path, custom_base_dir))
    try:
        yield
    finally:
        _CURRENT_SCOPE.reset(token)
```

- **`configure()` semantics unchanged for the CLI**: single-threaded run sets the ContextVar in the main context; every subsequent read sees it; process exit discards it. No reset needed.
- All existing signatures (`configure`, `get_repo_artifacts_dir`, `get_custom_base_dir`, `is_configured`, `reset`, `get_output_directory_provider()`) are preserved byte-for-byte.

### 2. `orchestration/session.py` — establish scope over the session

```python
async def __aenter__(self) -> "AnalysisSession":
    self._output_token = _CURRENT_SCOPE.set(
        _OutputScope(self.ctx.repo_path, self.ctx.output_base_dir)
    )
    ...                                  # existing setup
    return self

async def __aexit__(self, exc_type, exc, tb) -> None:
    ...                                  # existing teardown
    _CURRENT_SCOPE.reset(self._output_token)
```

- Uses the **same source of truth as `AnalysisContext`** (`ctx.repo_path`, `ctx.output_base_dir`), so the legacy provider consumers and the context's frozen paths are guaranteed to agree.
- `__aenter__` and `__aexit__` run in the same task context → token reset is valid.
- Children created in the body (`bounded_gather`, `to_thread`) copy the context *after* the set → inherit correctly.

**Contract for callers:** analysis must run inside `async with AnalysisSession.create(ctx) as session:` and in its **own asyncio task** (the FastAPI job registry already gives each analysis a dedicated task). `ensure_ast()` and `analyze_repo()` must be called in the `async with` body so they're inside scope.

### 3. Cache persistence — key the base dir by `repository_id` (API layer)

This is a *value* decision that lives in the FastAPI layer, enabled by the scoping fix:
- API computes `base_dir = settings.ARTIFACTS_BASE_DIR / str(repository_id)`.
- Builds `AnalysisContext` with `output_base_dir=base_dir` → the session's `__aenter__` sets the provider scope to the same `base_dir`.
- Because `AnalysisContext.artifacts_dir` and `provider.get_repo_artifacts_dir()` both resolve to `base_dir/{basename(repo_path)}`, and the API controls a stable clone path, `results/code_analysis/*.json` persists across analyses of the same repo → `check_existing_result` hits and the LLM call is skipped.

---

## Phases

1. **ContextVar rewrite of `output_directory_provider.py`** + `output_scope()` helper. API preserved. Unit tests for the provider in isolation.
2. **Session establishes scope** in `__aenter__`/`__aexit__` from `ctx.repo_path` + `ctx.output_base_dir`.
3. **Audit the two `ThreadPoolExecutor` publisher sites** — confirm prior-result stores hold explicit base dirs (do not read the global provider inside worker threads). If any does, capture the path before `submit()` or wrap the callable in `contextvars.copy_context().run(...)`.
4. **CLI regression pass** — run all 5 analyzers; confirm `~/llm_artifacts/{repo}/…` layout is byte-for-byte identical and the conftest `reset()` fixture still clears state.
5. **Concurrency + cache tests** (see below).

Phases 1–2 are the core (~2 files). 3–5 are verification.

---

## Testing & acceptance

- **Concurrency isolation (the acceptance criterion):** two `async with AnalysisSession.create(ctx_A/B)` in two tasks with different base dirs; interleave `await`s; inside each, assert `get_output_directory_provider().get_repo_artifacts_dir()` returns *its own* base. Must never see the sibling's.
- **Fan-out inheritance:** inside one session, `bounded_gather` workers each read the provider → all see the session's base.
- **`to_thread` inheritance:** a sync consumer invoked via `asyncio.to_thread` reads the correct base.
- **Cache persistence:** run `analyze_repo` twice against the same `repository_id` base dir with a call-counting fake LLM; assert the 2nd run emits `FunctionCompleteEvent(cached=True)` and makes zero LLM calls for unchanged functions.
- **CLI regression:** single-threaded `configure()` → `get_repo_artifacts_dir()` unchanged; artifact tree identical.
- **Scope leak:** after `async with` exits, `is_configured()` returns to its prior value (token reset); a following analysis in the same task starts clean.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `ThreadPoolExecutor` in result-store publishers doesn't inherit contextvars | Phase 3 audit. Stores are constructed with explicit base dirs, so lookups almost certainly don't read the global provider — but confirm; if they do, capture path pre-`submit()` or wrap with `copy_context().run`. |
| AST runs in `ProcessPoolExecutor` (separate processes) | No ContextVar crosses a process boundary, but AST workers get paths via pickled args and are process-isolated (no clobber). `ast_index.py` itself reads the provider in the main task → inherits. No change needed. |
| Scope leaks across requests if caller skips `async with` or shares a task | Mandate: one asyncio task per analysis (FastAPI job registry) + `async with` session. `__aexit__` resets the token. |
| Standalone provider use outside a session (e.g. onboarding AST warmup) | Wrap in the new `output_scope(repo_path, base_dir)` context manager. |
| Nested sessions in one task | ContextVar token stack handles it — `reset(token)` restores the exact prior value. |
| Context/provider base-dir divergence | Session sets scope from the *same* `ctx` fields the context computed from → they cannot diverge. |

---

## Blast radius

- **Changed:** `hindsight/utils/output_directory_provider.py` (state → ContextVar; +`output_scope()`), `hindsight/orchestration/session.py` (`__aenter__`/`__aexit__` set/reset). ~2 files.
- **Unchanged:** all ~35 read sites, all ~11 `.configure()` sites, `AnalysisContext`, the conftest `reset()` fixture, CLI behavior, `~/llm_artifacts/` layout.
- **Follow-on (separate, in the FastAPI plan):** derive `base_dir` from `repository_id`.
```
