"""
Hybrid Matcher module for multi-signal deduplication.

This module provides the HybridMatcher class that combines:
- File path matching
- Function name matching
- Cosine similarity (semantic matching)

Into a single hybrid score for more accurate duplicate detection.
"""

import logging
from typing import List, Dict, Optional, Any

from .issue import Issue, HybridMatch, IssueEntry
from .matching import (
    FilePathMatcher,
    FunctionNameMatcher,
    FilePathExtractor,
    FunctionNameExtractor,
)
from ..vector_db.store import VectorStore
from ..vector_db.embeddings import EmbeddingGenerator
from ..config import DEFAULT_THRESHOLD, DEFAULT_TOP_K

logger = logging.getLogger("issue_tracking_deduper.hybrid_matcher")


# Default weights for hybrid scoring
DEFAULT_HYBRID_WEIGHTS = {
    'file_path': 0.40,
    'function_name': 0.30,
    'cosine_similarity': 0.30,
}

# Default hybrid threshold (lower than pure cosine since we have more signals)
DEFAULT_HYBRID_THRESHOLD = 0.50


class HybridMatcher:
    """
    Combines multiple signals for hybrid deduplication scoring.
    
    This matcher uses three signals:
    1. File path matching - strongest signal for same-file bugs
    2. Function name matching - strong signal for same-function bugs
    3. Cosine similarity - semantic similarity for description matching
    
    The signals are combined using configurable weights to produce
    a hybrid score that is more accurate than any single signal alone.
    
    Attributes:
        vector_store: The VectorStore instance for querying issues.
        embedding_generator: The EmbeddingGenerator for creating embeddings.
        weights: Dictionary of weights for each signal.
        threshold: Minimum hybrid score for a match.
        top_k: Maximum number of matches to return per issue.
    """
    
    def __init__(
        self,
        vector_store: VectorStore,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        weights: Optional[Dict[str, float]] = None,
        threshold: float = DEFAULT_HYBRID_THRESHOLD,
        top_k: int = DEFAULT_TOP_K
    ):
        """
        Initialize the HybridMatcher.
        
        Args:
            vector_store: The VectorStore instance for querying issues.
            embedding_generator: Optional EmbeddingGenerator instance.
                               If not provided, a new one will be created.
            weights: Optional dictionary of weights for each signal.
                    Keys: 'file_path', 'function_name', 'cosine_similarity'
                    Values should sum to 1.0.
            threshold: Minimum hybrid score (0.0-1.0) for a match.
            top_k: Maximum number of matches to return per issue.
        """
        self.vector_store = vector_store
        self.embedding_generator = embedding_generator or EmbeddingGenerator()
        self.weights = weights or DEFAULT_HYBRID_WEIGHTS.copy()
        self.threshold = threshold
        self.top_k = top_k
        
        # Validate weights
        self._validate_weights()
        
        logger.debug(
            f"HybridMatcher initialized with weights={self.weights}, "
            f"threshold={threshold}, top_k={top_k}"
        )
    
    def _validate_weights(self) -> None:
        """Validate that weights are properly configured."""
        required_keys = {'file_path', 'function_name', 'cosine_similarity'}
        if not required_keys.issubset(self.weights.keys()):
            missing = required_keys - set(self.weights.keys())
            raise ValueError(f"Missing weight keys: {missing}")
        
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(
                f"Weights sum to {total}, not 1.0. Normalizing..."
            )
            for key in self.weights:
                self.weights[key] /= total
    
    def find_matches(self, issue: Issue) -> List[HybridMatch]:
        """
        Find potential duplicate issues using hybrid scoring.
        
        Strategy:
        1. Query vector DB for top-N semantically similar issues
        2. For each candidate, compute file_path and function_name scores
        3. Combine scores using weighted average
        4. Filter by threshold and sort by hybrid score
        
        Args:
            issue: The Issue to find matches for.
        
        Returns:
            List of HybridMatch objects, sorted by hybrid score (highest first).
            Only matches above the threshold are returned.
        """
        logger.debug(f"Finding hybrid matches for issue: {issue.id}")
        
        # Step 1: Get semantic candidates (cast wider net)
        query_text = issue.to_embedding_text()
        query_embedding = self.embedding_generator.generate(query_text)
        
        # Get more candidates than top_k to allow re-ranking
        candidates = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=self.top_k * 3  # Get 3x candidates for re-ranking
        )
        
        logger.debug(f"Got {len(candidates)} semantic candidates")
        
        # Get documents (descriptions) for the results
        result_ids = [doc_id for doc_id, _, _ in candidates]
        documents = self._get_documents(result_ids)
        
        # Step 2: Compute hybrid scores for each candidate
        matches = []
        for doc_id, metadata, distance in candidates:
            # Get issue description
            issue_description = documents.get(doc_id, '')
            
            # Compute individual scores
            cosine_score = self._distance_to_similarity(distance)
            file_score = self._compute_file_score(issue, metadata, issue_description)
            func_score = self._compute_function_score(issue, metadata, issue_description)
            
            # Compute weighted hybrid score
            hybrid_score = (
                self.weights['file_path'] * file_score +
                self.weights['function_name'] * func_score +
                self.weights['cosine_similarity'] * cosine_score
            )
            
            # Build match reasons
            reasons = self._build_match_reasons(
                file_score, func_score, cosine_score, issue, metadata
            )
            
            match = HybridMatch(
                issue_id=metadata.get('issue_id', ''),
                issue_title=metadata.get('title', ''),
                issue_url=f"rdar://{metadata.get('issue_id', '')}",
                issue_description=issue_description,
                file_path_score=file_score,
                function_name_score=func_score,
                cosine_similarity_score=cosine_score,
                hybrid_score=hybrid_score,
                match_reasons=reasons
            )
            matches.append(match)
            
            logger.debug(
                f"Candidate {match.issue_url}: "
                f"file={file_score:.2f}, func={func_score:.2f}, "
                f"cosine={cosine_score:.2f}, hybrid={hybrid_score:.2f}"
            )
        
        # Step 3: Filter and sort
        matches = [m for m in matches if m.hybrid_score >= self.threshold]
        matches.sort(key=lambda m: m.hybrid_score, reverse=True)
        
        # Limit to top_k
        matches = matches[:self.top_k]
        
        logger.info(
            f"Found {len(matches)} hybrid matches for issue {issue.id} "
            f"(above threshold {self.threshold})"
        )
        
        return matches
    
    def _get_documents(self, doc_ids: List[str]) -> Dict[str, str]:
        """
        Fetch documents (descriptions) from the vector store.
        
        Args:
            doc_ids: List of document IDs to fetch.
        
        Returns:
            Dictionary mapping document IDs to their descriptions.
        """
        documents = {}
        if not doc_ids:
            return documents
        
        try:
            doc_results = self.vector_store.collection.get(
                ids=doc_ids,
                include=['documents']
            )
            if doc_results and doc_results.get('ids') and doc_results.get('documents'):
                for i, doc_id in enumerate(doc_results['ids']):
                    if i < len(doc_results['documents']):
                        documents[doc_id] = doc_results['documents'][i] or ''
        except Exception as e:
            logger.debug(f"Could not fetch documents: {e}")
        
        return documents
    
    def _distance_to_similarity(self, distance: float) -> float:
        """
        Convert cosine distance to similarity score.
        
        Cosine distance: 0 = identical, 2 = opposite
        Similarity: 1 = identical, 0 = orthogonal, -1 = opposite
        
        Args:
            distance: Cosine distance from vector store.
        
        Returns:
            Similarity score between 0.0 and 1.0.
        """
        return max(0.0, 1 - (distance / 2))
    
    def _compute_file_score(
        self,
        issue: Issue,
        metadata: Dict[str, Any],
        issue_description: str
    ) -> float:
        """
        Compute file path similarity score.
        
        Args:
            issue: The issue being matched.
            metadata: Issue metadata from vector store.
            issue_description: Full issue description text.
        
        Returns:
            File path similarity score between 0.0 and 1.0.
        """
        if not issue.file_path:
            return 0.0
        
        # Collect all file paths from the stored issue
        stored_paths = []
        
        # Check metadata file_path first
        stored_file = metadata.get('file_path', '')
        if stored_file:
            stored_paths.append(stored_file)
        
        # Check extracted_files from metadata
        extracted_files_str = metadata.get('extracted_files', '')
        if extracted_files_str:
            stored_paths.extend([f for f in extracted_files_str.split(',') if f])
        
        # Extract file paths from description
        extracted_paths = FilePathExtractor.extract_file_paths(issue_description)
        stored_paths.extend(extracted_paths)
        
        if not stored_paths:
            return 0.0
        
        return FilePathMatcher.compute_best_score(issue.file_path, stored_paths)
    
    def _compute_function_score(
        self,
        issue: Issue,
        metadata: Dict[str, Any],
        issue_description: str
    ) -> float:
        """
        Compute function name similarity score.
        
        Args:
            issue: The issue being matched.
            metadata: Issue metadata from vector store.
            issue_description: Full issue description text.
        
        Returns:
            Function name similarity score between 0.0 and 1.0.
        """
        if not issue.function_name:
            return 0.0
        
        # Collect all function names from the stored issue
        stored_funcs = []
        
        # Check metadata function_name first
        stored_func = metadata.get('function_name', '')
        if stored_func:
            stored_funcs.append(stored_func)
        
        # Check extracted_functions from metadata
        extracted_funcs_str = metadata.get('extracted_functions', '')
        if extracted_funcs_str:
            stored_funcs.extend([f for f in extracted_funcs_str.split(',') if f])
        
        # Extract function names from description
        extracted_funcs = FunctionNameExtractor.extract_function_names(issue_description)
        stored_funcs.extend(extracted_funcs)
        
        if not stored_funcs:
            return 0.0
        
        return FunctionNameMatcher.compute_best_score(issue.function_name, stored_funcs)
    
    def _build_match_reasons(
        self,
        file_score: float,
        func_score: float,
        cosine_score: float,
        issue: Issue,
        metadata: Dict[str, Any]
    ) -> List[str]:
        """
        Build human-readable match reasons.
        
        Args:
            file_score: File path similarity score.
            func_score: Function name similarity score.
            cosine_score: Cosine similarity score.
            issue: The issue being matched.
            metadata: Issue metadata from vector store.
        
        Returns:
            List of human-readable match reason strings.
        """
        reasons = []
        
        if file_score >= 0.8:
            reasons.append(f"📁 Same file: {issue.file_name}")
        elif file_score >= 0.4:
            reasons.append(f"📁 Similar file path ({file_score:.0%})")
        
        if func_score >= 0.8:
            reasons.append(f"🔧 Same function: {issue.function_name}")
        elif func_score >= 0.4:
            reasons.append(f"🔧 Similar function ({func_score:.0%})")
        
        if cosine_score >= 0.8:
            reasons.append(f"📝 High semantic similarity ({cosine_score:.0%})")
        elif cosine_score >= 0.6:
            reasons.append(f"📝 Moderate semantic similarity ({cosine_score:.0%})")
        
        # Add component info if available
        component = metadata.get('component', '')
        if component:
            reasons.append(f"🏷️ Component: {component}")
        
        return reasons


