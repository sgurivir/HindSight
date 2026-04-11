"""Tests for environment variable handling."""
import pytest
import os
from hindsight.config.llm_providers.llm_provider_config import LLMProviderConfig
from hindsight.utils.config_util import (
    get_credentials_from_config,
    get_project_credentials_from_config
)


class TestEnvironmentVariableFallback:
    """Test environment variable fallback behavior."""
    
    def test_credentials_falls_back_to_anthropic_api_key(self, clean_env):
        """Test credentials falls back to ANTHROPIC_API_KEY env var."""
        os.environ['ANTHROPIC_API_KEY'] = 'env-oauth-token'
        config_dict = {}  # No credentials in config
        
        result = get_credentials_from_config(config_dict)
        
        assert result == 'env-oauth-token'
    
    def test_project_credentials_falls_back_to_floodgate_token(self, clean_env):
        """Test project-credentials falls back to FLOODGATE_PROJECT_TOKEN env var."""
        os.environ['FLOODGATE_PROJECT_TOKEN'] = 'env-floodgate-token'
        config_dict = {}  # No project-credentials in config
        
        result = get_project_credentials_from_config(config_dict)
        
        assert result == 'env-floodgate-token'
    
    def test_config_takes_priority_over_env_var(self, clean_env):
        """Test that config values take priority over environment variables."""
        os.environ['ANTHROPIC_API_KEY'] = 'env-token'
        os.environ['FLOODGATE_PROJECT_TOKEN'] = 'env-floodgate-token'
        
        config_dict = {
            "credentials": "config-token",
            "project-credentials": "config-floodgate-token"
        }
        
        creds = get_credentials_from_config(config_dict)
        project_creds = get_project_credentials_from_config(config_dict)
        
        assert creds == "config-token"
        assert project_creds == "config-floodgate-token"
    
    def test_empty_config_with_env_vars(self, clean_env):
        """Test empty config falls back to environment variables."""
        os.environ['ANTHROPIC_API_KEY'] = 'env-oauth'
        os.environ['FLOODGATE_PROJECT_TOKEN'] = 'env-floodgate'
        
        config_dict = {
            "credentials": "",  # Empty string
            "project-credentials": ""  # Empty string
        }
        
        creds = get_credentials_from_config(config_dict)
        project_creds = get_project_credentials_from_config(config_dict)
        
        assert creds == "env-oauth"
        assert project_creds == "env-floodgate"
    
    def test_whitespace_config_with_env_vars(self, clean_env):
        """Test whitespace-only config falls back to environment variables."""
        os.environ['ANTHROPIC_API_KEY'] = 'env-oauth'
        os.environ['FLOODGATE_PROJECT_TOKEN'] = 'env-floodgate'
        
        config_dict = {
            "credentials": "   ",  # Whitespace only
            "project-credentials": "   "  # Whitespace only
        }
        
        creds = get_credentials_from_config(config_dict)
        project_creds = get_project_credentials_from_config(config_dict)
        
        assert creds == "env-oauth"
        assert project_creds == "env-floodgate"


class TestLLMProviderConfigEnvVars:
    """Test LLMProviderConfig environment variable loading."""
    
    def test_loads_floodgate_token_from_env(self, clean_env, floodgate_api_url):
        """Test that LLMProviderConfig loads FLOODGATE_PROJECT_TOKEN."""
        os.environ['FLOODGATE_PROJECT_TOKEN'] = 'env-floodgate-token'
        
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.project_credentials == 'env-floodgate-token'
    
    def test_loads_anthropic_api_key_from_env(self, clean_env, floodgate_api_url):
        """Test that LLMProviderConfig loads ANTHROPIC_API_KEY."""
        os.environ['ANTHROPIC_API_KEY'] = 'env-oauth-token'
        
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == 'env-oauth-token'
    
    def test_config_credentials_override_env_vars(self, clean_env, floodgate_api_url):
        """Test that config credentials override environment variables."""
        os.environ['ANTHROPIC_API_KEY'] = 'env-oauth-token'
        os.environ['FLOODGATE_PROJECT_TOKEN'] = 'env-floodgate-token'
        
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "credentials": "config-oauth-token",
            "project-credentials": "config-floodgate-token"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == 'config-oauth-token'
        assert config.project_credentials == 'config-floodgate-token'
    
    def test_floodgate_env_takes_priority_in_get_api_key(self, clean_env, floodgate_api_url):
        """Test that FLOODGATE_PROJECT_TOKEN takes priority in get_api_key."""
        os.environ['ANTHROPIC_API_KEY'] = 'env-oauth-token'
        os.environ['FLOODGATE_PROJECT_TOKEN'] = 'env-floodgate-token'
        
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        # FloodGate token should take priority
        assert config.get_api_key() == 'env-floodgate-token'
        assert config.is_floodgate_mode() == True


class TestCredentialsEnvVarFallbackChain:
    """Test the fallback chain for credentials environment variables."""
    
    def test_anthropic_api_key_first(self, clean_env, floodgate_api_url):
        """Test ANTHROPIC_API_KEY is checked first."""
        os.environ['ANTHROPIC_API_KEY'] = 'anthropic-key'
        os.environ['CREDENTIALS'] = 'credentials-key'
        os.environ['AWS_API_KEY'] = 'aws-key'
        
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == 'anthropic-key'
    
    def test_credentials_env_second(self, clean_env, floodgate_api_url):
        """Test CREDENTIALS is checked second."""
        os.environ['CREDENTIALS'] = 'credentials-key'
        os.environ['AWS_API_KEY'] = 'aws-key'
        
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == 'credentials-key'
    
    def test_aws_api_key_third(self, clean_env, floodgate_api_url):
        """Test AWS_API_KEY is checked third."""
        os.environ['AWS_API_KEY'] = 'aws-key'
        
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == 'aws-key'


class TestClaudeProviderEnvVars:
    """Test environment variable handling for Claude provider."""
    
    def test_claude_loads_anthropic_api_key(self, clean_env):
        """Test Claude provider loads ANTHROPIC_API_KEY."""
        os.environ['ANTHROPIC_API_KEY'] = 'claude-env-key'
        
        config_dict = {
            "llm_provider_type": "claude",
            "api_end_point": "https://api.anthropic.com/v1/messages",
            "model": "claude-3-opus-20240229"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == 'claude-env-key'
    
    def test_claude_loads_claude_api_key(self, clean_env):
        """Test Claude provider loads CLAUDE_API_KEY as fallback."""
        os.environ['CLAUDE_API_KEY'] = 'claude-specific-key'
        
        config_dict = {
            "llm_provider_type": "claude",
            "api_end_point": "https://api.anthropic.com/v1/messages",
            "model": "claude-3-opus-20240229"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == 'claude-specific-key'


class TestNoEnvVarsSet:
    """Test behavior when no environment variables are set."""
    
    def test_no_env_vars_returns_none(self, clean_env):
        """Test that missing env vars result in None credentials."""
        config_dict = {}
        
        creds = get_credentials_from_config(config_dict)
        project_creds = get_project_credentials_from_config(config_dict)
        
        assert creds is None
        assert project_creds is None
    
    def test_llm_config_no_env_vars(self, clean_env, floodgate_api_url):
        """Test LLMProviderConfig with no env vars set."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials is None
        assert config.project_credentials is None
        assert config.get_api_key() is None
