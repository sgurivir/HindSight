#!/usr/bin/env python3
"""
Tests for hindsight/core/llm/llm.py - Claude LLM wrapper.

This module tests:
- ConversationState class for managing conversation history
- ClaudeConfig dataclass for configuration
- create_llm_provider factory function
- Claude class for LLM interactions
"""

import json
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from hindsight.core.llm.llm import (
    ConversationState,
    ClaudeConfig,
    create_llm_provider,
    Claude,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAYS,
)


# ============================================================================
# ConversationState Tests
# ============================================================================

class TestConversationState:
    """Tests for ConversationState class."""

    def test_initialization_default_provider(self):
        """Test ConversationState initializes with default provider type."""
        state = ConversationState()
        
        assert state.messages == []
        assert state.system_prompt is None
        assert state.original_request is None
        assert state.provider_type == "claude"

    def test_initialization_custom_provider(self):
        """Test ConversationState initializes with custom provider type."""
        state = ConversationState(provider_type="aws_bedrock")
        
        assert state.provider_type == "aws_bedrock"

    def test_initialization_provider_type_case_insensitive(self):
        """Test provider type is normalized to lowercase."""
        state = ConversationState(provider_type="AWS_BEDROCK")
        
        assert state.provider_type == "aws_bedrock"

    def test_set_system_prompt(self):
        """Test setting system prompt."""
        state = ConversationState()
        state.set_system_prompt("You are a helpful assistant.")
        
        assert state.system_prompt == "You are a helpful assistant."

    def test_set_original_request(self):
        """Test setting original request."""
        state = ConversationState()
        state.set_original_request("Analyze this code")
        
        assert state.original_request == "Analyze this code"

    def test_add_user_message(self):
        """Test adding user message to conversation."""
        state = ConversationState()
        state.add_user_message("Hello, world!")
        
        assert len(state.messages) == 1
        assert state.messages[0] == {"role": "user", "content": "Hello, world!"}

    def test_add_assistant_message(self):
        """Test adding assistant message to conversation."""
        state = ConversationState()
        state.add_assistant_message("Hello! How can I help?")
        
        assert len(state.messages) == 1
        assert state.messages[0] == {"role": "assistant", "content": "Hello! How can I help?"}

    def test_add_assistant_message_with_content_blocks(self):
        """Test adding assistant message with content blocks."""
        state = ConversationState()
        content_blocks = [
            {"type": "text", "text": "Here is my response"},
            {"type": "tool_use", "id": "123", "name": "readFile", "input": {"path": "test.py"}}
        ]
        state.add_assistant_message(content_blocks)
        
        assert len(state.messages) == 1
        assert state.messages[0]["role"] == "assistant"
        assert state.messages[0]["content"] == content_blocks

    def test_add_tool_result(self):
        """Test adding tool result to conversation."""
        state = ConversationState()
        state.add_tool_result("tool_123", "File content here")
        
        assert len(state.messages) == 1
        assert state.messages[0]["role"] == "user"
        assert "[TOOL_RESULT: tool_123]" in state.messages[0]["content"]
        assert "File content here" in state.messages[0]["content"]

    def test_add_tool_result_unified_format(self):
        """Test that tool results use unified plain text format for all providers."""
        # Test Claude provider
        state_claude = ConversationState(provider_type="claude")
        state_claude.add_tool_result("tool_1", "result_1")
        
        # Test AWS Bedrock provider
        state_bedrock = ConversationState(provider_type="aws_bedrock")
        state_bedrock.add_tool_result("tool_1", "result_1")
        
        # Both should use the same format
        assert state_claude.messages[0]["content"] == state_bedrock.messages[0]["content"]
        assert "[TOOL_RESULT: tool_1]" in state_claude.messages[0]["content"]

    def test_add_multiple_tool_results(self):
        """Test adding multiple tool results as single message."""
        state = ConversationState()
        tool_results = [
            {"tool_use_id": "tool_1", "result": "Result 1"},
            {"tool_use_id": "tool_2", "result": "Result 2"},
            {"tool_use_id": "tool_3", "result": "Result 3"}
        ]
        state.add_multiple_tool_results(tool_results)
        
        assert len(state.messages) == 1
        assert state.messages[0]["role"] == "user"
        assert "[TOOL_RESULT: tool_1]" in state.messages[0]["content"]
        assert "[TOOL_RESULT: tool_2]" in state.messages[0]["content"]
        assert "[TOOL_RESULT: tool_3]" in state.messages[0]["content"]
        assert "Result 1" in state.messages[0]["content"]
        assert "Result 2" in state.messages[0]["content"]
        assert "Result 3" in state.messages[0]["content"]

    def test_add_multiple_tool_results_empty_list(self):
        """Test adding empty tool results list does nothing."""
        state = ConversationState()
        state.add_multiple_tool_results([])
        
        assert len(state.messages) == 0

    def test_get_full_conversation(self):
        """Test getting full conversation history."""
        state = ConversationState()
        state.add_user_message("Hello")
        state.add_assistant_message("Hi there!")
        state.add_user_message("How are you?")
        
        conversation = state.get_full_conversation()
        
        assert len(conversation) == 3
        assert conversation[0]["role"] == "user"
        assert conversation[1]["role"] == "assistant"
        assert conversation[2]["role"] == "user"

    def test_get_full_conversation_returns_copy(self):
        """Test that get_full_conversation returns a copy."""
        state = ConversationState()
        state.add_user_message("Hello")
        
        conversation = state.get_full_conversation()
        conversation.append({"role": "user", "content": "Modified"})
        
        # Original should be unchanged
        assert len(state.messages) == 1

    def test_get_conversation_with_context(self):
        """Test getting conversation with additional context."""
        state = ConversationState()
        state.set_original_request("Analyze this code")
        state.add_user_message("Hello")
        
        conversation = state.get_conversation_with_context("Additional context here")
        
        assert len(conversation) == 2
        assert "Additional context here" in conversation[-1]["content"]
        assert "Analyze this code" in conversation[-1]["content"]

    def test_get_conversation_with_context_no_context(self):
        """Test getting conversation without additional context."""
        state = ConversationState()
        state.add_user_message("Hello")
        
        conversation = state.get_conversation_with_context()
        
        assert len(conversation) == 1

    def test_clear(self):
        """Test clearing conversation state."""
        state = ConversationState()
        state.set_system_prompt("System prompt")
        state.set_original_request("Original request")
        state.add_user_message("Hello")
        state.add_assistant_message("Hi")
        
        state.clear()
        
        assert state.messages == []
        assert state.system_prompt is None
        assert state.original_request is None


