#!/usr/bin/env python3
"""
Shared pytest fixtures for Hindsight tests.
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))




# ============================================================================
# Sample Data Fixtures
# ============================================================================

@pytest.fixture
def sample_issue() -> Dict[str, Any]:
    """Sample issue dictionary for testing."""
    return {
        'issue': 'Potential null pointer dereference',
        'severity': 'high',
        'category': 'logicBug',
        'file_path': 'src/main/java/Example.java',
        'function_name': 'processData',
        'line_number': 42,
        'description': 'The variable may be null when accessed',
        'suggestion': 'Add null check before accessing the variable'
    }


@pytest.fixture
def sample_issues() -> List[Dict[str, Any]]:
    """List of sample issues for testing."""
    return [
        {
            'issue': 'Potential null pointer dereference',
            'severity': 'high',
            'category': 'logicBug',
            'file_path': 'src/main/java/Example.java',
            'function_name': 'processData',
            'line_number': 42,
            'description': 'The variable may be null when accessed',
            'suggestion': 'Add null check before accessing the variable'
        },
        {
            'issue': 'Inefficient loop operation',
            'severity': 'medium',
            'category': 'performance',
            'file_path': 'src/main/java/Utils.java',
            'function_name': 'calculateSum',
            'line_number': 100,
            'description': 'Loop can be optimized using stream operations',
            'suggestion': 'Use Java streams for better performance'
        },
        {
            'issue': 'Memory leak in resource handling',
            'severity': 'critical',
            'category': 'memory',
            'file_path': 'src/main/java/FileHandler.java',
            'function_name': 'readFile',
            'line_number': 55,
            'description': 'Resource not properly closed',
            'suggestion': 'Use try-with-resources statement'
        },
        {
            'issue': 'Code style issue',
            'severity': 'low',
            'category': 'codeQuality',
            'file_path': 'src/main/java/Config.java',
            'function_name': 'loadConfig',
            'line_number': 20,
            'description': 'Variable naming does not follow conventions',
            'suggestion': 'Rename variable to follow camelCase convention'
        }
    ]


@pytest.fixture
def sample_code_analysis_result() -> Dict[str, Any]:
    """Sample code analysis result for testing."""
    return {
        'file_path': 'src/main/java/Example.java',
        'function': 'processData',
        'checksum': 'abc123def456',
        'results': [
            {
                'issue': 'Potential null pointer dereference',
                'severity': 'high',
                'category': 'logicBug',
                'description': 'The variable may be null when accessed',
                'suggestion': 'Add null check before accessing the variable'
            }
        ]
    }


@pytest.fixture
def sample_config(temp_dir) -> Dict[str, Any]:
    """Sample configuration dictionary for testing.
    
    Note: Uses temp_dir fixture to ensure path_to_repo exists.
    """
    return {
        'project_name': 'test-project',
        'api_end_point': 'https://api.anthropic.com/v1/messages',
        'model': 'claude-3-5-sonnet-20241022',
        'llm_provider_type': 'claude',
        'path_to_repo': temp_dir,  # Use actual temp directory that exists
        'exclude_directories': ['node_modules', '.git', 'build'],
        'exclude_files': ['*.min.js', '*.map'],
        'user_prompts': ['Find security issues', 'Check for performance problems']
    }


# ============================================================================
# Temporary Directory Fixtures
# ============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    # Cleanup after test
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path)


@pytest.fixture(autouse=True)
def configure_output_directory_provider(temp_dir):
    """
    Automatically configure OutputDirectoryProvider for all tests.
    
    This fixture runs automatically before each test to ensure the singleton
    is properly configured, preventing "OutputDirectoryProvider not configured"
    errors.
    
    The fixture also resets the provider after each test to ensure test isolation.
    
    This is a TEST FIX - the tests were not properly configuring the singleton
    before using code that depends on it.
    """
    from hindsight.utils.output_directory_provider import get_output_directory_provider
    
    provider = get_output_directory_provider()
    provider.configure(repo_path=temp_dir, custom_base_dir=temp_dir)
    
    yield provider
    
    # Reset after test to ensure clean state for next test
    provider.reset()


@pytest.fixture
def temp_repo_structure(temp_dir):
    """Create a temporary repository structure for testing."""
    # Create directory structure
    dirs = [
        'src/main/java',
        'src/test/java',
        'lib',
        'docs',
        'build/classes'
    ]
    
    for d in dirs:
        os.makedirs(os.path.join(temp_dir, d), exist_ok=True)
    
    # Create some sample files
    files = {
        'src/main/java/Example.java': 'public class Example { }',
        'src/main/java/Utils.java': 'public class Utils { }',
        'src/test/java/ExampleTest.java': 'public class ExampleTest { }',
        'lib/library.jar': 'binary content',
        'docs/README.md': '# Documentation',
        'build/classes/Example.class': 'binary content'
    }
    
    for file_path, content in files.items():
        full_path = os.path.join(temp_dir, file_path)
        with open(full_path, 'w') as f:
            f.write(content)
    
    return temp_dir


@pytest.fixture
def temp_config_file(temp_dir, sample_config):
    """Create a temporary config file for testing."""
    config_path = os.path.join(temp_dir, 'config.json')
    with open(config_path, 'w') as f:
        json.dump(sample_config, f)
    return config_path


@pytest.fixture
def temp_analysis_results_dir(temp_dir, sample_code_analysis_result):
    """Create a temporary directory with analysis results for testing."""
    results_dir = os.path.join(temp_dir, 'results', 'code_analysis')
    os.makedirs(results_dir, exist_ok=True)
    
    # Create a sample result file
    result_file = os.path.join(results_dir, 'processData_Example.java_abc123de_analysis.json')
    with open(result_file, 'w') as f:
        json.dump(sample_code_analysis_result, f)
    
    return results_dir


# ============================================================================
# Mock Fixtures
# ============================================================================

@pytest.fixture
def mock_logger():
    """Create a mock logger for testing."""
    return MagicMock()


@pytest.fixture
def mock_llm_response():
    """Create a mock LLM response for testing."""
    return {
        'result': False,
        'reason': 'This is a legitimate issue that should be kept'
    }


@pytest.fixture
def mock_file_content_provider():
    """Create a mock FileContentProvider for testing."""
    mock = MagicMock()
    mock.name_to_path_mapping = {
        'Example.java': [{'path': 'src/main/java/Example.java'}],
        'Utils.java': [{'path': 'src/main/java/Utils.java'}]
    }
    mock.resolve_file_path = MagicMock(return_value=Path('src/main/java/Example.java'))
    return mock


# ============================================================================
# Configuration Fixtures
# ============================================================================

@pytest.fixture
def valid_config_dict():
    """Valid configuration dictionary."""
    return {
        'project_name': 'test-project',
        'api_end_point': 'https://api.anthropic.com/v1/messages',
        'model': 'claude-3-5-sonnet-20241022',
        'llm_provider_type': 'claude'
    }


@pytest.fixture
def invalid_config_missing_required():
    """Invalid configuration missing required fields."""
    return {
        'description': 'Missing project_name and api_end_point'
    }


@pytest.fixture
def invalid_config_wrong_types():
    """Invalid configuration with wrong types."""
    return {
        'project_name': 123,  # Should be string
        'api_end_point': 'https://api.example.com',
        'exclude_directories': 'not-a-list'  # Should be list
    }


# ============================================================================
# Issue Filter Fixtures
# ============================================================================

@pytest.fixture
def issues_with_various_categories():
    """Issues with various categories for filter testing."""
    return [
        {'issue': 'Logic bug', 'category': 'logicBug', 'severity': 'high'},
        {'issue': 'Performance issue', 'category': 'performance', 'severity': 'medium'},
        {'issue': 'Memory leak', 'category': 'memory', 'severity': 'critical'},
        {'issue': 'Code quality', 'category': 'codeQuality', 'severity': 'low'},
        {'issue': 'Division by zero', 'category': 'divisionByZero', 'severity': 'high'},
        {'issue': 'Null access', 'category': 'nilAccess', 'severity': 'high'},
        {'issue': 'No issue', 'category': 'noIssue', 'severity': 'low'},
        {'issue': 'General issue', 'category': 'general', 'severity': 'medium'}
    ]


# ============================================================================
# Report Generator Fixtures
# ============================================================================

@pytest.fixture
def issues_for_report():
    """Issues formatted for report generation."""
    return [
        {
            'issue': 'Null pointer dereference',
            'severity': 'high',
            'category': 'logicBug',
            'file_path': 'src/main/java/Example.java',
            'function_name': 'processData',
            'line_number': 42,
            'description': 'Variable may be null',
            'suggestion': 'Add null check'
        },
        {
            'issue': 'Slow loop',
            'severity': 'medium',
            'category': 'performance',
            'file_path': 'src/main/java/Utils.java',
            'function_name': 'calculate',
            'line_number': 100,
            'description': 'Loop is inefficient',
            'suggestion': 'Use streams'
        },
        {
            'issue': 'Critical bug',
            'severity': 'critical',
            'category': 'logicBug',
            'file_path': 'src/main/java/Core.java',
            'function_name': 'init',
            'line_number': 10,
            'description': 'Initialization error',
            'suggestion': 'Fix initialization'
        }
    ]


# ============================================================================
# Directory Hierarchy Fixtures
# ============================================================================

@pytest.fixture
def directory_hierarchy_issues():
    """Issues organized by directory for hierarchy testing."""
    return [
        {'file_path': 'src/main/java/Example.java', 'issue': 'Issue 1', 'severity': 'high'},
        {'file_path': 'src/main/java/Utils.java', 'issue': 'Issue 2', 'severity': 'medium'},
        {'file_path': 'src/test/java/ExampleTest.java', 'issue': 'Issue 3', 'severity': 'low'},
        {'file_path': 'lib/Helper.java', 'issue': 'Issue 4', 'severity': 'high'},
        {'file_path': 'Unknown', 'issue': 'Issue 5', 'severity': 'medium'}
    ]
