#!/usr/bin/env python3
"""
Tests for hindsight/analyzers/base_analyzer.py

Tests the BaseAnalyzer abstract base class which provides:
- Common interface for all analyzers (AnalyzerProtocol)
- Default implementations for directory exclusion recommendations
- Result reading and statistics calculation
"""

import os
import json
import pytest
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from unittest.mock import patch, MagicMock

from hindsight.analyzers.base_analyzer import BaseAnalyzer, AnalyzerProtocol


class ConcreteAnalyzer(BaseAnalyzer):
    """Concrete implementation of BaseAnalyzer for testing."""
    
    def __init__(self):
        super().__init__()
        self.analysis_results = []
    
    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """Simple implementation that returns the input with an 'analyzed' flag."""
        if not self._initialized:
            raise RuntimeError("Analyzer not initialized")
        
        result = dict(func_record)
        result['analyzed'] = True
        self.analysis_results.append(result)
        return result


class TestBaseAnalyzer:
    """Tests for BaseAnalyzer class."""

    def test_name_returns_class_name(self):
        """Test that name() returns the class name."""
        analyzer = ConcreteAnalyzer()
        assert analyzer.name() == "ConcreteAnalyzer"

    def test_initialize_sets_initialized_flag(self):
        """Test that initialize() sets the _initialized flag."""
        analyzer = ConcreteAnalyzer()
        assert analyzer._initialized is False
        
        analyzer.initialize({})
        assert analyzer._initialized is True

    def test_analyze_function_requires_initialization(self):
        """Test that analyze_function raises error if not initialized."""
        analyzer = ConcreteAnalyzer()
        
        with pytest.raises(RuntimeError, match="not initialized"):
            analyzer.analyze_function({'function': 'test'})

    def test_analyze_function_after_initialization(self):
        """Test that analyze_function works after initialization."""
        analyzer = ConcreteAnalyzer()
        analyzer.initialize({})
        
        result = analyzer.analyze_function({'function': 'test_func'})
        
        assert result is not None
        assert result['function'] == 'test_func'
        assert result['analyzed'] is True

    def test_finalize_does_nothing_by_default(self):
        """Test that finalize() does nothing by default."""
        analyzer = ConcreteAnalyzer()
        analyzer.initialize({})
        
        # Should not raise
        analyzer.finalize()


