#!/usr/bin/env python3
"""
Tests for hindsight.utils.config_util module.
"""

import os
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.utils.config_util import (
    get_supported_llm_provider_types,
    is_valid_llm_provider_type,
    validate_llm_provider_type,
    ConfigValidationError,
    validate_config_structure,
    validate_config_values,
    validate_config_file,
    load_and_validate_config,
    load_config_tolerant,
    get_config_value,
    is_config_key_required,
    get_config_schema,
    get_llm_provider_type,
    get_credentials,
    get_llm_config_values,
    CONFIG_SCHEMA,
    SUPPORTED_LLM_PROVIDER_TYPES
)


class TestSupportedLLMProviderTypes:
    """Tests for LLM provider type functions."""

    def test_get_supported_llm_provider_types(self):
        """Test getting supported LLM provider types."""
        providers = get_supported_llm_provider_types()
        
        assert isinstance(providers, list)
        assert 'claude' in providers
        assert 'aws_bedrock' in providers
        assert 'dummy' in providers

    def test_get_supported_llm_provider_types_returns_copy(self):
        """Test that get_supported_llm_provider_types returns a copy."""
        providers1 = get_supported_llm_provider_types()
        providers2 = get_supported_llm_provider_types()
        
        # Modify one list
        providers1.append('test')
        
        # Other list should not be affected
        assert 'test' not in providers2

    def test_is_valid_llm_provider_type_valid(self):
        """Test is_valid_llm_provider_type with valid types."""
        assert is_valid_llm_provider_type('claude') is True
        assert is_valid_llm_provider_type('aws_bedrock') is True
        assert is_valid_llm_provider_type('dummy') is True

    def test_is_valid_llm_provider_type_invalid(self):
        """Test is_valid_llm_provider_type with invalid types."""
        assert is_valid_llm_provider_type('invalid') is False
        assert is_valid_llm_provider_type('openai') is False
        assert is_valid_llm_provider_type('') is False

    def test_validate_llm_provider_type_valid(self):
        """Test validate_llm_provider_type with valid type."""
        # Should not raise
        validate_llm_provider_type('claude')
        validate_llm_provider_type('aws_bedrock')
        validate_llm_provider_type('dummy')

    def test_validate_llm_provider_type_invalid(self):
        """Test validate_llm_provider_type with invalid type raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_llm_provider_type('invalid_provider')
        
        assert 'Unsupported provider type' in str(exc_info.value)
        assert 'invalid_provider' in str(exc_info.value)


class TestValidateConfigStructure:
    """Tests for validate_config_structure function."""

    def test_validate_config_structure_valid(self, valid_config_dict):
        """Test validation with valid config structure."""
        is_valid, errors = validate_config_structure(valid_config_dict)
        
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_config_structure_missing_required(self):
        """Test validation with missing required keys."""
        config = {'description': 'Test'}  # Missing project_name and api_end_point
        
        is_valid, errors = validate_config_structure(config)
        
        assert is_valid is False
        assert any('project_name' in error for error in errors)
        assert any('api_end_point' in error for error in errors)

    def test_validate_config_structure_empty_required(self):
        """Test validation with empty required values."""
        config = {
            'project_name': '',
            'api_end_point': 'https://api.example.com'
        }
        
        is_valid, errors = validate_config_structure(config)
        
        assert is_valid is False
        assert any('project_name' in error and 'empty' in error for error in errors)

    def test_validate_config_structure_wrong_type(self):
        """Test validation with wrong types."""
        config = {
            'project_name': 123,  # Should be string
            'api_end_point': 'https://api.example.com'
        }
        
        is_valid, errors = validate_config_structure(config)
        
        assert is_valid is False
        assert any('project_name' in error and 'type' in error for error in errors)

    def test_validate_config_structure_not_dict(self):
        """Test validation with non-dict input."""
        is_valid, errors = validate_config_structure("not a dict")
        
        assert is_valid is False
        assert any('dictionary' in error for error in errors)

    def test_validate_config_structure_optional_fields(self):
        """Test validation with optional fields."""
        config = {
            'project_name': 'test',
            'api_end_point': 'https://api.example.com',
            'exclude_directories': ['node_modules', '.git'],
            'user_prompts': ['Find bugs']
        }
        
        is_valid, errors = validate_config_structure(config)
        
        assert is_valid is True
        assert len(errors) == 0


class TestValidateConfigValues:
    """Tests for validate_config_values function."""

    def test_validate_config_values_valid_api_endpoint(self):
        """Test validation with valid API endpoint."""
        config = {'api_end_point': 'https://api.anthropic.com/v1/messages'}
        
        is_valid, errors = validate_config_values(config)
        
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_config_values_invalid_api_endpoint(self):
        """Test validation with invalid API endpoint (no http/https)."""
        config = {'api_end_point': 'api.anthropic.com/v1/messages'}
        
        is_valid, errors = validate_config_values(config)
        
        assert is_valid is False
        assert any('api_end_point' in error and 'http' in error for error in errors)

    def test_validate_config_values_nonexistent_repo_path(self, temp_dir):
        """Test validation with non-existent repo path."""
        config = {'path_to_repo': '/nonexistent/path/to/repo'}
        
        is_valid, errors = validate_config_values(config)
        
        assert is_valid is False
        assert any('path_to_repo' in error and 'not exist' in error for error in errors)

    def test_validate_config_values_valid_repo_path(self, temp_dir):
        """Test validation with valid repo path."""
        config = {'path_to_repo': temp_dir}
        
        is_valid, errors = validate_config_values(config)
        
        assert is_valid is True

    def test_validate_config_values_invalid_llm_provider(self):
        """Test validation with invalid LLM provider type."""
        config = {'llm_provider_type': 'invalid_provider'}
        
        is_valid, errors = validate_config_values(config)
        
        assert is_valid is False
        assert any('llm_provider_type' in error for error in errors)

    def test_validate_config_values_valid_llm_provider(self):
        """Test validation with valid LLM provider type."""
        config = {'llm_provider_type': 'claude'}
        
        is_valid, errors = validate_config_values(config)
        
        assert is_valid is True


class TestValidateConfigFile:
    """Tests for validate_config_file function."""

    def test_validate_config_file_valid(self, temp_config_file):
        """Test validation of valid config file."""
        is_valid, config, errors = validate_config_file(temp_config_file)
        
        assert is_valid is True
        assert config is not None
        assert len(errors) == 0

    def test_validate_config_file_nonexistent(self):
        """Test validation of non-existent config file."""
        is_valid, config, errors = validate_config_file('/nonexistent/config.json')
        
        assert is_valid is False
        assert config is None
        assert any('not found' in error for error in errors)

    def test_validate_config_file_invalid_json(self, temp_dir):
        """Test validation of file with invalid JSON."""
        config_path = os.path.join(temp_dir, 'invalid.json')
        with open(config_path, 'w') as f:
            f.write('{ invalid json }')
        
        is_valid, config, errors = validate_config_file(config_path)
        
        assert is_valid is False
        assert config is None
        assert any('parse' in error.lower() or 'json' in error.lower() for error in errors)


class TestLoadAndValidateConfig:
    """Tests for load_and_validate_config function."""

    def test_load_and_validate_config_from_file(self, temp_config_file):
        """Test loading and validating config from file."""
        config = load_and_validate_config(temp_config_file)
        
        assert config is not None
        assert 'project_name' in config
        assert 'api_end_point' in config

    def test_load_and_validate_config_from_dict(self, valid_config_dict):
        """Test loading and validating config from dictionary."""
        config = load_and_validate_config(valid_config_dict)
        
        assert config is not None
        assert config == valid_config_dict

    def test_load_and_validate_config_invalid_file(self):
        """Test loading invalid config file raises ConfigValidationError."""
        with pytest.raises(ConfigValidationError):
            load_and_validate_config('/nonexistent/config.json')

    def test_load_and_validate_config_invalid_dict(self):
        """Test loading invalid config dict raises ConfigValidationError."""
        invalid_config = {'description': 'Missing required fields'}
        
        with pytest.raises(ConfigValidationError):
            load_and_validate_config(invalid_config)

    def test_load_and_validate_config_invalid_type(self):
        """Test loading config with invalid type raises ConfigValidationError."""
        with pytest.raises(ConfigValidationError):
            load_and_validate_config(12345)


class TestLoadConfigTolerant:
    """Tests for load_config_tolerant function."""

    def test_load_config_tolerant_valid(self, temp_config_file):
        """Test tolerant loading of valid config."""
        config = load_config_tolerant(temp_config_file)
        
        assert config is not None
        assert isinstance(config, dict)

    def test_load_config_tolerant_missing_keys(self, temp_dir):
        """Test tolerant loading allows missing keys."""
        config_path = os.path.join(temp_dir, 'partial.json')
        with open(config_path, 'w') as f:
            json.dump({'description': 'Partial config'}, f)
        
        # Should not raise even with missing required keys
        config = load_config_tolerant(config_path)
        
        assert config is not None
        assert config.get('description') == 'Partial config'

    def test_load_config_tolerant_nonexistent_file(self):
        """Test tolerant loading of non-existent file raises error."""
        with pytest.raises(ConfigValidationError):
            load_config_tolerant('/nonexistent/config.json')


class TestConfigHelperFunctions:
    """Tests for config helper functions."""

    def test_get_config_value_existing(self):
        """Test getting existing config value."""
        config = {'key': 'value', 'number': 42}
        
        assert get_config_value(config, 'key') == 'value'
        assert get_config_value(config, 'number') == 42

    def test_get_config_value_missing_with_default(self):
        """Test getting missing config value with default."""
        config = {'key': 'value'}
        
        assert get_config_value(config, 'missing', 'default') == 'default'
        assert get_config_value(config, 'missing') is None

    def test_is_config_key_required(self):
        """Test checking if config key is required."""
        assert is_config_key_required('project_name') is True
        assert is_config_key_required('api_end_point') is True
        assert is_config_key_required('description') is False
        assert is_config_key_required('nonexistent_key') is False

    def test_get_config_schema(self):
        """Test getting config schema."""
        schema = get_config_schema()
        
        assert isinstance(schema, dict)
        assert 'project_name' in schema
        assert 'api_end_point' in schema
        
        # Verify it's a copy
        schema['new_key'] = {'type': str, 'required': False}
        assert 'new_key' not in CONFIG_SCHEMA


class TestLLMConfigFunctions:
    """Tests for LLM-specific config functions."""

    def test_get_llm_provider_type_default(self):
        """Test getting LLM provider type with default."""
        config = {}
        
        assert get_llm_provider_type(config) == 'claude'

    def test_get_llm_provider_type_specified(self):
        """Test getting specified LLM provider type."""
        config = {'llm_provider_type': 'aws_bedrock'}
        
        assert get_llm_provider_type(config) == 'aws_bedrock'

    def test_get_credentials_present(self):
        """Test getting credentials when present."""
        config = {'credentials': 'my-api-key'}
        
        assert get_credentials(config) == 'my-api-key'

    def test_get_credentials_missing(self):
        """Test getting credentials when missing."""
        config = {}
        
        assert get_credentials(config) is None

    def test_get_llm_config_values(self):
        """Test getting all LLM config values."""
        config = {
            'llm_provider_type': 'dummy',
            'credentials': 'test-key'
        }
        
        with patch('hindsight.utils.config_util.get_api_key_from_config') as mock_get_api_key:
            mock_get_api_key.return_value = 'dummy-key'
            
            result = get_llm_config_values(config)
            
            assert result['llm_provider_type'] == 'dummy'
            assert result['credentials'] == 'test-key'
            assert 'api_key' in result


class TestConfigValidationError:
    """Tests for ConfigValidationError exception."""

    def test_config_validation_error_message(self):
        """Test ConfigValidationError with message."""
        error = ConfigValidationError("Test error message")
        
        assert str(error) == "Test error message"

    def test_config_validation_error_inheritance(self):
        """Test ConfigValidationError inherits from Exception."""
        error = ConfigValidationError("Test")
        
        assert isinstance(error, Exception)
