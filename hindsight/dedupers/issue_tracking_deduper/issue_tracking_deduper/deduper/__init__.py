"""
Deduper package for issue matching and deduplication logic.

This package provides:
- Issue: Represents an issue from an LLM static analyzer report
- IssueEntry: Represents an issue entry in the vector database
- DedupeMatch: Represents a potential duplicate match
- HybridMatch: Represents a match with hybrid scoring from multiple signals
- IssueMatcher: Matches issues against the issue database
- ReportDeduper: High-level class for deduplicating entire reports
- dedupe_report: Convenience function for deduplication
"""

from .issue import Issue, IssueEntry, DedupeMatch, HybridMatch

# Lazy imports to avoid circular dependency with vector_db
# matcher.py imports from vector_db.store, which imports from deduper.issue
def __getattr__(name):
    """Lazy import for matcher classes to avoid circular imports."""
    if name in ('IssueMatcher', 'ReportDeduper', 'dedupe_report'):
        from .matcher import IssueMatcher, ReportDeduper, dedupe_report
        globals()['IssueMatcher'] = IssueMatcher
        globals()['ReportDeduper'] = ReportDeduper
        globals()['dedupe_report'] = dedupe_report
        return globals()[name]
    if name == 'HybridMatcher':
        from .hybrid_matcher import HybridMatcher
        globals()['HybridMatcher'] = HybridMatcher
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'Issue',
    'IssueEntry',
    'DedupeMatch',
    'HybridMatch',
    'IssueMatcher',
    'HybridMatcher',
    'ReportDeduper',
    'dedupe_report',
]
