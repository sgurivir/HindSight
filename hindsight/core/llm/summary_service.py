#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Summary Service Module
Handles file summary generation using ProjectSummaryGenerator
"""

from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from ..proj_util.project_summary_generator import ProjectSummaryGenerator, SummaryConfig
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)


@dataclass
class SummaryServiceConfig:
    """Configuration for summary service"""
    repo_path: str
    api_key: str
    api_url: str
    model: str
    max_tokens: int = 64000
    temperature: float = 0.1


class SummaryService:
    """
    Service for on-demand file summary generation using ProjectSummaryGenerator.
    """

    def __init__(self, config: SummaryServiceConfig):
        """
        Initialize SummaryService with configuration.

        Args:
            config: Summary service configuration
        """
        self.config = config
        self.repo_path = Path(config.repo_path).resolve()

        # Create SummaryConfig with summary_dir for ProjectSummaryGenerator
        output_provider = get_output_directory_provider()
        artifacts_dir = output_provider.get_repo_artifacts_dir()
        summary_dir = f"{artifacts_dir}/project_summary"

        summary_config = SummaryConfig(
            repo_path=config.repo_path,
            summary_dir=summary_dir,
            api_key=config.api_key,
            api_url=config.api_url,
            model=config.model,
            ignore_dirs=set(),
            max_tokens=config.max_tokens,
            temperature=config.temperature
        )

        # Use the ProjectSummaryGenerator singleton if available, otherwise create a temporary instance
        try:
            self.summarizer = ProjectSummaryGenerator.get_instance()
            logger.info(f"Using ProjectSummaryGenerator singleton for SummaryService")
        except RuntimeError:
            # Fallback: create a temporary instance if singleton not initialized
            logger.warning("ProjectSummaryGenerator singleton not initialized, creating temporary instance")
            self.summarizer = ProjectSummaryGenerator(summary_config)

        logger.info(f"Initialized SummaryService for repo: {self.repo_path}")
        logger.info("SummaryService operates in on-demand mode using ProjectSummaryGenerator")

    def get_file_summary(self, filename: str) -> Optional[str]:
        """
        Get summary for a file on-demand using ProjectSummaryGenerator.

        Args:
            filename: Name of the file to get summary for (may include partial path)

        Returns:
            str: File summary content or None if generation failed
        """
        return self.summarizer.get_file_summary_on_demand(filename)
