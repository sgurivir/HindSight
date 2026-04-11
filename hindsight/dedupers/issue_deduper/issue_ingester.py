"""
Issue ingester for the Issue Deduper module.

This module provides functionality to ingest analyzer issues into
a repository-specific vector store for deduplication.
"""

import logging
from pathlib import Path
from typing import List, Tuple

from ..common.vector_store import VectorStore
from ..common.embeddings import EmbeddingGenerator
from ..common.issue_models import AnalyzerIssue
from .config import COLLECTION_NAME, DEFAULT_BATCH_SIZE

logger = logging.getLogger("hindsight.dedupers.issue_deduper.ingester")


class IssueIngester:
    """
    Ingests analyzer issues into a repository-specific vector store.
    
    The vector store is persisted to the repository's artifacts directory,
    allowing for inspection and debugging after analysis completes.
    
    Attributes:
        db_path: Path to the vector database directory.
        vector_store: The VectorStore instance.
        embedding_generator: The EmbeddingGenerator instance.
        batch_size: Batch size for embedding generation.
    """
    
    def __init__(
        self,
        db_path: Path,
        batch_size: int = DEFAULT_BATCH_SIZE
    ):
        """
        Initialize the ingester with repository-specific vector store.
        
        Args:
            db_path: Path to the vector database directory
                    (e.g., ~/llm_artifacts/corelocation/issue_deduper_db/)
            batch_size: Batch size for embedding generation.
        """
        self.db_path = Path(db_path)
        self.batch_size = batch_size
        
        # Initialize vector store with persistent storage
        self.vector_store = VectorStore(
            db_path=self.db_path,
            collection_name=COLLECTION_NAME,
            ephemeral=False  # Persistent storage in artifacts directory
        )
        
        # Get singleton embedding generator
        self.embedding_generator = EmbeddingGenerator.get_instance()
        
        logger.info(f"IssueIngester initialized with DB at: {self.db_path}")
    
    def ingest(self, issues: List[AnalyzerIssue]) -> Tuple[int, int]:
        """
        Ingest issues into the vector store.
        
        Args:
            issues: List of AnalyzerIssue objects.
        
        Returns:
            Tuple of (ingested_count, skipped_count).
        """
        if not issues:
            logger.debug("No issues to ingest")
            return 0, 0
        
        logger.info(f"Ingesting {len(issues)} issues into vector store")
        
        # Generate embeddings in batch
        texts = [issue.to_embedding_text() for issue in issues]
        embeddings = self.embedding_generator.generate_batch(texts, self.batch_size)
        
        # Prepare documents for batch insertion
        documents = []
        for issue, embedding in zip(issues, embeddings):
            documents.append({
                'id': issue.id,
                'text': issue.to_embedding_text(),
                'embedding': embedding,
                'metadata': issue.to_metadata()
            })
        
        # Add to vector store
        ingested, skipped = self.vector_store.add_documents_batch(documents, embeddings)
        
        logger.info(f"Ingestion complete: {ingested} added, {skipped} skipped")
        return ingested, skipped
    
    def ingest_single(self, issue: AnalyzerIssue) -> bool:
        """
        Ingest a single issue into the vector store.
        
        Args:
            issue: The AnalyzerIssue to ingest.
        
        Returns:
            True if the issue was added, False if skipped.
        """
        text = issue.to_embedding_text()
        embedding = self.embedding_generator.generate(text)
        
        return self.vector_store.add_document(
            doc_id=issue.id,
            text=text,
            embedding=embedding,
            metadata=issue.to_metadata()
        )
    
    def get_count(self) -> int:
        """
        Get the number of issues in the vector store.
        
        Returns:
            The number of documents in the collection.
        """
        return self.vector_store.count()
    
    def close(self) -> None:
        """Close the vector store connection."""
        if self.vector_store:
            self.vector_store.close()
            logger.debug("Vector store connection closed")
