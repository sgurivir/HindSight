#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
TTL Manager Module
Manages Time-To-Live for system prompts with automatic resending logic
"""

import time
import os
from typing import Optional, Dict, Any
from pathlib import Path
from dataclasses import dataclass, asdict

from ...utils.file_util import write_json_file, read_json_file, ensure_directory_exists
from ...utils.hash_util import HashUtil
from ...utils.log_util import get_logger

logger = get_logger(__name__)

# Constants
# Claude's ephemeral cache lasts approximately 5 minutes (300 seconds)
# We use slightly shorter values to ensure we don't conflict with server-side caching
DEFAULT_TTL_SECONDS = 300  # 5 minutes TTL to match Claude's ephemeral cache
DEFAULT_RESEND_THRESHOLD_SECONDS = 240  # 4 minutes - resend after this time
TTL_CACHE_FILENAME = "system_prompt_ttl_cache.json"


@dataclass
class SystemPromptTTL:
    """Data class for system prompt TTL information"""
    prompt_hash: str
    first_sent_timestamp: float
    last_sent_timestamp: float
    ttl_seconds: int
    resend_threshold_seconds: int
    send_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SystemPromptTTL':
        """Create instance from dictionary"""
        return cls(**data)


class TTLManager:
    """
    Manages TTL (Time-To-Live) for system prompts to complement Claude's ephemeral caching.

    Features:
    - Tracks when system prompts were sent for client-side optimization
    - Uses TTL aligned with Claude's ~5-minute ephemeral cache (default 300 seconds)
    - Provides resend threshold for proactive cache warming (default 240 seconds)
    - Persists TTL data to disk for recovery across restarts
    - Uses content hash to identify unique system prompts

    Note: This is complementary to Claude's server-side caching, not a replacement.
    Claude's API handles the actual cache management with cache_control directives.
    """

    def __init__(
        self,
        cache_dir: str = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        resend_threshold_seconds: int = DEFAULT_RESEND_THRESHOLD_SECONDS
    ):
        """
        Initialize TTL Manager.

        Args:
            cache_dir: Directory to store TTL cache file (defaults to temp directory)
            ttl_seconds: TTL duration in seconds (default: 3600)
            resend_threshold_seconds: Resend threshold in seconds (default: 3000)
        """
        self.ttl_seconds = ttl_seconds
        self.resend_threshold_seconds = resend_threshold_seconds

        # Set up cache directory and file path
        if cache_dir is None:
            cache_dir = os.path.join(os.path.expanduser("~"), ".hindsight_cache")

        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / TTL_CACHE_FILENAME

        # Ensure cache directory exists
        ensure_directory_exists(str(self.cache_dir))

        # In-memory cache of TTL data
        self._ttl_cache: Dict[str, SystemPromptTTL] = {}

        # Load existing TTL data
        self._load_ttl_cache()

        logger.info(f"TTL Manager initialized with TTL={ttl_seconds}s, resend_threshold={resend_threshold_seconds}s")
        logger.debug(f"TTL cache file: {self.cache_file}")

    def _generate_prompt_hash(self, system_prompt: str) -> str:
        """
        Generate a hash for the system prompt content.

        Args:
            system_prompt: System prompt content

        Returns:
            str: SHA256 hash of the prompt content
        """
        return HashUtil.hash_for_prompt_sha256(system_prompt, truncate_length=16)

    def _load_ttl_cache(self) -> None:
        """Load TTL cache from disk"""
        try:
            if self.cache_file.exists():
                cache_data = read_json_file(str(self.cache_file))
                if cache_data:
                    for prompt_hash, ttl_data in cache_data.items():
                        try:
                            self._ttl_cache[prompt_hash] = SystemPromptTTL.from_dict(ttl_data)
                        except (KeyError, TypeError) as e:
                            logger.warning(f"Invalid TTL data for hash {prompt_hash}: {e}")
                            continue

                    logger.debug(f"Loaded {len(self._ttl_cache)} TTL entries from cache")
                else:
                    logger.debug("TTL cache file exists but is empty")
            else:
                logger.debug("No existing TTL cache file found")

        except Exception as e:
            logger.warning(f"Error loading TTL cache: {e}")
            self._ttl_cache = {}

    def _save_ttl_cache(self) -> None:
        """Save TTL cache to disk"""
        try:
            cache_data = {
                prompt_hash: ttl_info.to_dict()
                for prompt_hash, ttl_info in self._ttl_cache.items()
            }

            success = write_json_file(str(self.cache_file), cache_data)
            if success:
                logger.debug(f"Saved {len(cache_data)} TTL entries to cache")
            else:
                logger.warning("Failed to save TTL cache to disk")

        except Exception as e:
            logger.error(f"Error saving TTL cache: {e}")

    def _cleanup_expired_entries(self) -> None:
        """Remove expired TTL entries from cache"""
        current_time = time.time()
        expired_hashes = []

        for prompt_hash, ttl_info in self._ttl_cache.items():
            if current_time - ttl_info.first_sent_timestamp > ttl_info.ttl_seconds:
                expired_hashes.append(prompt_hash)

        for prompt_hash in expired_hashes:
            del self._ttl_cache[prompt_hash]
            logger.debug(f"Removed expired TTL entry: {prompt_hash}")

        if expired_hashes:
            logger.info(f"Cleaned up {len(expired_hashes)} expired TTL entries")
            self._save_ttl_cache()

    def should_resend_system_prompt(self, system_prompt: str) -> bool:
        """
        Check if system prompt should be resent based on TTL logic.

        Args:
            system_prompt: System prompt content

        Returns:
            bool: True if system prompt should be resent
        """
        prompt_hash = self._generate_prompt_hash(system_prompt)
        current_time = time.time()

        # Clean up expired entries periodically
        self._cleanup_expired_entries()

        # Check if we have TTL info for this prompt
        if prompt_hash not in self._ttl_cache:
            logger.info(f"New system prompt detected (hash: {prompt_hash}), will send")
            return True

        ttl_info = self._ttl_cache[prompt_hash]

        # Check if TTL has expired
        time_since_first_sent = current_time - ttl_info.first_sent_timestamp
        if time_since_first_sent > ttl_info.ttl_seconds:
            logger.info(f"System prompt TTL expired ({time_since_first_sent:.1f}s > {ttl_info.ttl_seconds}s), will resend")
            return True

        # Check if resend threshold has been reached
        time_since_last_sent = current_time - ttl_info.last_sent_timestamp
        if time_since_last_sent > ttl_info.resend_threshold_seconds:
            logger.info(f"System prompt resend threshold reached ({time_since_last_sent:.1f}s > {ttl_info.resend_threshold_seconds}s), will resend")
            return True

        # No need to resend
        remaining_ttl = ttl_info.ttl_seconds - time_since_first_sent
        time_until_resend = ttl_info.resend_threshold_seconds - time_since_last_sent
        logger.debug(f"System prompt cached (hash: {prompt_hash}), TTL remaining: {remaining_ttl:.1f}s, resend in: {time_until_resend:.1f}s")
        return False

    def record_system_prompt_sent(self, system_prompt: str) -> None:
        """
        Record that a system prompt was sent.

        Args:
            system_prompt: System prompt content that was sent
        """
        prompt_hash = self._generate_prompt_hash(system_prompt)
        current_time = time.time()

        if prompt_hash in self._ttl_cache:
            # Update existing entry
            ttl_info = self._ttl_cache[prompt_hash]
            ttl_info.last_sent_timestamp = current_time
            ttl_info.send_count += 1
            logger.info(f"Updated system prompt timestamp (hash: {prompt_hash}, send_count: {ttl_info.send_count})")
        else:
            # Create new entry
            ttl_info = SystemPromptTTL(
                prompt_hash=prompt_hash,
                first_sent_timestamp=current_time,
                last_sent_timestamp=current_time,
                ttl_seconds=self.ttl_seconds,
                resend_threshold_seconds=self.resend_threshold_seconds,
                send_count=1
            )
            self._ttl_cache[prompt_hash] = ttl_info
            logger.info(f"Recorded new system prompt (hash: {prompt_hash})")

        # Save to disk
        self._save_ttl_cache()

    def get_ttl_info(self, system_prompt: str) -> Optional[SystemPromptTTL]:
        """
        Get TTL information for a system prompt.

        Args:
            system_prompt: System prompt content

        Returns:
            SystemPromptTTL: TTL information or None if not found
        """
        prompt_hash = self._generate_prompt_hash(system_prompt)
        return self._ttl_cache.get(prompt_hash)

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the TTL cache.

        Returns:
            Dict: Cache statistics
        """
        current_time = time.time()
        active_entries = 0
        expired_entries = 0

        for ttl_info in self._ttl_cache.values():
            if current_time - ttl_info.first_sent_timestamp > ttl_info.ttl_seconds:
                expired_entries += 1
            else:
                active_entries += 1

        return {
            "total_entries": len(self._ttl_cache),
            "active_entries": active_entries,
            "expired_entries": expired_entries,
            "cache_file": str(self.cache_file),
            "ttl_seconds": self.ttl_seconds,
            "resend_threshold_seconds": self.resend_threshold_seconds
        }

    def clear_cache(self) -> None:
        """Clear all TTL cache data"""
        self._ttl_cache.clear()
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
                logger.info("TTL cache cleared and file removed")
            else:
                logger.info("TTL cache cleared (no file to remove)")
        except Exception as e:
            logger.error(f"Error removing TTL cache file: {e}")