# ============================================================================
# ClaudeConfig Tests
# ============================================================================

class TestClaudeConfig:
    """Tests for ClaudeConfig dataclass."""

    def test_default_values(self):
        """Test ClaudeConfig default values."""
        config = ClaudeConfig(
            api_key="test-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022"
        )
        
        assert config.api_key == "test-key"
        assert config.api_url == "https://api.anthropic.com/v1/messages"
        assert config.model == "claude-3-5-sonnet-20241022"
        assert config.max_tokens == 64000
        assert config.temperature == 0.05
        assert config.timeout == 300
        assert config.provider_type == "claude"

    def test_custom_values(self):
        """Test ClaudeConfig with custom values."""
        config = ClaudeConfig(
            api_key="custom-key",
            api_url="https://custom.api.com",
            model="custom-model",
            max_tokens=32000,
            temperature=0.7,
            timeout=600,
            provider_type="aws_bedrock"
        )
        
        assert config.max_tokens == 32000
        assert config.temperature == 0.7
        assert config.timeout == 600
        assert config.provider_type == "aws_bedrock"


# ============================================================================
# create_llm_provider Tests
# ============================================================================

class TestCreateLLMProvider:
    """Tests for create_llm_provider factory function."""

    @patch('hindsight.core.llm.llm.ClaudeProvider')
    def test_create_claude_provider(self, mock_claude_provider):
        """Test creating Claude provider."""
        config = ClaudeConfig(
            api_key="test-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022",
            provider_type="claude"
        )
        
        provider = create_llm_provider(config)
        
        mock_claude_provider.assert_called_once()

    @patch('hindsight.core.llm.llm.AWSBedrockProvider')
    def test_create_aws_bedrock_provider(self, mock_bedrock_provider):
        """Test creating AWS Bedrock provider."""
        config = ClaudeConfig(
            api_key="test-key",
            api_url="https://bedrock.amazonaws.com",
            model="anthropic.claude-3-sonnet-20240229-v1:0",
            provider_type="aws_bedrock"
        )
        
        provider = create_llm_provider(config)
        
        mock_bedrock_provider.assert_called_once()

    @patch('hindsight.core.llm.llm.DummyProvider')
    def test_create_dummy_provider(self, mock_dummy_provider):
        """Test creating dummy provider."""
        config = ClaudeConfig(
            api_key="test-key",
            api_url="https://dummy.api.com",
            model="dummy-model",
            provider_type="dummy"
        )
        
        provider = create_llm_provider(config)
        
        mock_dummy_provider.assert_called_once()

    def test_invalid_provider_type(self):
        """Test that invalid provider type raises ValueError."""
        config = ClaudeConfig(
            api_key="test-key",
            api_url="https://api.example.com",
            model="test-model",
            provider_type="invalid_provider"
        )
        
        with pytest.raises(ValueError):
            create_llm_provider(config)


