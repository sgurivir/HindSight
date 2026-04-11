"""
Embedding generation for the Issue Deduper tool.

This module provides functionality to generate embeddings using
sentence-transformers models for semantic similarity matching.
"""

import logging
from typing import List, Optional

from ..config import EMBEDDING_MODEL

logger = logging.getLogger("issue_tracking_deduper.embeddings")


class EmbeddingGenerator:
    """
    Generates embeddings using sentence-transformers models.
    
    This class wraps the sentence-transformers library to provide
    a simple interface for generating embeddings from text.
    
    Attributes:
        model_name: Name of the sentence-transformers model to use.
        model: The loaded SentenceTransformer model instance.
    """
    
    def __init__(self, model_name: Optional[str] = None):
        """
        Initialize the embedding generator.
        
        Args:
            model_name: Name of the sentence-transformers model to use.
                       Defaults to the model specified in config.
        """
        self.model_name = model_name or EMBEDDING_MODEL
        self._model = None
        logger.debug(f"EmbeddingGenerator initialized with model: {self.model_name}")
    
    @property
    def model(self):
        """
        Lazy-load the sentence-transformers model.
        
        Returns:
            The loaded SentenceTransformer model instance.
        """
        if self._model is None:
            logger.info(f"Loading embedding model: {self.model_name}")
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
                logger.info(f"Model loaded successfully: {self.model_name}")
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for embedding generation. "
                    "Install it with: pip install sentence-transformers"
                )
        return self._model
    
    def generate(self, text: str) -> List[float]:
        """
        Generate an embedding for a single text string.
        
        Args:
            text: The text to generate an embedding for.
        
        Returns:
            A list of floats representing the embedding vector.
        """
        embedding = self.model.encode(text, convert_to_numpy=True, show_progress_bar=False)
        return embedding.tolist()
    
    def generate_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Generate embeddings for multiple text strings.
        
        Args:
            texts: List of texts to generate embeddings for.
            batch_size: Number of texts to process at once.
        
        Returns:
            A list of embedding vectors (each a list of floats).
        """
        if not texts:
            return []
        
        logger.debug(f"Generating embeddings for {len(texts)} texts (batch_size={batch_size})")
        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            show_progress_bar=False
        )
        return [emb.tolist() for emb in embeddings]
    
    @property
    def embedding_dimension(self) -> int:
        """
        Get the dimension of the embedding vectors.
        
        Returns:
            The number of dimensions in the embedding vectors.
        """
        return self.model.get_sentence_embedding_dimension()


# Singleton instance for convenience
_default_generator: Optional[EmbeddingGenerator] = None


def get_default_generator() -> EmbeddingGenerator:
    """
    Get the default embedding generator instance.
    
    Returns:
        The default EmbeddingGenerator instance.
    """
    global _default_generator
    if _default_generator is None:
        _default_generator = EmbeddingGenerator()
    return _default_generator


def generate_embedding(text: str) -> List[float]:
    """
    Generate an embedding for a single text string using the default generator.
    
    Args:
        text: The text to generate an embedding for.
    
    Returns:
        A list of floats representing the embedding vector.
    """
    return get_default_generator().generate(text)


def generate_embeddings(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """
    Generate embeddings for multiple text strings using the default generator.
    
    Args:
        texts: List of texts to generate embeddings for.
        batch_size: Number of texts to process at once.
    
    Returns:
        A list of embedding vectors (each a list of floats).
    """
    return get_default_generator().generate_batch(texts, batch_size)
