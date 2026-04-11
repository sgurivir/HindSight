"""Tests for token selection logic in AWS Bedrock provider."""
import pytest
from unittest.mock import patch, Mock
from hindsight.core.llm.providers.aws_bedrock_provider import AWSBedrockProvider
from hindsight.core.llm.providers.base_provider import LLMConfig


class TestTokenSelectionPriority:
    """Test token selection priority: project_credentials > credentials > AppleConnect."""
    
    def test_project_credentials_takes_priority_over_credentials(self, floodgate_api_url):
        """When both are provided, project_credentials should be used."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="oauth-token",
            project_credentials="floodgate-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider.headers["Authorization"] == "Bearer floodgate-token"
        assert provider.headers["X-Floodgate-Project-Token"] == "floodgate-token"
        assert "X-Apple-OIDC-Token" not in provider.headers
        assert provider._use_apple_connect_auto_refresh == False
    
    def test_credentials_used_when_no_project_credentials(self, floodgate_api_url):
        """When only credentials provided, use OIDC mode."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="oauth-token",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider.headers["Authorization"] == "Bearer oauth-token"
        assert provider.headers["X-Apple-OIDC-Token"] == "oauth-token"
        assert "X-Floodgate-Project-Token" not in provider.headers
        assert provider._use_apple_connect_auto_refresh == False
    
    def test_apple_connect_used_when_no_credentials(self, floodgate_api_url):
        """When neither provided, use AppleConnect auto-refresh."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider._use_apple_connect_auto_refresh == True
        assert "Authorization" not in provider.headers
        assert "X-Apple-OIDC-Token" not in provider.headers
        assert "X-Floodgate-Project-Token" not in provider.headers
    
    def test_legacy_api_key_used_when_no_other_credentials(self, floodgate_api_url):
        """When only legacy api_key provided, use it as OIDC token with auto-refresh.
        
        AppleConnect tokens expire in ~30 minutes, so auto-refresh must be enabled
        even when a token is passed via api_key (since it was likely obtained via
        AppleConnect and will need to be refreshed).
        """
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            api_key="legacy-api-key",
            credentials="",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider.headers["Authorization"] == "Bearer legacy-api-key"
        assert provider.headers["X-Apple-OIDC-Token"] == "legacy-api-key"
        assert "X-Floodgate-Project-Token" not in provider.headers
        # Auto-refresh should be enabled for api_key since AppleConnect tokens expire
        assert provider._use_apple_connect_auto_refresh == True


class TestFloodGateTokenHeaders:
    """Test correct headers are set when using FloodGate project token."""
    
    def test_floodgate_token_sets_correct_headers(self, floodgate_api_url):
        """FloodGate token should set Authorization and X-Floodgate-Project-Token."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            project_credentials="my-floodgate-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert "Authorization" in provider.headers
        assert "X-Floodgate-Project-Token" in provider.headers
        assert provider.headers["Authorization"] == "Bearer my-floodgate-token"
        assert provider.headers["X-Floodgate-Project-Token"] == "my-floodgate-token"
    
    def test_floodgate_token_excludes_oidc_header(self, floodgate_api_url):
        """FloodGate mode should NOT include X-Apple-OIDC-Token."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            project_credentials="my-floodgate-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert "X-Apple-OIDC-Token" not in provider.headers


class TestOIDCTokenHeaders:
    """Test correct headers are set when using OIDC credentials."""
    
    def test_oidc_token_sets_correct_headers(self, floodgate_api_url):
        """OIDC token should set Authorization and X-Apple-OIDC-Token."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="my-oidc-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert "Authorization" in provider.headers
        assert "X-Apple-OIDC-Token" in provider.headers
        assert provider.headers["Authorization"] == "Bearer my-oidc-token"
        assert provider.headers["X-Apple-OIDC-Token"] == "my-oidc-token"
    
    def test_oidc_token_excludes_floodgate_header(self, floodgate_api_url):
        """OIDC mode should NOT include X-Floodgate-Project-Token."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="my-oidc-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert "X-Floodgate-Project-Token" not in provider.headers


class TestWhitespaceHandling:
    """Test that whitespace-only values are treated as empty."""
    
    def test_whitespace_project_credentials_treated_as_empty(self, floodgate_api_url):
        """Whitespace-only project_credentials should fall through to next priority."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="valid-token",
            project_credentials="   "  # Whitespace only
        )
        
        provider = AWSBedrockProvider(config)
        
        # Should use credentials instead
        assert provider.headers["Authorization"] == "Bearer valid-token"
        assert provider.headers["X-Apple-OIDC-Token"] == "valid-token"
        assert "X-Floodgate-Project-Token" not in provider.headers
    
    def test_whitespace_credentials_treated_as_empty(self, floodgate_api_url):
        """Whitespace-only credentials should fall through to AppleConnect."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="   ",  # Whitespace only
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider._use_apple_connect_auto_refresh == True
    
    def test_both_whitespace_falls_back_to_apple_connect(self, floodgate_api_url):
        """Both whitespace-only should fall back to AppleConnect."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="   ",
            project_credentials="   "
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider._use_apple_connect_auto_refresh == True


class TestNonAppleEndpoints:
    """Test behavior with non-Apple endpoints."""
    
    def test_standard_bedrock_uses_simple_auth(self, standard_bedrock_url):
        """Standard AWS Bedrock should use simple Authorization header."""
        config = LLMConfig(
            api_url=standard_bedrock_url,
            model="test-model",
            credentials="aws-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider.headers["Authorization"] == "Bearer aws-token"
        assert "X-Apple-OIDC-Token" not in provider.headers
        assert "X-Floodgate-Project-Token" not in provider.headers
        assert provider._use_apple_connect_auto_refresh == False
    
    def test_standard_bedrock_with_api_key(self, standard_bedrock_url):
        """Standard AWS Bedrock should work with legacy api_key."""
        config = LLMConfig(
            api_url=standard_bedrock_url,
            model="test-model",
            api_key="aws-api-key"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider.headers["Authorization"] == "Bearer aws-api-key"
        assert "X-Apple-OIDC-Token" not in provider.headers
        assert "X-Floodgate-Project-Token" not in provider.headers


class TestGenAIEndpoint:
    """Test behavior with GenAI endpoint (alternative Apple endpoint)."""
    
    def test_genai_endpoint_uses_floodgate_token(self, genai_api_url):
        """GenAI endpoint should support FloodGate token."""
        config = LLMConfig(
            api_url=genai_api_url,
            model="test-model",
            project_credentials="genai-floodgate-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider.headers["Authorization"] == "Bearer genai-floodgate-token"
        assert provider.headers["X-Floodgate-Project-Token"] == "genai-floodgate-token"
        assert "X-Apple-OIDC-Token" not in provider.headers
    
    def test_genai_endpoint_uses_oidc_token(self, genai_api_url):
        """GenAI endpoint should support OIDC token."""
        config = LLMConfig(
            api_url=genai_api_url,
            model="test-model",
            credentials="genai-oidc-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider.headers["Authorization"] == "Bearer genai-oidc-token"
        assert provider.headers["X-Apple-OIDC-Token"] == "genai-oidc-token"
        assert "X-Floodgate-Project-Token" not in provider.headers
