"""
Similarity calculation utilities for deduplication modules.

This module provides utility functions for:
- Converting ChromaDB cosine distance to similarity scores
- Getting appropriate thresholds for different match types
- Determining if a similarity score indicates a duplicate
"""


def cosine_distance_to_similarity(distance: float) -> float:
    """
    Convert ChromaDB cosine distance to similarity score (0-1).
    
    ChromaDB returns cosine distance, which ranges from 0 (identical)
    to 2 (opposite). This function converts it to a similarity score
    where 1 means identical and 0 means completely different.
    
    Formula: similarity = 1 - (distance / 2)
    
    Args:
        distance: Cosine distance from ChromaDB (0 to 2).
    
    Returns:
        Similarity score between 0.0 and 1.0.
    
    Examples:
        >>> cosine_distance_to_similarity(0.0)
        1.0
        >>> cosine_distance_to_similarity(1.0)
        0.5
        >>> cosine_distance_to_similarity(2.0)
        0.0
    """
    # Clamp distance to valid range
    distance = max(0.0, min(2.0, distance))
    return 1.0 - (distance / 2.0)


def similarity_to_cosine_distance(similarity: float) -> float:
    """
    Convert similarity score to ChromaDB cosine distance.
    
    This is the inverse of cosine_distance_to_similarity.
    
    Formula: distance = 2 * (1 - similarity)
    
    Args:
        similarity: Similarity score between 0.0 and 1.0.
    
    Returns:
        Cosine distance between 0.0 and 2.0.
    
    Examples:
        >>> similarity_to_cosine_distance(1.0)
        0.0
        >>> similarity_to_cosine_distance(0.5)
        1.0
        >>> similarity_to_cosine_distance(0.0)
        2.0
    """
    # Clamp similarity to valid range
    similarity = max(0.0, min(1.0, similarity))
    return 2.0 * (1.0 - similarity)


# Predefined thresholds for different match types
SIMILARITY_THRESHOLDS = {
    "exact": 0.99,      # Near-identical (content hash match or very high similarity)
    "semantic": 0.85,   # Semantically similar (same issue, different wording)
    "related": 0.70,    # Possibly related (similar topic or area)
    "loose": 0.60,      # Loosely related (might be worth reviewing)
}


def get_similarity_threshold(match_type: str = "semantic") -> float:
    """
    Get appropriate threshold for the specified match type.
    
    Args:
        match_type: Type of match to get threshold for.
                   Options: "exact", "semantic", "related", "loose"
    
    Returns:
        Similarity threshold value (0.0-1.0).
    
    Examples:
        >>> get_similarity_threshold("exact")
        0.99
        >>> get_similarity_threshold("semantic")
        0.85
        >>> get_similarity_threshold("related")
        0.70
    """
    return SIMILARITY_THRESHOLDS.get(match_type, SIMILARITY_THRESHOLDS["semantic"])


def is_duplicate(
    similarity: float,
    threshold: float = 0.85
) -> bool:
    """
    Determine if similarity score indicates a duplicate.
    
    Args:
        similarity: Similarity score between 0.0 and 1.0.
        threshold: Minimum similarity to consider as duplicate.
    
    Returns:
        True if similarity >= threshold, False otherwise.
    
    Examples:
        >>> is_duplicate(0.90, 0.85)
        True
        >>> is_duplicate(0.80, 0.85)
        False
    """
    return similarity >= threshold


def is_exact_match(similarity: float) -> bool:
    """
    Determine if similarity score indicates an exact match.
    
    Uses the "exact" threshold (0.99) to determine if two items
    are essentially identical.
    
    Args:
        similarity: Similarity score between 0.0 and 1.0.
    
    Returns:
        True if similarity indicates exact match.
    """
    return similarity >= SIMILARITY_THRESHOLDS["exact"]


def is_semantic_match(similarity: float) -> bool:
    """
    Determine if similarity score indicates a semantic match.
    
    Uses the "semantic" threshold (0.85) to determine if two items
    are semantically similar (same meaning, possibly different wording).
    
    Args:
        similarity: Similarity score between 0.0 and 1.0.
    
    Returns:
        True if similarity indicates semantic match.
    """
    return similarity >= SIMILARITY_THRESHOLDS["semantic"]


def get_match_type(similarity: float) -> str:
    """
    Get the match type based on similarity score.
    
    Args:
        similarity: Similarity score between 0.0 and 1.0.
    
    Returns:
        Match type string: "exact", "semantic", "related", "loose", or "none"
    """
    if similarity >= SIMILARITY_THRESHOLDS["exact"]:
        return "exact"
    elif similarity >= SIMILARITY_THRESHOLDS["semantic"]:
        return "semantic"
    elif similarity >= SIMILARITY_THRESHOLDS["related"]:
        return "related"
    elif similarity >= SIMILARITY_THRESHOLDS["loose"]:
        return "loose"
    else:
        return "none"


def get_confidence_level(similarity: float) -> str:
    """
    Get a human-readable confidence level based on similarity score.
    
    Args:
        similarity: Similarity score between 0.0 and 1.0.
    
    Returns:
        Confidence level: "very_high", "high", "moderate", "low", or "very_low"
    """
    if similarity >= 0.95:
        return "very_high"
    elif similarity >= 0.85:
        return "high"
    elif similarity >= 0.75:
        return "moderate"
    elif similarity >= 0.65:
        return "low"
    else:
        return "very_low"


def format_similarity_percentage(similarity: float) -> str:
    """
    Format similarity score as a percentage string.
    
    Args:
        similarity: Similarity score between 0.0 and 1.0.
    
    Returns:
        Formatted percentage string (e.g., "85%").
    """
    return f"{int(similarity * 100)}%"
