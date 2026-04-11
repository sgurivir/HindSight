"""
Database connection manager using asyncpg
Handles connection pooling and lifecycle management
"""
import asyncpg
import os
from typing import Optional

from ..utils.log_util import get_logger

logger = get_logger(__name__)


class DatabaseConnection:
    """
    Database connection pool manager.
    
    Uses asyncpg for PostgreSQL connections with connection pooling.
    """
    
    _pool: Optional[asyncpg.Pool] = None
    
    @classmethod
    async def initialize(cls, 
                        host: str = None,
                        port: int = None,
                        database: str = None,
                        user: str = None,
                        password: str = None,
                        min_size: int = 5,
                        max_size: int = 20):
        """
        Initialize the database connection pool.
        
        Args:
            host: Database host (defaults to env var DATABASE_HOST or 'localhost')
            port: Database port (defaults to env var DATABASE_PORT or 5432)
            database: Database name (defaults to env var DATABASE_NAME or 'repoiq')
            user: Database user (defaults to env var DATABASE_USER or 'repoiq')
            password: Database password (defaults to env var DATABASE_PASSWORD or 'repoiq')
            min_size: Minimum pool size
            max_size: Maximum pool size
        """
        if cls._pool is not None:
            logger.warning("Database pool already initialized")
            return
        
        # Use environment variables if not provided
        host = host or os.getenv('DATABASE_HOST', 'localhost')
        port = port or int(os.getenv('DATABASE_PORT', '5432'))
        database = database or os.getenv('DATABASE_NAME', 'repoiq')
        user = user or os.getenv('DATABASE_USER', 'repoiq')
        password = password or os.getenv('DATABASE_PASSWORD', 'repoiq')
        
        try:
            logger.info(f"Connecting to database {database} at {host}:{port} as {user}")
            
            cls._pool = await asyncpg.create_pool(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                min_size=min_size,
                max_size=max_size,
                command_timeout=60
            )
            
            # Test the connection
            async with cls._pool.acquire() as conn:
                version = await conn.fetchval('SELECT version()')
                logger.info(f"Connected to PostgreSQL: {version}")
            
        except Exception as e:
            logger.error(f"Failed to initialize database pool: {e}")
            raise
    
    @classmethod
    async def close(cls):
        """Close the database connection pool."""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None
            logger.info("Database pool closed")
    
    @classmethod
    def acquire(cls):
        """
        Acquire a connection from the pool.
        
        Returns:
            Connection context manager
            
        Raises:
            RuntimeError: If pool is not initialized
        """
        if cls._pool is None:
            raise RuntimeError("Database pool not initialized. Call initialize() first.")
        
        return cls._pool.acquire()
    
    @classmethod
    async def execute(cls, query: str, *args):
        """
        Execute a query without returning results.
        
        Args:
            query: SQL query
            *args: Query parameters
            
        Returns:
            Query status
        """
        async with cls.acquire() as conn:
            return await conn.execute(query, *args)
    
    @classmethod
    async def fetch(cls, query: str, *args):
        """
        Fetch multiple rows.
        
        Args:
            query: SQL query
            *args: Query parameters
            
        Returns:
            List of records
        """
        async with cls.acquire() as conn:
            return await conn.fetch(query, *args)
    
    @classmethod
    async def fetchrow(cls, query: str, *args):
        """
        Fetch a single row.
        
        Args:
            query: SQL query
            *args: Query parameters
            
        Returns:
            Single record or None
        """
        async with cls.acquire() as conn:
            return await conn.fetchrow(query, *args)
    
    @classmethod
    async def fetchval(cls, query: str, *args):
        """
        Fetch a single value.
        
        Args:
            query: SQL query
            *args: Query parameters
            
        Returns:
            Single value or None
        """
        async with cls.acquire() as conn:
            return await conn.fetchval(query, *args)

