#!/usr/bin/env python3
"""
Unit tests for hindsight/issue_filter/fp_csv_filter.py

Coverage:
  - CSV parsing (valid, malformed, empty, comments, unknown checksum)
  - Pass 1 – explicit checksum + title matching
  - Pass 2 – semantic matching (mocked embeddings / ChromaDB)
  - JSON file updates on disk (partial and full removal, zero-out behaviour)
  - Fault tolerance (missing CSV, ChromaDB failure, JSON write failure)
  - Integration: both passes together with disk state verification
  - Idempotency: running twice with the same CSV produces the same result
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from hindsight.issue_filter.fp_csv_filter import FpCsvFilter, FpEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_issue(
    title: str,
    function_name: str = "some_func",
    file_path: str = "src/file.cpp",
    description: str = "A description.",
    category: str = "logicBug",
) -> Dict[str, Any]:
    return {
        "issue": title,
        "function_name": function_name,
        "file_path": file_path,
        "description": description,
        "severity": "medium",
        "category": category,
    }


def _make_analysis_json(
    function: str,
    file_path: str,
    checksum: str,
    issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "file_path": file_path,
        "function": function,
        "checksum": checksum,
        "results": issues,
        "analysis_timestamp": "2026-01-01T00:00:00",
    }


def _write_analysis_file(
    code_analysis_dir: Path,
    function: str,
    file_path: str,
    checksum: str,
    issues: List[Dict[str, Any]],
) -> Path:
    """Write a mock analysis JSON file and return its path."""
    safe_func = "".join(c for c in function if c.isalnum() or c in ("_", "-"))
    safe_file = "".join(
        c for c in os.path.basename(file_path) if c.isalnum() or c in ("_", "-", ".")
    )
    filename = f"{safe_func}_{safe_file}_{checksum[:8]}_analysis.json"
    path = code_analysis_dir / filename
    with open(path, "w") as fh:
        json.dump(_make_analysis_json(function, file_path, checksum, issues), fh)
    return path


def _write_csv(path: str, rows: List[Dict[str, str]]) -> None:
    with open(path, "w") as fh:
        fh.write("checksum,issue_title,issue_description\n")
        for row in rows:
            cs = row.get("checksum", "")
            title = row.get("issue_title", "").replace('"', '""')
            desc = row.get("issue_description", "").replace('"', '""')
            fh.write(f'{cs},"{title}","{desc}"\n')


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp(tmp_path):
    """Yield a structured artifact directory."""
    code_analysis = tmp_path / "results" / "code_analysis"
    code_analysis.mkdir(parents=True)
    dropped = tmp_path / "dropped_issues"
    dropped.mkdir()
    return tmp_path


@pytest.fixture()
def filter_factory(tmp):
    """Return a factory that builds an FpCsvFilter for tmp."""

    def _make(csv_rows, same_func_threshold=None, cross_func_threshold=None):
        csv_path = str(tmp / "false_positives.csv")
        _write_csv(csv_path, csv_rows)
        kwargs = dict(
            csv_path=csv_path,
            code_analysis_dir=str(tmp / "results" / "code_analysis"),
            artifacts_dir=str(tmp),
        )
        if same_func_threshold is not None:
            kwargs["same_func_threshold"] = same_func_threshold
        if cross_func_threshold is not None:
            kwargs["cross_func_threshold"] = cross_func_threshold
        return FpCsvFilter(**kwargs)

    return _make


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

class TestCsvParsing:
    def test_basic_parse(self, filter_factory):
        f = filter_factory(
            [
                {"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "desc A"},
                {"checksum": "def67890", "issue_title": "Bug B", "issue_description": "desc B"},
            ]
        )
        entries = f._parse_csv()
        assert len(entries) == 2
        assert entries[0].checksum == "abc12345"
        assert entries[0].issue_title == "Bug A"
        assert entries[1].checksum == "def67890"

    def test_csv_with_comments(self, tmp):
        csv_path = str(tmp / "false_positives.csv")
        with open(csv_path, "w") as fh:
            fh.write("# comment line\n")
            fh.write("# another comment\n")
            fh.write("checksum,issue_title,issue_description\n")
            fh.write('abc12345,"Bug A","desc A"\n')
        f = FpCsvFilter(csv_path, str(tmp / "results" / "code_analysis"), str(tmp))
        entries = f._parse_csv()
        assert len(entries) == 1
        assert entries[0].checksum == "abc12345"

    def test_empty_csv_returns_empty(self, tmp):
        csv_path = str(tmp / "false_positives.csv")
        open(csv_path, "w").close()
        f = FpCsvFilter(csv_path, str(tmp / "results" / "code_analysis"), str(tmp))
        assert f._parse_csv() == []

    def test_header_only_csv_returns_empty(self, tmp):
        csv_path = str(tmp / "false_positives.csv")
        with open(csv_path, "w") as fh:
            fh.write("checksum,issue_title,issue_description\n")
        f = FpCsvFilter(csv_path, str(tmp / "results" / "code_analysis"), str(tmp))
        assert f._parse_csv() == []

    def test_missing_csv_returns_empty(self, tmp):
        f = FpCsvFilter(
            str(tmp / "nonexistent.csv"),
            str(tmp / "results" / "code_analysis"),
            str(tmp),
        )
        assert f._parse_csv() == []

    def test_skips_rows_with_empty_checksum(self, tmp):
        csv_path = str(tmp / "false_positives.csv")
        with open(csv_path, "w") as fh:
            fh.write("checksum,issue_title,issue_description\n")
            fh.write('abc12345,"Valid","desc"\n')
            fh.write(',"No checksum","skipped"\n')
        f = FpCsvFilter(csv_path, str(tmp / "results" / "code_analysis"), str(tmp))
        entries = f._parse_csv()
        assert len(entries) == 1
        assert entries[0].checksum == "abc12345"

    def test_unknown_checksum_is_kept(self, tmp):
        csv_path = str(tmp / "false_positives.csv")
        with open(csv_path, "w") as fh:
            fh.write("checksum,issue_title,issue_description\n")
            fh.write('unknown,"Can\'t find file","desc"\n')
        f = FpCsvFilter(csv_path, str(tmp / "results" / "code_analysis"), str(tmp))
        entries = f._parse_csv()
        assert len(entries) == 1
        assert entries[0].checksum == "unknown"

    def test_strips_whitespace(self, tmp):
        csv_path = str(tmp / "false_positives.csv")
        with open(csv_path, "w") as fh:
            fh.write("checksum,issue_title,issue_description\n")
            fh.write('  abc12345  ,  Bug A  ,  desc A  \n')
        f = FpCsvFilter(csv_path, str(tmp / "results" / "code_analysis"), str(tmp))
        entries = f._parse_csv()
        assert entries[0].checksum == "abc12345"
        assert entries[0].issue_title == "Bug A"
        assert entries[0].issue_description == "desc A"

    def test_quoted_fields_with_commas(self, tmp):
        csv_path = str(tmp / "false_positives.csv")
        with open(csv_path, "w") as fh:
            fh.write("checksum,issue_title,issue_description\n")
            fh.write('abc12345,"Title, with comma","desc, also with comma"\n')
        f = FpCsvFilter(csv_path, str(tmp / "results" / "code_analysis"), str(tmp))
        entries = f._parse_csv()
        assert entries[0].issue_title == "Title, with comma"
        assert entries[0].issue_description == "desc, also with comma"


# ---------------------------------------------------------------------------
# Pass 1 – explicit matching
# ---------------------------------------------------------------------------

class TestPass1Explicit:
    def test_removes_exact_match(self, filter_factory):
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Buffer overflow", "issue_description": "d"}]
        )
        issues = [_make_issue("Buffer overflow")]
        lookup = {("some_func", "src/file.cpp", "Buffer overflow"): "abc12345"}

        remaining, dropped = f._pass1_explicit(issues, f._parse_csv(), lookup)

        assert len(remaining) == 0
        assert len(dropped) == 1
        assert dropped[0]["_fp_checksum"] == "abc12345"

    def test_case_insensitive_title_match(self, filter_factory):
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "BUFFER OVERFLOW", "issue_description": "d"}]
        )
        issues = [_make_issue("buffer overflow")]
        lookup = {("some_func", "src/file.cpp", "buffer overflow"): "abc12345"}

        remaining, dropped = f._pass1_explicit(issues, f._parse_csv(), lookup)

        assert len(dropped) == 1

    def test_does_not_remove_different_checksum(self, filter_factory):
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Buffer overflow", "issue_description": "d"}]
        )
        issues = [_make_issue("Buffer overflow")]
        # Different checksum in lookup
        lookup = {("some_func", "src/file.cpp", "Buffer overflow"): "ffffffff"}

        remaining, dropped = f._pass1_explicit(issues, f._parse_csv(), lookup)

        assert len(remaining) == 1
        assert len(dropped) == 0

    def test_skips_unknown_checksum_entries(self, filter_factory):
        """CSV entries with 'unknown' checksum are skipped in Pass 1."""
        f = filter_factory(
            [{"checksum": "unknown", "issue_title": "Buffer overflow", "issue_description": "d"}]
        )
        issues = [_make_issue("Buffer overflow")]
        lookup = {("some_func", "src/file.cpp", "Buffer overflow"): "abc12345"}

        remaining, dropped = f._pass1_explicit(issues, f._parse_csv(), lookup)

        assert len(remaining) == 1  # not removed – no checksum to match
        assert len(dropped) == 0

    def test_preserves_unmatched_issues(self, filter_factory):
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        issues = [
            _make_issue("Bug A"),
            _make_issue("Bug B"),
            _make_issue("Bug C"),
        ]
        lookup = {("some_func", "src/file.cpp", "Bug A"): "abc12345"}

        remaining, dropped = f._pass1_explicit(issues, f._parse_csv(), lookup)

        assert len(remaining) == 2
        assert len(dropped) == 1
        remaining_titles = [i["issue"] for i in remaining]
        assert "Bug B" in remaining_titles
        assert "Bug C" in remaining_titles

    def test_empty_lookup_no_drops(self, filter_factory):
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        issues = [_make_issue("Bug A")]
        remaining, dropped = f._pass1_explicit(issues, f._parse_csv(), {})
        assert len(remaining) == 1
        assert len(dropped) == 0


# ---------------------------------------------------------------------------
# Pass 2 – semantic matching (mocked)
# ---------------------------------------------------------------------------

class TestPass2Semantic:
    """Pass 2 uses real ChromaDB + sentence-transformers, which may not be
    available in CI.  We mock the embedding generator and VectorStore so
    tests are fast and hermetic."""

    def _fake_embedding(self, text: str) -> List[float]:
        """Simple deterministic fake embedding."""
        h = sum(ord(c) for c in text)
        return [float(h % 100) / 100.0] * 384

    def _make_mock_embedding_gen(self, embedding_fn=None):
        if embedding_fn is None:
            embedding_fn = self._fake_embedding
        mock = MagicMock()
        mock.generate.side_effect = lambda text: embedding_fn(text)
        mock.generate_batch.side_effect = lambda texts, *a, **kw: [
            embedding_fn(t) for t in texts
        ]
        return mock

    @patch("hindsight.issue_filter.fp_csv_filter.FpCsvFilter._pass2_semantic")
    def test_pass2_called_after_pass1(self, mock_p2, filter_factory):
        """Pass 2 is invoked with the remaining (post-Pass-1) list."""
        mock_p2.return_value = ([], [])
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        issues = [_make_issue("Bug A"), _make_issue("Bug B")]
        lookup = {("some_func", "src/file.cpp", "Bug A"): "abc12345"}

        f.filter_issues(issues, lookup)

        # Pass 2 received only Bug B (Bug A was dropped by Pass 1)
        call_args = mock_p2.call_args[0]
        remaining_input = call_args[0]
        assert len(remaining_input) == 1
        assert remaining_input[0]["issue"] == "Bug B"

    @patch("hindsight.dedupers.common.embeddings.EmbeddingGenerator")
    @patch("hindsight.dedupers.common.vector_store.VectorStore")
    def test_semantic_match_drops_near_duplicate(
        self, MockVectorStore, MockEmbeddingGen, filter_factory
    ):
        # Arrange
        mock_eg = self._make_mock_embedding_gen()
        MockEmbeddingGen.get_instance.return_value = mock_eg

        mock_store = MagicMock()
        # Return a high-similarity result (distance close to 0)
        mock_store.query.return_value = [("fp_0", {}, 0.05)]  # similarity ≈ 0.975
        MockVectorStore.return_value = mock_store

        f = filter_factory(
            [
                {
                    "checksum": "unknown",
                    "issue_title": "Buffer overrun in loop",
                    "issue_description": "loop iterates past end of array",
                }
            ]
        )
        issues = [_make_issue("Buffer overflow in iteration")]

        remaining, dropped = f._pass2_semantic(issues, f._parse_csv(), {})

        assert len(dropped) == 1
        assert dropped[0]["issue"] == "Buffer overflow in iteration"
        assert dropped[0]["_fp_similarity"] >= f.cross_func_threshold

    @patch("hindsight.dedupers.common.embeddings.EmbeddingGenerator")
    @patch("hindsight.dedupers.common.vector_store.VectorStore")
    def test_semantic_below_threshold_keeps_issue(
        self, MockVectorStore, MockEmbeddingGen, filter_factory
    ):
        mock_eg = self._make_mock_embedding_gen()
        MockEmbeddingGen.get_instance.return_value = mock_eg

        mock_store = MagicMock()
        # Return low similarity (distance near 2)
        mock_store.query.return_value = [("fp_0", {}, 1.5)]  # similarity ≈ 0.25
        MockVectorStore.return_value = mock_store

        f = filter_factory(
            [
                {
                    "checksum": "abc12345",
                    "issue_title": "Memory leak",
                    "issue_description": "allocation not freed",
                }
            ]
        )
        issues = [_make_issue("Divide by zero")]

        remaining, dropped = f._pass2_semantic(issues, f._parse_csv(), {})

        assert len(remaining) == 1
        assert len(dropped) == 0

    @patch("hindsight.dedupers.common.embeddings.EmbeddingGenerator")
    @patch("hindsight.dedupers.common.vector_store.VectorStore")
    def test_semantic_query_failure_keeps_issue(
        self, MockVectorStore, MockEmbeddingGen, filter_factory
    ):
        """If the vector query throws, the issue is kept (fault tolerance)."""
        mock_eg = self._make_mock_embedding_gen()
        MockEmbeddingGen.get_instance.return_value = mock_eg

        mock_store = MagicMock()
        mock_store.query.side_effect = RuntimeError("ChromaDB unavailable")
        MockVectorStore.return_value = mock_store

        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        issues = [_make_issue("Bug A")]

        remaining, dropped = f._pass2_semantic(issues, f._parse_csv(), {})

        assert len(remaining) == 1
        assert len(dropped) == 0

    @patch("hindsight.dedupers.common.embeddings.EmbeddingGenerator")
    def test_chromadb_init_failure_skips_pass2(
        self, MockEmbeddingGen, filter_factory
    ):
        """If ChromaDB can't be initialised Pass 2 is skipped entirely."""
        MockEmbeddingGen.get_instance.side_effect = RuntimeError("no chromadb")

        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        issues = [_make_issue("Bug A"), _make_issue("Bug B")]

        # filter_issues must not raise; Pass 2 silently skipped
        result = f.filter_issues(issues, {})

        assert len(result) == 2  # nothing removed
        assert f.get_stats()["pass2_removed"] == 0


