"""Orchestration layer — pipelines + session + event fan-out.

Public surface:

    AnalysisSession    — long-lived per-repo runtime handle (FastAPI-facing)
    AnalysisContext    — frozen typed config for one session
    AsyncResultSink    — write-through bridge to the sync publisher
    bounded_gather     — fault-isolated async fan-out with rate limiting
    WorkerOutcome      — typed result from bounded_gather
    Events:            RunStartedEvent, FunctionStartEvent,
                       FunctionCompleteEvent, FunctionFailedEvent,
                       RunCompletedEvent, RunFailedEvent
    Selectors:         select_functions, FunctionFilters, FunctionWorkItem
                       select_affected_functions, AffectedFunction
    Pipelines (Step 3 stubs, filled in Steps 4-6):
                       CodePipeline, DiffPipeline, TracePipeline
"""

from __future__ import annotations

from .affected_selector import (
    AffectedFunction,
    AffectedFunctionSelection,
    select_affected_functions,
)
from .context import AnalysisContext
from .events import (
    AnalysisEvent,
    EventCallback,
    FunctionCompleteEvent,
    FunctionFailedEvent,
    FunctionStartEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
)
from .function_selector import (
    FunctionFilters,
    FunctionWorkItem,
    get_function_line_count,
    select_functions,
)
from .pipeline_code import CodePipeline, CodeRunSummary
from .pipeline_diff import (
    DiffCallTreeWork,
    DiffFunctionWork,
    DiffPipeline,
    DiffRunSummary,
)
from .pipeline_perf import (
    PerfPathWork,
    PerfPipeline,
    PerfRunSummary,
    perf_function_checksum,
)
from .pipeline_trace import (
    TracePipeline,
    TraceRunSummary,
    TraceWork,
)
from .result_sink import AsyncResultSink, PublishOutcome, ResultPublisher
from .session import AnalysisSession
from .worker import WorkerOutcome, bounded_gather, summarize

__all__ = [
    "AffectedFunction",
    "AffectedFunctionSelection",
    "AnalysisContext",
    "AnalysisEvent",
    "AnalysisSession",
    "AsyncResultSink",
    "CodePipeline",
    "CodeRunSummary",
    "DiffCallTreeWork",
    "DiffFunctionWork",
    "DiffPipeline",
    "DiffRunSummary",
    "EventCallback",
    "FunctionCompleteEvent",
    "FunctionFailedEvent",
    "FunctionFilters",
    "FunctionStartEvent",
    "FunctionWorkItem",
    "PerfPathWork",
    "PerfPipeline",
    "PerfRunSummary",
    "PublishOutcome",
    "ResultPublisher",
    "RunCompletedEvent",
    "RunFailedEvent",
    "RunStartedEvent",
    "TracePipeline",
    "TraceRunSummary",
    "TraceWork",
    "WorkerOutcome",
    "bounded_gather",
    "get_function_line_count",
    "perf_function_checksum",
    "select_affected_functions",
    "select_functions",
    "summarize",
]
