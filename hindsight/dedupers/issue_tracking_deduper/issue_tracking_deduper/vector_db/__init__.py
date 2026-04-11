"""
Vector database package for issue embeddings storage and retrieval.

This package provides:
- EmbeddingGenerator: Generate embeddings using sentence-transformers
- VectorStore: ChromaDB-based vector storage
- IssueIngester: Ingest issue files into the vector database
"""

from .embeddings import EmbeddingGenerator, generate_embedding, generate_embeddings
from .store import VectorStore
from .ingestion import IssueIngester, ingest_issues

__all__ = [
    'EmbeddingGenerator',
    'generate_embedding',
    'generate_embeddings',
    'VectorStore',
    'IssueIngester',
    'ingest_issues',
]
