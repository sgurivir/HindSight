"""
Issue ingestion logic for the vector database.

This module provides functionality to ingest issue markdown files
into the vector database, including parsing, embedding generation,
and storage.

Enhanced with file path and function name extraction for hybrid matching.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from tqdm import tqdm

from ..deduper.issue import IssueEntry
from ..deduper.matching import FilePathExtractor, FunctionNameExtractor
from ..parsers.issue_parser import IssueParser
from .embeddings import EmbeddingGenerator
from .store import VectorStore

logger = logging.getLogger("issue_tracking_deduper.ingestion")


class IssueIngester:
    """
    Ingests issue markdown files into the vector database.
    
    This class coordinates the parsing, embedding generation, and storage
    of issue entries for deduplication matching.
    
    Attributes:
        vector_store: The VectorStore instance for storage.
        embedding_generator: The EmbeddingGenerator for creating embeddings.
        parser: The IssueParser for parsing markdown files.
    """
    
    def __init__(
        self,
        db_path: Optional[Path] = None,
        embedding_model: Optional[str] = None
    ):
        """
        Initialize the issue ingester.
        
        Args:
            db_path: Path to the vector database.
            embedding_model: Name of the embedding model to use.
        """
        self.vector_store = VectorStore(db_path=db_path)
        self.embedding_generator = EmbeddingGenerator(model_name=embedding_model)
        self.parser = IssueParser()
        
        logger.debug("IssueIngester initialized")
    
    def ingest_file(self, file_path: Path) -> bool:
        """
        Ingest a single issue file.
        
        Args:
            file_path: Path to the issue markdown file.
        
        Returns:
            True if the issue was ingested, False if skipped or failed.
        """
        # Parse the file
        issue = self.parser.parse_file(file_path)
        if not issue:
            logger.warning(f"Failed to parse: {file_path}")
            return False
        
        # Extract file paths and function names from description
        issue = self._enhance_issue_metadata(issue)
        
        # Generate embedding
        embedding_text = issue.to_embedding_text()
        embedding = self.embedding_generator.generate(embedding_text)
        
        # Store in vector database
        added = self.vector_store.add_issue(issue, embedding)
        
        if added:
            logger.debug(f"Ingested: rdar://{issue.issue_id}")
        else:
            logger.debug(f"Skipped (unchanged): rdar://{issue.issue_id}")
        
        return added
    
    def _enhance_issue_metadata(self, issue: IssueEntry) -> IssueEntry:
        """
        Extract and add file paths and function names to issue metadata.
        
        This enhances the issue entry with extracted file paths and function
        names from the description text, enabling hybrid matching.
        
        Args:
            issue: The IssueEntry to enhance.
        
        Returns:
            The enhanced IssueEntry with extracted metadata.
        """
        # Extract file paths from description
        extracted_files = FilePathExtractor.extract_file_paths(issue.description)
        if extracted_files:
            issue.extracted_files = extracted_files
            # Set primary file_path if not already set
            if not issue.file_path and extracted_files:
                issue.file_path = extracted_files[0]
        
        # Extract function names from description
        extracted_functions = FunctionNameExtractor.extract_function_names(issue.description)
        if extracted_functions:
            issue.extracted_functions = extracted_functions
            # Set primary function_name if not already set
            if not issue.function_name and extracted_functions:
                issue.function_name = extracted_functions[0]
        
        logger.debug(
            f"Enhanced issue {issue.issue_id}: "
            f"{len(issue.extracted_files)} files, "
            f"{len(issue.extracted_functions)} functions"
        )
        
        return issue
    
    def ingest_directory(
        self,
        directory: Path,
        recursive: bool = True,
        batch_size: int = 32,
        show_progress: bool = True
    ) -> Tuple[int, int, int]:
        """
        Ingest all issue files from a directory.
        
        Args:
            directory: Path to the directory containing issue files.
            recursive: Whether to scan subdirectories.
            batch_size: Number of issues to process in each batch.
            show_progress: Whether to show a progress bar.
        
        Returns:
            Tuple of (total_files, added_count, skipped_count).
        """
        logger.info(f"Scanning directory: {directory}")
        
        # Parse all files
        issues = self.parser.parse_directory(directory, recursive)
        
        if not issues:
            logger.warning(f"No issue files found in: {directory}")
            return 0, 0, 0
        
        logger.info(f"Found {len(issues)} issue files to ingest")
        
        # Process in batches
        total_added = 0
        total_skipped = 0
        
        # Create batches
        batches = [issues[i:i + batch_size] for i in range(0, len(issues), batch_size)]
        
        # Process each batch
        iterator = tqdm(batches, desc="Ingesting issues", disable=not show_progress)
        
        for batch in iterator:
            # Enhance each issue with extracted metadata
            enhanced_batch = [self._enhance_issue_metadata(r) for r in batch]
            
            # Generate embeddings for batch
            embedding_texts = [r.to_embedding_text() for r in enhanced_batch]
            embeddings = self.embedding_generator.generate_batch(embedding_texts, batch_size)
            
            # Store batch
            added, skipped = self.vector_store.add_issues_batch(enhanced_batch, embeddings)
            total_added += added
            total_skipped += skipped
            
            # Update progress bar description
            if show_progress:
                iterator.set_postfix(added=total_added, skipped=total_skipped)
        
        logger.info(f"Ingestion complete: {total_added} added, {total_skipped} skipped")
        return len(issues), total_added, total_skipped
    
    def get_stats(self) -> dict:
        """
        Get statistics about the vector database.
        
        Returns:
            Dictionary with database statistics.
        """
        return {
            'total_issues': self.vector_store.count(),
            'db_path': str(self.vector_store.db_path),
            'embedding_model': self.embedding_generator.model_name,
        }
    
    def close(self):
        """Close the ingester and release resources."""
        self.vector_store.close()
        logger.debug("IssueIngester closed")


def ingest_issues(
    issue_dir: Path,
    db_path: Optional[Path] = None,
    recursive: bool = True,
    batch_size: int = 32,
    show_progress: bool = True
) -> Tuple[int, int, int]:
    """
    Convenience function to ingest issues from a directory.
    
    Args:
        issue_dir: Path to the directory containing issue files.
        db_path: Path to the vector database.
        recursive: Whether to scan subdirectories.
        batch_size: Number of issues to process in each batch.
        show_progress: Whether to show a progress bar.
    
    Returns:
        Tuple of (total_files, added_count, skipped_count).
    """
    ingester = IssueIngester(db_path=db_path)
    try:
        return ingester.ingest_directory(
            issue_dir,
            recursive=recursive,
            batch_size=batch_size,
            show_progress=show_progress
        )
    finally:
        ingester.close()
