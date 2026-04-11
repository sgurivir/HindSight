"""
Vector database store using ChromaDB.

This module provides a generalized wrapper around ChromaDB for storing and querying
embeddings. It supports both persistent and ephemeral (in-memory) modes.

This is extracted and generalized from issue_tracking_deduper/vector_db/store.py
to be shared between issue_deduper and issue_tracking_deduper.
"""

import logging
import warnings
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

# Filter numpy binary compatibility warnings that can occur with older Python versions
# (e.g., Python 3.9). This is a known issue when chromadb or its dependencies
# are compiled against a different numpy version than what's installed.
warnings.filterwarnings(
    "ignore",
    message=".*numpy\\.dtype size changed.*",
    category=RuntimeWarning
)
warnings.filterwarnings(
    "ignore",
    message=".*numpy\\.ufunc size changed.*",
    category=RuntimeWarning
)

logger = logging.getLogger("hindsight.dedupers.common.vector_store")


class VectorStore:
    """
    Generic ChromaDB-based vector store for embeddings.
    
    Supports both persistent and ephemeral (in-memory) modes.
    
    Attributes:
        db_path: Path to the ChromaDB persistent storage directory (ignored if ephemeral).
        collection_name: Name of the ChromaDB collection.
        ephemeral: If True, use in-memory storage (no persistence).
    """
    
    def __init__(
        self,
        db_path: Optional[Path] = None,
        collection_name: str = "embeddings",
        ephemeral: bool = False
    ):
        """
        Initialize the vector store.
        
        Args:
            db_path: Path for persistent storage (ignored if ephemeral=True).
            collection_name: Name of the ChromaDB collection.
            ephemeral: If True, use in-memory storage (no persistence).
        """
        self.db_path = Path(db_path) if db_path else None
        self.collection_name = collection_name
        self.ephemeral = ephemeral
        self._client = None
        self._collection = None
        
        if ephemeral:
            logger.debug(f"VectorStore initialized in ephemeral mode")
        else:
            logger.debug(f"VectorStore initialized with db_path: {self.db_path}")
    
    def _ensure_db_directory(self) -> None:
        """Ensure the database directory exists (for persistent mode)."""
        if self.db_path and not self.ephemeral:
            self.db_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured database directory exists: {self.db_path}")
    
    @property
    def client(self):
        """
        Lazy-load the ChromaDB client.
        
        Returns:
            The ChromaDB client instance (PersistentClient or EphemeralClient).
        
        Raises:
            ImportError: If chromadb is not installed.
            RuntimeError: If there's a numpy binary incompatibility issue.
        """
        if self._client is None:
            try:
                # Apply warnings filter before importing to suppress numpy compatibility warnings
                import warnings
                warnings.filterwarnings(
                    "ignore",
                    message=".*numpy\\.dtype size changed.*",
                    category=RuntimeWarning
                )
                warnings.filterwarnings(
                    "ignore",
                    message=".*numpy\\.ufunc size changed.*",
                    category=RuntimeWarning
                )
                
                import chromadb
            except ImportError as e:
                if "numpy.dtype size changed" in str(e) or "numpy.ufunc size changed" in str(e):
                    raise RuntimeError(
                        "Numpy binary incompatibility detected. This typically occurs when "
                        "chromadb or its dependencies were compiled against a different "
                        "numpy version than what's currently installed.\n\n"
                        "To fix this issue, try one of the following:\n"
                        "1. Upgrade numpy: pip install --upgrade numpy\n"
                        "2. Reinstall chromadb: pip install --force-reinstall chromadb\n"
                        "3. Create a fresh virtual environment and reinstall all dependencies\n\n"
                        f"Original error: {e}"
                    ) from e
                raise ImportError(
                    "chromadb is required for vector storage. "
                    "Install it with: pip install chromadb"
                ) from e
            except Exception as e:
                # Catch any other numpy-related errors that might be raised differently
                error_str = str(e)
                if "numpy.dtype size changed" in error_str or "numpy.ufunc size changed" in error_str:
                    raise RuntimeError(
                        "Numpy binary incompatibility detected. This typically occurs when "
                        "chromadb or its dependencies were compiled against a different "
                        "numpy version than what's currently installed.\n\n"
                        "To fix this issue, try one of the following:\n"
                        "1. Upgrade numpy: pip install --upgrade numpy\n"
                        "2. Reinstall chromadb: pip install --force-reinstall chromadb\n"
                        "3. Create a fresh virtual environment and reinstall all dependencies\n\n"
                        f"Original error: {e}"
                    ) from e
                raise
            
            # Set environment variables for ChromaDB configuration
            import os
            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
            
            if self.ephemeral:
                logger.info("Initializing ChromaDB ephemeral client")
                self._client = chromadb.EphemeralClient()
            else:
                self._ensure_db_directory()
                logger.info(f"Initializing ChromaDB persistent client at: {self.db_path}")
                self._client = chromadb.PersistentClient(path=str(self.db_path))
            
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
    
    def add_document(
        self,
        doc_id: str,
        text: str,
        embedding: List[float],
        metadata: Dict[str, Any]
    ) -> bool:
        """
        Add a single document to the store.
        
        Args:
            doc_id: Unique identifier for the document.
            text: The document text.
            embedding: The embedding vector for the document.
            metadata: Metadata dictionary to store with the document.
        
        Returns:
            True if the document was added/updated, False if skipped (identical content).
        """
        # Check if already exists with same content hash
        try:
            existing = self.collection.get(ids=[doc_id])
            if existing and existing['metadatas'] and len(existing['metadatas']) > 0:
                existing_hash = existing['metadatas'][0].get('content_hash')
                if existing_hash and metadata.get('content_hash') == existing_hash:
                    logger.debug(f"Skipping {doc_id} - identical content")
                    return False
        except Exception as e:
            logger.debug(f"Error checking existing document: {e}")
        
        # Add timestamp to metadata
        metadata_with_timestamp = metadata.copy()
        metadata_with_timestamp['ingested_at'] = datetime.utcnow().isoformat() + "Z"
        
        # Upsert (insert or update)
        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata_with_timestamp],
            embeddings=[embedding]
        )
        
        logger.debug(f"Added/updated document: {doc_id}")
        return True
    
    def add_documents_batch(
        self,
        documents: List[Dict[str, Any]],
        embeddings: List[List[float]]
    ) -> Tuple[int, int]:
        """
        Add multiple documents in batch.
        
        Args:
            documents: List of document dictionaries with keys:
                       - id: Document ID
                       - text: Document text
                       - metadata: Metadata dictionary
            embeddings: List of embedding vectors (one per document).
        
        Returns:
            Tuple of (added_count, skipped_count).
        """
        if len(documents) != len(embeddings):
            raise ValueError("Number of documents must match number of embeddings")
        
        if not documents:
            return 0, 0
        
        # Prepare batch data
        ids = []
        texts = []
        metadatas = []
        batch_embeddings = []
        
        # Check for existing documents to skip
        doc_ids = [d['id'] for d in documents]
        
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
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        for doc, embedding in zip(documents, embeddings):
            doc_id = doc['id']
            content_hash = doc.get('metadata', {}).get('content_hash')
            
            # Skip if identical content
            if doc_id in existing_hashes and existing_hashes[doc_id] == content_hash:
                skipped += 1
                continue
            
            ids.append(doc_id)
            texts.append(doc.get('text', ''))
            
            metadata = doc.get('metadata', {}).copy()
            metadata['ingested_at'] = timestamp
            metadatas.append(metadata)
            
            batch_embeddings.append(embedding)
        
        # Upsert batch
        if ids:
            self.collection.upsert(
                ids=ids,
                documents=texts,
                metadatas=metadatas,
                embeddings=batch_embeddings
            )
            logger.info(f"Added/updated {len(ids)} documents, skipped {skipped}")
        
        return len(ids), skipped
    
    def query(
        self,
        query_embedding: List[float],
        n_results: int = 5,
        exclude_ids: Optional[List[str]] = None
    ) -> List[Tuple[str, Dict[str, Any], float]]:
        """
        Query for similar documents.
        
        Args:
            query_embedding: The embedding vector to search for.
            n_results: Maximum number of results to return.
            exclude_ids: Optional list of document IDs to exclude from results.
        
        Returns:
            List of tuples (doc_id, metadata, distance).
            Distance is cosine distance (0 = identical, 2 = opposite).
        """
        # Request more results if we need to filter some out
        request_n = n_results + len(exclude_ids) if exclude_ids else n_results
        
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=request_n,
            include=['metadatas', 'distances', 'documents']
        )
        
        output = []
        exclude_set = set(exclude_ids) if exclude_ids else set()
        
        if results and results['ids'] and results['ids'][0]:
            for i, doc_id in enumerate(results['ids'][0]):
                # Skip excluded IDs
                if doc_id in exclude_set:
                    continue
                
                metadata = results['metadatas'][0][i] if results['metadatas'] else {}
                distance = results['distances'][0][i] if results['distances'] else 0.0
                output.append((doc_id, metadata, distance))
                
                # Stop once we have enough results
                if len(output) >= n_results:
                    break
        
        return output
    
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific document by ID.
        
        Args:
            doc_id: The document ID.
        
        Returns:
            Dictionary with document data, or None if not found.
        """
        try:
            result = self.collection.get(ids=[doc_id], include=['metadatas', 'documents'])
            if result and result['ids']:
                return {
                    'id': result['ids'][0],
                    'metadata': result['metadatas'][0] if result['metadatas'] else {},
                    'document': result['documents'][0] if result['documents'] else ""
                }
        except Exception as e:
            logger.error(f"Error getting document {doc_id}: {e}")
        
        return None
    
    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document from the store.
        
        Args:
            doc_id: The document ID.
        
        Returns:
            True if deleted, False if not found or error.
        """
        try:
            self.collection.delete(ids=[doc_id])
            logger.debug(f"Deleted document: {doc_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting document {doc_id}: {e}")
            return False
    
    def count(self) -> int:
        """
        Get the number of documents in the store.
        
        Returns:
            The number of documents in the collection.
        """
        return self.collection.count()
    
    def clear(self) -> None:
        """
        Clear all documents from the store.
        
        Warning: This will delete all stored documents!
        """
        logger.warning("Clearing vector store - all data will be deleted")
        self.client.delete_collection(self.collection_name)
        self._collection = None
        logger.info("Vector store cleared")
    
    def close(self) -> None:
        """
        Close the database connection.
        
        Note: ChromaDB clients don't require explicit closing,
        but this method is provided for consistency and cleanup.
        """
        self._collection = None
        self._client = None
        logger.debug("Vector store closed")
