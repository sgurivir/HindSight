"""
Unit tests for the Issue Deduper module.

Tests for:
- IssueDeduper: Main deduplication orchestrator
- IssueIngester: Issue ingestion into vector store
- DuplicateDetector: Duplicate detection logic
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import tempfile
import shutil
from pathlib import Path

# Import modules to test
from hindsight.dedupers.issue_deduper import (
    IssueDeduper,
    IssueIngester,
    DuplicateDetector,
    dedupe_issues,
    DeduplicationError,
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_BATCH_SIZE,
)
from hindsight.dedupers.common.issue_models import AnalyzerIssue, DuplicateMatch


class TestIssueDeduper:
    """Tests for IssueDeduper class."""
    
    @pytest.fixture
    def temp_artifacts_dir(self):
        """Create a temporary artifacts directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def sample_issues(self):
        """Sample issues for testing."""
        return [
            {
                "id": "issue-1",
                "title": "Memory leak in function A",
                "description": "Memory is not freed after allocation",
                "file_path": "/path/to/file_a.py",
                "function_name": "func_a",
                "severity": "high",
                "category": "memory"
            },
            {
                "id": "issue-2",
                "title": "Null pointer dereference in function B",
                "description": "Pointer is dereferenced without null check",
                "file_path": "/path/to/file_b.py",
                "function_name": "func_b",
                "severity": "high",
                "category": "null-pointer"
            },
            {
                "id": "issue-3",
                "title": "Performance issue in function C",
                "description": "Inefficient algorithm causing slowdown",
                "file_path": "/path/to/file_c.py",
                "function_name": "func_c",
                "severity": "medium",
                "category": "performance"
            }
        ]
    
    @pytest.fixture
    def duplicate_issues(self):
        """Issues with duplicates for testing."""
        return [
            {
                "id": "issue-1",
                "title": "Memory leak in function A",
                "description": "Memory is not freed after allocation",
                "file_path": "/path/to/file_a.py",
                "function_name": "func_a"
            },
            {
                "id": "issue-2",
                "title": "Memory leak in function A",  # Exact duplicate
                "description": "Memory is not freed after allocation",
                "file_path": "/path/to/file_a.py",
                "function_name": "func_a"
            },
            {
                "id": "issue-3",
                "title": "Different issue",
                "description": "This is a different issue",
                "file_path": "/path/to/file_b.py",
                "function_name": "func_b"
            }
        ]
    
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.EmbeddingGenerator')
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.VectorStore')
    def test_init_creates_db_directory(self, mock_store, mock_embed, temp_artifacts_dir):
        """Test that initialization creates the DB directory."""
        mock_embed.get_instance.return_value = Mock()
        
        deduper = IssueDeduper(artifacts_dir=temp_artifacts_dir, analyzer_type="code_analysis")

        db_path = Path(temp_artifacts_dir) / "issue_deduper_db" / "code_analysis"
        assert db_path.exists()
        
        deduper.cleanup()
    
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.EmbeddingGenerator')
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.VectorStore')
    def test_init_wipes_existing_db(self, mock_store, mock_embed, temp_artifacts_dir):
        """Test that initialization wipes existing DB."""
        mock_embed.get_instance.return_value = Mock()
        
        # Create existing DB directory with a file
        db_path = Path(temp_artifacts_dir) / "issue_deduper_db" / "code_analysis"
        db_path.mkdir(parents=True)
        (db_path / "old_file.txt").write_text("old data")
        
        deduper = IssueDeduper(artifacts_dir=temp_artifacts_dir, analyzer_type="code_analysis")
        
        # Old file should be gone
        assert not (db_path / "old_file.txt").exists()
        
        deduper.cleanup()
    
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.EmbeddingGenerator')
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.VectorStore')
    def test_dedupe_empty_list(self, mock_store, mock_embed, temp_artifacts_dir):
        """Test deduplication of empty list."""
        mock_embed.get_instance.return_value = Mock()
        
        deduper = IssueDeduper(artifacts_dir=temp_artifacts_dir, analyzer_type="code_analysis")
        result = deduper.dedupe([])
        
        assert result == []
        
        deduper.cleanup()
    
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.EmbeddingGenerator')
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.VectorStore')
    def test_get_stats_initial(self, mock_store, mock_embed, temp_artifacts_dir):
        """Test initial statistics."""
        mock_embed.get_instance.return_value = Mock()
        
        deduper = IssueDeduper(artifacts_dir=temp_artifacts_dir, analyzer_type="code_analysis")
        stats = deduper.get_stats()
        
        assert stats['total_input'] == 0
        assert stats['duplicates_removed'] == 0
        assert stats['unique_output'] == 0
        assert 'db_path' in stats
        
        deduper.cleanup()
    
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.EmbeddingGenerator')
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.VectorStore')
    def test_context_manager(self, mock_store, mock_embed, temp_artifacts_dir):
        """Test context manager usage."""
        mock_embed.get_instance.return_value = Mock()
        
        with IssueDeduper(artifacts_dir=temp_artifacts_dir, analyzer_type="code_analysis") as deduper:
            assert deduper is not None
            result = deduper.dedupe([])
            assert result == []


