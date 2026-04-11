"""
Duplicate detector for the Issue Deduper module.

This module provides functionality to detect duplicate issues using
both exact matching (content hash) and semantic similarity (vector search).
"""

import logging
from typing import List, Dict, Set, Optional

from ..common.vector_store import VectorStore
from ..common.embeddings import EmbeddingGenerator
from ..common.issue_models import AnalyzerIssue, DuplicateMatch
from ..common.similarity_utils import cosine_distance_to_similarity
from .config import DEFAULT_SIMILARITY_THRESHOLD

logger = logging.getLogger("hindsight.dedupers.issue_deduper.detector")


class DuplicateDetector:
    """
    Detects duplicate issues using semantic similarity.
    
    Two-stage detection:
    1. Exact match: Content hash comparison
    2. Semantic match: Vector similarity above threshold
    
    Attributes:
        threshold: Similarity threshold for duplicate detection.
        vector_store: The VectorStore instance for similarity queries.
        embedding_generator: The EmbeddingGenerator instance.
    """
    
    def __init__(
        self,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        vector_store: Optional[VectorStore] = None
    ):
        """
        Initialize the detector.
        
        Args:
            threshold: Similarity threshold for duplicate detection (0.0-1.0).
            vector_store: Optional VectorStore instance for queries.
        """
        self.threshold = threshold
        self.vector_store = vector_store
        self.embedding_generator = EmbeddingGenerator.get_instance()
        self._matches: List[DuplicateMatch] = []
        
        logger.debug(f"DuplicateDetector initialized with threshold: {threshold}")
    
    def set_vector_store(self, vector_store: VectorStore) -> None:
        """
        Set the vector store to use for queries.
        
        Args:
            vector_store: The VectorStore instance.
        """
        self.vector_store = vector_store
    
    def find_duplicates(self, issues: List[AnalyzerIssue]) -> List[DuplicateMatch]:
        """
        Find all duplicates in the issue list.
        
        Uses a greedy approach: for each issue, check if any previously
        seen issue is a duplicate. The first occurrence is kept.
        
        Args:
            issues: List of AnalyzerIssue objects (already ingested).
        
        Returns:
            List of DuplicateMatch objects.
        """
        self._matches = []
        seen_hashes: Dict[str, str] = {}  # hash -> issue_id
        seen_ids: Set[str] = set()
        
        logger.info(f"Finding duplicates among {len(issues)} issues")
        
        for issue in issues:
            # Stage 1: Exact match check (content hash)
            content_hash = issue.compute_content_hash()
            if content_hash in seen_hashes:
                match = DuplicateMatch(
                    original_id=seen_hashes[content_hash],
                    duplicate_id=issue.id,
                    similarity_score=1.0,
                    match_type="exact"
                )
                self._matches.append(match)
                logger.debug(f"Exact duplicate found: {issue.id} -> {seen_hashes[content_hash]}")
                continue
            
            # Stage 2: Semantic similarity check
            if self.vector_store and seen_ids:
                semantic_match = self._check_semantic_similarity(issue, seen_ids)
                if semantic_match:
                    self._matches.append(semantic_match)
                    logger.debug(
                        f"Semantic duplicate found: {issue.id} -> {semantic_match.original_id} "
                        f"({semantic_match.similarity_score:.2%})"
                    )
                    continue
            
            # Not a duplicate - add to seen
            seen_hashes[content_hash] = issue.id
            seen_ids.add(issue.id)
        
        logger.info(f"Found {len(self._matches)} duplicates")
        return self._matches
    
    def _check_semantic_similarity(
        self,
        issue: AnalyzerIssue,
        seen_ids: Set[str]
    ) -> Optional[DuplicateMatch]:
        """
        Check if an issue is semantically similar to any seen issue.
        
        Args:
            issue: The issue to check.
            seen_ids: Set of already-seen issue IDs.
        
        Returns:
            DuplicateMatch if a duplicate is found, None otherwise.
        """
        if not self.vector_store:
            return None
        
        # Generate embedding for the issue
        query_embedding = self.embedding_generator.generate(
            issue.to_embedding_text()
        )
        
        # Query excluding the current issue
        results = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=1,
            exclude_ids=[issue.id]
        )
        
        if not results:
            return None
        
        doc_id, metadata, distance = results[0]
        similarity = cosine_distance_to_similarity(distance)
        
        # Check if it's a duplicate and the original was already seen
        if similarity >= self.threshold and doc_id in seen_ids:
            return DuplicateMatch(
                original_id=doc_id,
                duplicate_id=issue.id,
                similarity_score=similarity,
                match_type="semantic"
            )
        
        return None
    
    def check_single(
        self,
        issue: AnalyzerIssue,
        exclude_ids: Optional[List[str]] = None
    ) -> Optional[DuplicateMatch]:
        """
        Check if a single issue is a duplicate of any existing issue.
        
        Args:
            issue: The issue to check.
            exclude_ids: Optional list of IDs to exclude from matching.
        
        Returns:
            DuplicateMatch if a duplicate is found, None otherwise.
        """
        if not self.vector_store:
            return None
        
        exclude = list(exclude_ids) if exclude_ids else []
        exclude.append(issue.id)  # Always exclude self
        
        query_embedding = self.embedding_generator.generate(
            issue.to_embedding_text()
        )
        
        results = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=1,
            exclude_ids=exclude
        )
        
        if not results:
            return None
        
        doc_id, metadata, distance = results[0]
        similarity = cosine_distance_to_similarity(distance)
        
        if similarity >= self.threshold:
            return DuplicateMatch(
                original_id=doc_id,
                duplicate_id=issue.id,
                similarity_score=similarity,
                match_type="semantic"
            )
        
        return None
    
    def get_matches(self) -> List[DuplicateMatch]:
        """
        Get all detected duplicate matches.
        
        Returns:
            Copy of the list of DuplicateMatch objects.
        """
        return self._matches.copy()
    
    def get_stats(self) -> Dict[str, int]:
        """
        Get statistics about detected duplicates.
        
        Returns:
            Dictionary with counts of exact and semantic matches.
        """
        exact_count = sum(1 for m in self._matches if m.match_type == "exact")
        semantic_count = sum(1 for m in self._matches if m.match_type == "semantic")
        
        return {
            'total_duplicates': len(self._matches),
            'exact_matches': exact_count,
            'semantic_matches': semantic_count,
        }
    
    def clear(self) -> None:
        """Clear all detected matches."""
        self._matches = []
