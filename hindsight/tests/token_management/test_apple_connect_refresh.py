"""Tests for AppleConnect auto-refresh functionality."""
import pytest
from unittest.mock import Mock, patch, MagicMock
from hindsight.core.llm.providers.aws_bedrock_provider import AWSBedrockProvider
from hindsight.core.llm.providers.base_provider import LLMConfig


class TestAppleConnectAutoRefresh:
    """Test AppleConnect token auto-refresh behavior."""
    
    def test_auto_refresh_enabled_when_no_credentials(self, floodgate_api_url):
        """Test that auto-refresh is enabled when no credentials provided."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider._use_apple_connect_auto_refresh == True
    
    def test_auto_refresh_disabled_with_credentials(self, floodgate_api_url):
        """Test that auto-refresh is disabled when credentials provided."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="my-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider._use_apple_connect_auto_refresh == False
    
    def test_auto_refresh_disabled_with_project_credentials(self, floodgate_api_url):
        """Test that auto-refresh is disabled when project_credentials provided."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            project_credentials="my-floodgate-token"
        )
        
        provider = AWSBedrockProvider(config)
        
        assert provider._use_apple_connect_auto_refresh == False
    
    def test_auto_refresh_enabled_with_api_key(self, floodgate_api_url):
        """Test that auto-refresh is enabled when legacy api_key provided.
        
        AppleConnect tokens expire in ~30 minutes, so auto-refresh must be enabled
        even when a token is passed via api_key (since it was likely obtained via
        AppleConnect and will need to be refreshed).
        """
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            api_key="my-api-key"
        )
        
        provider = AWSBedrockProvider(config)
        
        # Auto-refresh should be enabled for api_key since AppleConnect tokens expire
        assert provider._use_apple_connect_auto_refresh == True


class TestRefreshHeadersMethod:
    """Test the _refresh_headers_if_needed method."""
    
    def test_refresh_headers_updates_authorization(self, floodgate_api_url, mock_apple_connect):
        """Test that _refresh_headers_if_needed updates Authorization header."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        provider._refresh_headers_if_needed()
        
        assert provider.headers["Authorization"] == "Bearer mock-apple-connect-token"
        assert provider.headers["X-Apple-OIDC-Token"] == "mock-apple-connect-token"
    
    def test_refresh_headers_not_called_with_static_credentials(self, floodgate_api_url):
        """Test that refresh doesn't change headers when using static credentials."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="static-token"
        )
        
        provider = AWSBedrockProvider(config)
        original_auth = provider.headers["Authorization"]
        
        # This should do nothing since auto-refresh is disabled
        provider._refresh_headers_if_needed()
        
        assert provider.headers["Authorization"] == original_auth
        assert provider.headers["Authorization"] == "Bearer static-token"
    
    def test_refresh_headers_not_called_with_floodgate_token(self, floodgate_api_url):
        """Test that refresh doesn't change headers when using FloodGate token."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            project_credentials="floodgate-token"
        )
        
        provider = AWSBedrockProvider(config)
        original_auth = provider.headers["Authorization"]
        
        # This should do nothing since auto-refresh is disabled
        provider._refresh_headers_if_needed()
        
        assert provider.headers["Authorization"] == original_auth
        assert provider.headers["Authorization"] == "Bearer floodgate-token"


class TestMakeRequestWithRefresh:
    """Test that make_request calls refresh before each request."""
    
    def test_make_request_calls_refresh(self, floodgate_api_url, mock_apple_connect):
        """Test that make_request calls _refresh_headers_if_needed."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        
        with patch.object(provider, '_refresh_headers_if_needed') as mock_refresh:
            with patch.object(provider.session, 'post') as mock_post:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"choices": [{"message": {"content": "test"}}]}
                mock_post.return_value = mock_response
                
                provider.make_request({"messages": []})
                
                mock_refresh.assert_called_once()
    
    def test_make_request_uses_refreshed_token(self, floodgate_api_url, mock_apple_connect):
        """Test that make_request uses the refreshed token in headers."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        
        with patch.object(provider.session, 'post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"choices": [{"message": {"content": "test"}}]}
            mock_post.return_value = mock_response
            
            provider.make_request({"messages": []})
            
            # Verify the headers used in the request contain the refreshed token
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs['headers']['Authorization'] == 'Bearer mock-apple-connect-token'
            assert call_kwargs['headers']['X-Apple-OIDC-Token'] == 'mock-apple-connect-token'


class TestAppleConnectTokenManager:
    """Test interaction with AppleConnect token manager."""
    
    def test_token_manager_called_on_refresh(self, floodgate_api_url, mock_apple_connect):
        """Test that token manager is called during refresh."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        provider._refresh_headers_if_needed()
        
        mock_apple_connect.assert_called_once()
        mock_apple_connect.return_value.get_token.assert_called_once()
    
    def test_handles_token_manager_failure(self, floodgate_api_url):
        """Test graceful handling when token manager fails."""
        with patch('hindsight.utils.api_key_util.get_token_manager') as mock:
            token_manager = Mock()
            token_manager.get_token.return_value = None  # Simulate failure
            mock.return_value = token_manager
            
            config = LLMConfig(
                api_url=floodgate_api_url,
                model="test-model",
                credentials="",
                project_credentials=""
            )
            
            provider = AWSBedrockProvider(config)
            
            # Should not raise, just log warning
            provider._refresh_headers_if_needed()
            
            # Headers should not have Authorization set (or should be unchanged)
            # Since we started with no credentials, Authorization shouldn't be set
            assert "Authorization" not in provider.headers or provider.headers.get("Authorization") == ""
    
    def test_handles_import_error(self, floodgate_api_url):
        """Test graceful handling when token manager import fails."""
        config = LLMConfig(
            api_url=floodgate_api_url,
            model="test-model",
            credentials="",
            project_credentials=""
        )
        
        provider = AWSBedrockProvider(config)
        
        with patch.dict('sys.modules', {'hindsight.utils.api_key_util': None}):
            # This should not raise an exception
            try:
                provider._refresh_headers_if_needed()
            except ImportError:
                pytest.fail("_refresh_headers_if_needed should handle ImportError gracefully")


class TestNonAppleEndpointRefresh:
    """Test that non-Apple endpoints don't use auto-refresh."""
    
    def test_standard_bedrock_no_auto_refresh(self, standard_bedrock_url):
        """Test that standard AWS Bedrock doesn't use auto-refresh."""
        config = LLMConfig(
            api_url=standard_bedrock_url,
            model="test-model",
            credentials=""  # No credentials
        )
        
        provider = AWSBedrockProvider(config)
        
        # Even without credentials, non-Apple endpoints shouldn't use auto-refresh
        assert provider._use_apple_connect_auto_refresh == False
    
    def test_standard_bedrock_refresh_does_nothing(self, standard_bedrock_url, mock_apple_connect):
        """Test that refresh does nothing for standard AWS Bedrock."""
        config = LLMConfig(
            api_url=standard_bedrock_url,
            model="test-model",
            credentials="aws-token"
        )
        
        provider = AWSBedrockProvider(config)
        original_headers = provider.headers.copy()
        
        provider._refresh_headers_if_needed()
        
        # Headers should be unchanged
        assert provider.headers == original_headers
        # Token manager should not be called
        mock_apple_connect.assert_not_called()
