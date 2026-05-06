"""
Domain-specific exception hierarchy for Hindsight.

Reuses AnalyzerErrorCode from hindsight.core.errors for error classification;
these exceptions carry contextual fields for structured error handling.
"""

from typing import Optional


class HindsightError(Exception):
    """Root exception for all Hindsight errors."""


class ConfigurationError(HindsightError):
    """Configuration-related errors."""


class MissingConfigKeyError(ConfigurationError):
    def __init__(self, key: str):
        self.key = key
        super().__init__(f"Missing required configuration key: '{key}'")


class InvalidConfigValueError(ConfigurationError):
    def __init__(self, key: str, value, reason: str = ""):
        self.key = key
        self.value = value
        self.reason = reason
        msg = f"Invalid value for configuration key '{key}': {value}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class LLMError(HindsightError):
    """LLM interaction errors."""


class LLMConnectionError(LLMError):
    def __init__(self, url: str = ""):
        self.url = url
        super().__init__(f"Failed to connect to LLM endpoint: {url}" if url else "Failed to connect to LLM endpoint")


class LLMRateLimitError(LLMError):
    def __init__(self, retry_after: Optional[float] = None):
        self.retry_after = retry_after
        msg = "LLM rate limit exceeded"
        if retry_after is not None:
            msg += f" (retry after {retry_after}s)"
        super().__init__(msg)


class LLMResponseError(LLMError):
    def __init__(self, status_code: int = 0):
        self.status_code = status_code
        super().__init__(f"LLM returned error status {status_code}" if status_code else "LLM returned an error response")


class FileSystemError(HindsightError):
    """File system operation errors."""


class FileReadError(FileSystemError):
    def __init__(self, file_path: str, reason: str = ""):
        self.file_path = file_path
        self.reason = reason
        msg = f"Failed to read file: {file_path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class FileWriteError(FileSystemError):
    def __init__(self, file_path: str, reason: str = ""):
        self.file_path = file_path
        self.reason = reason
        msg = f"Failed to write file: {file_path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class JSONParseError(FileSystemError):
    def __init__(self, file_path: str, reason: str = ""):
        self.file_path = file_path
        self.reason = reason
        msg = f"Failed to parse JSON: {file_path}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class AnalysisError(HindsightError):
    """Analysis operation errors."""


class CodeAnalysisError(AnalysisError):
    def __init__(self, file_path: str = ""):
        self.file_path = file_path
        super().__init__(f"Code analysis failed for: {file_path}" if file_path else "Code analysis failed")


class DiffAnalysisError(AnalysisError):
    def __init__(self, file_path: str = ""):
        self.file_path = file_path
        super().__init__(f"Diff analysis failed for: {file_path}" if file_path else "Diff analysis failed")


class RepositoryError(HindsightError):
    """Repository operation errors."""


class RepositoryNotFoundError(RepositoryError):
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        super().__init__(f"Repository not found: {repo_path}")


class GitOperationError(RepositoryError):
    def __init__(self, command: str = "", reason: str = ""):
        self.command = command
        self.reason = reason
        msg = "Git operation failed"
        if command:
            msg += f": {command}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class ToolExecutionError(HindsightError):
    def __init__(self, tool_name: str = "", reason: str = ""):
        self.tool_name = tool_name
        self.reason = reason
        msg = "Tool execution failed"
        if tool_name:
            msg += f": {tool_name}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class ReportError(HindsightError):
    def __init__(self, reason: str = ""):
        self.reason = reason
        super().__init__(f"Report generation failed: {reason}" if reason else "Report generation failed")
