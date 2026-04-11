"""
Dedupers package - Issue deduplication tools.

This package contains modules for deduplicating issues:

1. common: Shared utilities for deduplication
   - VectorStore: ChromaDB-based vector storage
   - EmbeddingGenerator: Sentence-transformers embeddings
   - AnalyzerIssue: Common issue data model
   - Similarity utilities

2. issue_deduper: Deduplicates issues within analyzer output
   - Removes duplicate issues before HTML report generation
   - Uses semantic similarity and exact matching
   - Repository-specific vector database

3. issue_tracking_deduper: Deduplicates against external issue trackers
   - Matches issues against Radar/external systems
   - Annotates HTML reports with duplicate warnings

Usage:
    from hindsight.dedupers.issue_deduper import IssueDeduper, dedupe_issues
    
    # Using the class
    with IssueDeduper(artifacts_dir="~/llm_artifacts/repo") as deduper:
        unique_issues = deduper.dedupe(issues)
    
    # Using the convenience function
    unique_issues = dedupe_issues(issues, artifacts_dir="~/llm_artifacts/repo")
"""

# Apply numpy binary compatibility warnings filter BEFORE any imports
# This must be done early to prevent RuntimeWarning from being raised as an error
# when importing sentence-transformers or torch on older Python versions (e.g., 3.9)
import warnings
warnings.filterwarnings(
    "ignore",
    message=".*numpy\\.dtype size changed.*",
    category=RuntimeWarning
)
warnings.filterwarnings(
    "ignore",
    message=".*numpy\\.ufunc size changed.*",
    category=RuntimeWarning
)

# Re-export main classes for convenience
from .issue_deduper import IssueDeduper, dedupe_issues

__all__ = [
    "IssueDeduper",
    "dedupe_issues",
]