# ============================================================================
# Claude Class Tests
# ============================================================================

class TestClaude:
    """Tests for Claude class."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock LLM provider."""
        provider = MagicMock()
        provider.create_payload.return_value = {"messages": []}
        provider.make_request.return_value = {
            "content": [{"type": "text", "text": "Response"}],
            "usage": {"input_tokens": 100, "output_tokens": 50}
        }
        provider.validate_connection.return_value = True
        return provider

    @pytest.fixture
    def claude_config(self):
        """Create a test ClaudeConfig."""
        return ClaudeConfig(
            api_key="test-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022",
            provider_type="dummy"
        )

    @pytest.fixture
    def claude_instance(self, claude_config, mock_provider):
        """Create a Claude instance with mocked provider."""
        with patch('hindsight.core.llm.llm.create_llm_provider', return_value=mock_provider):
            return Claude(claude_config)

    def test_initialization(self, claude_instance, claude_config):
        """Test Claude initialization."""
        assert claude_instance.config == claude_config
        assert claude_instance.conversation_messages == []
        assert claude_instance.conversation_responses == []
        assert claude_instance.conversation_metadata == {}

    def test_start_conversation(self, claude_instance):
        """Test starting a new conversation."""
        claude_instance.start_conversation("code_analysis", "test_file.py")
        
        assert claude_instance.conversation_metadata['analysis_type'] == "code_analysis"
        assert claude_instance.conversation_metadata['context_info'] == "test_file.py"
        assert 'start_time' in claude_instance.conversation_metadata
        assert claude_instance.conversation_metadata['model'] == "claude-3-5-sonnet-20241022"

    def test_estimate_tokens(self, claude_instance):
        """Test token estimation."""
        text = "Hello, world! This is a test."
        estimated = claude_instance.estimate_tokens(text)
        
        # Estimation is ~3 characters per token
        assert estimated == len(text) // 3

    def test_check_token_limit_within_limit(self, claude_instance):
        """Test token limit check when within limits."""
        system_prompt = "You are a helpful assistant."
        user_prompt = "Hello!"
        
        result = claude_instance.check_token_limit(system_prompt, user_prompt)
        
        assert result is True

    def test_check_token_limit_exceeds_limit(self, claude_instance):
        """Test token limit check when exceeding limits."""
        # Create a very large prompt that exceeds limits
        system_prompt = "x" * 200000
        user_prompt = "y" * 200000
        
        result = claude_instance.check_token_limit(system_prompt, user_prompt)
        
        assert result is False

    def test_validate_connection(self, claude_instance, mock_provider):
        """Test connection validation."""
        result = claude_instance.validate_connection()
        
        assert result is True
        mock_provider.validate_connection.assert_called_once()

    def test_validate_connection_failure(self, claude_instance, mock_provider):
        """Test connection validation failure."""
        mock_provider.validate_connection.side_effect = Exception("Connection failed")
        
        result = claude_instance.validate_connection()
        
        assert result is False

    def test_send_message(self, claude_instance, mock_provider):
        """Test sending a message."""
        messages = [{"role": "user", "content": "Hello"}]
        
        response = claude_instance.send_message(messages)
        
        assert response is not None
        mock_provider.create_payload.assert_called_once()
        mock_provider.make_request.assert_called_once()

    def test_send_message_with_system(self, claude_instance, mock_provider):
        """Test sending a message with system prompt."""
        system_prompt = "You are a helpful assistant."
        messages = [{"role": "user", "content": "Hello"}]
        
        response = claude_instance.send_message_with_system(system_prompt, messages)
        
        assert response is not None
        mock_provider.create_payload.assert_called_once()

    def test_send_message_error_response(self, claude_instance, mock_provider):
        """Test handling error response."""
        mock_provider.make_request.return_value = {
            "error": "rate_limit_exceeded",
            "status_code": 429,
            "message": "Too many requests"
        }
        
        messages = [{"role": "user", "content": "Hello"}]
        response = claude_instance.send_message(messages)
        
        assert response is None

    def test_send_message_token_limit_exceeded(self, claude_instance, mock_provider):
        """Test handling token limit exceeded error."""
        mock_provider.create_payload.side_effect = ValueError("Token limit exceeded")
        
        messages = [{"role": "user", "content": "Hello"}]
        response = claude_instance.send_message(messages)
        
        assert response is not None
        assert response.get("error") == "token_limit_exceeded"

    def test_format_content_blocks_text(self, claude_instance):
        """Test formatting text content blocks."""
        content_blocks = [
            {"type": "text", "text": "Hello, world!"}
        ]
        
        result = claude_instance._format_content_blocks(content_blocks)
        
        assert "Hello, world!" in result

    def test_format_content_blocks_tool_use(self, claude_instance):
        """Test formatting tool_use content blocks."""
        content_blocks = [
            {"type": "tool_use", "id": "123", "name": "readFile", "input": {"path": "test.py"}}
        ]
        
        result = claude_instance._format_content_blocks(content_blocks)
        
        assert "[TOOL_USE: readFile" in result
        assert "123" in result

    def test_format_content_blocks_tool_result(self, claude_instance):
        """Test formatting tool_result content blocks."""
        content_blocks = [
            {"type": "tool_result", "tool_use_id": "123", "content": "File content"}
        ]
        
        result = claude_instance._format_content_blocks(content_blocks)
        
        assert "[TOOL_RESULT:" in result
        assert "File content" in result

    def test_format_content_blocks_empty(self, claude_instance):
        """Test formatting empty content blocks."""
        result = claude_instance._format_content_blocks([])
        
        assert result == ""

    def test_format_content_blocks_mixed(self, claude_instance):
        """Test formatting mixed content blocks."""
        content_blocks = [
            {"type": "text", "text": "Let me read the file."},
            {"type": "tool_use", "id": "123", "name": "readFile", "input": {"path": "test.py"}}
        ]
        
        result = claude_instance._format_content_blocks(content_blocks)
        
        assert "Let me read the file." in result
        assert "[TOOL_USE: readFile" in result

    def test_extract_json_tool_requests_markdown(self, claude_instance):
        """Test extracting JSON tool requests from markdown blocks."""
        content = '''
        Let me read the file.
        ```json
        {"tool": "readFile", "path": "test.py", "reason": "Need to analyze"}
        ```
        '''
        
        requests = claude_instance._extract_json_tool_requests(content)
        
        assert len(requests) == 1
        assert requests[0]["tool"] == "readFile"
        assert requests[0]["path"] == "test.py"

    def test_extract_json_tool_requests_simple(self, claude_instance):
        """Test extracting simple JSON tool requests."""
        content = 'I need to read the file: {"tool": "readFile", "path": "test.py"}'
        
        requests = claude_instance._extract_json_tool_requests(content)
        
        assert len(requests) == 1
        assert requests[0]["tool"] == "readFile"

    def test_extract_json_tool_requests_multiple(self, claude_instance):
        """Test extracting multiple JSON tool requests."""
        content = '''
        ```json
        {"tool": "readFile", "path": "file1.py"}
        ```
        And also:
        ```json
        {"tool": "readFile", "path": "file2.py"}
        ```
        '''
        
        requests = claude_instance._extract_json_tool_requests(content)
        
        assert len(requests) == 2

    def test_extract_json_tool_requests_no_tool_key(self, claude_instance):
        """Test that JSON without 'tool' key is not extracted."""
        content = '{"path": "test.py", "reason": "test"}'
        
        requests = claude_instance._extract_json_tool_requests(content)
        
        assert len(requests) == 0

    def test_extract_json_tool_requests_invalid_json(self, claude_instance):
        """Test handling invalid JSON in tool requests."""
        content = '{"tool": "readFile", "path": invalid}'
        
        requests = claude_instance._extract_json_tool_requests(content)
        
        assert len(requests) == 0


