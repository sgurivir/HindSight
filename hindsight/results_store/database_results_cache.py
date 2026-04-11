#!/usr/bin/env python3
"""
Database Results Cache - Simplified Synchronous Implementation
Uses psycopg2 for straightforward synchronous database access
"""

import json
import os
from typing import Any, Dict, List, Optional
from uuid import UUID
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import threading

from .interface.prior_results_store_interface import ResultsCache
from hindsight.utils.log_util import get_logger

# Import centralized schema
from hindsight.core.schema.code_analysis_result_schema import (
    CodeAnalysisResultValidator
)

logger = get_logger(__name__)


class SimpleDatabasePool:
    """Simple thread-safe connection pool using psycopg2"""
    
    _pool = None
    _lock = threading.Lock()
    
    @classmethod
    def initialize(cls, min_conn=5, max_conn=10):
        """Initialize the connection pool"""
        if cls._pool is not None:
            return
            
        with cls._lock:
            if cls._pool is not None:
                return
                
            # Get connection params from environment
            db_params = {
                'host': os.getenv('DATABASE_HOST', 'localhost'),
                'port': int(os.getenv('DATABASE_PORT', '5432')),
                'database': os.getenv('DATABASE_NAME', 'repoiq'),
                'user': os.getenv('DATABASE_USER', 'repoiq'),
                'password': os.getenv('DATABASE_PASSWORD', 'repoiq'),
            }
            
            try:
                cls._pool = pool.ThreadedConnectionPool(
                    min_conn,
                    max_conn,
                    **db_params
                )
                logger.info(f"Database pool initialized: {db_params['database']}@{db_params['host']}")
            except Exception as e:
                logger.error(f"Failed to initialize database pool: {e}")
                raise
    
    @classmethod
    def get_connection(cls):
        """Get a connection from the pool"""
        if cls._pool is None:
            cls.initialize()
        return cls._pool.getconn()
    
    @classmethod
    def return_connection(cls, conn):
        """Return a connection to the pool"""
        if cls._pool is not None:
            cls._pool.putconn(conn)
    
    @classmethod
    def close_all(cls):
        """Close all connections in the pool"""
        with cls._lock:
            if cls._pool is not None:
                cls._pool.closeall()
                cls._pool = None