# ---------------------------------------------------------------------------
# JSON file updates
# ---------------------------------------------------------------------------

class TestJsonFileUpdates:
    def test_partial_removal_updates_file(self, tmp):
        code_analysis = tmp / "results" / "code_analysis"
        issues = [
            _make_issue("Bug A"),
            _make_issue("Bug B"),
        ]
        json_path = _write_analysis_file(
            code_analysis, "my_func", "src/file.cpp", "abc12345", issues
        )

        csv_path = str(tmp / "fp.csv")
        _write_csv(csv_path, [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}])

        f = FpCsvFilter(csv_path, str(code_analysis), str(tmp))
        dropped = [
            {**_make_issue("Bug A"), "_fp_checksum": "abc12345"}
        ]
        f._update_json_files(dropped, FpCsvFilter.EXPLICIT_DROP_DIR)

        with open(json_path) as fh:
            data = json.load(fh)

        assert len(data["results"]) == 1
        assert data["results"][0]["issue"] == "Bug B"

    def test_full_removal_zeros_results(self, tmp):
        """When all issues are FPs the file stays but results[] is empty."""
        code_analysis = tmp / "results" / "code_analysis"
        json_path = _write_analysis_file(
            code_analysis, "my_func", "src/file.cpp", "abc12345",
            [_make_issue("Bug A")]
        )

        csv_path = str(tmp / "fp.csv")
        _write_csv(csv_path, [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}])

        f = FpCsvFilter(csv_path, str(code_analysis), str(tmp))
        f._update_json_files(
            [{**_make_issue("Bug A"), "_fp_checksum": "abc12345"}],
            FpCsvFilter.EXPLICIT_DROP_DIR,
        )

        # File still exists (cache still needs it)
        assert json_path.exists()
        with open(json_path) as fh:
            data = json.load(fh)
        assert data["results"] == []

    def test_archive_copy_created(self, tmp):
        """A copy is always placed in dropped_issues/<subdir>/."""
        code_analysis = tmp / "results" / "code_analysis"
        json_path = _write_analysis_file(
            code_analysis, "my_func", "src/file.cpp", "abc12345",
            [_make_issue("Bug A")]
        )

        csv_path = str(tmp / "fp.csv")
        _write_csv(csv_path, [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}])

        f = FpCsvFilter(csv_path, str(code_analysis), str(tmp))
        f._update_json_files(
            [{**_make_issue("Bug A"), "_fp_checksum": "abc12345"}],
            FpCsvFilter.EXPLICIT_DROP_DIR,
        )

        archive = (
            tmp / "dropped_issues" / FpCsvFilter.EXPLICIT_DROP_DIR / json_path.name
        )
        assert archive.exists()

    def test_json_write_failure_does_not_raise(self, tmp):
        """A write failure on one file must not bubble up."""
        code_analysis = tmp / "results" / "code_analysis"
        _write_analysis_file(
            code_analysis, "my_func", "src/file.cpp", "abc12345",
            [_make_issue("Bug A")]
        )

        csv_path = str(tmp / "fp.csv")
        _write_csv(csv_path, [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}])

        f = FpCsvFilter(csv_path, str(code_analysis), str(tmp))
        dropped = [{**_make_issue("Bug A"), "_fp_checksum": "abc12345"}]

        with patch("builtins.open", side_effect=[
            open(  # first open (read) succeeds
                code_analysis / list(code_analysis.glob("*abc12345*"))[0].name
            ),
            OSError("disk full"),  # second open (write) fails
        ]):
            # Should not raise
            f._update_json_files(dropped, FpCsvFilter.EXPLICIT_DROP_DIR)

        assert f.get_stats()["json_updates_failed"] >= 1

    def test_no_checksum_skips_disk_update(self, tmp):
        """Dropped issues without _fp_checksum are skipped for disk updates."""
        code_analysis = tmp / "results" / "code_analysis"
        csv_path = str(tmp / "fp.csv")
        _write_csv(csv_path, [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}])

        f = FpCsvFilter(csv_path, str(code_analysis), str(tmp))
        # Issue without _fp_checksum
        f._update_json_files([_make_issue("Bug A")], FpCsvFilter.EXPLICIT_DROP_DIR)

        # drop dir should be created but empty (no files to archive)
        drop_dir = tmp / "dropped_issues" / FpCsvFilter.EXPLICIT_DROP_DIR
        assert not drop_dir.exists() or list(drop_dir.glob("*.json")) == []


