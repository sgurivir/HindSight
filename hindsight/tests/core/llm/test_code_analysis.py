#!/usr/bin/env python3
"""
Tests for hindsight/core/llm/code_analysis.py - Code Analysis Module.

This module tests:
- AnalysisConfig dataclass for configuration
- CodeAnalysis class for orchestrating code analysis
"""

import json
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from hindsight.core.llm.code_analysis import (
    AnalysisConfig,
    CodeAnalysis,
)


# ============================================================================
# AnalysisConfig Tests
# ============================================================================

class TestAnalysisConfig:
    """Tests for AnalysisConfig dataclass."""

    def test_required_fields(self):
        """Test AnalysisConfig with required fields only."""
        config = AnalysisConfig(
            json_file_path="/path/to/input.json",
            api_key="test-api-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022",
            repo_path="/path/to/repo",
            output_file="/path/to/output.json"
        )
        
        assert config.json_file_path == "/path/to/input.json"
        assert config.api_key == "test-api-key"
        assert config.api_url == "https://api.anthropic.com/v1/messages"
        assert config.model == "claude-3-5-sonnet-20241022"
        assert config.repo_path == "/path/to/repo"
        assert config.output_file == "/path/to/output.json"

    def test_default_values(self):
        """Test AnalysisConfig default values."""
        config = AnalysisConfig(
            json_file_path="/path/to/input.json",
            api_key="test-api-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022",
            repo_path="/path/to/repo",
            output_file="/path/to/output.json"
        )
        
        assert config.max_tokens == 64000
        assert config.temperature == 0.1
        assert config.processed_cache_file is None
        assert config.config is None
        assert config.file_content_provider is None
        assert config.file_filter is None
        assert config.min_function_body_length == 7

    def test_custom_values(self):
        """Test AnalysisConfig with custom values."""
        custom_config = {"llm_provider_type": "aws_bedrock"}
        file_filter = ["src/main.py", "src/utils.py"]
        
        config = AnalysisConfig(
            json_file_path="/path/to/input.json",
            api_key="test-api-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022",
            repo_path="/path/to/repo",
            output_file="/path/to/output.json",
            max_tokens=32000,
            temperature=0.5,
            config=custom_config,
            file_filter=file_filter,
            min_function_body_length=10
        )
        
        assert config.max_tokens == 32000
        assert config.temperature == 0.5
        assert config.config == custom_config
        assert config.file_filter == file_filter
        assert config.min_function_body_length == 10


# ============================================================================
# CodeAnalysis Tests - Initialization
# ============================================================================