class DatabaseResultsCache(ResultsCache):
    """
    Simple synchronous database cache implementation.
    No event loops, no threading complexity - just straightforward database access.
    """

    def __init__(self, table_name: str = "function_analysis_cache"):
        """Initialize the database results cache"""
        self.table_name = table_name
        self._lock = threading.RLock()
        self.current_repo_id: Optional[UUID] = None
        self.current_repo_name: Optional[str] = None
        
        # Ensure pool is initialized
        SimpleDatabasePool.initialize()
        logger.debug("DatabaseResultsCache initialized")

    def _execute_query(self, query: str, params: tuple = None, fetch_one=False, fetch_all=False, fetch_val=False):
        """
        Execute a database query with automatic connection management.
        
        Args:
            query: SQL query to execute
            params: Query parameters
            fetch_one: Return one row as dict
            fetch_all: Return all rows as list of dicts
            fetch_val: Return single value
            
        Returns:
            Query result or None
        """
        conn = None
        try:
            conn = SimpleDatabasePool.get_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, params or ())
                
                if fetch_val:
                    row = cursor.fetchone()
                    if row:
                        # RealDictCursor returns dict-like objects, get first value
                        return list(row.values())[0]
                    return None
                elif fetch_one:
                    row = cursor.fetchone()
                    return dict(row) if row else None
                elif fetch_all:
                    rows = cursor.fetchall()
                    return [dict(row) for row in rows]
                else:
                    conn.commit()
                    return cursor.rowcount
                    
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database query error: {e}")
            raise
        finally:
            if conn:
                SimpleDatabasePool.return_connection(conn)

    def _get_repo_id_by_name_or_url(self, repo_identifier: str) -> Optional[UUID]:
        """Get repository ID by name or URL"""
        # Try by URL first
        query = "SELECT id FROM repositories WHERE github_url = %s"
        result = self._execute_query(query, (repo_identifier,), fetch_one=True)
        if result:
            return result['id']
        
        # Try by name
        query = "SELECT id FROM repositories WHERE name = %s"
        result = self._execute_query(query, (repo_identifier,), fetch_one=True)
        if result:
            return result['id']
        
        return None

    def has_result(self, file_name: str, function_name: str, checksum: str, timeout_seconds: float = 15.0) -> bool:
        """Check if result exists in cache"""
        if not self.current_repo_id:
            return False
        
        try:
            query = f"""
            SELECT 1 FROM {self.table_name}
            WHERE repository_id = %s AND file_path = %s 
              AND function_name = %s AND function_checksum = %s
            LIMIT 1
            """
            result = self._execute_query(
                query,
                (str(self.current_repo_id), file_name, function_name, checksum),
                fetch_val=True
            )
            exists = result is not None
            logger.debug(f"Cache lookup for {function_name}: {'found' if exists else 'not found'}")
            return exists
            
        except Exception as e:
            logger.warning(f"Cache lookup error for {function_name}: {e}")
            return False

    def get_existing_result(
        self,
        file_name: str,
        function_name: str,
        checksum: str,
        timeout_seconds: float = 15.0
    ) -> Optional[Dict[str, Any]]:
        """Get existing result from cache"""
        if not self.current_repo_id:
            return None
        
        try:
            query = f"""
            SELECT result_data FROM {self.table_name}
            WHERE repository_id = %s AND file_path = %s 
              AND function_name = %s AND function_checksum = %s
            """
            row = self._execute_query(
                query,
                (str(self.current_repo_id), file_name, function_name, checksum),
                fetch_one=True
            )
            
            if not row:
                return None
            
            result_data = row['result_data']
            
            # Parse JSON if needed
            if isinstance(result_data, str):
                try:
                    result_data = json.loads(result_data)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse JSON for {function_name}: {e}")
                    return None
            
            # Normalize and validate
            try:
                normalized = CodeAnalysisResultValidator.normalize_result(
                    result_data,
                    file_path=file_name,
                    function=function_name,
                    checksum=checksum
                )
                
                validation_errors = normalized.validate()
                if validation_errors:
                    logger.warning(f"Validation failed for {function_name}: {validation_errors}")
                    return None
                
                logger.info(f"Retrieved cached result for {function_name}")
                return normalized.to_dict()
                
            except Exception as e:
                logger.warning(f"Failed to normalize result for {function_name}: {e}")
                return None
                
        except Exception as e:
            logger.warning(f"Error retrieving result for {function_name}: {e}")
            return None

    def initialize_for_repo(self, repo_name: str) -> None:
        """Initialize cache for a specific repository"""
        with self._lock:
            self.current_repo_name = repo_name
            
            try:
                # Try parsing as UUID first
                try:
                    repo_id = UUID(repo_name)
                    # Verify it exists
                    query = "SELECT id FROM repositories WHERE id = %s"
                    result = self._execute_query(query, (str(repo_id),), fetch_one=True)
                    if not result:
                        logger.warning(f"Repository UUID {repo_id} not found")
                        self.current_repo_id = None
                        return
                except (ValueError, TypeError):
                    # Not a UUID, look up by name/URL
                    repo_id = self._get_repo_id_by_name_or_url(repo_name)
                    if not repo_id:
                        logger.warning(f"Repository '{repo_name}' not found")
                        self.current_repo_id = None
                        return
                
                # Count existing cached results
                count_query = f"SELECT COUNT(*) FROM {self.table_name} WHERE repository_id = %s"
                count = self._execute_query(count_query, (str(repo_id),), fetch_val=True)
                
                self.current_repo_id = repo_id
                logger.info(f"Initialized cache for repo '{repo_name}' (UUID: {repo_id}) with {count} cached results")
                
            except Exception as e:
                logger.error(f"Error initializing cache for repo '{repo_name}': {e}")
                self.current_repo_id = None

    def set_repository_id(self, repo_id: UUID) -> None:
        """Directly set the repository ID"""
        with self._lock:
            self.current_repo_id = repo_id
            logger.info(f"Set repository ID to {repo_id}")

    def on_result_added(self, result_id: str, result: Dict[str, Any]) -> None:
        """Store result in database cache"""
        if not self.current_repo_id:
            logger.debug(f"No repository ID set, skipping cache storage for {result_id}")
            return
        
        file_path = result.get('file_path', '')
        function_name = result.get('function', '')
        checksum = result.get('checksum', '')
        
        if not all([file_path, function_name, checksum]):
            logger.warning(f"Incomplete result data, skipping cache storage: {result_id}")
            return
        
        try:
            # Normalize and validate
            normalized = CodeAnalysisResultValidator.normalize_result(
                result,
                file_path=file_path,
                function=function_name,
                checksum=checksum
            )
            
            validation_errors = normalized.validate()
            if validation_errors:
                logger.warning(f"Validation failed for {function_name}, skipping: {validation_errors}")
                return
            
            # Store in database
            query = f"""
            INSERT INTO {self.table_name} 
                (repository_id, file_path, function_name, function_checksum, result_data)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (repository_id, file_path, function_name, function_checksum)
            DO UPDATE SET result_data = EXCLUDED.result_data, updated_at = NOW()
            """
            
            self._execute_query(
                query,
                (
                    str(self.current_repo_id),
                    file_path,
                    function_name,
                    checksum,
                    json.dumps(normalized.to_dict())
                )
            )
            
            logger.info(f"Stored result in cache: {function_name} (checksum: {checksum[:8]}...)")
            
        except Exception as e:
            logger.error(f"Error storing result for {function_name}: {e}")

    def on_result_updated(self, result_id: str, old_result: Dict[str, Any], new_result: Dict[str, Any]) -> None:
        """Update existing result in cache"""
        if not self.current_repo_id:
            return
        
        file_path = new_result.get('file_path', '')
        function_name = new_result.get('function', '')
        checksum = new_result.get('checksum', '')
        
        try:
            query = f"""
            UPDATE {self.table_name}
            SET result_data = %s, updated_at = NOW()
            WHERE repository_id = %s AND file_path = %s 
              AND function_name = %s AND function_checksum = %s
            """
            
            self._execute_query(
                query,
                (
                    json.dumps(new_result),
                    str(self.current_repo_id),
                    file_path,
                    function_name,
                    checksum
                )
            )
            
            logger.info(f"Updated cached result: {function_name}")
            
        except Exception as e:
            logger.error(f"Error updating result for {function_name}: {e}")

    def on_function_analyzed(self, function_name: str, file_path: str, result: Dict[str, Any]) -> None:
        """Called when function analysis completes"""
        result_id = f"{function_name}_{file_path}_{result.get('checksum', 'unknown')}"
        self.on_result_added(result_id, result)

    def on_analysis_batch_completed(self, batch_results: List[Dict[str, Any]]) -> None:
        """Called when batch of analyses completes"""
        for i, result in enumerate(batch_results):
            result_id = f"batch_result_{i}"
            self.on_result_added(result_id, result)
