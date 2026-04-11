"""
Issue Filter Module

This module provides a unified filtering system for all analyzers:
1. Level 1: Category-based filtering - Hard filter for unwanted categories
2. Level 2: LLM-based filtering - Intelligent filter for remaining issues
3. Specialized: Trace relevance filtering for trace analysis

All analyzers should use this module to ensure consistent filtering behavior.
"""

from .unified_issue_filter import UnifiedIssueFilter, create_unified_filter
from .category_filter import CategoryBasedFilter
from .llm_filter import LLMBasedFilter
from .trace_relevance_filter import TraceRelevanceFilter

__all__ = [
    'UnifiedIssueFilter',
    'CategoryBasedFilter',
    'LLMBasedFilter',
    'TraceRelevanceFilter',
    'create_unified_filter'
]