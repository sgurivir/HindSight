"""
Utilities module for Hindsight analysis
Contains file operations, logging, configuration, and other utility functions
"""

from .config_util import (
    load_and_validate_config, validate_config_file, validate_config_structure,
    validate_config_values, get_config_value, is_config_key_required,
    get_config_schema, print_config_summary, ConfigValidationError
)
from .file_util import (
    read_file, write_file, read_json_file, write_json_file,
    get_file_info, ensure_directory_exists, get_platform_temp_dir, get_repository_folder_name,
    get_artifacts_temp_dir, get_artifacts_temp_file_path, get_artifacts_temp_subdir_path
)
from .json_util import (
    parse_json, clean_json_response, validate_and_format_json
)
from .log_util import LogUtil, get_logger, setup_default_logging

__all__ = [
    # File utilities
    'read_file',
    'write_file',
    'read_json_file',
    'write_json_file',
    'get_file_info',
    'ensure_directory_exists',
    'get_platform_temp_dir',
    'get_repository_folder_name',
    'get_artifacts_temp_dir',
    'get_artifacts_temp_file_path',
    'get_artifacts_temp_subdir_path',
    # JSON utilities
    'parse_json',
    'clean_json_response',
    'validate_and_format_json',
    # Logging utilities
    'LogUtil',
    'get_logger',
    'setup_default_logging',
    # Configuration utilities
    'load_and_validate_config',
    'validate_config_file',
    'validate_config_structure',
    'validate_config_values',
    'get_config_value',
    'is_config_key_required',
    'get_config_schema',
    'print_config_summary',
    'ConfigValidationError'
]