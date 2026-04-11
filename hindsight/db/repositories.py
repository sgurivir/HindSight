"""
Database repository classes for Hindsight API
Handles CRUD operations for repositories, analyses, and results
"""
import json
from typing import List, Optional, Dict, Any, Tuple
from uuid import UUID
from datetime import datetime

from .connection import DatabaseConnection
from ..utils.log_util import get_logger

logger = get_logger(__name__)


class SourceAccountRepository:
    """Repository for source_accounts table - provider-agnostic account management"""
    
    @staticmethod
    async def get_or_create(
        provider: str,
        external_account_id: str,
        handle: str,
        account_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get existing source account or create new one.
        
        Args:
            provider: Provider name (github, gitlab, bitbucket)
            external_account_id: Provider's account ID
            handle: Username or org name
            account_type: Account type (USER, ORG, GROUP, WORKSPACE)
            metadata: Provider-specific metadata
            
        Returns:
            Dictionary containing the source account
        """
        async with DatabaseConnection.acquire() as conn:
            # Try to get existing
            account = await conn.fetchrow(
                """
                SELECT * FROM source_accounts
                WHERE provider = $1 AND external_account_id = $2
                """,
                provider, external_account_id
            )
            
            if account:
                # Update handle/metadata if changed
                await conn.execute(
                    """
                    UPDATE source_accounts
                    SET handle = $1, metadata = $2, updated_at = NOW()
                    WHERE id = $3
                    """,
                    handle, json.dumps(metadata) if metadata else None, account['id']
                )
                logger.info(f"Updated {provider} account: {handle} (type: {account_type})")
                return dict(account)
            
            # Create new
            account = await conn.fetchrow(
                """
                INSERT INTO source_accounts (provider, external_account_id, handle, account_type, metadata)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                provider, external_account_id, handle, account_type,
                json.dumps(metadata) if metadata else None
            )
            
            logger.info(f"Created {provider} account: {handle} (type: {account_type})")
            return dict(account)
    
    @staticmethod
    async def get_by_id(account_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get source account by ID.
        
        Args:
            account_id: Source account UUID
            
        Returns:
            Dictionary containing the source account or None
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM source_accounts WHERE id = $1",
                account_id
            )
            return dict(row) if row else None
    
    @staticmethod
    async def get_active_installation(
        account_id: UUID,
        provider: str = 'github',
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the active installation for this source account.
        Currently only supports GitHub, but extensible to gitlab_installations, etc.
        
        Args:
            account_id: Source account UUID
            provider: Provider name (default: github)
            user_id: Optional Hindsight user ID. If provided, returns installation for that user.
                     If None, returns any active installation for the account (for backward compatibility).
            
        Returns:
            Dictionary containing the active installation or None
        """
        async with DatabaseConnection.acquire() as conn:
            if provider == 'github':
                if user_id:
                    # Get installation for specific user and account
                    row = await conn.fetchrow(
                        """
                        SELECT * FROM github_installations
                        WHERE source_account_id = $1 AND user_id = $2 AND is_active = TRUE
                        LIMIT 1
                        """,
                        account_id, user_id
                    )
                else:
                    # Backward compatibility: get any active installation for the account
                    row = await conn.fetchrow(
                        """
                        SELECT * FROM github_installations
                        WHERE source_account_id = $1 AND is_active = TRUE
                        LIMIT 1
                        """,
                        account_id
                    )
                return dict(row) if row else None
            else:
                # Future: gitlab_installations, bitbucket_installations
                raise NotImplementedError(f"Provider {provider} not yet supported")
    
    @staticmethod
    async def get_by_provider_and_handle(
        provider: str,
        handle: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get source account by provider and handle.
        
        Args:
            provider: Provider name (github, gitlab, bitbucket)
            handle: Account handle/username
            
        Returns:
            Dictionary containing the source account or None
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM source_accounts
                WHERE provider = $1 AND handle = $2
                LIMIT 1
                """,
                provider, handle
            )
            return dict(row) if row else None