class TestDuplicateDetector:
    """Tests for DuplicateDetector class."""
    
    def test_init_default_threshold(self):
        """Test initialization with default threshold."""
        detector = DuplicateDetector()
        assert detector.threshold == DEFAULT_SIMILARITY_THRESHOLD
    
    def test_init_custom_threshold(self):
        """Test initialization with custom threshold."""
        detector = DuplicateDetector(threshold=0.90)
        assert detector.threshold == 0.90
    
    def test_find_duplicates_empty_list(self):
        """Test finding duplicates in empty list."""
        detector = DuplicateDetector()
        result = detector.find_duplicates([])
        assert result == []
    
    def test_find_duplicates_exact_match(self):
        """Test finding exact duplicates."""
        detector = DuplicateDetector()
        
        issues = [
            AnalyzerIssue(
                id="issue-1",
                title="Memory leak",
                description="Memory not freed",
                file_path="/path/file.py",
                function_name="func"
            ),
            AnalyzerIssue(
                id="issue-2",
                title="Memory leak",  # Same content
                description="Memory not freed",
                file_path="/path/file.py",
                function_name="func"
            ),
        ]
        
        duplicates = detector.find_duplicates(issues)
        
        assert len(duplicates) == 1
        assert duplicates[0].original_id == "issue-1"
        assert duplicates[0].duplicate_id == "issue-2"
        assert duplicates[0].match_type == "exact"
        assert duplicates[0].similarity_score == 1.0
    
    def test_find_duplicates_no_duplicates(self):
        """Test with no duplicates."""
        detector = DuplicateDetector()
        
        issues = [
            AnalyzerIssue(
                id="issue-1",
                title="Memory leak",
                description="Memory not freed",
                file_path="/path/file_a.py"
            ),
            AnalyzerIssue(
                id="issue-2",
                title="Null pointer",
                description="Null dereference",
                file_path="/path/file_b.py"
            ),
        ]
        
        duplicates = detector.find_duplicates(issues)
        
        assert len(duplicates) == 0
    
    def test_get_matches(self):
        """Test getting matches after detection."""
        detector = DuplicateDetector()
        
        issues = [
            AnalyzerIssue(id="1", title="Test", description="Desc"),
            AnalyzerIssue(id="2", title="Test", description="Desc"),  # Duplicate
        ]
        
        detector.find_duplicates(issues)
        matches = detector.get_matches()
        
        assert len(matches) == 1
        # Should return a copy
        matches.clear()
        assert len(detector.get_matches()) == 1
    
    def test_get_stats(self):
        """Test getting statistics."""
        detector = DuplicateDetector()
        
        issues = [
            AnalyzerIssue(id="1", title="Test", description="Desc"),
            AnalyzerIssue(id="2", title="Test", description="Desc"),  # Exact duplicate
            AnalyzerIssue(id="3", title="Other", description="Other"),
        ]
        
        detector.find_duplicates(issues)
        stats = detector.get_stats()
        
        assert stats['total_duplicates'] == 1
        assert stats['exact_matches'] == 1
        assert stats['semantic_matches'] == 0
    
    def test_clear(self):
        """Test clearing matches."""
        detector = DuplicateDetector()
        
        issues = [
            AnalyzerIssue(id="1", title="Test", description="Desc"),
            AnalyzerIssue(id="2", title="Test", description="Desc"),
        ]
        
        detector.find_duplicates(issues)
        assert len(detector.get_matches()) == 1
        
        detector.clear()
        assert len(detector.get_matches()) == 0