class TestBaseAnalyzerDirectoryRecommendations:
    """Tests for directory recommendation methods."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create basic structure
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()
            (src_dir / "main.py").write_text("# main")
            
            tests_dir = Path(tmpdir) / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_main.py").write_text("# test")
            
            yield tmpdir

    def test_get_recommended_exclude_directories(self, temp_repo):
        """Test getting recommended exclude directories."""
        analyzer = ConcreteAnalyzer()
        
        result = analyzer.get_recommended_exclude_directories(temp_repo)
        
        # get_recommended_exclude_directories calls get_recommended_exclude_directories_safe
        # which returns a List[str]
        assert isinstance(result, list)

    def test_get_recommended_exclude_directories_with_include_list(self, temp_repo):
        """Test with user-provided include list."""
        analyzer = ConcreteAnalyzer()
        
        result = analyzer.get_recommended_exclude_directories(
            temp_repo,
            user_provided_include_list=["src"]
        )
        
        assert isinstance(result, list)

    def test_get_recommended_exclude_directories_with_exclude_list(self, temp_repo):
        """Test with user-provided exclude list."""
        analyzer = ConcreteAnalyzer()
        
        result = analyzer.get_recommended_exclude_directories(
            temp_repo,
            user_provided_exclude_list=["tests"]
        )
        
        assert isinstance(result, list)


class TestBaseAnalyzerResultReading:
    """Tests for result reading and statistics methods."""

    @pytest.fixture
    def temp_results_dir(self):
        """Create a temporary directory with analysis result files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some analysis result files
            result1 = {
                'function': 'func1',
                'file': 'src/main.py',
                'severity': 'high',
                'category': 'security',
                'issue': 'SQL injection vulnerability'
            }
            
            result2 = {
                'function': 'func2',
                'file': 'src/utils.py',
                'severity': 'medium',
                'category': 'performance',
                'issue': 'Inefficient loop'
            }
            
            result3 = [
                {
                    'function': 'func3',
                    'file': 'src/helper.py',
                    'severity': 'low',
                    'category': 'style',
                    'issue': 'Missing docstring'
                },
                {
                    'function': 'func4',
                    'file': 'src/helper.py',
                    'severity': 'high',
                    'category': 'security',
                    'issue': 'Hardcoded password'
                }
            ]
            
            # Write result files
            with open(os.path.join(tmpdir, "func1_analysis.json"), 'w') as f:
                json.dump(result1, f)
            
            with open(os.path.join(tmpdir, "func2_analysis.json"), 'w') as f:
                json.dump(result2, f)
            
            with open(os.path.join(tmpdir, "func3_analysis.json"), 'w') as f:
                json.dump(result3, f)
            
            yield tmpdir

    def test_pull_results_from_directory(self, temp_results_dir):
        """Test pulling results from a directory."""
        analyzer = ConcreteAnalyzer()
        
        result = analyzer.pull_results_from_directory(temp_results_dir)
        
        assert 'results' in result
        assert 'statistics' in result
        assert 'summary' in result
        
        # Should have found all issues
        assert len(result['results']) == 4  # 1 + 1 + 2 from the three files

    def test_pull_results_from_nonexistent_directory(self):
        """Test pulling results from nonexistent directory."""
        analyzer = ConcreteAnalyzer()
        
        result = analyzer.pull_results_from_directory("/nonexistent/path")
        
        assert result['results'] == []
        assert result['summary']['total_files'] == 0

    def test_read_analysis_results(self, temp_results_dir):
        """Test reading analysis results with custom suffix."""
        analyzer = ConcreteAnalyzer()
        
        result = analyzer._read_analysis_results(temp_results_dir, "_analysis.json")
        
        assert 'results' in result
        assert len(result['results']) == 4

    def test_calculate_statistics_empty(self):
        """Test statistics calculation with empty issues list."""
        analyzer = ConcreteAnalyzer()
        
        stats = analyzer._calculate_statistics([])
        
        assert stats['total'] == 0
        assert stats['by_severity'] == {}
        assert stats['by_category'] == {}
        assert stats['by_file'] == {}
        assert stats['by_function'] == {}

    def test_calculate_statistics_with_issues(self):
        """Test statistics calculation with issues."""
        analyzer = ConcreteAnalyzer()
        
        issues = [
            {'severity': 'high', 'category': 'security', 'file': 'main.py', 'function': 'func1'},
            {'severity': 'high', 'category': 'security', 'file': 'main.py', 'function': 'func2'},
            {'severity': 'medium', 'category': 'performance', 'file': 'utils.py', 'function': 'func3'},
            {'severity': 'low', 'category': 'style', 'file': 'utils.py', 'function': 'func3'},
        ]
        
        stats = analyzer._calculate_statistics(issues)
        
        assert stats['total'] == 4
        assert stats['by_severity']['high'] == 2
        assert stats['by_severity']['medium'] == 1
        assert stats['by_severity']['low'] == 1
        assert stats['by_category']['security'] == 2
        assert stats['by_category']['performance'] == 1
        assert stats['by_category']['style'] == 1
        assert stats['by_file']['main.py'] == 2
        assert stats['by_file']['utils.py'] == 2
        assert stats['by_function']['func1'] == 1
        assert stats['by_function']['func3'] == 2

    def test_calculate_statistics_with_kind_field(self):
        """Test statistics calculation with 'kind' field instead of 'severity'."""
        analyzer = ConcreteAnalyzer()
        
        issues = [
            {'kind': 'error', 'category': 'security', 'file': 'main.py', 'function': 'func1'},
            {'kind': 'warning', 'category': 'style', 'file': 'main.py', 'function': 'func2'},
        ]
        
        stats = analyzer._calculate_statistics(issues)
        
        assert stats['by_severity']['error'] == 1
        assert stats['by_severity']['warning'] == 1

    def test_calculate_statistics_with_issueType_field(self):
        """Test statistics calculation with 'issueType' field instead of 'category'."""
        analyzer = ConcreteAnalyzer()
        
        issues = [
            {'severity': 'high', 'issueType': 'bug', 'file': 'main.py', 'function': 'func1'},
            {'severity': 'low', 'issueType': 'enhancement', 'file': 'main.py', 'function': 'func2'},
        ]
        
        stats = analyzer._calculate_statistics(issues)
        
        assert stats['by_category']['bug'] == 1
        assert stats['by_category']['enhancement'] == 1


class TestAnalyzerProtocol:
    """Tests for AnalyzerProtocol interface compliance."""

    def test_concrete_analyzer_satisfies_protocol(self):
        """Test that ConcreteAnalyzer satisfies AnalyzerProtocol."""
        analyzer = ConcreteAnalyzer()
        
        # Check all required methods exist
        assert hasattr(analyzer, 'name')
        assert hasattr(analyzer, 'initialize')
        assert hasattr(analyzer, 'analyze_function')
        assert hasattr(analyzer, 'finalize')
        assert hasattr(analyzer, 'pull_results_from_directory')
        
        # Check methods are callable
        assert callable(analyzer.name)
        assert callable(analyzer.initialize)
        assert callable(analyzer.analyze_function)
        assert callable(analyzer.finalize)
        assert callable(analyzer.pull_results_from_directory)


class TestEnhancedExcludeDirectories:
    """Tests for get_enhanced_exclude_directories method."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "src"
            src_dir.mkdir()
            (src_dir / "main.py").write_text("# main")
            yield tmpdir

    def test_get_enhanced_exclude_directories_no_api_key(self, temp_repo):
        """Test enhanced exclusions without API key."""
        analyzer = ConcreteAnalyzer()
        config = {}  # No API key
        
        result = analyzer.get_enhanced_exclude_directories(
            temp_repo,
            config
        )
        
        # Should still return results from static analysis
        assert isinstance(result, list)
