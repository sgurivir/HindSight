"""Shared fixtures for token management tests."""
import pytest
from unittest.mock import Mock, patch
import os


@pytest.fixture
def mock_apple_connect():
    """Mock AppleConnect token manager."""
    with patch('hindsight.utils.api_key_util.get_token_manager') as mock:
        token_manager = Mock()
        token_manager.get_token.return_value = "mock-apple-connect-token"
        mock.return_value = token_manager
        yield mock


@pytest.fixture
def clean_env():
    """Ensure environment variables are clean before each test."""
    env_vars = ['FLOODGATE_PROJECT_TOKEN', 'ANTHROPIC_API_KEY', 'CREDENTIALS', 'AWS_API_KEY']
    original_values = {var: os.environ.get(var) for var in env_vars}
    
    # Clear the variables
    for var in env_vars:
        if var in os.environ:
            del os.environ[var]
    
    yield
    
    # Restore original values
    for var, value in original_values.items():
        if value is not None:
            os.environ[var] = value
        elif var in os.environ:
            del os.environ[var]


@pytest.fixture
def floodgate_api_url():
    """Standard FloodGate API URL for testing."""
    return "https://floodgate.g.apple.com/api/openai/v1/chat/completions"


@pytest.fixture
def genai_api_url():
    """Standard GenAI API URL for testing."""
    return "https://genai.apple.com/api/v1/chat/completions"


@pytest.fixture
def standard_bedrock_url():
    """Standard AWS Bedrock URL for testing."""
    return "https://bedrock-runtime.us-east-1.amazonaws.com/model/invoke"


@pytest.fixture
def sample_floodgate_config(floodgate_api_url):
    """Sample configuration with FloodGate project token."""
    return {
        "llm_provider_type": "aws_bedrock",
        "api_end_point": floodgate_api_url,
        "model": "aws:anthropic.claude-sonnet-4-5-20250929-v1:0",
        "project-credentials": "test-floodgate-token"
    }


@pytest.fixture
def sample_oidc_config(floodgate_api_url):
    """Sample configuration with OIDC credentials."""
    return {
        "llm_provider_type": "aws_bedrock",
        "api_end_point": floodgate_api_url,
        "model": "aws:anthropic.claude-sonnet-4-5-20250929-v1:0",
        "credentials": "test-oidc-token"
    }


@pytest.fixture
def sample_both_config(floodgate_api_url):
    """Sample configuration with both credentials types."""
    return {
        "llm_provider_type": "aws_bedrock",
        "api_end_point": floodgate_api_url,
        "model": "aws:anthropic.claude-sonnet-4-5-20250929-v1:0",
        "credentials": "test-oidc-token",
        "project-credentials": "test-floodgate-token"
    }


@pytest.fixture
def sample_no_creds_config(floodgate_api_url):
    """Sample configuration with no credentials."""
    return {
        "llm_provider_type": "aws_bedrock",
        "api_end_point": floodgate_api_url,
        "model": "aws:anthropic.claude-sonnet-4-5-20250929-v1:0"
    }
