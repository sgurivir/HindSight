"""
Analyzers module for Hindsight.

This module contains analysis tools for traces and repositories.

Note: code_analyzer is intentionally NOT imported here because it's designed
to be run as a script via `python -m hindsight.analyzers.code_analyzer`.
Importing it in __init__.py causes RuntimeWarning when running as __main__
because the module gets imported twice (once via package init, once as __main__).
"""

# Import specific classes to avoid circular imports
# Note: code_analyzer excluded - use `from hindsight.analyzers.code_analyzer import ...` directly
from .trace_analyzer import TraceAnalyzer, TraceAnalysisRunner
from .directory_classifier import DirectoryClassifier, LLMBasedDirectoryClassifier
from .base_analyzer import BaseAnalyzer
from .token_tracker import TokenTracker

__all__ = [
    'TraceAnalyzer',
    'TraceAnalysisRunner',
    'DirectoryClassifier',
    'LLMBasedDirectoryClassifier',
    'BaseAnalyzer',
    'TokenTracker'
]
