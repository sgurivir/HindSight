#!/usr/bin/env python3
"""
Centralized Code Analysis Result Schema Definition

This module defines the standardized format for code analysis results used across
all components of the Hindsight system, including:
- File system storage
- Database storage
- API server
- Publisher-subscriber system
- Report generation

The schema ensures consistency and provides validation for all result operations.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
import json


@dataclass
class CodeAnalysisIssue:
    """
    Represents a single code analysis issue/finding.

    This is the core unit of analysis results - each issue represents
    a specific problem, suggestion, or observation about the code.
    """
    # Core issue identification
    issue: str  # Description of the issue
    severity: str  # critical, high, medium, low
    category: str  # Type of issue (e.g., "security", "performance", "maintainability")

    # Location information
    file_path: Optional[str] = None  # File path where issue was found
    function_name: Optional[str] = None  # Function where issue was found
    line_number: Optional[int] = None  # Specific line number if available

    # Additional context
    description: Optional[str] = None  # Detailed description
    suggestion: Optional[str] = None  # Suggested fix or improvement
    confidence: Optional[float] = None  # Confidence score (0.0 to 1.0)

    # Metadata
    rule_id: Optional[str] = None  # ID of the analysis rule that found this issue
    external_references: Optional[List[str]] = field(default_factory=list)  # URLs, docs, etc.
    
    # Validation evidence (from Level 3 Response Challenger)
    evidence: str = ""  # Reasoning from validation/filtering (empty by default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        result = {}
        for key, value in asdict(self).items():
            if value is not None:
                result[key] = value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CodeAnalysisIssue':
        """Create instance from dictionary."""
        # Filter out unknown fields
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        return cls(**filtered_data)


@dataclass
class CodeAnalysisResult:
    """
    Standardized format for code analysis results.

    This is the canonical format used across all Hindsight components for storing,
    retrieving, and processing analysis results. It includes metadata about the
    analyzed function and a list of issues found.
    """
    # Function identification (required)
    file_path: str  # Relative path to the file from repository root
    function: str   # Name of the analyzed function
    checksum: str   # Content checksum for caching/change detection

    # Analysis results
    results: List[CodeAnalysisIssue] = field(default_factory=list)  # List of issues found

    # Metadata (optional)
    analysis_timestamp: Optional[datetime] = None  # When analysis was performed
    analyzer_version: Optional[str] = None  # Version of analyzer used
    analysis_duration_ms: Optional[int] = None  # How long analysis took

    def __post_init__(self):
        """Post-initialization validation and normalization."""
        # Ensure results are CodeAnalysisIssue instances
        normalized_results = []
        for result in self.results:
            if isinstance(result, dict):
                normalized_results.append(CodeAnalysisIssue.from_dict(result))
            elif isinstance(result, CodeAnalysisIssue):
                normalized_results.append(result)
            else:
                # Handle other formats by converting to dict first
                try:
                    if hasattr(result, '__dict__'):
                        result_dict = result.__dict__
                    else:
                        result_dict = dict(result)
                    normalized_results.append(CodeAnalysisIssue.from_dict(result_dict))
                except Exception:
                    # Skip invalid results
                    continue

        self.results = normalized_results

        # Set analysis timestamp if not provided
        if self.analysis_timestamp is None:
            self.analysis_timestamp = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary format for JSON serialization.

        Returns:
            Dictionary representation suitable for JSON serialization
        """
        result = {
            'file_path': self.file_path,
            'function': self.function,
            'checksum': self.checksum,
            'results': [issue.to_dict() for issue in self.results]
        }

        # Add optional metadata if present
        if self.analysis_timestamp:
            result['analysis_timestamp'] = self.analysis_timestamp.isoformat()
        if self.analyzer_version:
            result['analyzer_version'] = self.analyzer_version
        if self.analysis_duration_ms:
            result['analysis_duration_ms'] = self.analysis_duration_ms

        return result

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CodeAnalysisResult':
        """Create instance from dictionary."""
        # Handle different input formats
        if isinstance(data, dict):
            # Extract required fields
            file_path = data.get('file_path', '')
            function = data.get('function', '')
            checksum = data.get('checksum', '')

            # Handle results field - can be list or single item
            results_data = data.get('results', [])
            if not isinstance(results_data, list):
                results_data = [results_data] if results_data else []

            # Handle optional metadata
            analysis_timestamp = None
            if 'analysis_timestamp' in data:
                timestamp_str = data['analysis_timestamp']
                if isinstance(timestamp_str, str):
                    try:
                        analysis_timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    except ValueError:
                        analysis_timestamp = None
                elif isinstance(timestamp_str, datetime):
                    analysis_timestamp = timestamp_str

            return cls(
                file_path=file_path,
                function=function,
                checksum=checksum,
                results=results_data,  # Will be normalized in __post_init__
                analysis_timestamp=analysis_timestamp,
                analyzer_version=data.get('analyzer_version'),
                analysis_duration_ms=data.get('analysis_duration_ms')
            )
        else:
            raise ValueError(f"Expected dictionary, got {type(data)}")

    @classmethod
    def from_json(cls, json_str: str) -> 'CodeAnalysisResult':
        """Create instance from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


    def has_issues(self) -> bool:
        """Check if this result contains any issues."""
        return len(self.results) > 0

    def get_issue_count(self) -> int:
        """Get the total number of issues."""
        return len(self.results)

    def get_issues_by_severity(self, severity: str) -> List[CodeAnalysisIssue]:
        """Get all issues with the specified severity."""
        return [issue for issue in self.results if issue.severity == severity]

    def get_critical_issues(self) -> List[CodeAnalysisIssue]:
        """Get all critical severity issues."""
        return self.get_issues_by_severity('critical')

    def get_high_issues(self) -> List[CodeAnalysisIssue]:
        """Get all high severity issues."""
        return self.get_issues_by_severity('high')

    def validate(self) -> List[str]:
        """
        Validate the result format and return list of validation errors.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if not self.file_path:
            errors.append("file_path is required")

        if not self.function:
            errors.append("function is required")

        if not self.checksum:
            errors.append("checksum is required")

        # Validate each issue
        for i, issue in enumerate(self.results):
            if not isinstance(issue, CodeAnalysisIssue):
                errors.append(f"results[{i}] is not a CodeAnalysisIssue instance")
                continue

            if not issue.issue:
                errors.append(f"results[{i}].issue is required")

            if not issue.severity:
                errors.append(f"results[{i}].severity is required")
            elif issue.severity not in ['critical', 'high', 'medium', 'low']:
                errors.append(f"results[{i}].severity must be one of: critical, high, medium, low")

            if not issue.category:
                errors.append(f"results[{i}].category is required")

        return errors


