"""
Common utilities for deduplication modules.

This module provides shared functionality for both issue_deduper and issue_tracking_deduper:
- VectorStore: ChromaDB-based vector storage with ephemeral and persistent modes
- EmbeddingGenerator: Sentence-transformers based embedding generation
- AnalyzerIssue: Common data model for analyzer issues
- Similarity utilities: Functions for similarity calculations
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

from .vector_store import VectorStore
from .embeddings import EmbeddingGenerator, get_default_generator
from .issue_models import AnalyzerIssue, DuplicateMatch
from .similarity_utils import (
    cosine_distance_to_similarity,
    get_similarity_threshold,
    is_duplicate,
)

__all__ = [
    # Vector store
    "VectorStore",
    # Embeddings
    "EmbeddingGenerator",
    "get_default_generator",
    # Issue models
    "AnalyzerIssue",
    "DuplicateMatch",
    # Similarity utilities
    "cosine_distance_to_similarity",
    "get_similarity_threshold",
    "is_duplicate",
]
