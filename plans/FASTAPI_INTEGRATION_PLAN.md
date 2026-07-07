# FastAPI Integration Plan

**Status:** 📋 PROPOSED — no code written yet
**Scope:** Add an HTTP/WebSocket API layer under `hindsight/api/` on top of the existing async orchestration stack, without disturbing the CLI analyzers.
**Depends on:** [ASYNC_ORCHESTRATION_REWRITE_PLAN.md](ASYNC_ORCHESTRATION_REWRITE_PLAN.md) (✅ complete — `AnalysisSession` is the designed FastAPI handle)
**Last updated:** 2026-07-06

---

## Guiding principle

**Both entry points stay first-class.** The CLI (`python -m hindsight.analyzers.code_analyzer …`) and the FastAPI service are two thin fronts over the *same* `AnalysisSession` orchestration core. The API adds no analysis logic — it adds auth, persistence, job lifecycle, and streaming around the existing session.

**All API code lives in `hindsight/api/`.** Nothing analysis-related moves. The API imports `hindsight.orchestration.session.AnalysisSession` and drives it.

---

## What already exists (reuse, don't rebuild)

| Requirement | Existing asset | Location |
|---|---|---|
| Async, concurrency-safe orchestration | `AnalysisSession.create()` / `ensure_ast()` / `analyze_repo()` | `hindsight/orchestration/session.py` |
| Event streaming (push + pull) | `session.subscribe(cb)`, `session.events()` async iterator | `session.py:223-273` |
| Event types incl. crash signalling | `RunStarted/FunctionStart/FunctionComplete/FunctionFailed/RunCompleted/RunFailed` | `hindsight/orchestration/events.py` |
| Write-through partial results | `AsyncResultSink.publish()` → one JSON per function on disk immediately | `hindsight/orchestration/result_sink.py` |
| Checksum cache (skip LLM) | `check_existing_result(file, function, checksum)` + `AsyncResultSink.check_existing()` | `results_store/code_analysis_publisher.py:258`, `result_sink.py:85` |
| Function checksum | `HashUtil.checksum_for_function_source(...)` (MD5 of source lines) | `hindsight/utils/hash_util.py:191` |
| Auth (users) | Kinde OAuth2; `user_id` = Kinde `sub` claim used across all tables | `.env.example:25-32`, `hindsight/db/repositories.py` |
| Encrypted credential storage | Fernet-backed DB token cache | `hindsight/services/token_cache.py` |
| Persistence schema | Postgres `repositories`, `analyses`, `analysis_results`, `function_analysis_cache` | `hindsight/db/schema_v2.sql` |
| DB access layer | `RepositoryRepository`, `SourceAccountRepository`, `PrAnalysisRepository`, async pool | `hindsight/db/connection.py`, `repositories.py` |
| Config schema | `CONFIG_SCHEMA` (project_name, include_directories, exclude_directories, exclude_files, min_function_body_length, user_prompts, model, …) | `hindsight/utils/config_util.py:18-31` |
| Issue schema (field names) | `CodeAnalysisIssue` | `hindsight/core/schema/code_analysis_result_schema.py:23-50` |

**Net:** the hard parts (async LLM stack, streaming, cache, partial-result durability, DB schema, auth provider) are done. This plan is mostly *wiring*.

---

## Proposed layout

```
hindsight/api/
  __init__.py
  app.py                 # create_app() factory; lifespan opens db pool + shared httpx; mounts routers
  settings.py            # pydantic-settings: DATABASE_*, KINDE_*, ENCRYPTION_KEY, ARTIFACTS_BASE_DIR, CORS
  deps.py                # FastAPI dependencies: get_db, get_current_user, get_owned_repo, get_owned_analysis
  auth/
    kinde.py             # verify Kinde JWT via JWKS; -> AuthenticatedUser(user_id=sub)
  schemas/
    repository.py        # RepositoryCreate / RepositoryOut
    analysis.py          # AnalysisConfigIn (mirrors CONFIG_SCHEMA + CLI knobs), AnalysisOut/status
    issue.py             # IssueOut  <-- EXACT CodeAnalysisIssue field names (req #6)
    events.py            # thin pass-through of orchestration event.to_dict()
  routers/
    repositories.py      # onboard / list / get / decommission (req #1)
    analyses.py          # trigger / status / results (req #2, #3, #5)
    stream.py            # WS /analyses/{id}/events + SSE /analyses/{id}/stream (req #3)
    health.py
  services/
    repo_service.py      # clone, credential storage (reuse token_cache), decommission + cleanup
    analysis_service.py  # builds AnalysisSession, runs it as a background job, bridges events->db
    result_bridge.py     # subscribes to session events; persists + updates counts (the DB seam, req #3)
    job_registry.py      # in-process registry of running asyncio tasks (cancel on delete)
    scheduler.py         # periodic trigger (req #2) — APScheduler or external-cron adapter
```

