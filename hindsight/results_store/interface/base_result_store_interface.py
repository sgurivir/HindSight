#!/usr/bin/env python3
"""
Base Result Store Interface
Core publisher-subscriber interface for result storage systems
"""

from typing import Any, Dict, List, Optional, Set
from abc import ABC, abstractmethod
import threading


class ResultSubscriber(ABC):
    """Base interface for result store subscribers"""

    @abstractmethod
    def on_result_added(self, result_id: str, result: Dict[str, Any]) -> None:
        """
        Called when a new result is added to the store

        Args:
            result_id: Unique identifier for the result
            result: The result data that was added
        """
        pass

    @abstractmethod
    def on_result_updated(self, result_id: str, old_result: Dict[str, Any], new_result: Dict[str, Any]) -> None:
        """
        Called when an existing result is updated

        Args:
            result_id: Unique identifier for the result
            old_result: The previous result data
            new_result: The updated result data
        """
        pass



class BaseResultsCacheInterface(ABC):
    """
    Abstract base class for publisher-subscriber result storage systems
    Provides core functionality for managing results and notifying subscribers
    """

    def __init__(self):
        self._subscribers: Set[ResultSubscriber] = set()
        self._lock = threading.RLock()

    def subscribe(self, subscriber: ResultSubscriber) -> None:
        """
        Subscribe to result store notifications

        Args:
            subscriber: The subscriber to add
        """
        with self._lock:
            self._subscribers.add(subscriber)

    def unsubscribe(self, subscriber: ResultSubscriber) -> None:
        """
        Unsubscribe from result store notifications

        Args:
            subscriber: The subscriber to remove
        """
        with self._lock:
            self._subscribers.discard(subscriber)

    def _notify_result_added(self, result_id: str, result: Dict[str, Any]) -> None:
        """Notify all subscribers that a result was added"""
        with self._lock:
            for subscriber in self._subscribers:
                try:
                    subscriber.on_result_added(result_id, result)
                except Exception as e:
                    # Log error but continue notifying other subscribers
                    import logging
                    logging.getLogger(__name__).error(
                        f"Error notifying subscriber {type(subscriber).__name__}: {e}",
                        exc_info=True
                    )

    def _notify_result_updated(self, result_id: str, old_result: Dict[str, Any], new_result: Dict[str, Any]) -> None:
        """Notify all subscribers that a result was updated"""
        with self._lock:
            for subscriber in self._subscribers:
                try:
                    subscriber.on_result_updated(result_id, old_result, new_result)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(
                        f"Error notifying subscriber {type(subscriber).__name__}: {e}",
                        exc_info=True
                    )


    @abstractmethod
    def initialize(self, location: str) -> None:
        """
        Initialize the result store with a storage location

        Args:
            location: Path or identifier for where results are stored
        """
        pass

    @abstractmethod
    def publish_result(self, repo_name: str, result: Dict[str, Any]) -> str:
        """
        Publish a new result to the store for a specific repository

        Args:
            repo_name: Name of the repository
            result: The result data to store

        Returns:
            Unique identifier for the stored result
        """
        pass


    @abstractmethod
    def update_result(self, repo_name: str, result_id: str, updated_result: Dict[str, Any]) -> bool:
        """
        Update an existing result for a specific repository

        Args:
            repo_name: Name of the repository
            result_id: Unique identifier for the result
            updated_result: The updated result data

        Returns:
            True if the result was updated, False if not found
        """
        pass

