"""
Duplicate detector for the Issue Deduper module.

This module provides functionality to detect duplicate issues using
both exact matching (content hash) and hybrid similarity scoring.

Hybrid scoring combines three signals:
- File name matching (strong signal)
- Function name matching (strong signal)
- Cosine similarity of descriptions (weaker signal)
"""

import logging
import re
from pathlib import Path
from typing import List, Dict, Set, Optional

from ..common.vector_store import VectorStore
from ..common.embeddings import EmbeddingGenerator
from ..common.issue_models import AnalyzerIssue, DuplicateMatch
from ..common.similarity_utils import cosine_distance_to_similarity
from .config import DEFAULT_SIMILARITY_THRESHOLD

HYBRID_WEIGHTS_ALL = {
    'file_name': 0.40,
    'function_name': 0.25,
    'cosine': 0.35,
}

HYBRID_WEIGHTS_FILE_ONLY = {
    'file_name': 0.50,
    'cosine': 0.50,
}

HYBRID_WEIGHTS_FUNC_ONLY = {
    'function_name': 0.40,
    'cosine': 0.60,
}

N_CANDIDATES = 10

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
        Check if an issue is a duplicate of any seen issue using hybrid scoring.

        Combines three signals with file_name and function_name as strong
        signals and cosine similarity as a weaker signal. When both issues
        have a file_path and the file names differ, the candidate is rejected.
        """
        if not self.vector_store:
            return None

        query_embedding = self.embedding_generator.generate(
            issue.to_embedding_text()
        )

        results = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=N_CANDIDATES,
            exclude_ids=[issue.id]
        )

        if not results:
            return None

        best_match: Optional[DuplicateMatch] = None
        best_score = 0.0

        issue_file = issue.file_path or ''
        issue_func = issue.function_name or ''

        for doc_id, metadata, distance in results:
            if doc_id not in seen_ids:
                continue

            candidate_file = metadata.get('file_path', '')
            candidate_func = metadata.get('function_name', '')
            cosine_sim = cosine_distance_to_similarity(distance)

            has_file = bool(issue_file and candidate_file)
            has_func = bool(issue_func and candidate_func)

            if has_file:
                file_score = self._compute_file_name_score(issue_file, candidate_file)
                if file_score == 0.0:
                    continue
            else:
                file_score = 0.0

            func_score = (
                self._compute_function_name_score(issue_func, candidate_func)
                if has_func else 0.0
            )

            hybrid = self._compute_hybrid_score(
                file_score, func_score, cosine_sim, has_file, has_func
            )

            if hybrid >= self.threshold and hybrid > best_score:
                best_score = hybrid
                best_match = DuplicateMatch(
                    original_id=doc_id,
                    duplicate_id=issue.id,
                    similarity_score=hybrid,
                    match_type="semantic"
                )

        return best_match

    @staticmethod
    def _compute_file_name_score(path_a: str, path_b: str) -> float:
        """Compare two file paths by their file names, with a directory bonus."""
        if not path_a or not path_b:
            return 0.0
        pa, pb = Path(path_a), Path(path_b)
        if pa.name != pb.name:
            return 0.0
        if path_a == path_b:
            return 1.0
        parts_a = set(pa.parts[:-1])
        parts_b = set(pb.parts[:-1])
        if not parts_a and not parts_b:
            return 1.0
        common = len(parts_a & parts_b)
        total = max(len(parts_a), len(parts_b), 1)
        return 0.8 + 0.2 * (common / total)

    @staticmethod
    def _compute_function_name_score(func_a: str, func_b: str) -> float:
        """Compare two function names with normalization."""
        if not func_a or not func_b:
            return 0.0

        def _normalize(name: str) -> str:
            n = name.strip()
            if n.startswith('-[') or n.startswith('+['):
                n = n[2:]
            n = n.rstrip(']')
            n = re.sub(r'WithOptions?:?$', '', n)
            return n.lower().strip()

        na, nb = _normalize(func_a), _normalize(func_b)
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        if na in nb or nb in na:
            return 0.7

        def _tokenize(name: str) -> set:
            parts = re.sub(r'([A-Z])', r' \1', name).lower().split()
            parts.extend(re.split(r'[_:]', name.lower()))
            return {t for t in parts if len(t) > 1}

        tokens_a = _tokenize(func_a)
        tokens_b = _tokenize(func_b)
        if tokens_a and tokens_b:
            common = len(tokens_a & tokens_b)
            total = max(len(tokens_a), len(tokens_b))
            if common > 0:
                return 0.3 + 0.3 * (common / total)
        return 0.0

    @staticmethod
    def _compute_hybrid_score(
        file_score: float,
        func_score: float,
        cosine_score: float,
        has_file: bool,
        has_func: bool,
    ) -> float:
        """Compute weighted hybrid score, redistributing weights when fields are absent."""
        if has_file and has_func:
            w = HYBRID_WEIGHTS_ALL
            return (
                w['file_name'] * file_score
                + w['function_name'] * func_score
                + w['cosine'] * cosine_score
            )
        if has_file:
            w = HYBRID_WEIGHTS_FILE_ONLY
            return w['file_name'] * file_score + w['cosine'] * cosine_score
        if has_func:
            w = HYBRID_WEIGHTS_FUNC_ONLY
            return w['function_name'] * func_score + w['cosine'] * cosine_score
        return cosine_score
    
    def check_single(
        self,
        issue: AnalyzerIssue,
        exclude_ids: Optional[List[str]] = None
    ) -> Optional[DuplicateMatch]:
        """
        Check if a single issue is a duplicate of any existing issue.
        Uses hybrid scoring with file/function as strong signals.
        """
        if not self.vector_store:
            return None

        exclude = list(exclude_ids) if exclude_ids else []
        exclude.append(issue.id)

        query_embedding = self.embedding_generator.generate(
            issue.to_embedding_text()
        )

        results = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=N_CANDIDATES,
            exclude_ids=exclude
        )

        if not results:
            return None

        issue_file = issue.file_path or ''
        issue_func = issue.function_name or ''
        best_match: Optional[DuplicateMatch] = None
        best_score = 0.0

        for doc_id, metadata, distance in results:
            candidate_file = metadata.get('file_path', '')
            candidate_func = metadata.get('function_name', '')
            cosine_sim = cosine_distance_to_similarity(distance)

            has_file = bool(issue_file and candidate_file)
            has_func = bool(issue_func and candidate_func)

            if has_file:
                file_score = self._compute_file_name_score(issue_file, candidate_file)
                if file_score == 0.0:
                    continue
            else:
                file_score = 0.0

            func_score = (
                self._compute_function_name_score(issue_func, candidate_func)
                if has_func else 0.0
            )

            hybrid = self._compute_hybrid_score(
                file_score, func_score, cosine_sim, has_file, has_func
            )

            if hybrid >= self.threshold and hybrid > best_score:
                best_score = hybrid
                best_match = DuplicateMatch(
                    original_id=doc_id,
                    duplicate_id=issue.id,
                    similarity_score=hybrid,
                    match_type="semantic"
                )

        return best_match
    
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