# ============================================================================
# Claude Class - Prompts Logging Tests
# ============================================================================

class TestClaudePromptsLogging:
    """Tests for Claude prompts logging functionality."""

    @pytest.fixture
    def temp_output_dir(self):
        """Create a temporary output directory."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    @pytest.fixture
    def mock_output_provider(self, temp_output_dir):
        """Create a mock output directory provider."""
        mock = MagicMock()
        mock.get_repo_artifacts_dir.return_value = temp_output_dir
        return mock

    def test_setup_prompts_logging(self, mock_output_provider, temp_output_dir):
        """Test setting up prompts logging directory."""
        with patch('hindsight.core.llm.llm.get_output_directory_provider', return_value=mock_output_provider):
            Claude.setup_prompts_logging()
            
            assert Claude._prompts_dir is not None
            assert os.path.exists(Claude._prompts_dir)

    def test_clear_older_prompts(self, mock_output_provider, temp_output_dir):
        """Test clearing older prompts."""
        with patch('hindsight.core.llm.llm.get_output_directory_provider', return_value=mock_output_provider):
            Claude.setup_prompts_logging()
            
            # Create a test file in prompts directory
            test_file = os.path.join(Claude._prompts_dir, "test.txt")
            with open(test_file, 'w') as f:
                f.write("test")
            
            Claude.clear_older_prompts()
            
            # Directory should be recreated but empty
            assert os.path.exists(Claude._prompts_dir)
            assert Claude._conversation_counter == 0


# ============================================================================
# Claude Class - Tool Execution Tests
# ============================================================================

class TestClaudeToolExecution:
    """Tests for Claude tool execution methods."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock LLM provider."""
        provider = MagicMock()
        provider.create_payload.return_value = {"messages": []}
        provider.make_request.return_value = {
            "content": [{"type": "text", "text": "Response"}],
            "usage": {"input_tokens": 100, "output_tokens": 50}
        }
        return provider

    @pytest.fixture
    def claude_config(self):
        """Create a test ClaudeConfig."""
        return ClaudeConfig(
            api_key="test-key",
            api_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet-20241022",
            provider_type="dummy"
        )

    @pytest.fixture
    def claude_instance(self, claude_config, mock_provider):
        """Create a Claude instance with mocked provider."""
        with patch('hindsight.core.llm.llm.create_llm_provider', return_value=mock_provider):
            return Claude(claude_config)

    def test_execute_json_tool_request(self, claude_instance):
        """Test executing JSON tool request."""
        mock_tools_executor = MagicMock()
        mock_tools_executor.tools.execute_tool_use.return_value = "File content"
        
        tool_request = {"tool": "readFile", "path": "test.py", "reason": "test"}
        supported_tools = ["readFile", "runTerminalCmd"]
        
        result = claude_instance._execute_json_tool_request(
            tool_request, mock_tools_executor, supported_tools
        )
        
        assert result == "File content"
        mock_tools_executor.tools.execute_tool_use.assert_called_once()

    def test_execute_json_tool_request_unsupported_tool(self, claude_instance):
        """Test executing unsupported JSON tool request."""
        mock_tools_executor = MagicMock()
        
        tool_request = {"tool": "unsupportedTool", "param": "value"}
        supported_tools = ["readFile", "runTerminalCmd"]
        
        result = claude_instance._execute_json_tool_request(
            tool_request, mock_tools_executor, supported_tools
        )
        
        assert "Error" in result
        assert "unsupportedTool" in result

    def test_execute_tool_use(self, claude_instance):
        """Test executing structured tool_use."""
        mock_tools_executor = MagicMock()
        mock_tools_executor.tools.execute_tool_use.return_value = "Tool result"
        
        tool_use = {
            "id": "tool_123",
            "name": "readFile",
            "input": {"path": "test.py"}
        }
        supported_tools = ["readFile"]
        
        result = claude_instance._execute_tool_use(
            tool_use, mock_tools_executor, supported_tools
        )
        
        assert result == "Tool result"

    def test_execute_tool_use_unsupported(self, claude_instance):
        """Test executing unsupported tool_use."""
        mock_tools_executor = MagicMock()
        
        tool_use = {
            "id": "tool_123",
            "name": "unsupportedTool",
            "input": {}
        }
        supported_tools = ["readFile"]
        
        result = claude_instance._execute_tool_use(
            tool_use, mock_tools_executor, supported_tools
        )
        
        assert "Error" in result
        assert "unsupportedTool" in result


# ============================================================================
# Constants Tests
# ============================================================================

class TestConstants:
    """Tests for module constants."""

    def test_default_max_retries(self):
        """Test DEFAULT_MAX_RETRIES constant."""
        assert DEFAULT_MAX_RETRIES == 3

    def test_default_retry_delays(self):
        """Test DEFAULT_RETRY_DELAYS constant."""
        assert DEFAULT_RETRY_DELAYS == [30, 60, 90]
        assert len(DEFAULT_RETRY_DELAYS) == DEFAULT_MAX_RETRIES
