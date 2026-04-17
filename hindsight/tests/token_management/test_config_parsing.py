"""Tests for configuration parsing with credentials fields."""
import pytest
from hindsight.config.llm_providers.llm_provider_config import LLMProviderConfig
from hindsight.utils.config_util import (
    get_credentials_from_config,
    get_project_credentials_from_config,
    is_floodgate_mode,
    get_effective_token
)


class TestLLMProviderConfigParsing:
    """Test LLMProviderConfig parses credentials correctly."""
    
    def test_parse_credentials_field(self, floodgate_api_url):
        """Test parsing credentials from config."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "credentials": "my-oauth-token"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == "my-oauth-token"
    
    def test_parse_project_credentials_field(self, floodgate_api_url):
        """Test parsing project-credentials from config."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "project-credentials": "my-floodgate-token"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.project_credentials == "my-floodgate-token"
    
    def test_parse_both_credentials_fields(self, floodgate_api_url):
        """Test parsing both credentials fields."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "credentials": "my-oauth-token",
            "project-credentials": "my-floodgate-token"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials == "my-oauth-token"
        assert config.project_credentials == "my-floodgate-token"
    
    def test_missing_credentials_defaults_to_none(self, floodgate_api_url, clean_env):
        """Test that missing credentials default to None."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.credentials is None
        assert config.project_credentials is None
    
    def test_get_api_key_returns_project_credentials_first(self, floodgate_api_url):
        """Test that get_api_key returns project_credentials when both are set."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "credentials": "my-oauth-token",
            "project-credentials": "my-floodgate-token"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.get_api_key() == "my-floodgate-token"
    
    def test_get_api_key_returns_credentials_when_no_project_creds(self, floodgate_api_url):
        """Test that get_api_key returns credentials when project_credentials is not set."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "credentials": "my-oauth-token"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.get_api_key() == "my-oauth-token"
    
    def test_is_floodgate_mode_true(self, floodgate_api_url):
        """Test is_floodgate_mode returns True when project_credentials is set."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "project-credentials": "my-floodgate-token"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.is_floodgate_mode() == True
    
    def test_is_floodgate_mode_false(self, floodgate_api_url):
        """Test is_floodgate_mode returns False when project_credentials is not set."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "credentials": "my-oauth-token"
        }
        
        config = LLMProviderConfig(**config_dict)
        
        assert config.is_floodgate_mode() == False


class TestLegacyCredentialsFormat:
    """Test backward compatibility with legacy nested credentials format."""
    
    def test_legacy_nested_credentials_dict(self, floodgate_api_url):
        """Test parsing legacy nested credentials dictionary."""
        config_dict = {
            "llm_provider_type": "aws_bedrock",
            "api_end_point": floodgate_api_url,
            "model": "test-model",
            "credentials": {
                "api_key": "legacy-api-key",
                "region_name": "us-east-1"
            }
        }
        
        config = LLMProviderConfig(**config_dict)
        
        # Should extract api_key to flat credentials field
        assert config.credentials == "legacy-api-key"
        # Legacy credentials object should be preserved
        assert config.legacy_credentials is not None
        assert config.legacy_credentials.api_key == "legacy-api-key"


class TestConfigUtilHelpers:
    """Test config utility helper functions."""
    
    def test_get_credentials_from_config(self, clean_env):
        """Test getting credentials from config dict."""
        config_dict = {"credentials": "my-token"}
        
        result = get_credentials_from_config(config_dict)
        
        assert result == "my-token"
    
    def test_get_credentials_strips_whitespace(self, clean_env):
        """Test that credentials are stripped of whitespace."""
        config_dict = {"credentials": "  my-token  "}
        
        result = get_credentials_from_config(config_dict)
        
        assert result == "my-token"
    
    def test_get_credentials_returns_none_for_empty(self, clean_env):
        """Test that empty credentials return None."""
        config_dict = {"credentials": ""}
        
        result = get_credentials_from_config(config_dict)
        
        assert result is None
    
    def test_get_credentials_returns_none_for_whitespace(self, clean_env):
        """Test that whitespace-only credentials return None."""
        config_dict = {"credentials": "   "}
        
        result = get_credentials_from_config(config_dict)
        
        assert result is None
    
    def test_get_project_credentials_from_config(self, clean_env):
        """Test getting project-credentials from config dict."""
        config_dict = {"project-credentials": "my-floodgate-token"}
        
        result = get_project_credentials_from_config(config_dict)
        
        assert result == "my-floodgate-token"
    
    def test_get_project_credentials_strips_whitespace(self, clean_env):
        """Test that project-credentials are stripped of whitespace."""
        config_dict = {"project-credentials": "  my-token  "}
        
        result = get_project_credentials_from_config(config_dict)
        
        assert result == "my-token"
    
    def test_get_project_credentials_supports_underscore(self, clean_env):
        """Test that project_credentials (underscore) is also supported."""
        config_dict = {"project_credentials": "my-floodgate-token"}
        
        result = get_project_credentials_from_config(config_dict)
        
        assert result == "my-floodgate-token"
    
    def test_is_floodgate_mode_helper(self, clean_env):
        """Test is_floodgate_mode helper function."""
        config_with_floodgate = {"project-credentials": "my-token"}
        config_without_floodgate = {"credentials": "my-token"}
        
        assert is_floodgate_mode(config_with_floodgate) == True
        assert is_floodgate_mode(config_without_floodgate) == False
    
    def test_get_effective_token_floodgate(self, clean_env):
        """Test get_effective_token returns floodgate token when available."""
        config_dict = {
            "credentials": "oauth-token",
            "project-credentials": "floodgate-token"
        }
        
        token, token_type = get_effective_token(config_dict)
        
        assert token == "floodgate-token"
        assert token_type == "floodgate"
    
    def test_get_effective_token_oidc(self, clean_env):
        """Test get_effective_token returns oidc token when no floodgate."""
        config_dict = {"credentials": "oauth-token"}
        
        token, token_type = get_effective_token(config_dict)
        
        assert token == "oauth-token"
        assert token_type == "oidc"
    
    def test_get_effective_token_auto_refresh(self, clean_env):
        """Test get_effective_token returns auto_refresh when no tokens."""
        config_dict = {}
        
        token, token_type = get_effective_token(config_dict)
        
        assert token is None
        assert token_type == "auto_refresh"


