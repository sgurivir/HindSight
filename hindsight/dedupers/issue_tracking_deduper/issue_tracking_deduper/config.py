"""
Configuration constants for the Issue Deduper tool.

This module contains default settings for:
- Vector database paths and settings
- Embedding model configuration
- Matching thresholds and parameters
- Hybrid scoring weights
- Default directories
"""

import os
from pathlib import Path
from typing import Dict


# Vector DB settings
DEFAULT_DB_PATH = Path.home() / ".llm" / "issue_vectors"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
COLLECTION_NAME = "issue_embeddings"

# Matching settings
DEFAULT_THRESHOLD = 0.75
DEFAULT_TOP_K = 5

# Hybrid scoring weights
# These weights determine how much each signal contributes to the final score
# file_path: Strongest signal - same file = likely same bug
# function_name: Strong signal - same function = related issues
# cosine_similarity: Semantic similarity for description matching
DEFAULT_HYBRID_WEIGHTS = {
    'file_path': 0.40,
    'function_name': 0.30,
    'cosine_similarity': 0.30,
}

# Hybrid threshold (lower than pure cosine since we have more signals)
DEFAULT_HYBRID_THRESHOLD = 0.50

# Minimum scores for each signal to contribute
MIN_FILE_SCORE = 0.2
MIN_FUNCTION_SCORE = 0.2
MIN_COSINE_SCORE = 0.5

# Issue directory
DEFAULT_ISSUE_DIR = Path.home() / "issues_on_file"

# Environment variable overrides
def get_db_path() -> Path:
    """Get the vector database path from environment or default."""
    env_path = os.environ.get("ISSUE_DEDUPER_DB_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_DB_PATH


def get_threshold() -> float:
    """Get the similarity threshold from environment or default."""
    env_threshold = os.environ.get("ISSUE_DEDUPER_THRESHOLD")
    if env_threshold:
        try:
            return float(env_threshold)
        except ValueError:
            pass
    return DEFAULT_THRESHOLD


def get_top_k() -> int:
    """Get the top-k results count from environment or default."""
    env_top_k = os.environ.get("ISSUE_DEDUPER_TOP_K")
    if env_top_k:
        try:
            return int(env_top_k)
        except ValueError:
            pass
    return DEFAULT_TOP_K


def get_issue_dir() -> Path:
    """Get the issue directory from environment or default."""
    env_dir = os.environ.get("ISSUE_DEDUPER_ISSUE_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return DEFAULT_ISSUE_DIR


def get_hybrid_weights() -> Dict[str, float]:
    """
    Get hybrid scoring weights from environment or defaults.
    
    Environment variables:
    - ISSUE_DEDUPER_FILE_WEIGHT: Weight for file path matching
    - ISSUE_DEDUPER_FUNC_WEIGHT: Weight for function name matching
    - ISSUE_DEDUPER_COSINE_WEIGHT: Weight for cosine similarity
    
    Returns:
        Dictionary of weights for hybrid scoring.
    """
    weights = DEFAULT_HYBRID_WEIGHTS.copy()
    
    env_file = os.environ.get("ISSUE_DEDUPER_FILE_WEIGHT")
    if env_file:
        try:
            weights['file_path'] = float(env_file)
        except ValueError:
            pass
    
    env_func = os.environ.get("ISSUE_DEDUPER_FUNC_WEIGHT")
    if env_func:
        try:
            weights['function_name'] = float(env_func)
        except ValueError:
            pass
    
    env_cosine = os.environ.get("ISSUE_DEDUPER_COSINE_WEIGHT")
    if env_cosine:
        try:
            weights['cosine_similarity'] = float(env_cosine)
        except ValueError:
            pass
    
    # Normalize weights to sum to 1.0
    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        for key in weights:
            weights[key] /= total
    
    return weights


def get_hybrid_threshold() -> float:
    """
    Get the hybrid similarity threshold from environment or default.
    
    Environment variable: ISSUE_DEDUPER_HYBRID_THRESHOLD
    
    Returns:
        Hybrid threshold value (0.0-1.0).
    """
    env_threshold = os.environ.get("ISSUE_DEDUPER_HYBRID_THRESHOLD")
    if env_threshold:
        try:
            return float(env_threshold)
        except ValueError:
            pass
    return DEFAULT_HYBRID_THRESHOLD


# Logging configuration
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
