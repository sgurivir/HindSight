"""
Matcher module for finding duplicate issues.

This module provides the IssueMatcher class that compares issues from
HTML reports against issues stored in the vector database to find
potential duplicates.
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional, Any

from .issue import Issue, DedupeMatch
from ..vector_db.store import VectorStore
from ..vector_db.embeddings import EmbeddingGenerator
from ..parsers import ParserRegistry, get_default_registry
from ..config import DEFAULT_THRESHOLD, DEFAULT_TOP_K

logger = logging.getLogger("issue_tracking_deduper.matcher")


class IssueMatcher:
    """
    Matches issues against the issue database to find potential duplicates.
    
    This class uses semantic similarity via embeddings to find issues
    that are similar to issues from HTML reports.
    
    Attributes:
        vector_store: The VectorStore instance for querying issues.
        embedding_generator: The EmbeddingGenerator for creating embeddings.
        threshold: Minimum similarity score (0.0-1.0) for a match.
        top_k: Maximum number of matches to return per issue.
    """
    
    def __init__(
        self,
        vector_store: VectorStore,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        threshold: float = DEFAULT_THRESHOLD,
        top_k: int = DEFAULT_TOP_K
    ):
        """
        Initialize the IssueMatcher.
        
        Args:
            vector_store: The VectorStore instance for querying issues.
            embedding_generator: Optional EmbeddingGenerator instance.
                               If not provided, a new one will be created.
            threshold: Minimum similarity score (0.0-1.0) for a match.
                      Default is from config.
            top_k: Maximum number of matches to return per issue.
                  Default is from config.
        """
        self.vector_store = vector_store
        self.embedding_generator = embedding_generator or EmbeddingGenerator()
        self.threshold = threshold
        self.top_k = top_k
        
        logger.debug(
            f"IssueMatcher initialized with threshold={threshold}, top_k={top_k}"
        )
    
    def find_matches(self, issue: Issue) -> List[DedupeMatch]:
        """
        Find potential duplicate issues for an issue.
        
        Args:
            issue: The Issue to find matches for.
        
        Returns:
            List of DedupeMatch objects, sorted by similarity (highest first).
            Only matches above the threshold are returned.
        """
        # Generate embedding text from the issue
        query_text = issue.to_embedding_text()
        logger.debug(f"Finding matches for issue: {issue.id}")
        logger.debug(f"Query text length: {len(query_text)} chars")
        
        # Generate embedding
        query_embedding = self.embedding_generator.generate(query_text)
        
        # Query vector database - include documents to get descriptions
        results = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=self.top_k
        )
        
        # Get documents (descriptions) for the results
        result_ids = [doc_id for doc_id, _, _ in results]
        documents = {}
        if result_ids:
            try:
                doc_results = self.vector_store.collection.get(
                    ids=result_ids,
                    include=['documents']
                )
                if doc_results and doc_results.get('ids') and doc_results.get('documents'):
                    for i, doc_id in enumerate(doc_results['ids']):
                        if i < len(doc_results['documents']):
                            documents[doc_id] = doc_results['documents'][i] or ''
            except Exception as e:
                logger.debug(f"Could not fetch documents: {e}")
        
        # Convert results to DedupeMatch objects
        matches = []
        for doc_id, metadata, distance in results:
            # Convert cosine distance to similarity score
            # Cosine distance: 0 = identical, 2 = opposite
            # Similarity: 1 = identical, 0 = orthogonal, -1 = opposite
            similarity = 1 - (distance / 2)
            
            # Filter by threshold
            if similarity < self.threshold:
                logger.debug(
                    f"Skipping {doc_id} - similarity {similarity:.3f} below threshold {self.threshold}"
                )
                continue
            
            # Get description from documents
            description = documents.get(doc_id, '')
            
            # Create match object
            match = DedupeMatch(
                issue_id=metadata.get('issue_id', ''),
                issue_title=metadata.get('title', ''),
                similarity_score=similarity,
                issue_url=f"rdar://{metadata.get('issue_id', '')}",
                match_reason=self._generate_match_reason(similarity, metadata),
                issue_description=description
            )
            matches.append(match)
            
            logger.debug(
                f"Found match: {match.issue_url} with similarity {similarity:.3f}"
            )
        
        # Sort by similarity (highest first)
        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        
        logger.info(
            f"Found {len(matches)} matches for issue {issue.id} "
            f"(above threshold {self.threshold})"
        )
        
        return matches
    
    def _generate_match_reason(
        self,
        similarity: float,
        metadata: Dict[str, Any]
    ) -> str:
        """
        Generate a human-readable explanation for why this is a match.
        
        Args:
            similarity: The similarity score (0.0-1.0).
            metadata: The issue metadata from the vector store.
        
        Returns:
            A string explaining the match.
        """
        # Base reason from similarity score
        if similarity > 0.9:
            reason = "Very high similarity - likely duplicate"
        elif similarity > 0.8:
            reason = "High similarity - probable duplicate"
        elif similarity > 0.7:
            reason = "Moderate similarity - possible duplicate"
        else:
            reason = "Low similarity - may be related"
        
        # Add component info if available
        component = metadata.get('component', '')
        if component:
            reason += f" (Component: {component})"
        
        return reason


class ReportDeduper:
    """
    High-level class for deduplicating an entire HTML report.
    
    This class orchestrates the parsing of HTML reports and matching
    of all issues against the issue database.
    """
    
    def __init__(
        self,
        vector_store: VectorStore,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        threshold: float = DEFAULT_THRESHOLD,
        top_k: int = DEFAULT_TOP_K,
        parser_registry: Optional[ParserRegistry] = None
    ):
        """
        Initialize the ReportDeduper.
        
        Args:
            vector_store: The VectorStore instance for querying issues.
            embedding_generator: Optional EmbeddingGenerator instance.
            threshold: Minimum similarity score for matches.
            top_k: Maximum number of matches per issue.
            parser_registry: Optional ParserRegistry for finding parsers.
                           Defaults to the default registry.
        """
        self.vector_store = vector_store
        self.embedding_generator = embedding_generator or EmbeddingGenerator()
        self.threshold = threshold
        self.top_k = top_k
        self.parser_registry = parser_registry or get_default_registry()
        
        self.matcher = IssueMatcher(
            vector_store=vector_store,
            embedding_generator=self.embedding_generator,
            threshold=threshold,
            top_k=top_k
        )
    
    def dedupe_report(
        self,
        report_path: Path
    ) -> Dict[str, Dict[str, Any]]:
        """
        Process an entire report and find matches for all issues.
        
        Args:
            report_path: Path to the HTML report file.
        
        Returns:
            Dictionary mapping issue IDs to their match results:
            {
                "issue_id": {
                    "issue": Issue,
                    "matches": List[DedupeMatch]
                }
            }
        
        Raises:
            ValueError: If no parser is available for the report format.
        """
        report_path = Path(report_path)
        logger.info(f"Deduplicating report: {report_path}")
        
        # Find appropriate parser
        parser = self.parser_registry.get_parser(report_path)
        if not parser:
            raise ValueError(
                f"No parser available for report: {report_path}\n"
                f"Supported formats: {self.parser_registry.get_supported_formats()}"
            )
        
        logger.info(f"Using parser: {parser.get_format_name()}")
        
        # Parse the report
        issues = parser.parse(report_path)
        logger.info(f"Parsed {len(issues)} issues from report")
        
        # Find matches for each issue
        results = {}
        for issue in issues:
            matches = self.matcher.find_matches(issue)
            results[issue.id] = {
                "issue": issue,
                "matches": matches
            }
        
        # Log summary
        issues_with_matches = sum(
            1 for r in results.values() if r["matches"]
        )
        total_matches = sum(
            len(r["matches"]) for r in results.values()
        )
        
        logger.info(
            f"Deduplication complete: {issues_with_matches}/{len(issues)} issues "
            f"have potential duplicates ({total_matches} total matches)"
        )
        
        return results
    
    def get_summary(
        self,
        results: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate a summary of deduplication results.
        
        Args:
            results: The results from dedupe_report().
        
        Returns:
            Dictionary containing summary statistics.
        """
        total_issues = len(results)
        issues_with_matches = sum(
            1 for r in results.values() if r["matches"]
        )
        total_matches = sum(
            len(r["matches"]) for r in results.values()
        )
        
        # Count by confidence level
        high_confidence = 0
        moderate_confidence = 0
        low_confidence = 0
        
        for result in results.values():
            for match in result["matches"]:
                if match.confidence_level == "high":
                    high_confidence += 1
                elif match.confidence_level == "moderate":
                    moderate_confidence += 1
                else:
                    low_confidence += 1
        
        return {
            "total_issues": total_issues,
            "issues_with_matches": issues_with_matches,
            "issues_without_matches": total_issues - issues_with_matches,
            "total_matches": total_matches,
            "high_confidence_matches": high_confidence,
            "moderate_confidence_matches": moderate_confidence,
            "low_confidence_matches": low_confidence,
            "threshold": self.threshold,
            "top_k": self.top_k,
        }


def dedupe_report(
    report_path: Path,
    vector_store: VectorStore,
    embedding_generator: Optional[EmbeddingGenerator] = None,
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int = DEFAULT_TOP_K
) -> Dict[str, Dict[str, Any]]:
    """
    Convenience function to deduplicate a report.
    
    This is a shortcut for creating a ReportDeduper and calling dedupe_report().
    
    Args:
        report_path: Path to the HTML report file.
        vector_store: The VectorStore instance for querying issues.
        embedding_generator: Optional EmbeddingGenerator instance.
        threshold: Minimum similarity score for matches.
        top_k: Maximum number of matches per issue.
    
    Returns:
        Dictionary mapping issue IDs to their match results.
    """
    deduper = ReportDeduper(
        vector_store=vector_store,
        embedding_generator=embedding_generator,
        threshold=threshold,
        top_k=top_k
    )
    return deduper.dedupe_report(report_path)