New deps: `fastapi`, `uvicorn[standard]`, `pyjwt[crypto]` (Kinde JWKS verification), `apscheduler` (only if in-process scheduling chosen). `httpx` already present.

---

## Prerequisite (blocking) — session-scoped output directories

`OutputDirectoryProvider` is a **singleton**. Two concurrent API analyses would write into each other's `~/llm_artifacts/{repo}/…`. The async plan already flagged this (Risk table, line 324). **Before any concurrent server traffic**, add a session-scoped wrapper so each `AnalysisSession` resolves artifact paths under a stable per-repository root.

**Decision:** key the artifacts dir by `repository_id` (stable across analyses of the same repo) so the checksum cache and write-through JSONs persist run-to-run — this is what makes req #4 work in a server context. `ARTIFACTS_BASE_DIR/{repository_id}/…`.

---

## Requirement-by-requirement design

### 0) User onboarding + auth
- No new user table. `user_id` = Kinde `sub`, already the FK across every table.
- `auth/kinde.py`: `get_current_user` dependency validates the `Authorization: Bearer` JWT against Kinde JWKS (issuer/audience from `KINDE_*` env), returns `AuthenticatedUser(user_id)`. Every router depends on it.
- Onboarding a user = first authenticated call; rows are namespaced by `user_id`. (OAuth login/redirect handshake itself stays with the existing Kinde flow / frontend; the API only consumes verified tokens.)

### 1) Onboard a repository + credentials; decommission
- `POST /repositories` `{github_url, name, credentials?}` → insert `repositories` (status `pending`), store credential **encrypted** via `token_cache`/`source_repo_connections` (never store plaintext, never echo it back), then kick a background clone → status `ready`/`error`. Uses `RepositoryRepository`.
- `GET /repositories`, `GET /repositories/{id}` — scoped to `user_id` (`get_owned_repo` dependency → 404 if not owned).
- `DELETE /repositories/{id}` (decommission) → cancel any running analysis job (`job_registry`), delete DB rows (FK `ON DELETE CASCADE` already handles `analyses`/`analysis_results`/`function_analysis_cache`), delete the clone + artifacts dir from disk, revoke stored credentials.

### 2) Trigger periodic analysis with config
- `POST /repositories/{id}/analyses` with `AnalysisConfigIn` mirroring `CONFIG_SCHEMA` + CLI knobs: `include_directories`, `exclude_directories`, `exclude_files`, `min_function_body_length`, `user_prompts`, `model`, `num_functions_to_analyze`, `analysys_type`, `file_filter`. Insert `analyses` row (config stored in the existing `config` JSONB), spawn background job. Returns `analysis_id` immediately (202).
- **Periodic:** store an optional `schedule` (cron expression) on the repository. `scheduler.py` (APScheduler in the app lifespan) enqueues an analysis per repo on its cadence. Alternative (recommended for ops simplicity): expose the trigger endpoint and let an **external** cron/CI call it — the endpoint is idempotent per (repo, config). Pick one at build time; both reuse the same trigger path.

### 3) Results streamed and picked up (with a future-DB seam)
- `analysis_service` calls `AnalysisSession.create(...)`, `await session.ensure_ast()`, then iterates `session.analyze_repo()` inside a background task.
- `result_bridge` registers via `session.subscribe(cb)`:
  - `FunctionCompleteEvent` → update `analyses.progress` + severity counts; **(future)** insert rows into `analysis_results`. This callback is the single DB seam — today it can be a no-op/count-only writer, later it persists full rows.
  - `RunCompleted/RunFailed` → set terminal `analyses.status`.
- Client streaming: `WS /analyses/{id}/events` and `SSE /analyses/{id}/stream` fan out `event.to_dict()` straight from `session.events()` — exactly the pattern documented in `session.py:247-254`.
- **"Already-produced results are picked up even on intermittent failure":** results are durable the moment they're produced because `AsyncResultSink` is write-through to `results/code_analysis/*.json`. `GET /analyses/{id}/results` **reads from that on-disk store** (via the existing publisher/subscriber), so a client that missed the live stream — or connected after a crash — still gets every function that completed. The live stream is an optimization; disk is the source of truth.