# ---------------------------------------------------------------------------
# Fault tolerance of filter_issues()
# ---------------------------------------------------------------------------

class TestFaultTolerance:
    def test_missing_csv_returns_original(self, tmp):
        f = FpCsvFilter(
            str(tmp / "missing.csv"),
            str(tmp / "results" / "code_analysis"),
            str(tmp),
        )
        issues = [_make_issue("Bug A"), _make_issue("Bug B")]
        result = f.filter_issues(issues)
        assert result == issues

    def test_empty_issues_list_returns_empty(self, filter_factory):
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        assert f.filter_issues([]) == []

    @patch(
        "hindsight.issue_filter.fp_csv_filter.FpCsvFilter._pass1_explicit",
        side_effect=RuntimeError("unexpected"),
    )
    def test_pass1_exception_falls_back_gracefully(self, _, filter_factory):
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        issues = [_make_issue("Bug A")]
        # Must not raise; all issues returned
        result = f.filter_issues(issues)
        assert len(result) == len(issues)

    @patch(
        "hindsight.issue_filter.fp_csv_filter.FpCsvFilter._pass2_semantic",
        side_effect=RuntimeError("unexpected"),
    )
    def test_pass2_exception_keeps_pass1_result(self, _, filter_factory):
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        issues = [_make_issue("Bug A"), _make_issue("Bug B")]
        lookup = {("some_func", "src/file.cpp", "Bug A"): "abc12345"}

        result = f.filter_issues(issues, lookup)

        # Pass 1 still removed Bug A; Pass 2 failure is silent
        assert len(result) == 1
        assert result[0]["issue"] == "Bug B"
        assert f.get_stats()["pass1_removed"] == 1
        assert f.get_stats()["pass2_removed"] == 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_reflect_removals(self, filter_factory, tmp):
        """get_stats() returns correct counts after both passes."""
        code_analysis = tmp / "results" / "code_analysis"
        _write_analysis_file(
            code_analysis, "func_a", "src/a.cpp", "abc12345",
            [_make_issue("Bug A")]
        )

        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        issues = [_make_issue("Bug A")]
        lookup = {("some_func", "src/file.cpp", "Bug A"): "abc12345"}

        with patch.object(f, "_pass2_semantic", return_value=([], [])):
            f.filter_issues(issues, lookup)

        stats = f.get_stats()
        assert stats["csv_entries"] == 1
        assert stats["pass1_removed"] == 1
        assert stats["pass2_removed"] == 0

    def test_stats_independent_copies(self, filter_factory):
        """get_stats() returns a copy; mutating it does not affect the filter."""
        f = filter_factory(
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}]
        )
        s1 = f.get_stats()
        s1["pass1_removed"] = 999
        assert f.get_stats()["pass1_removed"] == 0


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_end_to_end_explicit_removal(self, tmp):
        """Full run: CSV → Pass 1 removes issue → JSON zeroed → report clean."""
        code_analysis = tmp / "results" / "code_analysis"
        json_path = _write_analysis_file(
            code_analysis,
            "my_func",
            "src/file.cpp",
            "abc12345ef000000",  # 16-char checksum
            [_make_issue("Bug A"), _make_issue("Bug B")],
        )

        csv_path = str(tmp / "fp.csv")
        _write_csv(
            csv_path,
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}],
        )

        f = FpCsvFilter(csv_path, str(code_analysis), str(tmp))
        issues = [
            _make_issue("Bug A"),
            _make_issue("Bug B"),
        ]
        lookup = {
            ("some_func", "src/file.cpp", "Bug A"): "abc12345",
            ("some_func", "src/file.cpp", "Bug B"): "abc12345",
        }

        with patch.object(f, "_pass2_semantic", return_value=(issues[1:], [])):
            result = f.filter_issues(issues, lookup)

        # In-memory: only Bug B remains
        assert len(result) == 1
        assert result[0]["issue"] == "Bug B"

        # On disk: Bug A removed from JSON
        with open(json_path) as fh:
            data = json.load(fh)
        remaining_titles = [r["issue"] for r in data["results"]]
        assert "Bug A" not in remaining_titles
        assert "Bug B" in remaining_titles

    def test_idempotent_on_second_run(self, tmp):
        """Running the filter twice with the same CSV produces the same result."""
        code_analysis = tmp / "results" / "code_analysis"
        _write_analysis_file(
            code_analysis,
            "my_func",
            "src/file.cpp",
            "abc12345ef000000",
            [_make_issue("Bug A"), _make_issue("Bug B")],
        )

        csv_path = str(tmp / "fp.csv")
        _write_csv(
            csv_path,
            [{"checksum": "abc12345", "issue_title": "Bug A", "issue_description": "d"}],
        )
        lookup = {
            ("some_func", "src/file.cpp", "Bug A"): "abc12345",
            ("some_func", "src/file.cpp", "Bug B"): "abc12345",
        }

        def _run(issues):
            f = FpCsvFilter(csv_path, str(code_analysis), str(tmp))
            with patch.object(f, "_pass2_semantic", return_value=(issues, [])):
                return f.filter_issues(issues, lookup)

        issues = [_make_issue("Bug A"), _make_issue("Bug B")]
        result1 = _run(issues)
        result2 = _run(result1)  # second run with already-filtered list

        assert [i["issue"] for i in result1] == [i["issue"] for i in result2]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
