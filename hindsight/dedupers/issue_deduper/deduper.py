"""
Main deduplication orchestrator for the Issue Deduper module.

This module provides the main IssueDeduper class that orchestrates
the deduplication process for analyzer issues.
"""

import logging
import shutil
import sys
import warnings
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..common.issue_models import AnalyzerIssue, DuplicateMatch
from .config import (
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_BATCH_SIZE,
    VECTOR_DB_SUBDIR,
)

# Filter numpy binary compatibility warnings that can occur with older Python versions
# This is a known issue when sentence-transformers/torch are compiled against
# a different numpy version than what's installed
warnings.filterwarnings(
    "ignore",
    message="numpy.dtype size changed",
    category=RuntimeWarning
)
warnings.filterwarnings(
    "ignore",
    message="numpy.ufunc size changed",
    category=RuntimeWarning
)

from .issue_ingester import IssueIngester
from .duplicate_detector import DuplicateDetector

logger = logging.getLogger("hindsight.dedupers.issue_deduper")


class DeduplicationError(Exception):
    """Base exception for deduplication errors."""
    pass


class EmbeddingGenerationError(DeduplicationError):
    """Error during embedding generation."""
    pass


class VectorStoreError(DeduplicationError):
    """Error with vector store operations."""
    pass


class IssueDeduper:
    """
    Main class for deduplicating analyzer issues.
    
    This class orchestrates the deduplication process:
    1. Wipes existing vector DB to ensure fresh state
    2. Ingests issues into a repository-specific vector store
    3. Detects duplicates using semantic similarity
    4. Returns filtered list with duplicates removed
    
    The vector DB is stored in the repository's artifacts directory:
    ~/llm_artifacts/<repo_name>/issue_deduper_db/
    
    Usage:
        deduper = IssueDeduper(
            artifacts_dir="~/llm_artifacts/corelocation",
            threshold=0.85
        )
        unique_issues = deduper.dedupe(issues)
    
    Attributes:
        artifacts_dir: Path to repository artifacts directory.
        db_path: Path to the vector database directory.
        threshold: Similarity threshold for duplicate detection.
        batch_size: Batch size for embedding generation.
    """
    
    def __init__(
        self,
        artifacts_dir: str,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        batch_size: int = DEFAULT_BATCH_SIZE
    ):
        """
        Initialize the deduper with repository-specific vector DB.
        
        Args:
            artifacts_dir: Path to repository artifacts directory
                          (e.g., ~/llm_artifacts/corelocation/)
            threshold: Similarity threshold for duplicate detection (0.0-1.0).
            batch_size: Batch size for embedding generation.
        """
        self.artifacts_dir = Path(artifacts_dir).expanduser()
        self.db_path = self.artifacts_dir / VECTOR_DB_SUBDIR
        self.threshold = threshold
        self.batch_size = batch_size
        
        # Wipe existing DB before starting (ensures fresh state)
        self._wipe_existing_db()
        
        # Initialize components with the repository-specific DB path
        self.ingester = IssueIngester(
            db_path=self.db_path,
            batch_size=batch_size
        )
        self.detector = DuplicateDetector(
            threshold=threshold,
            vector_store=self.ingester.vector_store
        )
        
        self._stats: Dict[str, Any] = {
            'total_input': 0,
            'duplicates_removed': 0,
            'unique_output': 0,
            'exact_matches': 0,
            'semantic_matches': 0,
            'db_path': str(self.db_path)
        }
        
        logger.info(f"IssueDeduper initialized with artifacts_dir: {self.artifacts_dir}")
        logger.info(f"Vector DB path: {self.db_path}")
        logger.info(f"Threshold: {self.threshold}, Batch size: {self.batch_size}")
    
    def _wipe_existing_db(self) -> None:
        """
        Wipe existing vector database before starting ingestion.
        
        This ensures each deduplication run starts with a fresh state,
        preventing stale data from previous runs from affecting results.
        """
        if self.db_path.exists():
            shutil.rmtree(self.db_path)
            logger.info(f"Wiped existing vector DB at: {self.db_path}")
        
        self.db_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created fresh vector DB directory at: {self.db_path}")
    
    def dedupe(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicate a list of issues.
        
        Args:
            issues: List of issue dictionaries from analyzer.
        
        Returns:
            List of unique issues (duplicates removed).
        
        Raises:
            DeduplicationError: If deduplication fails.
        """
        if not issues:
            logger.info("No issues to deduplicate")
            return []
        
        self._stats['total_input'] = len(issues)
        logger.info(f"Starting deduplication of {len(issues)} issues")
        logger.info(f"Vector DB location: {self.db_path}")
        
        try:
            # Step 1: Convert to AnalyzerIssue objects
            analyzer_issues = [
                AnalyzerIssue.from_analyzer_result(i) for i in issues
            ]
            logger.debug(f"Converted {len(analyzer_issues)} issues to AnalyzerIssue objects")
            
            # Step 2: Ingest into vector store
            ingested, skipped = self.ingester.ingest(analyzer_issues)
            logger.info(f"Ingested {ingested} issues into vector DB, skipped {skipped}")
            
            # Step 3: Detect duplicates
            duplicates = self.detector.find_duplicates(analyzer_issues)
            logger.info(f"Detected {len(duplicates)} duplicate issues")
            
            # Step 4: Filter out duplicates (keep first occurrence)
            duplicate_ids = {d.duplicate_id for d in duplicates}
            unique_issues = [
                issue.to_dict()
                for issue in analyzer_issues
                if issue.id not in duplicate_ids
            ]
            
            # Update stats
            detector_stats = self.detector.get_stats()
            self._stats['duplicates_removed'] = len(duplicate_ids)
            self._stats['unique_output'] = len(unique_issues)
            self._stats['exact_matches'] = detector_stats['exact_matches']
            self._stats['semantic_matches'] = detector_stats['semantic_matches']
            
            logger.info(
                f"Deduplication complete: {self._stats['total_input']} -> "
                f"{self._stats['unique_output']} issues "
                f"({self._stats['duplicates_removed']} duplicates removed: "
                f"{self._stats['exact_matches']} exact, "
                f"{self._stats['semantic_matches']} semantic)"
            )
            
            return unique_issues
            
        except Exception as e:
            logger.error(f"Deduplication failed: {e}")
            raise DeduplicationError(f"Deduplication failed: {e}") from e
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get deduplication statistics.
        
        Returns:
            Dictionary with deduplication statistics.
        """
        return self._stats.copy()
    
    def get_duplicate_report(self) -> List[DuplicateMatch]:
        """
        Get detailed report of detected duplicates.
        
        Returns:
            List of DuplicateMatch objects.
        """
        return self.detector.get_matches()
    
    def get_duplicate_report_dict(self) -> List[Dict[str, Any]]:
        """
        Get detailed report of detected duplicates as dictionaries.
        
        Returns:
            List of dictionaries with duplicate information.
        """
        return [
            {
                'original_id': m.original_id,
                'duplicate_id': m.duplicate_id,
                'similarity_score': m.similarity_score,
                'similarity_percentage': m.similarity_percentage,
                'match_type': m.match_type,
                'confidence_level': m.confidence_level,
            }
            for m in self.detector.get_matches()
        ]
    
    def cleanup(self) -> None:
        """
        Clean up resources.
        
        Note: The vector DB is NOT deleted here - it remains in the artifacts
        directory for debugging/inspection. It will be wiped on the next run.
        """
        if hasattr(self, 'ingester') and self.ingester:
            self.ingester.close()
            logger.debug("Ingester closed")
    
    def __enter__(self) -> 'IssueDeduper':
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - cleanup resources."""
        self.cleanup()


def dedupe_issues(
    issues: List[Dict[str, Any]],
    artifacts_dir: str,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    batch_size: int = DEFAULT_BATCH_SIZE
) -> List[Dict[str, Any]]:
    """
    Convenience function to deduplicate issues.
    
    This is a simple wrapper around IssueDeduper for one-off deduplication.
    
    Args:
        issues: List of issue dictionaries from analyzer.
        artifacts_dir: Path to repository artifacts directory.
        threshold: Similarity threshold for duplicate detection.
        batch_size: Batch size for embedding generation.
    
    Returns:
        List of unique issues (duplicates removed).
    """
    with IssueDeduper(
        artifacts_dir=artifacts_dir,
        threshold=threshold,
        batch_size=batch_size
    ) as deduper:
        return deduper.dedupe(issues)
