#!/usr/bin/env python3
"""
Performance Context Cache

Per-function context cache that stores Stage A output for reuse across
different call paths that share common functions.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)


class PerfContextCache:
    """
    Caches per-function context collected during Stage A.

    Each function's context (body, data types, resource patterns, threading)
    is stored by checksum so it can be reused when the same function appears
    in a different call path.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            self.cache_dir = Path(artifacts_dir) / "perf_context_cache"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._hits = 0
        self._misses = 0

    def get(self, func_name: str, checksum: str) -> Optional[Dict[str, Any]]:
        """
        Load cached context if checksum matches.

        Args:
            func_name: Function name (for logging)
            checksum: Content-based checksum of the function

        Returns:
            Cached context dict or None if not found/stale
        """
        cache_file = self.cache_dir / f"{checksum}.json"
        if not cache_file.exists():
            self._misses += 1
            return None

        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("checksum") == checksum:
                self._hits += 1
                logger.debug(f"Cache hit for {func_name} ({checksum[:8]})")
                return cached.get("context")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Cache read error for {func_name}: {e}")

        self._misses += 1
        return None

    def put(self, func_name: str, checksum: str, context: Dict[str, Any]) -> None:
        """
        Persist function context for reuse.

        Args:
            func_name: Function name
            checksum: Content-based checksum
            context: The context data to cache
        """
        cache_file = self.cache_dir / f"{checksum}.json"
        try:
            payload = {
                "function_name": func_name,
                "checksum": checksum,
                "context": context,
                "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            cache_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.debug(f"Cached context for {func_name} ({checksum[:8]})")
        except OSError as e:
            logger.warning(f"Cache write error for {func_name}: {e}")

    def invalidate(self, checksum: str) -> None:
        """Remove a cached entry by checksum."""
        cache_file = self.cache_dir / f"{checksum}.json"
        try:
            if cache_file.exists():
                cache_file.unlink()
        except OSError:
            pass

    def get_stats(self) -> Dict[str, int]:
        """Return cache hit/miss statistics."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total_cached": len(list(self.cache_dir.glob("*.json"))),
        }
