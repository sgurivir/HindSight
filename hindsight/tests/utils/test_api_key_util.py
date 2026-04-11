#!/usr/bin/env python3
"""
Tests for hindsight/utils/api_key_util.py

Tests the API key utility module which provides:
- API key retrieval with fallback to Apple Connect token
- Apple Connect OAuth token retrieval
"""

import pytest
from unittest.mock import patch, MagicMock
import subprocess

from hindsight.utils.api_key_util import (
    get_api_key,
    get_apple_connect_token,
)


class TestGetApiKey:
    """Tests for get_api_key function."""

    def test_get_api_key_from_config(self):
        """Test getting API key from config."""
        result = get_api_key("my-api-key-123")
        
        assert result == "my-api-key-123"

    def test_get_api_key_empty_config(self):
        """Test with empty config API key falls back to Apple Connect."""
        with patch('hindsight.utils.api_key_util.get_apple_connect_token') as mock_token:
            mock_token.return_value = None
            
            result = get_api_key("")
            
            # Empty string is falsy, so should try Apple Connect
            mock_token.assert_called_once()

    def test_get_api_key_none_config(self):
        """Test with None config API key falls back to Apple Connect."""
        with patch('hindsight.utils.api_key_util.get_apple_connect_token') as mock_token:
            mock_token.return_value = "apple-token-123"
            
            result = get_api_key(None)
            
            assert result == "apple-token-123"
            mock_token.assert_called_once()

    def test_get_api_key_fallback_to_apple_connect(self):
        """Test fallback to Apple Connect token when no config key."""
        with patch('hindsight.utils.api_key_util.get_apple_connect_token') as mock_token:
            mock_token.return_value = "apple-connect-token"
            
            result = get_api_key(None)
            
            assert result == "apple-connect-token"

    def test_get_api_key_no_fallback_available(self):
        """Test when no API key and Apple Connect fails."""
        with patch('hindsight.utils.api_key_util.get_apple_connect_token') as mock_token:
            mock_token.return_value = None
            
            result = get_api_key(None)
            
            assert result is None


class TestGetAppleConnectToken:
    """Tests for get_apple_connect_token function."""

    @patch('subprocess.run')
    def test_get_apple_connect_token_success(self, mock_run):
        """Test successful Apple Connect token retrieval."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "some output token-value-123"
        mock_run.return_value = mock_result
        
        result = get_apple_connect_token()
        
        assert result == "token-value-123"
        mock_run.assert_called_once()

    @patch('subprocess.run')
    def test_get_apple_connect_token_failure(self, mock_run):
        """Test Apple Connect token retrieval failure."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: authentication failed"
        mock_run.return_value = mock_result
        
        result = get_apple_connect_token()
        
        assert result is None

    @patch('subprocess.run')
    def test_get_apple_connect_token_timeout(self, mock_run):
        """Test Apple Connect token retrieval timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="appleconnect", timeout=30)
        
        result = get_apple_connect_token()
        
        assert result is None

    @patch('subprocess.run')
    def test_get_apple_connect_token_not_found(self, mock_run):
        """Test when Apple Connect tool is not found."""
        mock_run.side_effect = FileNotFoundError("appleconnect not found")
        
        result = get_apple_connect_token()
        
        assert result is None

    @patch('subprocess.run')
    def test_get_apple_connect_token_empty_output(self, mock_run):
        """Test Apple Connect with empty output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run.return_value = mock_result
        
        result = get_apple_connect_token()
        
        assert result is None

    @patch('subprocess.run')
    def test_get_apple_connect_token_exception(self, mock_run):
        """Test Apple Connect with general exception."""
        mock_run.side_effect = Exception("Unexpected error")
        
        result = get_apple_connect_token()
        
        assert result is None

    @patch('subprocess.run')
    def test_get_apple_connect_token_command_format(self, mock_run):
        """Test that the correct command is called."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "token-123"
        mock_run.return_value = mock_result
        
        get_apple_connect_token()
        
        # Verify the command includes expected arguments
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        
        assert '/usr/local/bin/appleconnect' in cmd
        assert 'getToken' in cmd
        assert '--token-type=oauth' in cmd
