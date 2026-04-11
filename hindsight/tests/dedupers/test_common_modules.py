"""
Unit tests for the common deduplication modules.

Tests for:
- similarity_utils: Similarity calculation functions
- issue_models: AnalyzerIssue and DuplicateMatch data models
- vector_store: VectorStore operations (with mocking)
- embeddings: EmbeddingGenerator (with mocking)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import tempfile
import shutil
from pathlib import Path

# Import modules to test
from hindsight.dedupers.common.similarity_utils import (
    cosine_distance_to_similarity,
    similarity_to_cosine_distance,
    get_similarity_threshold,
    is_duplicate,
    is_exact_match,
    is_semantic_match,
    get_match_type,
    get_confidence_level,
    format_similarity_percentage,
    SIMILARITY_THRESHOLDS,
)
from hindsight.dedupers.common.issue_models import (
    AnalyzerIssue,
    DuplicateMatch,
)


class TestSimilarityUtils:
    """Tests for similarity_utils module."""
    
    def test_cosine_distance_to_similarity_identical(self):
        """Test conversion of distance 0 (identical) to similarity 1."""
        assert cosine_distance_to_similarity(0.0) == 1.0
    
    def test_cosine_distance_to_similarity_opposite(self):
        """Test conversion of distance 2 (opposite) to similarity 0."""
        assert cosine_distance_to_similarity(2.0) == 0.0
    
    def test_cosine_distance_to_similarity_middle(self):
        """Test conversion of distance 1 to similarity 0.5."""
        assert cosine_distance_to_similarity(1.0) == 0.5
    
    def test_cosine_distance_to_similarity_clamping(self):
        """Test that out-of-range values are clamped."""
        assert cosine_distance_to_similarity(-1.0) == 1.0
        assert cosine_distance_to_similarity(3.0) == 0.0
    
    def test_similarity_to_cosine_distance_identical(self):
        """Test conversion of similarity 1 to distance 0."""
        assert similarity_to_cosine_distance(1.0) == 0.0
    
    def test_similarity_to_cosine_distance_opposite(self):
        """Test conversion of similarity 0 to distance 2."""
        assert similarity_to_cosine_distance(0.0) == 2.0
    
    def test_similarity_to_cosine_distance_middle(self):
        """Test conversion of similarity 0.5 to distance 1."""
        assert similarity_to_cosine_distance(0.5) == 1.0
    
    def test_get_similarity_threshold_exact(self):
        """Test getting exact match threshold."""
        assert get_similarity_threshold("exact") == 0.99
    
    def test_get_similarity_threshold_semantic(self):
        """Test getting semantic match threshold."""
        assert get_similarity_threshold("semantic") == 0.85
    
    def test_get_similarity_threshold_related(self):
        """Test getting related match threshold."""
        assert get_similarity_threshold("related") == 0.70
    
    def test_get_similarity_threshold_default(self):
        """Test that unknown type returns semantic threshold."""
        assert get_similarity_threshold("unknown") == 0.85
    
    def test_is_duplicate_above_threshold(self):
        """Test that similarity above threshold is duplicate."""
        assert is_duplicate(0.90, 0.85) is True
    
    def test_is_duplicate_below_threshold(self):
        """Test that similarity below threshold is not duplicate."""
        assert is_duplicate(0.80, 0.85) is False
    
    def test_is_duplicate_at_threshold(self):
        """Test that similarity at threshold is duplicate."""
        assert is_duplicate(0.85, 0.85) is True
    
    def test_is_exact_match(self):
        """Test exact match detection."""
        assert is_exact_match(0.99) is True
        assert is_exact_match(0.98) is False
    
    def test_is_semantic_match(self):
        """Test semantic match detection."""
        assert is_semantic_match(0.85) is True
        assert is_semantic_match(0.84) is False
    
    def test_get_match_type_exact(self):
        """Test match type for exact similarity."""
        assert get_match_type(0.99) == "exact"
        assert get_match_type(1.0) == "exact"
    
    def test_get_match_type_semantic(self):
        """Test match type for semantic similarity."""
        assert get_match_type(0.90) == "semantic"
        assert get_match_type(0.85) == "semantic"
    
    def test_get_match_type_related(self):
        """Test match type for related similarity."""
        assert get_match_type(0.75) == "related"
        assert get_match_type(0.70) == "related"
    
    def test_get_match_type_loose(self):
        """Test match type for loose similarity."""
        assert get_match_type(0.65) == "loose"
        assert get_match_type(0.60) == "loose"
    
    def test_get_match_type_none(self):
        """Test match type for low similarity."""
        assert get_match_type(0.50) == "none"
        assert get_match_type(0.0) == "none"
    
    def test_get_confidence_level(self):
        """Test confidence level mapping."""
        assert get_confidence_level(0.95) == "very_high"
        assert get_confidence_level(0.85) == "high"
        assert get_confidence_level(0.75) == "moderate"
        assert get_confidence_level(0.65) == "low"
        assert get_confidence_level(0.50) == "very_low"
    
    def test_format_similarity_percentage(self):
        """Test percentage formatting."""
        assert format_similarity_percentage(0.85) == "85%"
        assert format_similarity_percentage(1.0) == "100%"
        assert format_similarity_percentage(0.0) == "0%"


class TestAnalyzerIssue:
    """Tests for AnalyzerIssue data model."""
    
    def test_create_with_defaults(self):
        """Test creating issue with default values."""
        issue = AnalyzerIssue(title="Test Issue", description="Test description")
        assert issue.title == "Test Issue"
        assert issue.description == "Test description"
        assert issue.id != ""  # Auto-generated
    
    def test_create_with_all_fields(self):
        """Test creating issue with all fields."""
        issue = AnalyzerIssue(
            id="test-123",
            title="Memory Leak",
            description="Memory leak in function",
            file_path="/path/to/file.py",
            function_name="my_function",
            severity="high",
            category="memory",
            line_number=42,
            evidence="code snippet",
            callstack=["func1", "func2"],
        )
        assert issue.id == "test-123"
        assert issue.file_path == "/path/to/file.py"
        assert issue.function_name == "my_function"
        assert issue.severity == "high"
        assert issue.category == "memory"
        assert issue.line_number == 42
        assert issue.evidence == "code snippet"
        assert issue.callstack == ["func1", "func2"]
    
    def test_file_name_property(self):
        """Test file_name property extraction."""
        issue = AnalyzerIssue(
            title="Test",
            description="Test",
            file_path="/path/to/file.py"
        )
        assert issue.file_name == "file.py"
    
    def test_file_name_property_none(self):
        """Test file_name property when file_path is None."""
        issue = AnalyzerIssue(title="Test", description="Test")
        assert issue.file_name is None
    
    def test_directory_path_property(self):
        """Test directory_path property extraction."""
        issue = AnalyzerIssue(
            title="Test",
            description="Test",
            file_path="/path/to/file.py"
        )
        assert issue.directory_path == "/path/to"
    
    def test_to_embedding_text(self):
        """Test embedding text generation."""
        issue = AnalyzerIssue(
            title="Memory Leak",
            description="Leak in function",
            file_path="/path/to/file.py",
            function_name="my_func",
            category="memory"
        )
        text = issue.to_embedding_text()
        assert "Memory Leak" in text
        assert "Leak in function" in text
        assert "File: /path/to/file.py" in text
        assert "Function: my_func" in text
        assert "Category: memory" in text
    
    def test_compute_content_hash(self):
        """Test content hash computation."""
        issue1 = AnalyzerIssue(
            title="Test",
            description="Description",
            file_path="/path/file.py",
            function_name="func"
        )
        issue2 = AnalyzerIssue(
            title="Test",
            description="Description",
            file_path="/path/file.py",
            function_name="func"
        )
        issue3 = AnalyzerIssue(
            title="Different",
            description="Description",
            file_path="/path/file.py",
            function_name="func"
        )
        
        # Same content should have same hash
        assert issue1.compute_content_hash() == issue2.compute_content_hash()
        # Different content should have different hash
        assert issue1.compute_content_hash() != issue3.compute_content_hash()
    
    def test_from_analyzer_result_basic(self):
        """Test creating from analyzer result dictionary."""
        result = {
            "title": "Memory Leak",
            "description": "Leak detected",
            "file_path": "/path/file.py",
            "function_name": "my_func",
            "severity": "high",
            "category": "memory"
        }
        issue = AnalyzerIssue.from_analyzer_result(result)
        assert issue.title == "Memory Leak"
        assert issue.description == "Leak detected"
        assert issue.file_path == "/path/file.py"
        assert issue.function_name == "my_func"
        assert issue.severity == "high"
        assert issue.category == "memory"
    
    def test_from_analyzer_result_alternative_fields(self):
        """Test creating from analyzer result with alternative field names."""
        result = {
            "issue": "Memory Leak",  # Alternative to 'title'
            "details": "Leak detected",  # Alternative to 'description'
            "file": "/path/file.py",  # Alternative to 'file_path'
            "method": "my_func",  # Alternative to 'function_name'
            "priority": "high",  # Alternative to 'severity'
            "type": "memory"  # Alternative to 'category'
        }
        issue = AnalyzerIssue.from_analyzer_result(result)
        assert issue.title == "Memory Leak"
        assert issue.description == "Leak detected"
        assert issue.file_path == "/path/file.py"
        assert issue.function_name == "my_func"
        assert issue.severity == "high"
        assert issue.category == "memory"
    
    def test_to_dict_with_raw_data(self):
        """Test to_dict returns raw_data when available."""
        raw = {"original": "data", "extra": "field"}
        issue = AnalyzerIssue(
            title="Test",
            description="Test",
            raw_data=raw
        )
        assert issue.to_dict() == raw
    
    def test_to_dict_without_raw_data(self):
        """Test to_dict generates dict when raw_data not available."""
        issue = AnalyzerIssue(
            id="test-123",
            title="Test",
            description="Description",
            file_path="/path/file.py"
        )
        result = issue.to_dict()
        assert result['id'] == "test-123"
        assert result['title'] == "Test"
        assert result['description'] == "Description"
        assert result['file_path'] == "/path/file.py"
    
    def test_to_metadata(self):
        """Test metadata generation for vector store."""
        issue = AnalyzerIssue(
            id="test-123",
            title="Test",
            description="Description",
            file_path="/path/file.py",
            function_name="func",
            severity="high",
            category="memory"
        )
        metadata = issue.to_metadata()
        assert metadata['id'] == "test-123"
        assert metadata['title'] == "Test"
        assert metadata['file_path'] == "/path/file.py"
        assert metadata['function_name'] == "func"
        assert metadata['severity'] == "high"
        assert metadata['category'] == "memory"
        assert 'content_hash' in metadata
    
    def test_equality(self):
        """Test equality based on content hash."""
        issue1 = AnalyzerIssue(
            title="Test",
            description="Description",
            file_path="/path/file.py"
        )
        issue2 = AnalyzerIssue(
            title="Test",
            description="Description",
            file_path="/path/file.py"
        )
        issue3 = AnalyzerIssue(
            title="Different",
            description="Description",
            file_path="/path/file.py"
        )
        
        assert issue1 == issue2
        assert issue1 != issue3
    
    def test_hash(self):
        """Test hash for use in sets/dicts."""
        issue1 = AnalyzerIssue(
            title="Test",
            description="Description"
        )
        issue2 = AnalyzerIssue(
            title="Test",
            description="Description"
        )
        
        # Same content should have same hash
        assert hash(issue1) == hash(issue2)
        
        # Should be usable in sets
        issue_set = {issue1, issue2}
        assert len(issue_set) == 1


class TestDuplicateMatch:
    """Tests for DuplicateMatch data model."""
    
    def test_create_basic(self):
        """Test creating a basic duplicate match."""
        match = DuplicateMatch(
            original_id="orig-123",
            duplicate_id="dup-456",
            similarity_score=0.90,
            match_type="semantic"
        )
        assert match.original_id == "orig-123"
        assert match.duplicate_id == "dup-456"
        assert match.similarity_score == 0.90
        assert match.match_type == "semantic"
    
    def test_similarity_percentage(self):
        """Test similarity percentage calculation."""
        match = DuplicateMatch(
            original_id="orig",
            duplicate_id="dup",
            similarity_score=0.85,
            match_type="semantic"
        )
        assert match.similarity_percentage == 85
    
    def test_confidence_level_exact(self):
        """Test confidence level for exact match."""
        match = DuplicateMatch(
            original_id="orig",
            duplicate_id="dup",
            similarity_score=1.0,
            match_type="exact"
        )
        assert match.confidence_level == "very_high"
    
    def test_confidence_level_high(self):
        """Test confidence level for high similarity."""
        match = DuplicateMatch(
            original_id="orig",
            duplicate_id="dup",
            similarity_score=0.95,
            match_type="semantic"
        )
        assert match.confidence_level == "very_high"
    
    def test_confidence_level_moderate(self):
        """Test confidence level for moderate similarity."""
        match = DuplicateMatch(
            original_id="orig",
            duplicate_id="dup",
            similarity_score=0.75,
            match_type="semantic"
        )
        assert match.confidence_level == "moderate"
    
    def test_confidence_level_low(self):
        """Test confidence level for low similarity."""
        match = DuplicateMatch(
            original_id="orig",
            duplicate_id="dup",
            similarity_score=0.65,
            match_type="semantic"
        )
        assert match.confidence_level == "low"
    
    def test_str_representation(self):
        """Test string representation."""
        match = DuplicateMatch(
            original_id="orig-123",
            duplicate_id="dup-456",
            similarity_score=0.90,
            match_type="semantic"
        )
        str_repr = str(match)
        assert "dup-456" in str_repr
        assert "orig-123" in str_repr
        assert "90%" in str_repr
        assert "semantic" in str_repr
    
    def test_sorting(self):
        """Test sorting by similarity score (descending)."""
        match1 = DuplicateMatch("o1", "d1", 0.80, "semantic")
        match2 = DuplicateMatch("o2", "d2", 0.95, "semantic")
        match3 = DuplicateMatch("o3", "d3", 0.85, "semantic")
        
        sorted_matches = sorted([match1, match2, match3])
        assert sorted_matches[0].similarity_score == 0.95
        assert sorted_matches[1].similarity_score == 0.85
        assert sorted_matches[2].similarity_score == 0.80
