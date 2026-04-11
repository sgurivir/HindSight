"""
Error handling infrastructure for Hindsight analyzers.
Provides standardized error codes and result types for analyzer operations.
"""

from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass


class AnalyzerErrorCode(str, Enum):
    """
    Standardized error codes for analyzer operations.
    These errors are exposed to users and system administrators.
    """
    # Success
    SUCCESS = "SUCCESS_0000"
    
    # Repository Errors (1xxx)
    ERROR_REPOSITORY_NOT_FOUND = "REPO_1001"
    ERROR_REPOSITORY_TOO_MANY_FILES = "REPO_1002"
    ERROR_REPOSITORY_NOT_SUPPORTED = "REPO_1003"
    ERROR_REPOSITORY_ACCESS_DENIED = "REPO_1004"
    ERROR_REPOSITORY_CLONE_FAILED = "REPO_1005"
    
    # LLM/Credentials Errors (2xxx)
    ERROR_LLM_CREDENTIALS_INVALID = "LLM_2001"
    ERROR_NO_CONNECTION_TO_LLM = "LLM_2002"
    ERROR_LLM_RATE_LIMIT_EXCEEDED = "LLM_2003"
    ERROR_LLM_QUOTA_EXCEEDED = "LLM_2004"
    ERROR_LLM_MODEL_NOT_AVAILABLE = "LLM_2005"
    
    # Analysis Errors (3xxx)
    ERROR_ANALYSIS_TIMEOUT = "ANALYSIS_3001"
    ERROR_ANALYSIS_CANCELLED = "ANALYSIS_3002"
    ERROR_ANALYSIS_INVALID_CONFIG = "ANALYSIS_3003"
    ERROR_ANALYSIS_NO_FILES_FOUND = "ANALYSIS_3004"
    ERROR_ANALYSIS_AST_GENERATION_FAILED = "ANALYSIS_3005"
    
    # Internal Errors (5xxx)
    ERROR_INTERNAL_UNKNOWN = "INTERNAL_5000"
    
    def is_success(self) -> bool:
        """Check if this is a success code."""
        return self == AnalyzerErrorCode.SUCCESS
    
    def is_error(self) -> bool:
        """Check if this is an error code."""
        return not self.is_success()


@dataclass
class AnalysisResult:
    """
    Result object returned by analyzer operations.
    Uses return codes instead of exceptions for error handling.
    """
    code: AnalyzerErrorCode
    message: str
    data: Optional[Dict[str, Any]] = None
    details: Optional[Dict[str, Any]] = None
    recoverable: bool = False
    user_action: Optional[str] = None
    
    @classmethod
    def success(cls, message: str = "Operation completed successfully", data: Optional[Dict[str, Any]] = None) -> 'AnalysisResult':
        """Create a success result."""
        return cls(
            code=AnalyzerErrorCode.SUCCESS,
            message=message,
            data=data,
            recoverable=False
        )
    
    @classmethod
    def error(
        cls,
        code: AnalyzerErrorCode,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        recoverable: bool = False,
        user_action: Optional[str] = None
    ) -> 'AnalysisResult':
        """Create an error result."""
        return cls(
            code=code,
            message=message,
            details=details,
            recoverable=recoverable,
            user_action=user_action
        )
    
    def is_success(self) -> bool:
        """Check if operation was successful."""
        return self.code.is_success()
    
    def is_error(self) -> bool:
        """Check if operation failed."""
        return self.code.is_error()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary for API responses."""
        result = {
            "error_code": self.code.value,
            "message": self.message,
            "success": self.is_success()
        }
        
        if self.data is not None:
            result["data"] = self.data
        
        if self.details is not None:
            result["details"] = self.details
        
        if self.is_error():
            result["recoverable"] = self.recoverable
            if self.user_action:
                result["user_action"] = self.user_action
        
        return result