class PrAnalysisRepository:
    """Repository for database operations on pr_analyses table"""
    
    @staticmethod
    async def create(
        repository_id: UUID,
        pr_number: int,
        head_sha: str,
        base_sha: str,
        pr_url: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a new PR analysis record.
        
        Args:
            repository_id: Repository UUID
            pr_number: Pull request number
            head_sha: Head commit SHA
            base_sha: Base commit SHA
            pr_url: Pull request URL
            config: Analysis configuration
            
        Returns:
            Dictionary containing the created PR analysis
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO pr_analyses (
                    repository_id, pr_number, head_sha, base_sha,
                    pr_url, config, status
                )
                VALUES ($1, $2, $3, $4, $5, $6, 'pending')
                RETURNING *
                """,
                repository_id, pr_number, head_sha, base_sha,
                pr_url, json.dumps(config) if config else None
            )
            
            logger.info(f"Created PR analysis {row['id']} for PR #{pr_number} in repository {repository_id}")
            return PrAnalysisRepository._parse_row(row)
    
    @staticmethod
    async def get_by_id(analysis_id: UUID) -> Optional[Dict[str, Any]]:
        """Get PR analysis by ID"""
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pr_analyses WHERE id = $1",
                analysis_id
            )
            return PrAnalysisRepository._parse_row(row) if row else None
    
    @staticmethod
    async def get_by_pr(
        repository_id: UUID,
        pr_number: int,
        head_sha: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get PR analysis by repository and PR number.
        Optionally filter by head SHA.
        """
        async with DatabaseConnection.acquire() as conn:
            if head_sha:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM pr_analyses 
                    WHERE repository_id = $1 AND pr_number = $2 AND head_sha = $3
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    repository_id, pr_number, head_sha
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM pr_analyses 
                    WHERE repository_id = $1 AND pr_number = $2
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    repository_id, pr_number
                )
            return PrAnalysisRepository._parse_row(row) if row else None
    
    @staticmethod
    async def get_all_by_pr(
        repository_id: UUID,
        pr_number: int,
        exclude_analysis_id: Optional[UUID] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all PR analyses for a repository and PR number.
        Useful for finding all previous reviews to delete.
        
        Args:
            repository_id: Repository UUID
            pr_number: Pull request number
            exclude_analysis_id: Optional analysis ID to exclude from results
            
        Returns:
            List of PR analysis dictionaries
        """
        async with DatabaseConnection.acquire() as conn:
            if exclude_analysis_id:
                rows = await conn.fetch(
                    """
                    SELECT * FROM pr_analyses 
                    WHERE repository_id = $1 AND pr_number = $2 AND id != $3
                    ORDER BY created_at DESC
                    """,
                    repository_id, pr_number, exclude_analysis_id
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM pr_analyses 
                    WHERE repository_id = $1 AND pr_number = $2
                    ORDER BY created_at DESC
                    """,
                    repository_id, pr_number
                )
            return [PrAnalysisRepository._parse_row(row) for row in rows]
    
    @staticmethod
    async def list_by_repository(
        repository_id: UUID,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List PR analyses for a repository with pagination"""
        async with DatabaseConnection.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    """
                    SELECT * FROM pr_analyses
                    WHERE repository_id = $1 AND status = $2
                    ORDER BY created_at DESC
                    LIMIT $3 OFFSET $4
                    """,
                    repository_id, status, limit, offset
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM pr_analyses
                    WHERE repository_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    repository_id, limit, offset
                )
            return [PrAnalysisRepository._parse_row(row) for row in rows]
    
    @staticmethod
    async def list_for_user(
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        List PR analyses for all repositories owned by user with pagination.
        Returns tuple of (analyses, total_count).
        """
        async with DatabaseConnection.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    """
                    SELECT pa.* FROM pr_analyses pa
                    JOIN repositories r ON pa.repository_id = r.id
                    WHERE r.user_id = $1 AND pa.status = $2
                    ORDER BY pa.created_at DESC
                    LIMIT $3 OFFSET $4
                    """,
                    user_id, status, limit, offset
                )
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM pr_analyses pa
                    JOIN repositories r ON pa.repository_id = r.id
                    WHERE r.user_id = $1 AND pa.status = $2
                    """,
                    user_id, status
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT pa.* FROM pr_analyses pa
                    JOIN repositories r ON pa.repository_id = r.id
                    WHERE r.user_id = $1
                    ORDER BY pa.created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    user_id, limit, offset
                )
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM pr_analyses pa
                    JOIN repositories r ON pa.repository_id = r.id
                    WHERE r.user_id = $1
                    """,
                    user_id
                )
            
            return [PrAnalysisRepository._parse_row(row) for row in rows], total
    
    @staticmethod
    async def get_user_stats(user_id: str) -> Dict[str, int]:
        """
        Get PR analysis statistics for user's repositories.
        
        Returns counts of total, running, completed, and failed analyses.
        """
        async with DatabaseConnection.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT 
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE pa.status = 'running') as running,
                    COUNT(*) FILTER (WHERE pa.status = 'completed') as completed,
                    COUNT(*) FILTER (WHERE pa.status = 'failed') as failed
                FROM pr_analyses pa
                INNER JOIN repositories r ON pa.repository_id = r.id
                WHERE r.user_id = $1
                """,
                user_id
            )
            if result:
                # Ensure all values are integers (asyncpg may return bigint)
                return {
                    'total': int(result['total']),
                    'running': int(result['running']),
                    'completed': int(result['completed']),
                    'failed': int(result['failed'])
                }
            return {'total': 0, 'running': 0, 'completed': 0, 'failed': 0}
    
    @staticmethod
    async def update_status(
        analysis_id: UUID,
        status: str,
        progress: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update PR analysis status and progress.
        
        Args:
            analysis_id: PR analysis UUID
            status: New status
            progress: Optional progress percentage (0-100)
            error_message: Optional error message if failed
        
        Returns:
            True if update successful
        """
        async with DatabaseConnection.acquire() as conn:
            completed_at = datetime.utcnow() if status in ['completed', 'failed'] else None
            
            # Build dynamic query based on what's being updated
            updates = ["status = $2"]
            params = [analysis_id, status]
            param_idx = 3
            
            if progress is not None:
                updates.append(f"progress = ${param_idx}")
                params.append(progress)
                param_idx += 1
            
            if error_message is not None:
                updates.append(f"error_message = ${param_idx}")
                params.append(error_message)
                param_idx += 1
            
            updates.append(f"completed_at = ${param_idx}")
            params.append(completed_at)
            param_idx += 1
            
            query = f"""
                UPDATE pr_analyses
                SET {', '.join(updates)}
                WHERE id = $1
            """
            
            result = await conn.execute(query, *params)
            return result == "UPDATE 1"
    
    @staticmethod
    async def update_results(
        analysis_id: UUID,
        results: List[Dict[str, Any]],
        input_tokens: int,
        output_tokens: int
    ) -> bool:
        """Update PR analysis with results and token counts"""
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE pr_analyses
                SET results = $1, input_tokens = $2, output_tokens = $3,
                    status = 'completed', completed_at = NOW()
                WHERE id = $4
                """,
                json.dumps(results), input_tokens, output_tokens, analysis_id
            )
            return result == "UPDATE 1"
    
    @staticmethod
    async def update_github_review_id(analysis_id: UUID, review_id: int) -> bool:
        """
        Update the GitHub review ID for a PR analysis.
        
        Args:
            analysis_id: PR analysis UUID
            review_id: GitHub review ID
            
        Returns:
            True if updated, False if not found
        """
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                "UPDATE pr_analyses SET github_review_id = $1 WHERE id = $2",
                review_id, analysis_id
            )
            if result == "UPDATE 1":
                logger.info(f"Updated GitHub review ID {review_id} for analysis {analysis_id}")
            return result == "UPDATE 1"
    
    @staticmethod
    async def delete(analysis_id: UUID) -> bool:
        """Delete PR analysis"""
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM pr_analyses WHERE id = $1",
                analysis_id
            )
            return result == "DELETE 1"
    
    @staticmethod
    async def get_by_repo_pr_sha(
        repository_id: UUID,
        pr_number: int,
        head_sha: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get PR analysis by repository, PR number, and head SHA.
        This matches the unique constraint and is used to check for duplicates.
        
        Args:
            repository_id: Repository UUID
            pr_number: PR number
            head_sha: Head commit SHA
            
        Returns:
            PR analysis record or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM pr_analyses
                WHERE repository_id = $1 AND pr_number = $2 AND head_sha = $3
                """,
                repository_id, pr_number, head_sha
            )
            return PrAnalysisRepository._parse_row(row) if row else None
    
    @staticmethod
    async def reset_for_reanalysis(analysis_id: UUID) -> bool:
        """
        Reset an existing PR analysis to rerun it.
        Clears results and error state, resets status to pending.
        Used when a PR is reopened with the same head SHA.
        
        Args:
            analysis_id: PR analysis UUID
            
        Returns:
            True if update succeeded, False otherwise
        """
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE pr_analyses
                SET status = 'pending',
                    results = NULL,
                    error_message = NULL,
                    completed_at = NULL,
                    input_tokens = 0,
                    output_tokens = 0,
                    created_at = NOW()
                WHERE id = $1
                """,
                analysis_id
            )
            success = result == "UPDATE 1"
            if success:
                logger.info(f"Reset PR analysis {analysis_id} for reanalysis")
            return success
    
    @staticmethod
    def _parse_row(row) -> Dict[str, Any]:
        """Parse database row to dictionary with JSON fields deserialized"""
        if not row:
            return None
        
        data = dict(row)
        
        # Deserialize JSON fields
        if data.get('config') and isinstance(data['config'], str):
            data['config'] = json.loads(data['config'])
        
        if data.get('results') and isinstance(data['results'], str):
            data['results'] = json.loads(data['results'])
        
        return data


class WebhookEventRepository:
    """Repository for database operations on webhook_events table"""
    
    @staticmethod
    async def create(
        event_type: str,
        payload: Dict[str, Any],
        delivery_id: Optional[str] = None,
        signature: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new webhook event record"""
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO webhook_events (
                    event_type, payload, delivery_id, signature, status
                )
                VALUES ($1, $2, $3, $4, 'received')
                RETURNING *
                """,
                event_type, json.dumps(payload), delivery_id, signature
            )
            
            logger.info(f"Created webhook event {row['id']} for type {event_type}")
            return WebhookEventRepository._parse_row(row)
    
    @staticmethod
    async def get_by_id(event_id: UUID) -> Optional[Dict[str, Any]]:
        """Get webhook event by ID"""
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM webhook_events WHERE id = $1",
                event_id
            )
            return WebhookEventRepository._parse_row(row) if row else None
    
    @staticmethod
    async def update_status(
        event_id: UUID,
        status: str,
        error_message: Optional[str] = None
    ) -> bool:
        """Update webhook event status"""
        async with DatabaseConnection.acquire() as conn:
            processed_at = datetime.utcnow() if status in ['processed', 'failed'] else None
            
            result = await conn.execute(
                """
                UPDATE webhook_events
                SET status = $1, error_message = $2, processed_at = $3
                WHERE id = $4
                """,
                status, error_message, processed_at, event_id
            )
            return result == "UPDATE 1"
    
    @staticmethod
    def _parse_row(row) -> Dict[str, Any]:
        """Parse database row to dictionary with JSON fields deserialized"""
        if not row:
            return None
        
        data = dict(row)
        
        # Deserialize payload JSON
        if data.get('payload') and isinstance(data['payload'], str):
            data['payload'] = json.loads(data['payload'])
        
        return data


class RepositoryRepository:
    """Repository for database operations on repositories table"""
    
    @staticmethod
    async def create(github_url: str, name: str, user_id: str) -> Dict[str, Any]:
        """
        Create a new repository record.
        
        Args:
            github_url: GitHub repository URL
            name: Repository name in format "org/repo"
            user_id: Kinde user ID (owner of the repository)
            
        Returns:
            Dictionary containing the created repository
            
        Raises:
            Exception: If repository with same URL already exists for this user
        """
        async with DatabaseConnection.acquire() as conn:
            # Check if repository already exists for this user
            existing = await conn.fetchrow(
                "SELECT id FROM repositories WHERE user_id = $1 AND github_url = $2",
                user_id, github_url
            )
            
            if existing:
                raise ValueError(f"Repository with URL {github_url} already exists for this user")
            
            row = await conn.fetchrow(
                """
                INSERT INTO repositories (user_id, github_url, name, status)
                VALUES ($1, $2, $3, 'ready')
                RETURNING *
                """,
                user_id, github_url, name
            )
            
            logger.info(f"Created repository {row['id']}: {name} for user {user_id}")
            return dict(row)
    
    @staticmethod
    async def get_by_id(repo_id: UUID, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get repository by ID.
        
        Args:
            repo_id: Repository UUID
            user_id: Optional user ID to verify ownership
            
        Returns:
            Repository dictionary or None if not found or user doesn't own it
        """
        async with DatabaseConnection.acquire() as conn:
            if user_id:
                # Verify ownership
                row = await conn.fetchrow(
                    "SELECT * FROM repositories WHERE id = $1 AND user_id = $2",
                    repo_id, user_id
                )
            else:
                # No ownership check (for internal use)
                row = await conn.fetchrow(
                    "SELECT * FROM repositories WHERE id = $1",
                    repo_id
                )
            return MCPServerRepository._parse_server_row(row) if row else None
    
    @staticmethod
    async def get_by_url(github_url: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get repository by GitHub URL.
        
        Args:
            github_url: GitHub repository URL
            user_id: Optional user ID to filter by owner
            
        Returns:
            Repository dictionary or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            if user_id:
                # Filter by user
                row = await conn.fetchrow(
                    "SELECT * FROM repositories WHERE github_url = $1 AND user_id = $2",
                    github_url, user_id
                )
            else:
                # No user filter (for internal use)
                row = await conn.fetchrow(
                    "SELECT * FROM repositories WHERE github_url = $1",
                    github_url
                )
            return MCPServerRepository._parse_server_row(row) if row else None
    
    @staticmethod
    async def get_by_external_id(external_repo_id: str) -> Optional[Dict[str, Any]]:
        """
        Get repository by GitHub external_repo_id.

        Args:
            external_repo_id: GitHub repository ID (from API)

        Returns:
            Repository dictionary or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM repositories WHERE external_repo_id = $1",
                external_repo_id
            )
            return dict(row) if row else None

    @staticmethod
    async def get_by_external_id_and_user(external_repo_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get repository by GitHub external_repo_id for a specific user.

        Args:
            external_repo_id: GitHub repository ID (from API)
            user_id: Hindsight user ID

        Returns:
            Repository dictionary or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM repositories
                WHERE external_repo_id = $1 AND user_id = $2
                """,
                external_repo_id, user_id
            )
            return dict(row) if row else None

    @staticmethod
    async def list_all(user_id: str, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        List all repositories for a user with pagination.
        
        Args:
            user_id: User ID to filter repositories by owner
            limit: Maximum number of repositories to return
            offset: Number of repositories to skip
            
        Returns:
            List of repository dictionaries owned by the user
        """
        async with DatabaseConnection.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM repositories
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_id, limit, offset
            )
            return [dict(row) for row in rows]
    
    @staticmethod
    async def update_clone_path(repo_id: UUID, clone_path: str, status: str = "ready") -> None:
        """
        Update the clone path and status for a repository.
        
        Args:
            repo_id: Repository UUID
            clone_path: Local filesystem path where repo is cloned
            status: New status (default: "ready")
        """
        async with DatabaseConnection.acquire() as conn:
            await conn.execute(
                """
                UPDATE repositories
                SET clone_path = $2, status = $3, updated_at = NOW()
                WHERE id = $1
                """,
                repo_id, clone_path, status
            )
            logger.info(f"Updated repository {repo_id} clone path: {clone_path}")
    
    @staticmethod
    async def update_status(repo_id: UUID, status: str, error_message: str = None) -> None:
        """
        Update repository status.
        
        Args:
            repo_id: Repository UUID
            status: New status
            error_message: Optional error message if status is 'error'
        """
        async with DatabaseConnection.acquire() as conn:
            await conn.execute(
                """
                UPDATE repositories
                SET status = $2, updated_at = NOW()
                WHERE id = $1
                """,
                repo_id, status
            )
            logger.info(f"Updated repository {repo_id} status: {status}")
    
    @staticmethod
    async def delete(repo_id: UUID, user_id: str) -> bool:
        """
        Delete a repository.
        Cascades to delete all associated analyses and results.
        Only allows deletion if the user owns the repository.
        
        Args:
            repo_id: Repository UUID
            user_id: User ID to verify ownership
            
        Returns:
            True if deleted, False if not found or user doesn't own it
        """
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM repositories WHERE id = $1 AND user_id = $2",
                repo_id, user_id
            )
            deleted = result.split()[-1] == "1"
            if deleted:
                logger.info(f"Deleted repository {repo_id} for user {user_id}")
            return deleted
    
    @staticmethod
    async def update_repositories_connection(
        user_id: str,
        old_connection_id: str,
        new_connection_id: str
    ) -> int:
        """
        Update all repositories from old connection to new connection.
        Used when user reinstalls GitHub App or updates permissions.
        
        Args:
            user_id: User ID who owns the repositories
            old_connection_id: UUID of old (inactive) connection
            new_connection_id: UUID of new (active) connection
            
        Returns:
            Number of repositories updated
        """
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE repositories
                SET connection_id = $1, updated_at = NOW()
                WHERE user_id = $2 AND connection_id = $3
                """,
                new_connection_id, user_id, old_connection_id
            )
            count = int(result.split()[-1])
            logger.info(f"Updated {count} repositories from connection {old_connection_id} to {new_connection_id}")
            return count