class TestIssueIngester:
    """Tests for IssueIngester class."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir) / "test_db"
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.EmbeddingGenerator')
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.VectorStore')
    def test_init(self, mock_store, mock_embed, temp_db_path):
        """Test initialization."""
        mock_embed.get_instance.return_value = Mock()
        
        ingester = IssueIngester(db_path=temp_db_path)
        
        assert ingester.db_path == temp_db_path
        assert ingester.batch_size == DEFAULT_BATCH_SIZE
        
        ingester.close()
    
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.EmbeddingGenerator')
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.VectorStore')
    def test_ingest_empty_list(self, mock_store, mock_embed, temp_db_path):
        """Test ingesting empty list."""
        mock_embed.get_instance.return_value = Mock()
        
        ingester = IssueIngester(db_path=temp_db_path)
        ingested, skipped = ingester.ingest([])
        
        assert ingested == 0
        assert skipped == 0
        
        ingester.close()
    
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.EmbeddingGenerator')
    @patch('hindsight.dedupers.issue_deduper.issue_ingester.VectorStore')
    def test_ingest_issues(self, mock_store_class, mock_embed, temp_db_path):
        """Test ingesting issues."""
        # Setup mocks
        mock_generator = Mock()
        mock_generator.generate_batch.return_value = [[0.1, 0.2], [0.3, 0.4]]
        mock_embed.get_instance.return_value = mock_generator
        
        mock_store = Mock()
        mock_store.add_documents_batch.return_value = (2, 0)
        mock_store_class.return_value = mock_store
        
        ingester = IssueIngester(db_path=temp_db_path)
        
        issues = [
            AnalyzerIssue(id="1", title="Issue 1", description="Desc 1"),
            AnalyzerIssue(id="2", title="Issue 2", description="Desc 2"),
        ]
        
        ingested, skipped = ingester.ingest(issues)
        
        assert ingested == 2
        assert skipped == 0
        mock_generator.generate_batch.assert_called_once()
        mock_store.add_documents_batch.assert_called_once()
        
        ingester.close()


class TestDedupeIssuesFunction:
    """Tests for dedupe_issues convenience function."""
    
    @pytest.fixture
    def temp_artifacts_dir(self):
        """Create a temporary artifacts directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @patch('hindsight.dedupers.issue_deduper.deduper.IssueIngester')
    @patch('hindsight.dedupers.issue_deduper.deduper.DuplicateDetector')
    def test_dedupe_issues_empty(self, mock_detector, mock_ingester, temp_artifacts_dir):
        """Test dedupe_issues with empty list."""
        result = dedupe_issues([], temp_artifacts_dir, analyzer_type="code_analysis")
        assert result == []


