"""
Embedding generation for deduplication modules.

This module provides functionality to generate embeddings using
sentence-transformers models for semantic similarity matching.

This is extracted and generalized from issue_tracking_deduper/vector_db/embeddings.py
to be shared between issue_deduper and issue_tracking_deduper.
"""

import logging
import warnings
from typing import List, Optional

# Filter numpy binary compatibility warnings that can occur with older Python versions
# (e.g., Python 3.9). This is a known issue when sentence-transformers/torch are
# compiled against a different numpy version than what's installed.
# The warning message is: "numpy.dtype size changed, may indicate binary incompatibility"
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

logger = logging.getLogger("hindsight.dedupers.common.embeddings")

# Default embedding model
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingGenerator:
    """
    Generates embeddings using sentence-transformers models.
    
    This class wraps the sentence-transformers library to provide
    a simple interface for generating embeddings from text.
    
    Supports singleton pattern for efficiency when using the same model
    across multiple components.
    
    Attributes:
        model_name: Name of the sentence-transformers model to use.
        model: The loaded SentenceTransformer model instance.
    """
    
    _instances: dict = {}  # Class-level cache for singleton instances
    
    def __init__(self, model_name: Optional[str] = None):
        """
        Initialize the embedding generator.
        
        Args:
            model_name: Name of the sentence-transformers model to use.
                       Defaults to all-MiniLM-L6-v2.
        """
        self.model_name = model_name or DEFAULT_EMBEDDING_MODEL
        self._model = None
        logger.debug(f"EmbeddingGenerator initialized with model: {self.model_name}")
    
    @classmethod
    def get_instance(cls, model_name: Optional[str] = None) -> 'EmbeddingGenerator':
        """
        Get or create a singleton instance for the specified model.
        
        This ensures that the same model is reused across multiple components,
        avoiding redundant model loading.
        
        Args:
            model_name: Name of the sentence-transformers model to use.
                       Defaults to all-MiniLM-L6-v2.
        
        Returns:
            The EmbeddingGenerator instance for the specified model.
        """
        model = model_name or DEFAULT_EMBEDDING_MODEL
        if model not in cls._instances:
            cls._instances[model] = cls(model_name=model)
        return cls._instances[model]
    
    @property
    def model(self):
        """
        Lazy-load the sentence-transformers model.
        
        Returns:
            The loaded SentenceTransformer model instance.
        
        Raises:
            ImportError: If sentence-transformers is not installed.
            RuntimeError: If there's a numpy binary incompatibility issue.
        """
        if self._model is None:
            logger.info(f"Loading embedding model: {self.model_name}")
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
                
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
                logger.info(f"Model loaded successfully: {self.model_name}")
            except ImportError as e:
                if "numpy.dtype size changed" in str(e) or "numpy.ufunc size changed" in str(e):
                    raise RuntimeError(
                        "Numpy binary incompatibility detected. This typically occurs when "
                        "sentence-transformers or its dependencies (torch, numpy) were compiled "
                        "against a different numpy version than what's currently installed.\n\n"
                        "To fix this issue, try one of the following:\n"
                        "1. Upgrade numpy: pip install --upgrade numpy\n"
                        "2. Reinstall sentence-transformers: pip install --force-reinstall sentence-transformers\n"
                        "3. Create a fresh virtual environment and reinstall all dependencies\n\n"
                        f"Original error: {e}"
                    ) from e
                raise ImportError(
                    "sentence-transformers is required for embedding generation. "
                    "Install it with: pip install sentence-transformers"
                ) from e
            except RuntimeError as e:
                if "numpy.dtype size changed" in str(e) or "numpy.ufunc size changed" in str(e):
                    raise RuntimeError(
                        "Numpy binary incompatibility detected. This typically occurs when "
                        "sentence-transformers or its dependencies (torch, numpy) were compiled "
                        "against a different numpy version than what's currently installed.\n\n"
                        "To fix this issue, try one of the following:\n"
                        "1. Upgrade numpy: pip install --upgrade numpy\n"
                        "2. Reinstall sentence-transformers: pip install --force-reinstall sentence-transformers\n"
                        "3. Create a fresh virtual environment and reinstall all dependencies\n\n"
                        f"Original error: {e}"
                    ) from e
                raise
            except Exception as e:
                # Catch any other numpy-related errors that might be raised differently
                error_str = str(e)
                if "numpy.dtype size changed" in error_str or "numpy.ufunc size changed" in error_str:
                    raise RuntimeError(
                        "Numpy binary incompatibility detected. This typically occurs when "
                        "sentence-transformers or its dependencies (torch, numpy) were compiled "
                        "against a different numpy version than what's currently installed.\n\n"
                        "To fix this issue, try one of the following:\n"
                        "1. Upgrade numpy: pip install --upgrade numpy\n"
                        "2. Reinstall sentence-transformers: pip install --force-reinstall sentence-transformers\n"
                        "3. Create a fresh virtual environment and reinstall all dependencies\n\n"
                        f"Original error: {e}"
                    ) from e
                raise
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


# Module-level singleton for convenience
_default_generator: Optional[EmbeddingGenerator] = None


def get_default_generator(model_name: Optional[str] = None) -> EmbeddingGenerator:
    """
    Get the default embedding generator instance.
    
    This is a convenience function that returns a singleton instance
    of the EmbeddingGenerator.
    
    Args:
        model_name: Optional model name. If provided and different from
                   the current default, a new instance will be created.
    
    Returns:
        The default EmbeddingGenerator instance.
    """
    global _default_generator
    
    target_model = model_name or DEFAULT_EMBEDDING_MODEL
    
    if _default_generator is None or _default_generator.model_name != target_model:
        _default_generator = EmbeddingGenerator.get_instance(target_model)
    
    return _default_generator


def generate_embedding(text: str, model_name: Optional[str] = None) -> List[float]:
    """
    Generate an embedding for a single text string using the default generator.
    
    Args:
        text: The text to generate an embedding for.
        model_name: Optional model name to use.
    
    Returns:
        A list of floats representing the embedding vector.
    """
    return get_default_generator(model_name).generate(text)


def generate_embeddings(
    texts: List[str],
    batch_size: int = 32,
    model_name: Optional[str] = None
) -> List[List[float]]:
    """
    Generate embeddings for multiple text strings using the default generator.
    
    Args:
        texts: List of texts to generate embeddings for.
        batch_size: Number of texts to process at once.
        model_name: Optional model name to use.
    
    Returns:
        A list of embedding vectors (each a list of floats).
    """
    return get_default_generator(model_name).generate_batch(texts, batch_size)