### 4) Checksum cache to skip repeat LLM analysis
- Already implemented and automatic: the pipeline computes `checksum_for_function_source`, calls `check_existing_result(file, function, checksum)`, and on hit **skips the LLM** and emits `FunctionCompleteEvent(cached=True)`.
- Server-side requirement: the per-repo artifacts dir must be **stable across analyses** (see Prerequisite) so the on-disk `*_analysis.json` cache index survives between runs. Cache key = `(file_path, function_name, checksum)`.
- **Optional durability upgrade:** implement a Postgres-backed prior-results store (against the existing `function_analysis_cache` table, keyed by `repository_id, file_path, function_name, function_checksum`) that satisfies the same `check_existing_result` interface. This survives artifact-dir cleanup and enables a shared cache. Slots in behind the existing publisher interface — no pipeline changes.

### 5) Partial results on failure
- Covered by #3's durability plus explicit failure signalling. When the background job catches an exception (or receives `RunFailedEvent`), it sets `analyses.status='failed'` + `error_message` but **preserves** the already-written results and the counts accumulated so far.
- `GET /analyses/{id}/results` therefore returns partial results for a `failed` analysis. `FunctionFailedEvent`s are surfaced in the stream and can be recorded per-function without aborting the run (the pipeline already continues past per-function failures).

### 6) Identical issue metadata field names
- `schemas/issue.py::IssueOut` uses the **exact** `CodeAnalysisIssue` field names, verbatim:
  `issue`, `severity`, `category`, `file_path`, `function_name`, `line_number`, `description`, `suggestion`, `confidence`, `rule_id`, `external_references`, `evidence`.
- Severity enum: `critical | high | medium | low`. `issue`/`severity`/`category` are always present (the sink sanitizes them). `FunctionCompleteEvent.issues` are already dicts in this shape → the API forwards them unchanged.
- ⚠️ **Naming mismatch to resolve:** the `analysis_results` DB table uses *different* column names (`issue_type` not `category`; `impact`/`potential_solution`; no `issue`/`suggestion`/`confidence`/`rule_id`/`evidence`). **Decision: the API contract exposes analyzer field names; any DB mapping is an internal adapter in `result_bridge`.** If we want DB columns to match the API 1:1, add a migration to align `analysis_results` with `CodeAnalysisIssue` (recommended for the future-DB work in #3, but not required for the streaming contract).

---

## Build phases (each independently shippable, CLI untouched throughout)

1. **Prereq — session-scoped output dirs.** Wrap `OutputDirectoryProvider`; artifacts keyed by `repository_id`. Unit test: two sessions don't collide. *(Blocks concurrency.)*
2. **App skeleton + auth.** `create_app()`, lifespan (db pool + shared `httpx`), `settings`, Kinde JWT dependency, `/health`. Test with a mocked JWKS.
3. **Repositories router + service.** Onboard (clone + encrypted creds), list/get, decommission (cascade + disk cleanup + job cancel). Ownership scoping.
4. **Analyses trigger + background job.** `POST …/analyses`, `job_registry`, drive `AnalysisSession` in a task, `GET …/analyses/{id}` status. No streaming yet.
5. **Result bridge + results endpoint.** Subscribe callback → progress/counts; `GET …/analyses/{id}/results` reading the write-through disk store (proves #3/#5 partial-result durability).
6. **Streaming.** WS + SSE over `session.events()`.
7. **Periodic scheduling.** APScheduler in lifespan *or* document the external-cron trigger contract.
8. **(Optional) Postgres-backed cache + full `analysis_results` persistence.** Implements the future-DB seam and the durable shared checksum cache (#4 upgrade, #6 DB alignment migration).

---

## Risks & open decisions

| Item | Note / recommendation |
|---|---|
| Singleton `OutputDirectoryProvider` under concurrency | **Must** fix in Phase 1 before concurrent traffic. |
| Issue field-name mismatch (analyzer vs `analysis_results`) | API = analyzer names (req #6). DB alignment = optional migration in Phase 8. |
| Periodic scheduling: in-process vs external cron | Recommend external cron hitting the trigger endpoint for ops simplicity; APScheduler if self-contained is required. |
| Long-running jobs across API restarts | Phase 4 keeps jobs in-process. If durability across restarts matters, add a job table + resume-from-disk (the write-through store already makes resume cheap). Flag, don't build yet. |
| Credential handling | Encrypt at rest (reuse `token_cache` Fernet); never log or return credentials. |
| Cancellation | `AnalysisSession` is cancellation-aware; DELETE repo/analysis cancels the task and the in-flight `await`s unwind cleanly. |
| Auth login handshake | The Kinde OAuth *login/redirect* flow stays outside this API (frontend/existing flow); the API only verifies bearer tokens. Confirm this boundary. |
```
