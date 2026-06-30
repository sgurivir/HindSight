"""Analysis events — typed payloads emitted by pipelines.

Every event is a frozen dataclass with a `type` literal so subscribers
(including future FastAPI WebSocket handlers) can pattern-match on the
discriminator. Each event has `to_dict()` for JSON serialization at the
wire boundary.

The shapes are stable and intentionally minimal — pipelines emit them, the
session fans them out to subscribers, and that's it. Anything richer (full
issue body, conversation log path, etc.) lives in the result sink, not here.

Fault tolerance:
  - Failed function analyses produce `FunctionFailedEvent` rather than raising.
  - The session's `emit()` catches every subscriber exception so a buggy
    callback can't break the pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Optional, Union


@dataclass(frozen=True)
class RunStartedEvent:
    """Fired once at the start of a pipeline run."""

    pipeline: str            # "code" | "diff" | "trace" | "perf"
    total_units: int         # functions / affected functions / callstacks
    type: ClassVar[str] = "run_started"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, **asdict(self)}


@dataclass(frozen=True)
class FunctionStartEvent:
    """One unit of work is about to be analyzed."""

    pipeline: str
    function_name: str
    file_path: str
    index: int                # 1-based
    total: int
    type: ClassVar[str] = "function_started"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, **asdict(self)}


@dataclass(frozen=True)
class FunctionCompleteEvent:
    """One unit of work finished successfully (or with cached results)."""

    pipeline: str
    function_name: str
    file_path: str
    issues: List[Dict[str, Any]]
    duration_seconds: float
    cached: bool                              # True if served from publisher cache
    index: int
    total: int
    type: ClassVar[str] = "function_complete"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, **asdict(self)}


@dataclass(frozen=True)
class FunctionFailedEvent:
    """One unit of work failed. Pipeline continues with the next unit."""

    pipeline: str
    function_name: str
    file_path: str
    error: str
    stage: Optional[str] = None               # "context_collection" | "analysis" | "publish" | ...
    type: ClassVar[str] = "function_failed"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, **asdict(self)}


@dataclass(frozen=True)
class RunCompletedEvent:
    """Fired once at the end of a pipeline run that finished cleanly.

    Even if individual functions failed, the run as a whole succeeded — see
    `failed` count for the body count. Subscribers can use this as their
    "stream terminated normally" signal.
    """

    pipeline: str
    successful: int
    failed: int
    cached: int
    duration_seconds: float
    type: ClassVar[str] = "run_completed"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, **asdict(self)}


@dataclass(frozen=True)
class RunFailedEvent:
    """Fired when the pipeline aborted before processing every unit.

    Distinct from `RunCompletedEvent` because a partial run is recoverable
    state — the events that came before this one represent results that ARE
    on disk and visible in reports. FastAPI consumers should treat this as
    "stream ended abnormally; the partial state is still useful".
    """

    pipeline: str
    error: str
    successful: int                            # completed before the crash
    failed: int                                # per-function failures before the crash
    cached: int
    duration_seconds: float
    type: ClassVar[str] = "run_failed"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, **asdict(self)}


# Union for type annotations on subscriber callbacks.
AnalysisEvent = Union[
    RunStartedEvent,
    FunctionStartEvent,
    FunctionCompleteEvent,
    FunctionFailedEvent,
    RunCompletedEvent,
    RunFailedEvent,
]


# Subscribers are async callbacks. Sync callbacks are also accepted and run
# in-place; the session wraps everything so a raising subscriber can't break
# the pipeline.
EventCallback = Callable[[AnalysisEvent], Any]