class AnalysisRepository:
    """Repository for database operations on analyses table"""
    
    @staticmethod
    async def create(
        repository_id: UUID,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new analysis record.
        
        Args:
            repository_id: Parent repository UUID
            config: Analysis configuration dictionary
            
        Returns:
            Dictionary containing the created analysis
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO analyses (repository_id, status, config)
                VALUES ($1, 'pending', $2)
                RETURNING *
                """,
                repository_id, json.dumps(config)
            )
            
            logger.info(f"Created analysis {row['id']} for repository {repository_id}")
            result = dict(row)
            # Parse JSON config back to dictionary
            if result.get('config'):
                result['config'] = json.loads(result['config']) if isinstance(result['config'], str) else result['config']
            return result
    
    @staticmethod
    async def get_by_id(analysis_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get analysis by ID.
        
        Args:
            analysis_id: Analysis UUID
            
        Returns:
            Analysis dictionary or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM analyses WHERE id = $1",
                analysis_id
            )
            if row:
                result = dict(row)
                # Parse JSON config if present
                if result.get('config'):
                    result['config'] = json.loads(result['config']) if isinstance(result['config'], str) else result['config']
                return result
            return None
    
    @staticmethod
    async def list_by_repository(
        repository_id: UUID,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        List all analyses for a repository.
        
        Args:
            repository_id: Repository UUID
            limit: Maximum number of analyses to return
            offset: Number of analyses to skip
            
        Returns:
            List of analysis dictionaries
        """
        async with DatabaseConnection.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM analyses
                WHERE repository_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                repository_id, limit, offset
            )
            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON config if present
                if result.get('config'):
                    result['config'] = json.loads(result['config']) if isinstance(result['config'], str) else result['config']
                results.append(result)
            return results
    
    @staticmethod
    async def update_status(
        analysis_id: UUID,
        status: str,
        progress: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> None:
        """
        Update analysis status and progress.
        
        Args:
            analysis_id: Analysis UUID
            status: New status
            progress: Optional progress percentage (0-100)
            error_message: Optional error message if failed
        """
        async with DatabaseConnection.acquire() as conn:
            # Build dynamic query based on what's being updated
            updates = ["status = $2"]
            params = [analysis_id, status]
            param_idx = 3
            
            if progress is not None:
                updates.append(f"progress = ${param_idx}")
                params.append(progress)
                param_idx += 1
            
            if error_message is not None:
                updates.append(f"error_message = ${param_idx}")
                params.append(error_message)
                param_idx += 1
            
            # Update timestamps based on status
            if status == "running":
                updates.append("started_at = NOW()")
            elif status in ["completed", "failed"]:
                updates.append("completed_at = NOW()")
            
            query = f"""
                UPDATE analyses
                SET {', '.join(updates)}
                WHERE id = $1
            """
            
            await conn.execute(query, *params)
            logger.debug(f"Updated analysis {analysis_id} status: {status}")
    
    @staticmethod
    async def delete(analysis_id: UUID) -> bool:
        """
        Delete an analysis.
        Cascades to delete all associated analysis_results.
        Does NOT delete function_analysis_cache (tied to repository).
        
        Args:
            analysis_id: Analysis UUID
            
        Returns:
            True if deleted, False if not found
        """
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM analyses WHERE id = $1",
                analysis_id
            )
            deleted = result.split()[-1] == "1"
            if deleted:
                logger.info(f"Deleted analysis {analysis_id} and its results")
            return deleted
    
    @staticmethod
    async def update_issue_counts(
        analysis_id: UUID,
        total: int,
        critical: int,
        high: int,
        medium: int,
        low: int
    ) -> None:
        """
        Update issue counts for an analysis.
        
        Args:
            analysis_id: Analysis UUID
            total: Total number of issues
            critical: Number of critical issues
            high: Number of high issues
            medium: Number of medium issues
            low: Number of low issues
        """
        async with DatabaseConnection.acquire() as conn:
            await conn.execute(
                """
                UPDATE analyses
                SET total_issues = $2,
                    critical_issues = $3,
                    high_issues = $4,
                    medium_issues = $5,
                    low_issues = $6
                WHERE id = $1
                """,
                analysis_id, total, critical, high, medium, low
            )
            logger.debug(f"Updated analysis {analysis_id} issue counts: {total} total")


