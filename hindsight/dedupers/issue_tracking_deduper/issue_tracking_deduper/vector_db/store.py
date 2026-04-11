"""
Vector database store using ChromaDB.

This module provides a wrapper around ChromaDB for storing and querying
issue embeddings for deduplication matching.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from ..config import DEFAULT_DB_PATH, COLLECTION_NAME
from ..deduper.issue import IssueEntry

logger = logging.getLogger("issue_tracking_deduper.store")


class VectorStore:
    """
    ChromaDB-based vector store for issue embeddings.
    
    This class provides methods for:
    - Storing issue entries with their embeddings
    - Querying for similar issues
    - Managing the persistent database
    
    Attributes:
        db_path: Path to the ChromaDB persistent storage directory.
        collection_name: Name of the ChromaDB collection.
    """
    
    def __init__(
        self,
        db_path: Optional[Path] = None,
        collection_name: Optional[str] = None
    ):
        """
        Initialize the vector store.
        
        Args:
            db_path: Path to the ChromaDB persistent storage directory.
                    Defaults to the path specified in config.
            collection_name: Name of the ChromaDB collection.
                           Defaults to the name specified in config.
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.collection_name = collection_name or COLLECTION_NAME
        self._client = None
        self._collection = None
        logger.debug(f"VectorStore initialized with db_path: {self.db_path}")
    
    def _ensure_db_directory(self):
        """Ensure the database directory exists."""
        self.db_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured database directory exists: {self.db_path}")
    
    @property
    def client(self):
        """
        Lazy-load the ChromaDB client.
        
        Returns:
            The ChromaDB PersistentClient instance.
        """
        if self._client is None:
            try:
                import chromadb
            except ImportError:
                raise ImportError(
                    "chromadb is required for vector storage. "
                    "Install it with: pip install chromadb"
                )
            
            self._ensure_db_directory()
            logger.info(f"Initializing ChromaDB client at: {self.db_path}")
            
            # Set environment variables for ChromaDB configuration
            # This avoids Pydantic compatibility issues with Python 3.14+
            import os
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
            
            # Create client without explicit Settings to avoid Pydantic issues
            self._client = chromadb.PersistentClient(
                path=str(self.db_path)
            )
            logger.info("ChromaDB client initialized successfully")
        
        return self._client
    
    @property
    def collection(self):
        """
        Get or create the ChromaDB collection.
        
        Returns:
            The ChromaDB collection instance.
        """
        if self._collection is None:
            logger.info(f"Getting or creating collection: {self.collection_name}")
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}  # Use cosine similarity
            )
            logger.info(f"Collection ready with {self._collection.count()} documents")
        
        return self._collection
    
    def add_issue(
        self,
        issue: IssueEntry,
        embedding: List[float]
    ) -> bool:
        """
        Add an issue entry to the vector store.
        
        Args:
            issue: The IssueEntry to add.
            embedding: The embedding vector for the issue.
        
        Returns:
            True if the issue was added/updated, False if skipped (identical content).
        """
        doc_id = f"rdar_{issue.issue_id}"
        
        # Check if already exists with same content
        try:
            existing = self.collection.get(ids=[doc_id])
            if existing and existing['metadatas'] and len(existing['metadatas']) > 0:
                existing_hash = existing['metadatas'][0].get('content_hash')
                if existing_hash == issue.content_hash:
                    logger.debug(f"Skipping {doc_id} - identical content")
                    return False
        except Exception as e:
            logger.debug(f"Error checking existing document: {e}")
        
        # Prepare metadata
        metadata = issue.to_metadata()
        metadata['ingested_at'] = datetime.utcnow().isoformat() + "Z"
        
        # Upsert (insert or update)
        self.collection.upsert(
            ids=[doc_id],
            documents=[issue.to_embedding_text()],
            metadatas=[metadata],
            embeddings=[embedding]
        )
        
        logger.debug(f"Added/updated issue: {doc_id}")
        return True
    
    def add_issues_batch(
        self,
        issues: List[IssueEntry],
        embeddings: List[List[float]]
    ) -> Tuple[int, int]:
        """
        Add multiple issue entries to the vector store.
        
        Args:
            issues: List of IssueEntry objects to add.
            embeddings: List of embedding vectors (one per issue).
        
        Returns:
            Tuple of (added_count, skipped_count).
        """
        if len(issues) != len(embeddings):
            raise ValueError("Number of issues must match number of embeddings")
        
        if not issues:
            return 0, 0
        
        # Prepare batch data
        ids = []
        documents = []
        metadatas = []
        batch_embeddings = []
        
        # Check for existing documents to skip
        doc_ids = [f"rdar_{r.issue_id}" for r in issues]
        
        try:
            existing = self.collection.get(ids=doc_ids)
            existing_hashes = {}
            if existing and existing['metadatas']:
                for i, doc_id in enumerate(existing['ids']):
                    if i < len(existing['metadatas']) and existing['metadatas'][i]:
                        existing_hashes[doc_id] = existing['metadatas'][i].get('content_hash')
        except Exception as e:
            logger.debug(f"Error checking existing documents: {e}")
            existing_hashes = {}
        
        skipped = 0
        for issue, embedding in zip(issues, embeddings):
            doc_id = f"rdar_{issue.issue_id}"
            
            # Skip if identical content
            if doc_id in existing_hashes and existing_hashes[doc_id] == issue.content_hash:
                skipped += 1
                continue
            
            ids.append(doc_id)
            documents.append(issue.to_embedding_text())
            metadata = issue.to_metadata()
            metadata['ingested_at'] = datetime.utcnow().isoformat() + "Z"
            metadatas.append(metadata)
            batch_embeddings.append(embedding)
        
        # Upsert batch
        if ids:
            self.collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=batch_embeddings
            )
            logger.info(f"Added/updated {len(ids)} issues, skipped {skipped}")
        
        return len(ids), skipped
    
    def query(
        self,
        query_embedding: List[float],
        n_results: int = 5
    ) -> List[Tuple[str, Dict[str, Any], float]]:
        """
        Query for similar issues.
        
        Args:
            query_embedding: The embedding vector to search for.
            n_results: Maximum number of results to return.
        
        Returns:
            List of tuples (doc_id, metadata, distance).
            Distance is cosine distance (0 = identical, 2 = opposite).
        """
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=['metadatas', 'distances', 'documents']
        )
        
        output = []
        if results and results['ids'] and results['ids'][0]:
            for i, doc_id in enumerate(results['ids'][0]):
                metadata = results['metadatas'][0][i] if results['metadatas'] else {}
                distance = results['distances'][0][i] if results['distances'] else 0.0
                output.append((doc_id, metadata, distance))
        
        return output
    
    def query_text(
        self,
        query_text: str,
        embedding_generator,
        n_results: int = 5
    ) -> List[Tuple[str, Dict[str, Any], float]]:
        """
        Query for similar issues using text.
        
        Args:
            query_text: The text to search for.
            embedding_generator: An EmbeddingGenerator instance.
            n_results: Maximum number of results to return.
        
        Returns:
            List of tuples (doc_id, metadata, distance).
        """
        embedding = embedding_generator.generate(query_text)
        return self.query(embedding, n_results)
    
    def get_issue(self, issue_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific issue by ID.
        
        Args:
            issue_id: The issue ID (without 'rdar_' prefix).
        
        Returns:
            Dictionary with issue data, or None if not found.
        """
        doc_id = f"rdar_{issue_id}"
        try:
            result = self.collection.get(ids=[doc_id], include=['metadatas', 'documents'])
            if result and result['ids']:
                return {
                    'id': result['ids'][0],
                    'metadata': result['metadatas'][0] if result['metadatas'] else {},
                    'document': result['documents'][0] if result['documents'] else ""
                }
        except Exception as e:
            logger.error(f"Error getting issue {issue_id}: {e}")
        
        return None
    
    def delete_issue(self, issue_id: str) -> bool:
        """
        Delete an issue from the store.
        
        Args:
            issue_id: The issue ID (without 'rdar_' prefix).
        
        Returns:
            True if deleted, False if not found.
        """
        doc_id = f"rdar_{issue_id}"
        try:
            self.collection.delete(ids=[doc_id])
            logger.debug(f"Deleted issue: {doc_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting issue {issue_id}: {e}")
            return False
    
    def count(self) -> int:
        """
        Get the number of issues in the store.
        
        Returns:
            The number of documents in the collection.
        """
        return self.collection.count()
    
    def reset(self) -> None:
        """
        Reset the collection (delete all documents).
        
        Warning: This will delete all stored issues!
        """
        logger.warning("Resetting vector store - all data will be deleted")
        self.client.delete_collection(self.collection_name)
        self._collection = None
        logger.info("Vector store reset complete")
    
    def close(self) -> None:
        """
        Close the database connection.
        
        Note: ChromaDB PersistentClient doesn't require explicit closing,
        but this method is provided for consistency.
        """
        self._collection = None
        self._client = None
        logger.debug("Vector store closed")
