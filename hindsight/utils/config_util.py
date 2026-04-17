#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Configuration Utility Module
Provides validation for config.json to ensure all required keys and values are present
"""

import os
import sys
from typing import Dict, Any, List, Union, Optional, Tuple
from .file_util import read_json_file
from .log_util import get_logger

logger = get_logger(__name__)

# Define the required configuration schema
CONFIG_SCHEMA = {
    "project_name": {"type": str, "required": True},
    "description": {"type": str, "required": False},
    "api_end_point": {"type": str, "required": True},
    "model": {"type": str, "required": False},  # Optional: defaults to DEFAULT_LLM_MODEL based on provider
    "llm_provider_type": {"type": str, "required": False},  # Optional: aws_bedrock
    "credentials": {"type": str, "required": False},
    "path_to_repo": {"type": str, "required": False},
    "user_prompts": {"type": list, "required": False},  # Optional value
    "exclude_directories": {"type": list, "required": False},  # Optional value
    "include_directories": {"type": list, "required": False},  # Optional value
    "exclude_files": {"type": list, "required": False},  # Optional value
    "min_function_body_length": {"type": int, "required": False}  # Optional value
}

# Centralized list of supported LLM provider types
SUPPORTED_LLM_PROVIDER_TYPES = ["aws_bedrock"]


def get_supported_llm_provider_types() -> List[str]:
    """
    Get the list of supported LLM provider types.

    Returns:
        List[str]: List of supported provider types
    """
    return SUPPORTED_LLM_PROVIDER_TYPES.copy()


def is_valid_llm_provider_type(provider_type: str) -> bool:
    """
    Check if a provider type is valid.

    Args:
        provider_type (str): Provider type to validate

    Returns:
        bool: True if provider type is supported
    """
    return provider_type in SUPPORTED_LLM_PROVIDER_TYPES


def validate_llm_provider_type(provider_type: str) -> None:
    """
    Validate an LLM provider type, raising an exception if invalid.

    Args:
        provider_type (str): Provider type to validate

    Raises:
        ValueError: If provider type is not supported
    """
    if not is_valid_llm_provider_type(provider_type):
        raise ValueError(f"Unsupported provider type: {provider_type}. Supported types: {', '.join(SUPPORTED_LLM_PROVIDER_TYPES)}")


class ConfigValidationError(Exception):
    """Custom exception for configuration validation errors."""
    pass


def validate_config_structure(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate the structure of the configuration dictionary.

    Args:
        config (Dict[str, Any]): Configuration dictionary to validate

    Returns:
        Tuple[bool, List[str]]: (is_valid, list_of_errors)
    """
    errors = []

    if not isinstance(config, dict):
        errors.append("Configuration must be a dictionary")
        return False, errors

    # Check for missing required keys
    for key, schema in CONFIG_SCHEMA.items():
        if key not in config:
            if schema["required"]:
                errors.append(f"Missing required key: '{key}'")
            continue

        # Check if value is required and present
        value = config[key]
        if schema["required"] and (value is None or value == ""):
            errors.append(f"Required key '{key}' cannot be empty or null")
            continue

        # Check type validation for non-empty values
        if value is not None and value != "":
            expected_type = schema["type"]
            if not isinstance(value, expected_type):
                errors.append(f"Key '{key}' must be of type {expected_type.__name__}, got {type(value).__name__}")

    # Check for unexpected keys (warn but don't fail)
    for key in config:
        if key not in CONFIG_SCHEMA:
            logger.warning(f"Unexpected configuration key found: '{key}'")

    return len(errors) == 0, errors