class CodeAnalysisResultValidator:
    """Utility class for validating code analysis results."""

    @staticmethod
    def is_valid_result(data: Any) -> bool:
        """Check if data represents a valid code analysis result."""
        try:
            if isinstance(data, CodeAnalysisResult):
                return len(data.validate()) == 0
            elif isinstance(data, dict):
                result = CodeAnalysisResult.from_dict(data)
                return len(result.validate()) == 0
            else:
                return False
        except Exception:
            return False

    @staticmethod
    def normalize_result(data: Any, file_path: str = '', function: str = '', checksum: str = '') -> CodeAnalysisResult:
        """
        Normalize result format to the standard CodeAnalysisResult format.
        Only accepts CodeAnalysisResult instances or properly formatted dictionaries.
        """
        if isinstance(data, CodeAnalysisResult):
            return data
        elif isinstance(data, dict):
            # Only accept standard format with required fields
            if all(key in data for key in ['file_path', 'function', 'checksum']):
                return CodeAnalysisResult.from_dict(data)
            else:
                raise ValueError("Dictionary must contain required fields: file_path, function, checksum")
        else:
            raise ValueError(f"Unsupported data type: {type(data)}. Expected CodeAnalysisResult or dict.")


# Convenience functions for common operations
def create_result(file_path: str, function: str, checksum: str, issues: List[Dict[str, Any]] = None) -> CodeAnalysisResult:
    """
    Create a new CodeAnalysisResult with the given parameters.

    Args:
        file_path: Relative path to the file from repository root
        function: Name of the analyzed function
        checksum: Content checksum for caching
        issues: List of issue dictionaries (optional)

    Returns:
        New CodeAnalysisResult instance
    """
    return CodeAnalysisResult(
        file_path=file_path,
        function=function,
        checksum=checksum,
        results=issues or []
    )


def create_issue(issue: str, severity: str, category: str, **kwargs) -> CodeAnalysisIssue:
    """
    Create a new CodeAnalysisIssue with the given parameters.

    Args:
        issue: Description of the issue
        severity: Severity level (critical, high, medium, low)
        category: Category of the issue
        **kwargs: Additional optional fields

    Returns:
        New CodeAnalysisIssue instance
    """
    return CodeAnalysisIssue(
        issue=issue,
        severity=severity,
        category=category,
        **kwargs
    )


def validate_result_format(data: Any) -> List[str]:
    """
    Validate that data conforms to the expected result format.

    Args:
        data: Data to validate

    Returns:
        List of validation error messages (empty if valid)
    """
    try:
        if isinstance(data, CodeAnalysisResult):
            return data.validate()
        elif isinstance(data, dict):
            result = CodeAnalysisResult.from_dict(data)
            return result.validate()
        else:
            return [f"Invalid data type: {type(data)}. Expected CodeAnalysisResult or dict."]
    except Exception as e:
        return [f"Failed to parse result: {str(e)}"]


# Export the main classes and functions
__all__ = [
    'CodeAnalysisResult',
    'CodeAnalysisIssue',
    'CodeAnalysisResultValidator',
    'create_result',
    'create_issue',
    'validate_result_format'
]