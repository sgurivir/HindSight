"""Tests for `AnalysisContext` — defaults, type coercion, path layout."""

from __future__ import annotations

from hindsight.orchestration import AnalysisContext


def test_context_defaults_apply_when_config_is_empty():
    ctx = AnalysisContext.from_config(
        repo_path="/tmp/myrepo",
        config={},
        output_base_dir="/tmp/out",
        api_key="key",
    )
    assert ctx.repo_name == "myrepo"
    assert ctx.artifacts_dir == "/tmp/out/myrepo"
    assert ctx.code_insights_dir == "/tmp/out/myrepo/code_insights"
    assert ctx.results_dir == "/tmp/out/myrepo/results"
    assert ctx.api_key == "key"
    # Defaults are pulled from core.constants.
    assert ctx.max_workers >= 1
    assert ctx.min_function_body_length == 7
    assert ctx.max_function_body_length == 1000


def test_context_coerces_list_and_scalar_filters():
    ctx = AnalysisContext.from_config(
        repo_path="/tmp/r",
        config={
            "file_filter": ["a.swift", "b.swift"],
            "exclude_directories": "Tests",  # scalar instead of list
            "user_provided_prompts": None,
        },
        output_base_dir="/tmp/out",
    )
    assert ctx.file_filter == ("a.swift", "b.swift")
    assert ctx.exclude_directories == ("Tests",)
    assert ctx.user_provided_prompts == ()


def test_context_strips_trailing_slash_from_repo_path():
    ctx = AnalysisContext.from_config(
        repo_path="/tmp/repo/",
        config={},
        output_base_dir="/tmp/out",
    )
    assert ctx.repo_name == "repo"


def test_context_is_frozen():
    ctx = AnalysisContext.from_config(repo_path="/tmp/r", config={}, output_base_dir="/tmp/o")
    try:
        ctx.repo_path = "/tmp/different"
    except Exception as exc:
        assert "frozen" in str(exc).lower() or "FrozenInstance" in type(exc).__name__
    else:
        raise AssertionError("AnalysisContext should be frozen")


def test_context_ensure_directories_is_idempotent(tmp_path):
    ctx = AnalysisContext.from_config(
        repo_path=str(tmp_path / "src"),
        config={},
        output_base_dir=str(tmp_path / "out"),
    )
    ctx.ensure_directories()
    # Second call is a no-op (idempotent).
    ctx.ensure_directories()
    assert (tmp_path / "out" / "src" / "results").exists()
    assert (tmp_path / "out" / "src" / "context_bundles").exists()