class ResultsRepository:
    """Repository for database operations on analysis_results table"""
    
    @staticmethod
    async def save_results(analysis_id: UUID, results: List[Dict[str, Any]]) -> int:
        """
        Save analysis results in bulk.
        
        Args:
            analysis_id: Parent analysis UUID
            results: List of result dictionaries
            
        Returns:
            Number of results saved
        """
        if not results:
            return 0
        
        async with DatabaseConnection.acquire() as conn:
            async with conn.transaction():
                # Prepare data for bulk insert
                values = []
                for result in results:
                    values.append((
                        analysis_id,
                        result.get('file_path', ''),
                        result.get('function') or result.get('function_name'),
                        _parse_line_number_as_text(result.get('lines') or result.get('line_number')),
                        result.get('kind') or result.get('severity'),
                        result.get('issueType') or result.get('category'),
                        result.get('description') or result.get('issue', ''),
                        result.get('Impact') or result.get('impact'),
                        result.get('Potential solution') or result.get('potentialSolution') or result.get('suggestion'),
                        result.get('evidence', '')  # Extract evidence field
                    ))
                
                # Bulk insert
                await conn.executemany(
                    """
                    INSERT INTO analysis_results (
                        analysis_id, file_path, function_name,
                        line_number, severity, issue_type, description,
                        impact, potential_solution, evidence
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    values
                )
                
                # Calculate and update issue counts
                stats = _calculate_issue_stats(results)
                await AnalysisRepository.update_issue_counts(
                    analysis_id,
                    stats['total'],
                    stats['critical'],
                    stats['high'],
                    stats['medium'],
                    stats['low']
                )
            
            logger.info(f"Saved {len(results)} results for analysis {analysis_id}")
            return len(results)
    
    @staticmethod
    async def get_paginated_results(
        analysis_id: UUID,
        limit: int = 100,
        offset: int = 0,
        severity: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Get paginated results for an analysis.
        
        Args:
            analysis_id: Analysis UUID
            limit: Maximum number of results to return
            offset: Number of results to skip
            severity: Optional severity filter
            
        Returns:
            Tuple of (results list, total count)
        """
        async with DatabaseConnection.acquire() as conn:
            # Build query with optional severity filter
            where_clause = "WHERE analysis_id = $1"
            params = [analysis_id]
            
            if severity:
                where_clause += " AND severity = $2"
                params.append(severity)
                count_params = params.copy()
                params.extend([limit, offset])
            else:
                count_params = params.copy()
                params.extend([limit, offset])
            
            # Get total count
            count_query = f"SELECT COUNT(*) FROM analysis_results {where_clause}"
            total = await conn.fetchval(count_query, *count_params)
            
            # Get paginated results
            param_limit = len(params) - 1
            param_offset = len(params)
            results_query = f"""
                SELECT * FROM analysis_results
                {where_clause}
                ORDER BY 
                    CASE severity
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        WHEN 'low' THEN 4
                        ELSE 5
                    END,
                    created_at ASC
                LIMIT ${param_limit} OFFSET ${param_offset}
            """
            
            rows = await conn.fetch(results_query, *params)
            results = [dict(row) for row in rows]
            
            return results, total


def _calculate_issue_stats(issues: List[Dict[str, Any]]) -> Dict[str, int]:
    """Calculate issue statistics."""
    stats = {
        'total': len(issues),
        'critical': 0,
        'high': 0,
        'medium': 0,
        'low': 0
    }
    
    for issue in issues:
        severity = (issue.get('kind') or issue.get('severity', '')).lower()
        if severity in stats:
            stats[severity] += 1
    
    return stats


def _parse_line_number_as_text(line_value: Any) -> Optional[str]:
    """Parse line number from various formats and return as text to support ranges."""
    if line_value is None:
        return None
    
    if isinstance(line_value, int):
        return str(line_value)
    
    if isinstance(line_value, str):
        # Return the string as-is to support ranges like "234-898"
        return line_value.strip() if line_value.strip() else None
    
    # Convert other types to string
    return str(line_value) if line_value is not None else None

def _parse_line_number(line_value: Any) -> Optional[int]:
    """Legacy function - parse line number from various formats (kept for backward compatibility)."""
    if line_value is None:
        return None

    if isinstance(line_value, int):
        return line_value

    if isinstance(line_value, str):
        # Try to extract first number from string like "45-50" or "45"
        import re
        match = re.search(r'\d+', line_value)
        if match:
            return int(match.group())

    return None


class MCPServerRepository:
    """Repository for database operations on mcp_servers table"""

    @staticmethod
    def _parse_server_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse server row and convert JSON/JSONB strings to dicts.

        Args:
            row: Database row as dictionary

        Returns:
            Parsed dictionary with capabilities as dict
        """
        result = dict(row)
        # Parse JSONB capabilities field if it's a string
        if isinstance(result.get('capabilities'), str):
            import json as json_module
            try:
                result['capabilities'] = json_module.loads(result['capabilities'])
            except (json_module.JSONDecodeError, TypeError):
                result['capabilities'] = {}
        return result

    @staticmethod
    async def create(
        name: str,
        server_type: str,
        connection_type: str,
        endpoint: str,
        auth_config_encrypted: str,
        created_by: str,
        description: Optional[str] = None,
        is_enabled: bool = True
    ) -> Dict[str, Any]:
        """
        Create a new MCP server record.

        Args:
            name: Unique server name
            server_type: Type of server (datadog, splunk, etc.)
            connection_type: Connection protocol (stdio, sse, http)
            endpoint: Server endpoint URL or command
            auth_config_encrypted: Encrypted authentication configuration
            created_by: Kinde user ID of admin who created it
            description: Optional description
            is_enabled: Whether server is enabled

        Returns:
            Dictionary containing the created server

        Raises:
            ValueError: If server with same name already exists
        """
        async with DatabaseConnection.acquire() as conn:
            # Check if server with same name exists
            existing = await conn.fetchrow(
                "SELECT id FROM mcp_servers WHERE name = $1",
                name
            )

            if existing:
                raise ValueError(f"MCP server with name '{name}' already exists")

            row = await conn.fetchrow(
                """
                INSERT INTO mcp_servers (
                    name, description, server_type, connection_type,
                    endpoint, auth_config, status, is_enabled, created_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, 'active', $7, $8)
                RETURNING *
                """,
                name, description, server_type, connection_type,
                endpoint, auth_config_encrypted, is_enabled, created_by
            )

            logger.info(f"Created MCP server {row['id']}: {name} by {created_by}")
            return MCPServerRepository._parse_server_row(row)

    @staticmethod
    async def get_by_id(server_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get MCP server by ID.

        Args:
            server_id: Server UUID

        Returns:
            Server dictionary or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM mcp_servers WHERE id = $1",
                server_id
            )
            return MCPServerRepository._parse_server_row(row) if row else None

    @staticmethod
    async def get_by_name(name: str) -> Optional[Dict[str, Any]]:
        """
        Get MCP server by name.

        Args:
            name: Server name

        Returns:
            Server dictionary or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM mcp_servers WHERE name = $1",
                name
            )
            return MCPServerRepository._parse_server_row(row) if row else None

    @staticmethod
    async def list_all() -> List[Dict[str, Any]]:
        """
        List all MCP servers.

        Returns:
            List of all MCP server dictionaries
        """
        async with DatabaseConnection.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM mcp_servers ORDER BY created_at DESC"
            )
            return [MCPServerRepository._parse_server_row(row) for row in rows]

    @staticmethod
    async def list_enabled() -> List[Dict[str, Any]]:
        """
        List only enabled MCP servers.

        Returns:
            List of enabled MCP server dictionaries
        """
        async with DatabaseConnection.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM mcp_servers WHERE is_enabled = true ORDER BY created_at DESC"
            )
            return [MCPServerRepository._parse_server_row(row) for row in rows]

    @staticmethod
    async def update(
        server_id: UUID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        server_type: Optional[str] = None,
        connection_type: Optional[str] = None,
        endpoint: Optional[str] = None,
        auth_config_encrypted: Optional[str] = None,
        capabilities: Optional[Dict] = None,
        status: Optional[str] = None,
        is_enabled: Optional[bool] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Update MCP server (partial update).

        Args:
            server_id: Server UUID
            name: New name (must be unique)
            description: New description
            server_type: New server type
            connection_type: New connection type
            endpoint: New endpoint
            auth_config_encrypted: New encrypted auth config
            capabilities: New capabilities
            status: New status
            is_enabled: New enabled state

        Returns:
            Updated server dictionary or None if not found

        Raises:
            ValueError: If new name conflicts with existing server
        """
        async with DatabaseConnection.acquire() as conn:
            # Build dynamic UPDATE query
            updates = []
            params = []
            param_idx = 1

            if name is not None:
                # Check name uniqueness
                existing = await conn.fetchrow(
                    "SELECT id FROM mcp_servers WHERE name = $1 AND id != $2",
                    name, server_id
                )
                if existing:
                    raise ValueError(f"MCP server with name '{name}' already exists")
                updates.append(f"name = ${param_idx}")
                params.append(name)
                param_idx += 1

            if description is not None:
                updates.append(f"description = ${param_idx}")
                params.append(description)
                param_idx += 1

            if server_type is not None:
                updates.append(f"server_type = ${param_idx}")
                params.append(server_type)
                param_idx += 1

            if connection_type is not None:
                updates.append(f"connection_type = ${param_idx}")
                params.append(connection_type)
                param_idx += 1

            if endpoint is not None:
                updates.append(f"endpoint = ${param_idx}")
                params.append(endpoint)
                param_idx += 1

            if auth_config_encrypted is not None:
                updates.append(f"auth_config = ${param_idx}")
                params.append(auth_config_encrypted)
                param_idx += 1

            if capabilities is not None:
                updates.append(f"capabilities = ${param_idx}::jsonb")
                params.append(json.dumps(capabilities))
                param_idx += 1

            if status is not None:
                updates.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if is_enabled is not None:
                updates.append(f"is_enabled = ${param_idx}")
                params.append(is_enabled)
                param_idx += 1

            if not updates:
                # No updates provided, just return current record
                return await MCPServerRepository.get_by_id(server_id)

            # Always update updated_at
            updates.append(f"updated_at = NOW()")

            # Add server_id as last parameter
            params.append(server_id)

            query = f"""
                UPDATE mcp_servers
                SET {', '.join(updates)}
                WHERE id = ${param_idx}
                RETURNING *
            """

            row = await conn.fetchrow(query, *params)
            if row:
                logger.info(f"Updated MCP server {server_id}")
            return MCPServerRepository._parse_server_row(row) if row else None

    @staticmethod
    async def delete(server_id: UUID) -> bool:
        """
        Delete MCP server.

        Args:
            server_id: Server UUID

        Returns:
            True if deleted, False if not found
        """
        async with DatabaseConnection.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM mcp_servers WHERE id = $1",
                server_id
            )
            deleted = result == "DELETE 1"
            if deleted:
                logger.info(f"Deleted MCP server {server_id}")
            return deleted

    @staticmethod
    async def update_capabilities(server_id: UUID, capabilities: Dict) -> Optional[Dict[str, Any]]:
        """
        Update server capabilities (discovered from connection test).

        Args:
            server_id: Server UUID
            capabilities: Capabilities dictionary

        Returns:
            Updated server dictionary or None if not found
        """
        import json as json_module
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE mcp_servers
                SET capabilities = $1::jsonb, updated_at = NOW()
                WHERE id = $2
                RETURNING *
                """,
                json_module.dumps(capabilities), server_id
            )
            return MCPServerRepository._parse_server_row(row) if row else None

    @staticmethod
    async def update_status(server_id: UUID, status: str) -> Optional[Dict[str, Any]]:
        """
        Update server status.

        Args:
            server_id: Server UUID
            status: New status (active, inactive, error)

        Returns:
            Updated server dictionary or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE mcp_servers
                SET status = $1, updated_at = NOW()
                WHERE id = $2
                RETURNING *
                """,
                status, server_id
            )
            return MCPServerRepository._parse_server_row(row) if row else None


class SourceRepoConnectionRepository:
    """Repository for database operations on source_repo_connections table"""

    @staticmethod
    async def get_by_account_login(provider: str, account_login: str) -> Optional[Dict[str, Any]]:
        """
        Find user connection by provider account username.

        Args:
            provider: Provider name (e.g., 'github')
            account_login: Username on the provider (e.g., GitHub username)

        Returns:
            Connection dictionary with user_id, or None if not found
        """
        async with DatabaseConnection.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 
                    i.*, a.provider, a.handle as account_login, a.account_type
                FROM github_installations i
                JOIN source_accounts a ON i.source_account_id = a.id
                WHERE a.provider = $1 AND a.handle = $2 AND i.is_active = true
                ORDER BY i.last_used_at DESC NULLS LAST
                LIMIT 1
                """,
                provider, account_login
            )
            return dict(row) if row else None