class FileClusterIndex:
    """
    Pre-computed index of issues grouped by file name.
    
    This allows O(1) lookup of issues affecting the same file,
    avoiding the need to scan all issues for file matching.
    
    This is an optional optimization for large issue databases.
    """
    
    def __init__(self, vector_store: VectorStore):
        """
        Initialize the FileClusterIndex.
        
        Args:
            vector_store: The VectorStore to build the index from.
        """
        self.vector_store = vector_store
        self._file_to_issues: Dict[str, List[str]] = {}
        self._func_to_issues: Dict[str, List[str]] = {}
        self._built = False
    
    def build_index(self) -> None:
        """
        Build the cluster index from all issues in the store.
        
        This scans all issues and indexes them by file name and function name.
        """
        logger.info("Building file cluster index...")
        
        # Get all issues from the store
        all_issues = self._get_all_issues()
        
        for issue_id, metadata, description in all_issues:
            # Index by file name
            file_paths = FilePathExtractor.extract_file_paths(description)
            
            # Also include file_path from metadata
            if metadata.get('file_path'):
                file_paths.append(metadata['file_path'])
            
            # Also include extracted_files from metadata
            extracted_files_str = metadata.get('extracted_files', '')
            if extracted_files_str:
                file_paths.extend([f for f in extracted_files_str.split(',') if f])
            
            for path in file_paths:
                from pathlib import Path
                file_name = Path(path).name
                if file_name not in self._file_to_issues:
                    self._file_to_issues[file_name] = []
                if issue_id not in self._file_to_issues[file_name]:
                    self._file_to_issues[file_name].append(issue_id)
            
            # Index by function name
            func_names = FunctionNameExtractor.extract_function_names(description)
            
            # Also include function_name from metadata
            if metadata.get('function_name'):
                func_names.append(metadata['function_name'])
            
            # Also include extracted_functions from metadata
            extracted_funcs_str = metadata.get('extracted_functions', '')
            if extracted_funcs_str:
                func_names.extend([f for f in extracted_funcs_str.split(',') if f])
            
            for func in func_names:
                norm_func = FunctionNameMatcher.normalize(func)
                if norm_func not in self._func_to_issues:
                    self._func_to_issues[norm_func] = []
                if issue_id not in self._func_to_issues[norm_func]:
                    self._func_to_issues[norm_func].append(issue_id)
        
        self._built = True
        logger.info(
            f"Index built: {len(self._file_to_issues)} files, "
            f"{len(self._func_to_issues)} functions"
        )
    
    def _get_all_issues(self) -> List[tuple]:
        """
        Get all issues from the vector store.
        
        Returns:
            List of (issue_id, metadata, description) tuples.
        """
        issues = []
        try:
            # Get all items from the collection
            results = self.vector_store.collection.get(
                include=['metadatas', 'documents']
            )
            
            if results and results.get('ids'):
                for i, doc_id in enumerate(results['ids']):
                    metadata = results['metadatas'][i] if results.get('metadatas') else {}
                    description = results['documents'][i] if results.get('documents') else ''
                    issues.append((doc_id, metadata, description))
        except Exception as e:
            logger.error(f"Error getting all issues: {e}")
        
        return issues
    
    def get_issues_by_file(self, file_name: str) -> List[str]:
        """
        Get issue IDs that mention the given file name.
        
        Args:
            file_name: The file name to search for.
        
        Returns:
            List of issue IDs that mention this file.
        """
        if not self._built:
            logger.warning("Index not built. Call build_index() first.")
            return []
        return self._file_to_issues.get(file_name, [])
    
    def get_issues_by_function(self, func_name: str) -> List[str]:
        """
        Get issue IDs that mention the given function name.
        
        Args:
            func_name: The function name to search for.
        
        Returns:
            List of issue IDs that mention this function.
        """
        if not self._built:
            logger.warning("Index not built. Call build_index() first.")
            return []
        norm_func = FunctionNameMatcher.normalize(func_name)
        return self._func_to_issues.get(norm_func, [])
    
    @property
    def is_built(self) -> bool:
        """Check if the index has been built."""
        return self._built