class TestExactDuplicateDetection:
    """Integration-style tests for exact duplicate detection."""
    
    def test_exact_duplicate_detection(self):
        """Test that exact duplicates are detected."""
        issues = [
            {"title": "Memory leak in function A", "description": "Desc", "file_path": "a.py", "function_name": "func_a"},
            {"title": "Memory leak in function A", "description": "Desc", "file_path": "a.py", "function_name": "func_a"},  # Exact duplicate
        ]
        
        # Convert to AnalyzerIssue
        analyzer_issues = [AnalyzerIssue.from_analyzer_result(i) for i in issues]
        
        detector = DuplicateDetector()
        duplicates = detector.find_duplicates(analyzer_issues)
        
        assert len(duplicates) == 1
        assert duplicates[0].match_type == "exact"
    
    def test_different_issues_preserved(self):
        """Test that different issues are not removed."""
        issues = [
            {"title": "Memory leak in function A", "description": "Desc A", "file_path": "a.py"},
            {"title": "Null pointer dereference in function B", "description": "Desc B", "file_path": "b.py"},
        ]
        
        analyzer_issues = [AnalyzerIssue.from_analyzer_result(i) for i in issues]
        
        detector = DuplicateDetector()
        duplicates = detector.find_duplicates(analyzer_issues)
        
        assert len(duplicates) == 0
    
    def test_multiple_exact_duplicates(self):
        """Test detection of multiple exact duplicates."""
        issues = [
            {"title": "Issue A", "description": "Desc A", "file_path": "a.py"},
            {"title": "Issue A", "description": "Desc A", "file_path": "a.py"},  # Duplicate of first
            {"title": "Issue A", "description": "Desc A", "file_path": "a.py"},  # Duplicate of first
            {"title": "Issue B", "description": "Desc B", "file_path": "b.py"},
        ]
        
        analyzer_issues = [AnalyzerIssue.from_analyzer_result(i) for i in issues]
        
        detector = DuplicateDetector()
        duplicates = detector.find_duplicates(analyzer_issues)
        
        # Should find 2 duplicates (second and third are duplicates of first)
        assert len(duplicates) == 2
        
        # All should be exact matches
        for dup in duplicates:
            assert dup.match_type == "exact"
            assert dup.similarity_score == 1.0


class TestStatsTracking:
    """Tests for statistics tracking."""

    def test_stats_tracking(self):
        """Test that statistics are tracked correctly."""
        issues = [
            {"title": "Issue 1", "description": "Desc 1", "file_path": "a.py"},
            {"title": "Issue 1", "description": "Desc 1", "file_path": "a.py"},  # Duplicate
            {"title": "Issue 2", "description": "Desc 2", "file_path": "b.py"},
        ]

        analyzer_issues = [AnalyzerIssue.from_analyzer_result(i) for i in issues]

        detector = DuplicateDetector()
        detector.find_duplicates(analyzer_issues)

        stats = detector.get_stats()
        assert stats['total_duplicates'] == 1
        assert stats['exact_matches'] == 1
        assert stats['semantic_matches'] == 0


class TestFileNameScoring:
    """Tests for DuplicateDetector._compute_file_name_score."""

    def test_exact_same_path(self):
        score = DuplicateDetector._compute_file_name_score(
            "Daemon/Core/Foo.mm", "Daemon/Core/Foo.mm"
        )
        assert score == 1.0

    def test_same_filename_different_dir(self):
        score = DuplicateDetector._compute_file_name_score(
            "Daemon/Core/Foo.mm", "Other/Foo.mm"
        )
        assert 0.8 <= score < 1.0

    def test_same_filename_no_dirs(self):
        score = DuplicateDetector._compute_file_name_score("Foo.mm", "Foo.mm")
        assert score == 1.0

    def test_different_filenames(self):
        score = DuplicateDetector._compute_file_name_score(
            "Daemon/Core/Foo.mm", "Daemon/Core/Bar.mm"
        )
        assert score == 0.0

    def test_empty_path(self):
        assert DuplicateDetector._compute_file_name_score("", "Foo.mm") == 0.0
        assert DuplicateDetector._compute_file_name_score("Foo.mm", "") == 0.0

    def test_same_filename_partial_dir_overlap(self):
        score = DuplicateDetector._compute_file_name_score(
            "Daemon/Motion/Utilities/Foo.mm", "Daemon/Motion/Other/Foo.mm"
        )
        assert score > 0.8