def validate_config_values(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate the values in the configuration dictionary.

    Args:
        config (Dict[str, Any]): Configuration dictionary to validate

    Returns:
        Tuple[bool, List[str]]: (is_valid, list_of_errors)
    """
    errors = []

    # Validate api_end_point format
    if "api_end_point" in config and config["api_end_point"]:
        api_url = config["api_end_point"]
        if not (api_url.startswith("http://") or api_url.startswith("https://")):
            errors.append("api_end_point must start with 'http://' or 'https://'")

    # Validate path_to_repo exists (if provided)
    if "path_to_repo" in config and config["path_to_repo"]:
        repo_path = config["path_to_repo"]
        if not os.path.exists(repo_path):
            errors.append(f"path_to_repo does not exist: {repo_path}")
        elif not os.path.isdir(repo_path):
            errors.append(f"path_to_repo must be a directory: {repo_path}")

    # Validate user_prompts is a list (if present)
    if "user_prompts" in config and config["user_prompts"] is not None:
        if not isinstance(config["user_prompts"], list):
            errors.append("user_prompts must be a list")

    # Validate exclude_directories is a list (if present)
    if "exclude_directories" in config and config["exclude_directories"] is not None:
        if not isinstance(config["exclude_directories"], list):
            errors.append("exclude_directories must be a list")

    # Validate exclude_directories is a list (if present)
    if "exclude_directories" in config and config["exclude_directories"] is not None:
        if not isinstance(config["exclude_directories"], list):
            errors.append("exclude_directories must be a list")

    # Validate include_directories is a list (if present)
    if "include_directories" in config and config["include_directories"] is not None:
        if not isinstance(config["include_directories"], list):
            errors.append("include_directories must be a list")

    # Validate exclude_files is a list (if present)
    if "exclude_files" in config and config["exclude_files"] is not None:
        if not isinstance(config["exclude_files"], list):
            errors.append("exclude_files must be a list")

    # Validate llm_provider_type is a valid value (if present)
    if "llm_provider_type" in config and config["llm_provider_type"] is not None:
        provider_type = config["llm_provider_type"]
        if not is_valid_llm_provider_type(provider_type):
            errors.append(f"llm_provider_type must be one of {get_supported_llm_provider_types()}, got '{provider_type}'")

    return len(errors) == 0, errors


def validate_config_file(config_path: str) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
    """
    Validate a configuration file.

    Args:
        config_path (str): Path to the configuration file

    Returns:
        Tuple[bool, Optional[Dict[str, Any]], List[str]]: (is_valid, config_dict, list_of_errors)
    """
    errors = []

    # Check if file exists
    if not os.path.exists(config_path):
        errors.append(f"Configuration file not found: {config_path}")
        return False, None, errors

    # Load the configuration file using file_util
    config = read_json_file(config_path)
    if config is None:
        errors.append(f"Failed to parse JSON from configuration file: {config_path}")
        return False, None, errors

    # Validate structure
    structure_valid, structure_errors = validate_config_structure(config)
    errors.extend(structure_errors)

    # Validate values (only if structure is valid)
    if structure_valid:
        _values_valid, value_errors = validate_config_values(config)
        errors.extend(value_errors)

    is_valid = len(errors) == 0
    return is_valid, config if is_valid else None, errors


def load_and_validate_config(config_input: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Load and validate configuration from file path or dictionary, raising exception if invalid.

    Args:
        config_input (Union[str, Dict[str, Any]]): Path to the configuration file or configuration dictionary

    Returns:
        Dict[str, Any]: Validated configuration dictionary

    Raises:
        ConfigValidationError: If configuration is invalid
    """
    if isinstance(config_input, str):
        # Handle string input as file path
        is_valid, config, errors = validate_config_file(config_input)

        if not is_valid:
            error_message = f"Configuration validation failed for {config_input}:\n" + "\n".join(f"  - {error}" for error in errors)
            logger.error(error_message)
            raise ConfigValidationError(error_message)

        logger.info(f"Configuration successfully validated: {config_input}")
        return config

    elif isinstance(config_input, dict):
        # Handle dictionary input directly
        errors = []

        # Validate structure
        structure_valid, structure_errors = validate_config_structure(config_input)
        errors.extend(structure_errors)

        # Validate values (only if structure is valid)
        if structure_valid:
            _values_valid, value_errors = validate_config_values(config_input)
            errors.extend(value_errors)

        if errors:
            error_message = f"Configuration validation failed for provided dictionary:\n" + "\n".join(f"  - {error}" for error in errors)
            logger.error(error_message)
            raise ConfigValidationError(error_message)

        logger.info("Configuration dictionary successfully validated")
        return config_input

    else:
        error_message = f"config_input must be either a string (file path) or dictionary, got {type(config_input).__name__}"
        logger.error(error_message)
        raise ConfigValidationError(error_message)


def load_config_tolerant(config_path: str) -> Dict[str, Any]:
    """
    Load configuration file with tolerance for missing keys.
    This is useful for specialized tools that don't need all config keys.

    Args:
        config_path (str): Path to the configuration file

    Returns:
        Dict[str, Any]: Configuration dictionary (may have missing keys)

    Raises:
        ConfigValidationError: If file cannot be loaded or parsed
    """
    # Check if file exists
    if not os.path.exists(config_path):
        error_message = f"Configuration file not found: {config_path}"
        logger.error(error_message)
        raise ConfigValidationError(error_message)

    # Load the configuration file using file_util
    config = read_json_file(config_path)
    if config is None:
        error_message = f"Failed to parse JSON from configuration file: {config_path}"
        logger.error(error_message)
        raise ConfigValidationError(error_message)

    # Only validate that it's a dictionary
    if not isinstance(config, dict):
        error_message = f"Configuration must be a dictionary: {config_path}"
        logger.error(error_message)
        raise ConfigValidationError(error_message)

    # Validate critical paths if they exist (path_to_repo is now optional)
    errors = []

    if errors:
        error_message = f"Configuration validation failed for {config_path}:\n" + "\n".join(f"  - {error}" for error in errors)
        logger.error(error_message)
        raise ConfigValidationError(error_message)

    # Log warnings for unexpected keys (but don't fail)
    for key in config:
        if key not in CONFIG_SCHEMA:
            logger.warning(f"Unexpected configuration key found: '{key}'")

    logger.info(f"Configuration loaded successfully (tolerant mode): {config_path}")
    return config


def get_config_value(config: Dict[str, Any], key: str, default: Any = None) -> Any:
    """
    Get a configuration value with optional default.

    Args:
        config (Dict[str, Any]): Configuration dictionary
        key (str): Configuration key to retrieve
        default (Any): Default value if key is not found

    Returns:
        Any: Configuration value or default
    """
    return config.get(key, default)


def is_config_key_required(key: str) -> bool:
    """
    Check if a configuration key is required.

    Args:
        key (str): Configuration key to check

    Returns:
        bool: True if key is required, False otherwise
    """
    return CONFIG_SCHEMA.get(key, {}).get("required", False)


def get_config_schema() -> Dict[str, Dict[str, Union[type, bool]]]:
    """
    Get the configuration schema.

    Returns:
        Dict[str, Dict[str, Union[type, bool]]]: Configuration schema
    """
    return CONFIG_SCHEMA.copy()


def print_config_summary(config: Dict[str, Any]) -> None:
    """
    Print a summary of the configuration.

    Args:
        config (Dict[str, Any]): Configuration dictionary
    """
    print("Configuration Summary:")
    print("=" * 50)

    for key, value in config.items():
        required_status = "Required" if is_config_key_required(key) else "Optional"
        value_str = str(value)
        if len(value_str) > 50:
            value_str = value_str[:47] + "..."
        print(f"  {key:25} [{required_status:8}]: {value_str}")

    print("=" * 50)


if __name__ == "__main__":
    """
    Command-line interface for configuration validation.
    """

    config_file = sys.argv[1]

    try:
        config = load_and_validate_config(config_file)
        print(f"✅ Configuration file '{config_file}' is valid!")
        print_config_summary(config)
    except ConfigValidationError as e:
        print(f"❌ Configuration validation failed:")
        print(e)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


def get_llm_provider_type(config: Dict[str, Any]) -> str:
    """
    Get LLM provider type from config with default fallback.

    Args:
        config: Configuration dictionary

    Returns:
        str: LLM provider type ('aws_bedrock')
    """
    return config.get('llm_provider_type', 'aws_bedrock')


def get_credentials(config: Dict[str, Any]) -> Optional[str]:
    """
    Get credentials from config.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Optional[str]: Credentials string or None if not found
    """
    return config.get('credentials')


def get_api_key_from_config(config: Dict[str, Any]) -> Optional[str]:
    """
    Get API key from config with provider-specific logic.
    This consolidates the logic from code_analyzer.py lines 1882-1899.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Optional[str]: API key or None if not available
    """
    from .api_key_util import get_api_key
    
    llm_provider_type = get_llm_provider_type(config)

    # For AWS Bedrock, check both api_key and credentials fields
    config_api_key = config.get('api_key') or get_credentials(config)
    return get_api_key(config_api_key)


def get_llm_config_values(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get all LLM-related configuration values in a standardized format.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Dict[str, Any]: Dictionary containing llm_provider_type, credentials, and api_key
    """
    llm_provider_type = get_llm_provider_type(config)
    credentials = get_credentials(config)
    api_key = get_api_key_from_config(config)
    
    return {
        'llm_provider_type': llm_provider_type,
        'credentials': credentials,
        'api_key': api_key
    }


def get_credentials_from_config(config_dict: Dict[str, Any]) -> Optional[str]:
    """
    Get credentials token from configuration or environment.
    
    This function supports the flat configuration format where credentials
    is a simple string field.
    
    Args:
        config_dict: Configuration dictionary
        
    Returns:
        str: Credentials token if available, None otherwise
    """
    # Check config first
    creds = config_dict.get('credentials', '')
    if isinstance(creds, str):
        creds = creds.strip()
        if creds:
            return creds
    elif isinstance(creds, dict):
        # Legacy nested credentials format
        api_key = creds.get('api_key', '').strip()
        if api_key:
            return api_key
    
    # Fallback to environment variable
    env_token = os.getenv('ANTHROPIC_API_KEY', '').strip()
    if env_token:
        return env_token
    
    return None


def get_project_credentials_from_config(config_dict: Dict[str, Any]) -> Optional[str]:
    """
    Get project credentials (FloodGate token) from configuration or environment.
    
    This function supports the flat configuration format where project-credentials
    is a simple string field.
    
    Args:
        config_dict: Configuration dictionary
        
    Returns:
        str: Project credentials token if available, None otherwise
    """
    # Check config first - support both hyphenated and underscore versions
    project_creds = config_dict.get('project-credentials', '') or config_dict.get('project_credentials', '')
    if isinstance(project_creds, str):
        project_creds = project_creds.strip()
        if project_creds:
            return project_creds
    
    # Fallback to environment variable
    env_token = os.getenv('FLOODGATE_PROJECT_TOKEN', '').strip()
    if env_token:
        return env_token
    
    return None


def is_floodgate_mode(config_dict: Dict[str, Any]) -> bool:
    """
    Check if the configuration is using FloodGate project token authentication.
    
    Args:
        config_dict: Configuration dictionary
        
    Returns:
        bool: True if project-credentials is provided
    """
    project_creds = get_project_credentials_from_config(config_dict)
    return bool(project_creds)


def get_effective_token(config_dict: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """
    Get the effective token to use based on priority.
    
    Priority order:
    1. project-credentials (FloodGate token) - highest priority
    2. credentials (OAuth/OIDC token)
    3. None (will trigger AppleConnect auto-refresh)
    
    Args:
        config_dict: Configuration dictionary
        
    Returns:
        Tuple[Optional[str], str]: (token, token_type) where token_type is one of:
            - 'floodgate': Using FloodGate project token
            - 'oidc': Using OAuth/OIDC credentials
            - 'auto_refresh': No token provided, will use AppleConnect
    """
    # Check project-credentials first (highest priority)
    project_creds = get_project_credentials_from_config(config_dict)
    if project_creds:
        return project_creds, 'floodgate'
    
    # Check credentials
    creds = get_credentials_from_config(config_dict)
    if creds:
        return creds, 'oidc'
    
    # No credentials - will use AppleConnect auto-refresh
    return None, 'auto_refresh'