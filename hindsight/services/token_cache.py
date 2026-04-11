"""
Token cache using asyncpg for storing OAuth/App tokens.
Provides database-backed storage with encryption, designed for future Redis integration.
"""
import os
import json
import logging
from typing import Optional, Dict, List
from datetime import datetime, timezone

from cryptography.fernet import Fernet

from ..db.connection import DatabaseConnection
from ..utils.log_util import get_logger

logger = get_logger(__name__)


class TokenCache:
    """
    Database token cache using asyncpg.
    
    Stores provider OAuth/App installation credentials with encryption.
    Uses the existing DatabaseConnection pool for consistency with the codebase.
    """
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize database token cache.
        
        Args:
            encryption_key: Fernet encryption key (base64 encoded)
                           If None, reads from ENCRYPTION_KEY environment variable
        """
        # Get encryption key
        if encryption_key is None:
            encryption_key = os.getenv("ENCRYPTION_KEY")
        
        if not encryption_key:
            raise ValueError(
                "Encryption key required. Set ENCRYPTION_KEY environment variable. "
                "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        
        # Initialize cipher
        try:
            self.cipher = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
        except Exception as e:
            raise ValueError(f"Invalid encryption key: {e}")
        
        logger.info("Initialized TokenCache with encryption")
    
    def _encrypt(self, value: str) -> str:
        """Encrypt a string value"""
        if not value:
            return value
        return self.cipher.encrypt(value.encode()).decode()
    
    def _decrypt(self, value: str) -> str:
        """Decrypt a string value"""
        if not value:
            return value
        return self.cipher.decrypt(value.encode()).decode()
    
    def _extract_scopes_from_metadata(self, metadata: Optional[Dict]) -> List[str]:
        """
        Extract scopes array from metadata->permissions dict.
        
        Converts permissions dict like {"contents": "read", "pull_requests": "write"}
        to scopes array like ["contents:read", "pull_requests:write"].
        
        Args:
            metadata: Metadata dict containing permissions
            
        Returns:
            List of scope strings in format "permission:level"
        """
        if not metadata or not isinstance(metadata, dict):
            return []
        
        permissions = metadata.get("permissions")
        if not permissions or not isinstance(permissions, dict):
            return []
        
        return [f"{key}:{value}" for key, value in permissions.items()]
    
    async def get_connection(self, user_id: str, provider: str, installation_id: Optional[str] = None) -> Optional[Dict]:
        """
        Retrieve and decrypt a provider connection.
        
        If installation_id is provided, fetch that specific installation.
        Otherwise, fetch the most recently used active connection for the provider.
        
        Args:
            user_id: Kinde user ID
            provider: Provider name (e.g., 'github')
            installation_id: Optional specific installation ID
            
        Returns:
            Dict with connection details and decrypted tokens, or None
        """
        async with DatabaseConnection.acquire() as conn:
            if installation_id:
                row = await conn.fetchrow(
                    """
                    SELECT 
                        i.id, i.user_id, i.installation_id, i.access_token, i.refresh_token,
                        i.token_expires_at, i.metadata, i.created_at, i.last_used_at,
                        i.source_account_id,
                        a.provider, a.handle as account_login, a.account_type
                    FROM github_installations i
                    JOIN source_accounts a ON i.source_account_id = a.id
                    WHERE i.user_id = $1 AND a.provider = $2 AND i.installation_id = $3 AND i.is_active = true
                    """,
                    user_id, provider, installation_id
                )
            else:
                # Get most recently used connection
                row = await conn.fetchrow(
                    """
                    SELECT 
                        i.id, i.user_id, i.installation_id, i.access_token, i.refresh_token,
                        i.token_expires_at, i.metadata, i.created_at, i.last_used_at,
                        i.source_account_id,
                        a.provider, a.handle as account_login, a.account_type
                    FROM github_installations i
                    JOIN source_accounts a ON i.source_account_id = a.id
                    WHERE i.user_id = $1 AND a.provider = $2 AND i.is_active = true
                    ORDER BY i.last_used_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    user_id, provider
                )
            
            if not row:
                logger.info(f"No active connection found for user {user_id}, provider {provider}")
                return None
            
            # Check if token is expired
            # Note: database stores naive UTC datetimes, so compare with naive UTC
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            if row['token_expires_at'] and row['token_expires_at'] < now_naive:
                logger.warning(f"Token expired for connection {row['id']}")
                # Caller should handle token refresh
            
            # Update last_used_at
            await conn.execute(
                "UPDATE github_installations SET last_used_at = $1 WHERE id = $2",
                now_naive, row['id']
            )
            
            # Return decrypted connection data
            return {
                "connection_id": str(row['id']),
                "user_id": row['user_id'],
                "provider": row['provider'],
                "installation_id": row['installation_id'],
                "account_login": row['account_login'],
                "account_type": row['account_type'],
                "source_account_id": str(row['source_account_id']) if row.get('source_account_id') else None,
                "access_token": self._decrypt(row['access_token']),
                "refresh_token": self._decrypt(row['refresh_token']) if row['refresh_token'] else None,
                "expires_at": row['token_expires_at'],
                "scopes": self._extract_scopes_from_metadata(row.get('metadata')),
                "metadata": row['metadata'],
                "created_at": row['created_at'],
                "last_used_at": row['last_used_at']
            }
    
    async def get_connection_by_id(self, connection_id: str) -> Optional[Dict]:
        """
        Get a connection by its ID.
        
        Args:
            connection_id: UUID of the connection
            
        Returns:
            Connection data with decrypted token, or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 
                    i.id, i.user_id, i.installation_id, i.access_token, i.refresh_token,
                    i.token_expires_at, i.is_active, i.created_at, i.updated_at,
                    i.last_used_at, i.metadata, i.source_account_id,
                    a.provider, a.handle as account_login, a.account_type
                FROM github_installations i
                JOIN source_accounts a ON i.source_account_id = a.id
                WHERE i.id = $1 AND i.is_active = TRUE
                """,
                connection_id
            )
            
            if not row:
                logger.warning(f"Connection {connection_id} not found or inactive")
                return None
            
            # Check if token is expired
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            if row['token_expires_at'] and row['token_expires_at'] < now_naive:
                logger.warning(f"Token expired for connection {row['id']}")
                # Caller should handle token refresh
            
            # Update last_used_at
            await conn.execute(
                "UPDATE github_installations SET last_used_at = $1 WHERE id = $2",
                now_naive, row['id']
            )
            
            # Return decrypted connection data
            return {
                "connection_id": str(row['id']),
                "user_id": row['user_id'],
                "provider": row['provider'],
                "installation_id": row['installation_id'],
                "account_type": row['account_type'],
                "account_login": row['account_login'],
                "source_account_id": str(row['source_account_id']) if row.get('source_account_id') else None,
                "access_token": self._decrypt(row['access_token']),
                "refresh_token": self._decrypt(row['refresh_token']) if row['refresh_token'] else None,
                "expires_at": row['token_expires_at'],
                "scopes": self._extract_scopes_from_metadata(row.get('metadata')),
                "created_at": row['created_at'],
                "updated_at": row['updated_at'],
                "last_used_at": row['last_used_at'],
                "metadata": row['metadata']
            }
    
    async def store_connection(
        self,
        user_id: str,
        provider: str,
        data: Dict,
        source_account_id: Optional[str] = None
    ) -> str:
        """
        Store or update a provider connection (GitHub installation).
        
        If a connection with the same user_id, provider, and installation_id exists,
        it will be updated. Otherwise, a new connection is created.
        
        Args:
            user_id: Kinde user ID
            provider: Provider name
            data: Dict containing tokens and connection details
            source_account_id: Optional UUID of source_account (for new architecture)
            
        Returns:
            Connection ID (UUID string)
        """
        installation_id = data.get("installation_id")
        
        # Convert expires_at to naive datetime if it's timezone-aware
        # (database columns are TIMESTAMP without timezone)
        expires_at = data.get("expires_at")
        if expires_at and expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)
        
        # Convert metadata dict to JSON string for JSONB column
        metadata = data.get("metadata")
        if metadata and isinstance(metadata, dict):
            metadata = json.dumps(metadata)
        
        async with DatabaseConnection.acquire() as conn:
            # Check if connection already exists
            existing = None
            if installation_id:
                existing = await conn.fetchrow(
                    """
                    SELECT i.id FROM github_installations i
                    JOIN source_accounts a ON i.source_account_id = a.id
                    WHERE i.user_id = $1 AND a.provider = $2 AND i.installation_id = $3
                    """,
                    user_id, provider, installation_id
                )
            
            now = datetime.now(timezone.utc).replace(tzinfo=None)  # Naive datetime for DB
            
            if existing:
                # Update existing connection
                logger.info(f"Updating existing connection {existing['id']}")
                await conn.execute(
                    """
                    UPDATE github_installations
                    SET access_token = $1,
                        refresh_token = $2,
                        token_expires_at = $3,
                        is_active = true,
                        last_used_at = $4,
                        metadata = $5,
                        updated_at = $4,
                        source_account_id = COALESCE($6, source_account_id)
                    WHERE id = $7
                    """,
                    self._encrypt(data["access_token"]),
                    self._encrypt(data["refresh_token"]) if data.get("refresh_token") else None,
                    expires_at,
                    now,
                    metadata,
                    source_account_id,
                    existing['id']
                )
                return str(existing['id'])
            else:
                # Create new connection
                logger.info(f"Creating new connection for user {user_id}, provider {provider}")
                row = await conn.fetchrow(
                    """
                    INSERT INTO github_installations (
                        user_id, installation_id,
                        access_token, refresh_token, token_expires_at,
                        is_active, last_used_at, metadata, source_account_id
                    )
                    VALUES ($1, $2, $3, $4, $5, true, $6, $7, $8)
                    RETURNING id
                    """,
                    user_id,
                    installation_id,
                    self._encrypt(data["access_token"]),
                    self._encrypt(data["refresh_token"]) if data.get("refresh_token") else None,
                    expires_at,
                    now,
                    metadata,
                    source_account_id
                )
                
                connection_id = str(row['id'])
                logger.info(f"Created connection {connection_id}")
                return connection_id
    
    async def update_token(self, connection_id: str, access_token: str, expires_at: Optional[datetime] = None) -> bool:
        """
        Update only the access token for a connection.
        
        Used during token refresh operations.
        
        Args:
            connection_id: Connection UUID
            access_token: New access token
            expires_at: New expiration timestamp
            
        Returns:
            True if successful
        """
        # Convert expires_at to naive if timezone-aware
        if expires_at and expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)
        
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE github_installations
                SET access_token = $1,
                    token_expires_at = $2,
                    last_used_at = $3,
                    updated_at = $3
                WHERE id = $4
                """,
                self._encrypt(access_token),
                expires_at,
                now_naive,
                connection_id
            )
            
            if result == "UPDATE 0":
                logger.error(f"Connection {connection_id} not found")
                return False
            
            logger.info(f"Updated token for connection {connection_id}")
            return True
    
    async def decrypt_token(self, encrypted_token: str) -> str:
        """
        Decrypt an encrypted access token.
        
        Helper method for decrypting tokens retrieved directly from database.
        
        Args:
            encrypted_token: Encrypted token string
            
        Returns:
            Decrypted token string
        """
        return self._decrypt(encrypted_token)
    
    async def delete_connection(self, connection_id: str) -> bool:
        """
        Mark a connection as inactive (soft delete).
        
        We don't hard delete to preserve audit trail.
        
        Args:
            connection_id: Connection UUID
            
        Returns:
            True if successful
        """
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE github_installations
                SET is_active = false, updated_at = $1
                WHERE id = $2
                """,
                now_naive,
                connection_id
            )
            
            if result == "UPDATE 0":
                logger.error(f"Connection {connection_id} not found")
                return False
            
            logger.info(f"Deactivated connection {connection_id}")
            return True
    
    async def list_connections(self, user_id: str) -> List[Dict]:
        """
        List all active connections for a user.
        
        Returns connection metadata without decrypted tokens.
        
        Args:
            user_id: Kinde user ID
            
        Returns:
            List of connection dicts
        """
        async with DatabaseConnection.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    i.id, i.installation_id, i.token_expires_at, i.created_at,
                    i.last_used_at, i.metadata, i.source_account_id,
                    a.provider, a.handle as account_login, a.account_type
                FROM github_installations i
                JOIN source_accounts a ON i.source_account_id = a.id
                WHERE i.user_id = $1 AND i.is_active = true
                ORDER BY i.created_at DESC
                """,
                user_id
            )
            
            return [
                {
                    "connection_id": str(row['id']),
                    "provider": row['provider'],
                    "installation_id": row['installation_id'],
                    "account_login": row['account_login'],
                    "account_type": row['account_type'],
                    "source_account_id": str(row['source_account_id']) if row.get('source_account_id') else None,
                    "scopes": self._extract_scopes_from_metadata(row.get('metadata')),
                    "expires_at": row['token_expires_at'],
                    "created_at": row['created_at'],
                    "last_used_at": row['last_used_at'],
                    "metadata": row['metadata']
                }
                for row in rows
            ]


# Global token cache instance
_token_cache: Optional[TokenCache] = None


def get_token_cache() -> TokenCache:
    """
    Get or create the global token cache instance.
    
    Returns:
        TokenCache instance
    """
    global _token_cache
    if _token_cache is None:
        _token_cache = TokenCache()
    return _token_cache
