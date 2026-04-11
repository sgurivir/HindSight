"""
Configuration constants for the Issue Deduper module.

This module contains default settings for:
- Embedding model configuration
- Deduplication thresholds
- Batch processing settings
- Vector database settings
"""

from pathlib import Path

# Embedding settings
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Deduplication settings
DEFAULT_SIMILARITY_THRESHOLD = 0.85  # Semantic similarity threshold
EXACT_MATCH_THRESHOLD = 0.99         # For exact duplicate detection
DEFAULT_BATCH_SIZE = 32

# Collection name for vector store
COLLECTION_NAME = "issue_deduper_vectors"

# Vector DB subdirectory name (relative to repo artifacts directory)
VECTOR_DB_SUBDIR = "issue_deduper_db"

# Logging configuration
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