class TestFunctionNameScoring:
    """Tests for DuplicateDetector._compute_function_name_score."""

    def test_exact_match(self):
        score = DuplicateDetector._compute_function_name_score(
            "sendCommandPayload", "sendCommandPayload"
        )
        assert score == 1.0

    def test_objc_prefix_match(self):
        score = DuplicateDetector._compute_function_name_score(
            "-[CLFoo sendCommandPayload]", "sendCommandPayload"
        )
        assert score == 0.7

    def test_substring_match(self):
        score = DuplicateDetector._compute_function_name_score(
            "sendCommand", "sendCommandPayload"
        )
        assert score == 0.7

    def test_no_match(self):
        score = DuplicateDetector._compute_function_name_score(
            "fooBar", "bazQux"
        )
        assert score == 0.0

    def test_token_overlap(self):
        score = DuplicateDetector._compute_function_name_score(
            "sendCommandPayload", "receiveCommandData"
        )
        assert score > 0.3

    def test_empty_names(self):
        assert DuplicateDetector._compute_function_name_score("", "func") == 0.0
        assert DuplicateDetector._compute_function_name_score("func", "") == 0.0

    def test_case_insensitive(self):
        score = DuplicateDetector._compute_function_name_score(
            "SendCommand", "sendcommand"
        )
        assert score == 1.0

    def test_objc_class_method_prefix(self):
        score = DuplicateDetector._compute_function_name_score(
            "+[CLManager startUpdating]", "-[CLManager startUpdating]"
        )
        assert score == 1.0


class TestHybridScoring:
    """Tests for DuplicateDetector._compute_hybrid_score."""

    def test_all_signals_perfect(self):
        score = DuplicateDetector._compute_hybrid_score(1.0, 1.0, 1.0, True, True)
        assert abs(score - 1.0) < 0.001

    def test_all_signals_zero(self):
        score = DuplicateDetector._compute_hybrid_score(0.0, 0.0, 0.0, True, True)
        assert score == 0.0

    def test_file_only_mode(self):
        score = DuplicateDetector._compute_hybrid_score(1.0, 0.0, 0.8, True, False)
        # Should use HYBRID_WEIGHTS_FILE_ONLY: 0.50 * 1.0 + 0.50 * 0.8 = 0.9
        assert abs(score - 0.9) < 0.001

    def test_func_only_mode(self):
        score = DuplicateDetector._compute_hybrid_score(0.0, 1.0, 0.8, False, True)
        # Should use HYBRID_WEIGHTS_FUNC_ONLY: 0.40 * 1.0 + 0.60 * 0.8 = 0.88
        assert abs(score - 0.88) < 0.001

    def test_no_structural_fields(self):
        score = DuplicateDetector._compute_hybrid_score(0.0, 0.0, 0.9, False, False)
        assert abs(score - 0.9) < 0.001

    def test_strong_file_weak_cosine(self):
        score = DuplicateDetector._compute_hybrid_score(1.0, 1.0, 0.4, True, True)
        # 0.40*1.0 + 0.25*1.0 + 0.35*0.4 = 0.79
        assert abs(score - 0.79) < 0.001


