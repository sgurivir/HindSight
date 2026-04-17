#!/usr/bin/env python3
"""
Stage-Isolated Iterative Analyzers Package

This package provides stage-specific iterative analyzers for LLM-based code analysis.
Each analyzer implements its own JSON extraction and validation logic to ensure
correct handling of stage-specific output formats.

Architecture:
- BaseIterativeAnalyzer: Abstract base class with shared utilities
- ContextCollectionAnalyzer: Stage 4a - expects dict with 'primary_function'
- CodeAnalysisAnalyzer: Stage 4b - expects array of issue dicts
- DiffContextAnalyzer: Stage Da - expects dict with 'changed_functions'
- DiffAnalysisAnalyzer: Stage Db - expects array of issue dicts
- ResponseChallengerAnalyzer: Level 3 filtering - expects dict with 'result' (bool) and 'reason' (str)
- TrivialFilterAnalyzer: Level 2 filtering - expects dict with 'result' (bool)

Usage:
    from hindsight.core.llm.iterative import ContextCollectionAnalyzer
    
    analyzer = ContextCollectionAnalyzer(claude)
    result = analyzer.run_iterative_analysis(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tools_executor=self,
        supported_tools=available_tools
    )
"""

from .base_iterative_analyzer import BaseIterativeAnalyzer
from .context_collection_analyzer import ContextCollectionAnalyzer
from .code_analysis_analyzer import CodeAnalysisAnalyzer
from .diff_context_analyzer import DiffContextAnalyzer
from .diff_analysis_analyzer import DiffAnalysisAnalyzer
from .response_challenger_analyzer import ResponseChallengerAnalyzer
from .trivial_filter_analyzer import TrivialFilterAnalyzer

__all__ = [
    'BaseIterativeAnalyzer',
    'ContextCollectionAnalyzer',
    'CodeAnalysisAnalyzer',
    'DiffContextAnalyzer',
    'DiffAnalysisAnalyzer',
    'ResponseChallengerAnalyzer',
    'TrivialFilterAnalyzer',
]