class TestCodeAnalysisInitialization:
    """Tests for CodeAnalysis initialization."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    @pytest.fixture
    def temp_json_file(self, temp_repo):
        """Create a temporary JSON input file."""
        json_file = os.path.join(temp_repo, "input.json")
        with open(json_file, 'w') as f:
            json.dump({"function": "test_func", "file": "test.py"}, f)
        return json_file

    @pytest.fixture
    def mock_output_provider(self, temp_repo):
        """Create a mock output directory provider."""
        mock = MagicMock()
        mock.get_custom_base_dir.return_value = temp_repo
        mock.get_repo_artifacts_dir.return_value = os.path.join(temp_repo, "artifacts")
        return mock

    @pytest.fixture
    def analysis_config(self, temp_repo, temp_json_file):
        """Create a test AnalysisConfig."""
        return AnalysisConfig(
            json_file_path=temp_json_file,
            api_key="test-api-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022",
            repo_path=temp_repo,
            output_file=os.path.join(temp_repo, "output.json"),
            config={"llm_provider_type": "aws_bedrock"}
        )

    @patch('hindsight.core.llm.code_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.code_analysis.Claude')
    @patch('hindsight.core.llm.code_analysis.Tools')
    @patch('hindsight.core.llm.code_analysis.RepoAstIndex')
    def test_initialization(self, mock_ast_index, mock_tools, mock_claude, 
                           mock_get_output_provider, analysis_config, mock_output_provider):
        """Test CodeAnalysis initialization."""
        mock_get_output_provider.return_value = mock_output_provider
        mock_ast_index_instance = MagicMock()
        mock_ast_index.return_value = mock_ast_index_instance
        
        code_analysis = CodeAnalysis(analysis_config)
        
        assert code_analysis.config == analysis_config
        assert code_analysis.file_filter == []
        assert code_analysis.publisher is None
        assert code_analysis.total_input_tokens == 0
        assert code_analysis.total_output_tokens == 0

    @patch('hindsight.core.llm.code_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.code_analysis.Claude')
    @patch('hindsight.core.llm.code_analysis.Tools')
    @patch('hindsight.core.llm.code_analysis.RepoAstIndex')
    def test_initialization_with_file_filter(self, mock_ast_index, mock_tools, mock_claude,
                                             mock_get_output_provider, temp_repo, temp_json_file, 
                                             mock_output_provider):
        """Test CodeAnalysis initialization with file filter."""
        mock_get_output_provider.return_value = mock_output_provider
        mock_ast_index_instance = MagicMock()
        mock_ast_index.return_value = mock_ast_index_instance
        
        file_filter = ["src/main.py", "src/utils.py"]
        config = AnalysisConfig(
            json_file_path=temp_json_file,
            api_key="test-api-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022",
            repo_path=temp_repo,
            output_file=os.path.join(temp_repo, "output.json"),
            config={"llm_provider_type": "aws_bedrock"},
            file_filter=file_filter
        )
        
        code_analysis = CodeAnalysis(config)
        
        assert code_analysis.file_filter == file_filter


# ============================================================================
# CodeAnalysis Tests - Publisher
# ============================================================================

class TestCodeAnalysisPublisher:
    """Tests for CodeAnalysis publisher functionality."""

    @pytest.fixture
    def mock_code_analysis(self):
        """Create a mock CodeAnalysis instance."""
        with patch('hindsight.core.llm.code_analysis.get_output_directory_provider'), \
             patch('hindsight.core.llm.code_analysis.Claude'), \
             patch('hindsight.core.llm.code_analysis.Tools'), \
             patch('hindsight.core.llm.code_analysis.RepoAstIndex'):
            
            temp_dir = tempfile.mkdtemp()
            json_file = os.path.join(temp_dir, "input.json")
            with open(json_file, 'w') as f:
                json.dump({"function": "test"}, f)
            
            config = AnalysisConfig(
                json_file_path=json_file,
                api_key="test-key",
                api_url="https://api.test.com",
                model="test-model",
                repo_path=temp_dir,
                output_file=os.path.join(temp_dir, "output.json"),
                config={"llm_provider_type": "aws_bedrock"}
            )
            
            code_analysis = CodeAnalysis(config)
            yield code_analysis
            
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def test_set_publisher(self, mock_code_analysis):
        """Test setting publisher."""
        mock_publisher = MagicMock()
        
        mock_code_analysis.set_publisher(mock_publisher)
        
        assert mock_code_analysis.publisher == mock_publisher

    def test_should_analyze_function_no_publisher(self, mock_code_analysis):
        """Test should_analyze_function returns True when no publisher."""
        result = mock_code_analysis.should_analyze_function(
            "test.py", "test_func", "abc123"
        )
        
        assert result is True

    def test_should_analyze_function_with_existing_result(self, mock_code_analysis):
        """Test should_analyze_function returns False when result exists."""
        mock_publisher = MagicMock()
        mock_publisher.check_existing_result.return_value = {"existing": "result"}
        mock_code_analysis.set_publisher(mock_publisher)
        
        result = mock_code_analysis.should_analyze_function(
            "test.py", "test_func", "abc123"
        )
        
        assert result is False
        mock_publisher.check_existing_result.assert_called_once_with(
            "test.py", "test_func", "abc123"
        )

    def test_should_analyze_function_no_existing_result(self, mock_code_analysis):
        """Test should_analyze_function returns True when no existing result."""
        mock_publisher = MagicMock()
        mock_publisher.check_existing_result.return_value = None
        mock_code_analysis.set_publisher(mock_publisher)
        
        result = mock_code_analysis.should_analyze_function(
            "test.py", "test_func", "abc123"
        )
        
        assert result is True


# ============================================================================
# CodeAnalysis Tests - File Filtering
# ============================================================================

class TestCodeAnalysisFileFiltering:
    """Tests for CodeAnalysis file filtering functionality."""

    @pytest.fixture
    def mock_code_analysis_with_filter(self):
        """Create a mock CodeAnalysis instance with file filter."""
        with patch('hindsight.core.llm.code_analysis.get_output_directory_provider'), \
             patch('hindsight.core.llm.code_analysis.Claude'), \
             patch('hindsight.core.llm.code_analysis.Tools'), \
             patch('hindsight.core.llm.code_analysis.RepoAstIndex'):
            
            temp_dir = tempfile.mkdtemp()
            json_file = os.path.join(temp_dir, "input.json")
            with open(json_file, 'w') as f:
                json.dump({"function": "test"}, f)
            
            config = AnalysisConfig(
                json_file_path=json_file,
                api_key="test-key",
                api_url="https://api.test.com",
                model="test-model",
                repo_path=temp_dir,
                output_file=os.path.join(temp_dir, "output.json"),
                config={"llm_provider_type": "aws_bedrock"},
                file_filter=["src/main.py", "src/utils.py"]
            )
            
            code_analysis = CodeAnalysis(config)
            yield code_analysis
            
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def test_should_analyze_function_data_in_filter(self, mock_code_analysis_with_filter):
        """Test _should_analyze_function_data returns True for files in filter."""
        function_data = {"file": "src/main.py", "function": "main"}
        
        result = mock_code_analysis_with_filter._should_analyze_function_data(function_data)
        
        assert result is True

    def test_should_analyze_function_data_not_in_filter(self, mock_code_analysis_with_filter):
        """Test _should_analyze_function_data returns False for files not in filter."""
        function_data = {"file": "src/other.py", "function": "other"}
        
        result = mock_code_analysis_with_filter._should_analyze_function_data(function_data)
        
        assert result is False

    def test_should_analyze_function_data_no_file_path(self, mock_code_analysis_with_filter):
        """Test _should_analyze_function_data returns True when no file path."""
        function_data = {"function": "test_func"}
        
        result = mock_code_analysis_with_filter._should_analyze_function_data(function_data)
        
        assert result is True

    def test_should_analyze_function_data_context_format(self, mock_code_analysis_with_filter):
        """Test _should_analyze_function_data handles context format."""
        function_data = {
            "context": {"file": "src/main.py"},
            "function": "main"
        }
        
        result = mock_code_analysis_with_filter._should_analyze_function_data(function_data)
        
        assert result is True

    def test_filter_json_content_single_item_in_filter(self, mock_code_analysis_with_filter):
        """Test _filter_json_content with single item in filter."""
        json_content = json.dumps({"file": "src/main.py", "function": "main"})
        
        result = mock_code_analysis_with_filter._filter_json_content(json_content)
        
        assert result == json_content

    def test_filter_json_content_single_item_not_in_filter(self, mock_code_analysis_with_filter):
        """Test _filter_json_content with single item not in filter."""
        json_content = json.dumps({"file": "src/other.py", "function": "other"})
        
        result = mock_code_analysis_with_filter._filter_json_content(json_content)
        result_data = json.loads(result)
        
        assert result_data.get("filtered") is True

    def test_filter_json_content_list_partial_filter(self, mock_code_analysis_with_filter):
        """Test _filter_json_content with list where some items are filtered."""
        json_content = json.dumps([
            {"file": "src/main.py", "function": "main"},
            {"file": "src/other.py", "function": "other"},
            {"file": "src/utils.py", "function": "util"}
        ])
        
        result = mock_code_analysis_with_filter._filter_json_content(json_content)
        result_data = json.loads(result)
        
        assert len(result_data) == 2
        assert any(item["file"] == "src/main.py" for item in result_data)
        assert any(item["file"] == "src/utils.py" for item in result_data)

    def test_filter_json_content_invalid_json(self, mock_code_analysis_with_filter):
        """Test _filter_json_content handles invalid JSON gracefully."""
        invalid_json = "not valid json {"
        
        result = mock_code_analysis_with_filter._filter_json_content(invalid_json)
        
        # Should return original content on error
        assert result == invalid_json


# ============================================================================
# CodeAnalysis Tests - Token Tracking
# ============================================================================

class TestCodeAnalysisTokenTracking:
    """Tests for CodeAnalysis token tracking functionality."""

    @pytest.fixture
    def mock_code_analysis(self):
        """Create a mock CodeAnalysis instance."""
        with patch('hindsight.core.llm.code_analysis.get_output_directory_provider'), \
             patch('hindsight.core.llm.code_analysis.Claude'), \
             patch('hindsight.core.llm.code_analysis.Tools'), \
             patch('hindsight.core.llm.code_analysis.RepoAstIndex'):
            
            temp_dir = tempfile.mkdtemp()
            json_file = os.path.join(temp_dir, "input.json")
            with open(json_file, 'w') as f:
                json.dump({"function": "test"}, f)
            
            config = AnalysisConfig(
                json_file_path=json_file,
                api_key="test-key",
                api_url="https://api.test.com",
                model="test-model",
                repo_path=temp_dir,
                output_file=os.path.join(temp_dir, "output.json"),
                config={"llm_provider_type": "aws_bedrock"}
            )
            
            code_analysis = CodeAnalysis(config)
            yield code_analysis
            
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def test_get_token_totals_initial(self, mock_code_analysis):
        """Test get_token_totals returns zeros initially."""
        input_tokens, output_tokens = mock_code_analysis.get_token_totals()
        
        assert input_tokens == 0
        assert output_tokens == 0

    def test_extract_and_log_token_usage(self, mock_code_analysis):
        """Test _extract_and_log_token_usage updates totals."""
        response = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50
            }
        }
        
        mock_code_analysis._extract_and_log_token_usage(response, 1)
        
        input_tokens, output_tokens = mock_code_analysis.get_token_totals()
        assert input_tokens == 100
        assert output_tokens == 50

    def test_extract_and_log_token_usage_cumulative(self, mock_code_analysis):
        """Test _extract_and_log_token_usage accumulates tokens."""
        response1 = {"usage": {"input_tokens": 100, "output_tokens": 50}}
        response2 = {"usage": {"input_tokens": 200, "output_tokens": 100}}
        
        mock_code_analysis._extract_and_log_token_usage(response1, 1)
        mock_code_analysis._extract_and_log_token_usage(response2, 2)
        
        input_tokens, output_tokens = mock_code_analysis.get_token_totals()
        assert input_tokens == 300
        assert output_tokens == 150

    def test_extract_and_log_token_usage_alternative_keys(self, mock_code_analysis):
        """Test _extract_and_log_token_usage handles alternative key names."""
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50
            }
        }
        
        mock_code_analysis._extract_and_log_token_usage(response, 1)
        
        input_tokens, output_tokens = mock_code_analysis.get_token_totals()
        assert input_tokens == 100
        assert output_tokens == 50

    def test_extract_and_log_token_usage_missing_usage(self, mock_code_analysis):
        """Test _extract_and_log_token_usage handles missing usage gracefully."""
        response = {"content": "some response"}
        
        # Should not raise exception
        mock_code_analysis._extract_and_log_token_usage(response, 1)
        
        input_tokens, output_tokens = mock_code_analysis.get_token_totals()
        assert input_tokens == 0
        assert output_tokens == 0


# ============================================================================
# CodeAnalysis Tests - Result Processing
# ============================================================================

class TestCodeAnalysisResultProcessing:
    """Tests for CodeAnalysis result processing functionality."""

    @pytest.fixture
    def mock_code_analysis(self):
        """Create a mock CodeAnalysis instance."""
        with patch('hindsight.core.llm.code_analysis.get_output_directory_provider'), \
             patch('hindsight.core.llm.code_analysis.Claude'), \
             patch('hindsight.core.llm.code_analysis.Tools'), \
             patch('hindsight.core.llm.code_analysis.RepoAstIndex'):
            
            temp_dir = tempfile.mkdtemp()
            json_file = os.path.join(temp_dir, "input.json")
            with open(json_file, 'w') as f:
                json.dump({"function": "test"}, f)
            
            config = AnalysisConfig(
                json_file_path=json_file,
                api_key="test-key",
                api_url="https://api.test.com",
                model="test-model",
                repo_path=temp_dir,
                output_file=os.path.join(temp_dir, "output.json"),
                config={"llm_provider_type": "aws_bedrock"}
            )
            
            code_analysis = CodeAnalysis(config)
            yield code_analysis
            
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def test_process_analysis_result_valid_json(self, mock_code_analysis):
        """Test _process_analysis_result with valid JSON."""
        result = '{"issues": [{"type": "bug", "description": "test"}]}'
        
        success, processed, is_drop = mock_code_analysis._process_analysis_result(result)
        
        assert success is True
        assert is_drop is False
        # Result should be valid JSON
        parsed = json.loads(processed)
        # The result may be reformatted but should still be valid JSON
        assert parsed is not None

    def test_process_analysis_result_with_markdown(self, mock_code_analysis):
        """Test _process_analysis_result strips markdown code blocks."""
        result = '```json\n{"type": "bug", "description": "test"}\n```'
        
        success, processed, is_drop = mock_code_analysis._process_analysis_result(result)
        
        assert success is True
        # Should have cleaned the markdown and be valid JSON
        parsed = json.loads(processed)
        assert parsed is not None

    def test_process_analysis_result_invalid_json(self, mock_code_analysis):
        """Test _process_analysis_result handles invalid JSON."""
        result = "This is not valid JSON"
        
        success, processed, is_drop = mock_code_analysis._process_analysis_result(result)
        
        # Should still succeed but return cleaned result
        assert success is True
        assert is_drop is False


# ============================================================================
# CodeAnalysis Tests - Cache Management
# ============================================================================

class TestCodeAnalysisCacheManagement:
    """Tests for CodeAnalysis cache management functionality."""

    @pytest.fixture
    def mock_code_analysis_with_cache(self):
        """Create a mock CodeAnalysis instance with cache file."""
        with patch('hindsight.core.llm.code_analysis.get_output_directory_provider'), \
             patch('hindsight.core.llm.code_analysis.Claude'), \
             patch('hindsight.core.llm.code_analysis.Tools'), \
             patch('hindsight.core.llm.code_analysis.RepoAstIndex'):
            
            temp_dir = tempfile.mkdtemp()
            json_file = os.path.join(temp_dir, "input.json")
            cache_file = os.path.join(temp_dir, "cache.json")
            
            with open(json_file, 'w') as f:
                json.dump({"function": "test"}, f)
            
            config = AnalysisConfig(
                json_file_path=json_file,
                api_key="test-key",
                api_url="https://api.test.com",
                model="test-model",
                repo_path=temp_dir,
                output_file=os.path.join(temp_dir, "output.json"),
                config={"llm_provider_type": "aws_bedrock"},
                processed_cache_file=cache_file
            )
            
            code_analysis = CodeAnalysis(config)
            yield code_analysis, temp_dir, cache_file
            
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def test_load_processed_cache_empty(self, mock_code_analysis_with_cache):
        """Test _load_processed_cache returns empty dict when no cache."""
        code_analysis, _, _ = mock_code_analysis_with_cache
        
        cache = code_analysis._load_processed_cache()
        
        assert cache == {}

    def test_load_processed_cache_existing(self, mock_code_analysis_with_cache):
        """Test _load_processed_cache loads existing cache."""
        code_analysis, _, cache_file = mock_code_analysis_with_cache
        
        # Create cache file
        cache_data = {"test.json": {"success": True}}
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)
        
        cache = code_analysis._load_processed_cache()
        
        assert cache == cache_data

    def test_load_processed_cache_no_file_configured(self):
        """Test _load_processed_cache returns empty dict when no file configured."""
        with patch('hindsight.core.llm.code_analysis.get_output_directory_provider'), \
             patch('hindsight.core.llm.code_analysis.Claude'), \
             patch('hindsight.core.llm.code_analysis.Tools'), \
             patch('hindsight.core.llm.code_analysis.RepoAstIndex'):

            temp_dir = tempfile.mkdtemp()
            json_file = os.path.join(temp_dir, "input.json")

            with open(json_file, 'w') as f:
                json.dump({"function": "test"}, f)

            config = AnalysisConfig(
                json_file_path=json_file,
                api_key="test-key",
                api_url="https://api.test.com",
                model="test-model",
                repo_path=temp_dir,
                output_file=os.path.join(temp_dir, "output.json"),
                config={"llm_provider_type": "aws_bedrock"}
                # No processed_cache_file
            )

            code_analysis = CodeAnalysis(config)
            cache = code_analysis._load_processed_cache()

            assert cache == {}

            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)


# ============================================================================
# Two-Stage Analysis Tests
# ============================================================================

class TestRunContextCollection:
    """Tests for CodeAnalysis.run_context_collection()"""

    @pytest.fixture
    def analysis_config(self, tmp_path):
        """Create a real temp file as json_file_path."""
        temp_json = tmp_path / "func.json"
        temp_json.write_text('{"function": "myFunc", "file": "myFile.swift", "code": "func myFunc() {}"}')
        return AnalysisConfig(
            json_file_path=str(temp_json),
            api_key="test-key",
            api_url="https://api.test.com",
            model="claude-3-5-sonnet",
            repo_path="/repo",
            output_file="",
            config={"project_name": "TestProject", "description": "Test"}
        )

    @patch('hindsight.core.llm.code_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.code_analysis.RepoAstIndex')
    @patch('hindsight.core.llm.code_analysis.Claude')
    @patch('hindsight.core.llm.code_analysis.Tools')
    @patch('hindsight.core.llm.code_analysis.ContextCollectionAnalyzer')
    def test_returns_valid_context_bundle_on_success(self, mock_analyzer_cls, mock_tools, mock_claude_cls, mock_ast_cls, mock_provider, analysis_config):
        """Returns a valid context bundle dict on success."""
        mock_provider.return_value.get_repo_artifacts_dir.return_value = "/tmp/artifacts"
        mock_provider.return_value.get_custom_base_dir.return_value = "/tmp"

        mock_ast = MagicMock()
        mock_ast_cls.return_value = mock_ast
        mock_ast.merged_functions = {}
        mock_ast.merged_types = {}
        mock_ast.merged_call_graph = {}

        mock_claude = MagicMock()
        mock_claude_cls.return_value = mock_claude
        mock_claude.check_token_limit.return_value = True
        mock_claude.estimate_tokens.return_value = 1000

        # Mock the analyzer to return valid JSON
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        mock_analyzer.run_iterative_analysis.return_value = '{"primary_function": {"name": "myFunc", "file_path": "myFile.swift", "start_line": 1, "end_line": 3, "source": "  1: func myFunc() {}"}, "callers": [], "callees": [], "data_types": [], "constants_and_globals": [], "file_summaries": {}, "knowledge_hits": [], "collection_notes": "OK"}'

        with patch('hindsight.core.prompts.prompt_builder.PromptBuilder.build_context_collection_prompt', return_value=("sys", "usr")):
            code_analysis = CodeAnalysis(analysis_config)
            result = code_analysis.run_context_collection(
                json_data={"function": "myFunc", "file": "myFile.swift"},
                checksum="abc123"
            )

        assert result is not None
        assert isinstance(result, dict)
        assert 'primary_function' in result

    @patch('os.path.exists', return_value=False)
    @patch('hindsight.core.llm.code_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.code_analysis.RepoAstIndex')
    @patch('hindsight.core.llm.code_analysis.Claude')
    @patch('hindsight.core.llm.code_analysis.Tools')
    @patch('hindsight.core.llm.code_analysis.ContextCollectionAnalyzer')
    def test_returns_none_on_invalid_json_after_retry(self, mock_analyzer_cls, mock_tools, mock_claude_cls, mock_ast_cls, mock_provider, mock_exists, analysis_config):
        """Returns None if LLM returns invalid JSON after retry."""
        mock_provider.return_value.get_repo_artifacts_dir.return_value = "/tmp/artifacts"
        mock_provider.return_value.get_custom_base_dir.return_value = "/tmp"

        mock_ast = MagicMock()
        mock_ast_cls.return_value = mock_ast
        mock_ast.merged_functions = {}
        mock_ast.merged_types = {}
        mock_ast.merged_call_graph = {}

        mock_claude = MagicMock()
        mock_claude_cls.return_value = mock_claude
        mock_claude.check_token_limit.return_value = True

        # Mock the analyzer to return invalid JSON
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        mock_analyzer.run_iterative_analysis.return_value = "This is not JSON at all"

        with patch('hindsight.core.prompts.prompt_builder.PromptBuilder.build_context_collection_prompt', return_value=("sys", "usr")):
            code_analysis = CodeAnalysis(analysis_config)
            result = code_analysis.run_context_collection(
                json_data={"function": "myFunc"},
                checksum="abc123"
            )

        assert result is None


class TestRunAnalysisFromContext:
    """Tests for CodeAnalysis.run_analysis_from_context()"""

    @pytest.fixture
    def context_bundle(self):
        return {
            "primary_function": {
                "name": "myFunc",
                "file_path": "myFile.swift",
                "start_line": 10,
                "end_line": 20,
                "source": "  10: func myFunc() {}"
            },
            "callers": [],
            "callees": [],
            "data_types": [],
            "constants_and_globals": [],
            "file_summaries": {},
            "knowledge_hits": [],
            "collection_notes": "OK"
        }

    @pytest.fixture
    def analysis_config(self, tmp_path):
        temp_json = tmp_path / "func.json"
        temp_json.write_text('{"function": "myFunc"}')
        return AnalysisConfig(
            json_file_path=str(temp_json),
            api_key="test-key",
            api_url="https://api.test.com",
            model="claude-3-5-sonnet",
            repo_path="/repo",
            output_file="",
            config={"project_name": "TestProject", "description": "Test"}
        )

    @patch('hindsight.core.llm.code_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.code_analysis.RepoAstIndex')
    @patch('hindsight.core.llm.code_analysis.Claude')
    @patch('hindsight.core.llm.code_analysis.Tools')
    @patch('hindsight.core.llm.code_analysis.CodeAnalysisAnalyzer')
    def test_returns_issues_list_on_success(self, mock_analyzer_cls, mock_tools, mock_claude_cls, mock_ast_cls, mock_provider, analysis_config, context_bundle):
        """Returns a list of issues matching the output schema."""
        mock_provider.return_value.get_repo_artifacts_dir.return_value = "/tmp/artifacts"
        mock_provider.return_value.get_custom_base_dir.return_value = "/tmp"

        mock_ast = MagicMock()
        mock_ast_cls.return_value = mock_ast

        mock_claude = MagicMock()
        mock_claude_cls.return_value = mock_claude
        mock_claude.check_token_limit.return_value = True

        # Mock the analyzer to return valid issues JSON
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        issues_json = '[{"file_path": "myFile.swift", "file_name": "myFile.swift", "function_name": "myFunc", "line_number": "10", "severity": "high", "issue": "Null pointer", "description": "desc", "suggestion": "fix", "category": "logicBug", "issueType": "logicBug"}]'
        mock_analyzer.run_iterative_analysis.return_value = issues_json

        with patch('hindsight.core.prompts.prompt_builder.PromptBuilder.build_analysis_from_context_prompt', return_value=("sys", "usr")):
            code_analysis = CodeAnalysis(analysis_config)
            result = code_analysis.run_analysis_from_context(context_bundle)

        assert result is not None
        assert isinstance(result, list)
        if result:
            assert isinstance(result[0], dict)

    @patch('hindsight.core.llm.code_analysis.get_output_directory_provider')
    @patch('hindsight.core.llm.code_analysis.RepoAstIndex')
    @patch('hindsight.core.llm.code_analysis.Claude')
    @patch('hindsight.core.llm.code_analysis.Tools')
    @patch('hindsight.core.llm.code_analysis.CodeAnalysisAnalyzer')
    def test_returns_empty_list_when_no_issues(self, mock_analyzer_cls, mock_tools, mock_claude_cls, mock_ast_cls, mock_provider, analysis_config, context_bundle):
        """Returns empty list when LLM finds no issues."""
        mock_provider.return_value.get_repo_artifacts_dir.return_value = "/tmp/artifacts"
        mock_provider.return_value.get_custom_base_dir.return_value = "/tmp"

        mock_ast = MagicMock()
        mock_ast_cls.return_value = mock_ast

        mock_claude = MagicMock()
        mock_claude_cls.return_value = mock_claude
        mock_claude.check_token_limit.return_value = True

        # Mock the analyzer to return empty array
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        mock_analyzer.run_iterative_analysis.return_value = '[]'

        with patch('hindsight.core.prompts.prompt_builder.PromptBuilder.build_analysis_from_context_prompt', return_value=("sys", "usr")):
            code_analysis = CodeAnalysis(analysis_config)
            result = code_analysis.run_analysis_from_context(context_bundle)

        assert result == []