class TestHybridSemanticDuplication:
    """Tests for hybrid semantic duplicate detection with mocked vector store."""

    def _make_issue(self, id, title, desc, file_path=None, func=None):
        return AnalyzerIssue(
            id=id, title=title, description=desc,
            file_path=file_path, function_name=func
        )

    @patch('hindsight.dedupers.issue_deduper.duplicate_detector.EmbeddingGenerator')
    def test_same_file_same_func_detected(self, mock_embed_cls):
        mock_gen = Mock()
        mock_gen.generate.return_value = [0.1] * 384
        mock_embed_cls.get_instance.return_value = mock_gen

        mock_store = Mock()
        mock_store.query.return_value = [
            ("issue-1", {"file_path": "Daemon/Core/Foo.mm", "function_name": "sendCmd"}, 0.3)
        ]

        detector = DuplicateDetector(threshold=0.85, vector_store=mock_store)
        issue = self._make_issue("issue-2", "perf problem", "slow", "Daemon/Core/Foo.mm", "sendCmd")

        match = detector._check_semantic_similarity(issue, {"issue-1"})
        assert match is not None
        assert match.original_id == "issue-1"
        assert match.similarity_score >= 0.85

    @patch('hindsight.dedupers.issue_deduper.duplicate_detector.EmbeddingGenerator')
    def test_different_file_rejected(self, mock_embed_cls):
        mock_gen = Mock()
        mock_gen.generate.return_value = [0.1] * 384
        mock_embed_cls.get_instance.return_value = mock_gen

        mock_store = Mock()
        # Very high cosine similarity (distance=0.05) but different file
        mock_store.query.return_value = [
            ("issue-1", {"file_path": "Daemon/Core/Bar.mm", "function_name": "sendCmd"}, 0.05)
        ]

        detector = DuplicateDetector(threshold=0.85, vector_store=mock_store)
        issue = self._make_issue("issue-2", "perf problem", "slow", "Daemon/Core/Foo.mm", "sendCmd")

        match = detector._check_semantic_similarity(issue, {"issue-1"})
        assert match is None

    @patch('hindsight.dedupers.issue_deduper.duplicate_detector.EmbeddingGenerator')
    def test_missing_file_falls_back_to_cosine(self, mock_embed_cls):
        mock_gen = Mock()
        mock_gen.generate.return_value = [0.1] * 384
        mock_embed_cls.get_instance.return_value = mock_gen

        mock_store = Mock()
        # High cosine similarity, no file_path on candidate
        mock_store.query.return_value = [
            ("issue-1", {"file_path": "", "function_name": ""}, 0.1)
        ]

        detector = DuplicateDetector(threshold=0.85, vector_store=mock_store)
        issue = self._make_issue("issue-2", "perf problem", "slow")

        match = detector._check_semantic_similarity(issue, {"issue-1"})
        # cosine = 1 - 0.1/2 = 0.95 → above 0.85
        assert match is not None
        assert abs(match.similarity_score - 0.95) < 0.01

    @patch('hindsight.dedupers.issue_deduper.duplicate_detector.EmbeddingGenerator')
    def test_candidate_not_in_seen_ids_skipped(self, mock_embed_cls):
        mock_gen = Mock()
        mock_gen.generate.return_value = [0.1] * 384
        mock_embed_cls.get_instance.return_value = mock_gen

        mock_store = Mock()
        mock_store.query.return_value = [
            ("issue-1", {"file_path": "Foo.mm", "function_name": "f"}, 0.05)
        ]

        detector = DuplicateDetector(threshold=0.85, vector_store=mock_store)
        issue = self._make_issue("issue-2", "perf", "slow", "Foo.mm", "f")

        match = detector._check_semantic_similarity(issue, {"other-id"})
        assert match is None

    @patch('hindsight.dedupers.issue_deduper.duplicate_detector.EmbeddingGenerator')
    def test_best_candidate_selected(self, mock_embed_cls):
        mock_gen = Mock()
        mock_gen.generate.return_value = [0.1] * 384
        mock_embed_cls.get_instance.return_value = mock_gen

        mock_store = Mock()
        mock_store.query.return_value = [
            ("issue-1", {"file_path": "Foo.mm", "function_name": "funcA"}, 0.4),
            ("issue-3", {"file_path": "Foo.mm", "function_name": "funcA"}, 0.1),
        ]

        detector = DuplicateDetector(threshold=0.50, vector_store=mock_store)
        issue = self._make_issue("issue-2", "perf", "slow", "Foo.mm", "funcA")

        match = detector._check_semantic_similarity(issue, {"issue-1", "issue-3"})
        assert match is not None
        # issue-3 has lower distance (higher cosine) → higher hybrid score
        assert match.original_id == "issue-3"
