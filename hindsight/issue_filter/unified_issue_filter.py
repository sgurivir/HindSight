#!/usr/bin/env python3
"""
Unified Issue Filter

This module provides a unified three-level filtering system that all analyzers should use:
1. Level 1: Category-based filtering (hard filter for unwanted categories)
2. Level 2: LLM-based filtering (intelligent filter for remaining issues)
3. Level 3: Response Challenger filtering (verification filter using OpenAI-compatible tools)

Level 3 uses the shared Tools class from hindsight.core.llm.tools.tools for OpenAI-compatible
tool execution, ensuring consistency with the main code analyzer pipeline.

This ensures consistent filtering behavior across all analyzers.
"""

import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from ..utils.log_util import get_logger

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from .category_filter import CategoryBasedFilter
from .llm_filter import LLMBasedFilter
from .response_challenger import LLMResponseChallenger


class UnifiedIssueFilter:
    """
    Unified three-level issue filtering system for all analyzers.
    
    This class orchestrates category-based, LLM-based, and response challenger filtering
    to ensure consistent behavior across all analysis types.
    """
    
    def __init__(self, api_key: str, config: dict,
                 additional_allowed_categories: List[str] = None,
                 dropped_issues_dir: Optional[str] = None,
                 enable_llm_filtering: bool = True,
                 capture_evidence: bool = True,
                 file_content_provider=None,
                 directory_tree_util=None,
                 repo_path: Optional[str] = None,
                 artifacts_dir: Optional[str] = None):
        """
        Initialize the unified issue filter.
        
        Args:
            api_key: API key for LLM provider (required for Level 2 and Level 3 filtering)
            config: Configuration dictionary (same format as used by analyzers)
            additional_allowed_categories: Additional categories to allow beyond defaults (logicBug, performance)
            dropped_issues_dir: Directory to save dropped issues (optional)
            enable_llm_filtering: Whether to enable Level 2 LLM and Level 3 Response Challenger filtering (default: True)
            capture_evidence: Whether to capture validation evidence (default: True)
            file_content_provider: FileContentProvider instance for file resolution (optional)
            directory_tree_util: DirectoryTreeUtil instance for directory listing (optional)
            repo_path: Path to the repository (optional, for runTerminalCmd tool)
            artifacts_dir: Path to artifacts directory (optional, for code insights)
        """
        self.logger = get_logger(__name__)
        self.api_key = api_key
        self.config = config
        self.enable_llm_filtering = enable_llm_filtering
        self.capture_evidence = capture_evidence
        self.file_content_provider = file_content_provider
        self.directory_tree_util = directory_tree_util
        self.repo_path = repo_path
        self.artifacts_dir = artifacts_dir
        
        # Track dropped issues counts for statistics
        self.level1_dropped_count = 0
        self.level2_dropped_count = 0
        self.level3_dropped_count = 0
        
        # Initialize Level 1: Category-based filter (always enabled)
        # Now uses ALLOWLIST approach - only logicBug and performance are kept by default
        self.category_filter = CategoryBasedFilter(
            additional_allowed_categories=additional_allowed_categories,
            dropped_issues_dir=dropped_issues_dir
        )
        
        # Initialize Level 2: LLM-based filter (optional)
        self.llm_filter = None
        
        # Initialize Level 3: Response challenger filter (optional)
        self.response_challenger = None
        
        if enable_llm_filtering and api_key:
            try:
                self.llm_filter = LLMBasedFilter(api_key, config, dropped_issues_dir, file_content_provider)
                
                if self.llm_filter.is_available():
                    self.logger.info("UnifiedIssueFilter: Level 2 (LLM) filtering enabled")
                else:
                    self.logger.warning("UnifiedIssueFilter: Level 2 (LLM) filtering not available")
            except Exception as e:
                self.logger.error(f"Failed to initialize LLM filter: {e}")
                self.logger.warning("UnifiedIssueFilter: Level 2 (LLM) filtering disabled due to error")
        else:
            if not enable_llm_filtering:
                self.logger.info("UnifiedIssueFilter: Level 2 (LLM) filtering disabled by configuration")
            else:
                self.logger.warning("UnifiedIssueFilter: Level 2 (LLM) filtering disabled - no API key provided")
        
        # Initialize Level 3: Response challenger (controlled by enable_llm_filtering parameter)
        # Uses shared Tools class from hindsight.core.llm.tools.tools for OpenAI-compatible tool execution
        if enable_llm_filtering and api_key:
            try:
                self.response_challenger = LLMResponseChallenger(
                    api_key,
                    config,
                    dropped_issues_dir,
                    capture_evidence=capture_evidence,
                    file_content_provider=file_content_provider,
                    directory_tree_util=directory_tree_util,
                    repo_path=repo_path,
                    artifacts_dir=artifacts_dir
                )

                if self.response_challenger.is_available():
                    self.logger.info("UnifiedIssueFilter: Level 3 (Response Challenger) filtering enabled")
                    self.logger.info("UnifiedIssueFilter: Using shared Tools class for OpenAI-compatible tool execution")
                    if capture_evidence:
                        self.logger.info("UnifiedIssueFilter: Evidence capture enabled for Level 3")
                else:
                    self.logger.warning("UnifiedIssueFilter: Level 3 (Response Challenger) filtering not available")
            except Exception as e:
                self.logger.error(f"Failed to initialize Response Challenger: {e}")
                self.logger.warning("UnifiedIssueFilter: Level 3 (Response Challenger) filtering disabled due to error")
        else:
            if not enable_llm_filtering:
                self.logger.info("UnifiedIssueFilter: Level 3 (Response Challenger) filtering disabled by configuration")
            else:
                self.logger.warning("UnifiedIssueFilter: Level 3 (Response Challenger) filtering disabled - no API key provided")
        
        # Log final configuration
        levels_enabled = ["Level 1 (Category)"]
        if self.llm_filter and self.llm_filter.is_available():
            levels_enabled.append("Level 2 (LLM)")
        if self.response_challenger and self.response_challenger.is_available():
            levels_enabled.append("Level 3 (Response Challenger)")
        
        self.logger.info(f"UnifiedIssueFilter: Initialized with {', '.join(levels_enabled)} filtering")
    
    def filter_issues(self, issues: List[Dict[str, Any]], function_context: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Apply three-level filtering to the issues.
        
        Args:
            issues: List of issue dictionaries to filter
            function_context: Optional original function code context for Level 3 filtering
            
        Returns:
            List of issues after applying all levels of filtering
        """
        if not issues:
            return issues
        
        original_count = len(issues)
        self.logger.info(f"UnifiedIssueFilter: Starting multi-level filtering of {original_count} issues")
        
        # Reset dropped counts for this filtering session
        self.level1_dropped_count = 0
        self.level2_dropped_count = 0
        self.level3_dropped_count = 0
        
        # Level 1: Category-based filtering (hard filter)
        self.logger.info("UnifiedIssueFilter: Applying Level 1 (category-based) filtering...")
        filtered_issues = self.category_filter.filter_issues(issues)
        
        self.level1_dropped_count = original_count - len(filtered_issues)
        if self.level1_dropped_count > 0:
            self.logger.info(f"UnifiedIssueFilter: Level 1 dropped {self.level1_dropped_count} issues, {len(filtered_issues)} remaining")
        
        # Level 2: LLM-based filtering (intelligent filter)
        if self.llm_filter and self.llm_filter.is_available() and filtered_issues:
            self.logger.info("UnifiedIssueFilter: Applying Level 2 (LLM-based) filtering...")
            level1_count = len(filtered_issues)
            filtered_issues = self.llm_filter.filter_issues(filtered_issues)
            
            self.level2_dropped_count = level1_count - len(filtered_issues)
            if self.level2_dropped_count > 0:
                self.logger.info(f"UnifiedIssueFilter: Level 2 dropped {self.level2_dropped_count} issues, {len(filtered_issues)} remaining")
        else:
            if filtered_issues:
                self.logger.info("UnifiedIssueFilter: Skipping Level 2 (LLM-based) filtering - not available or disabled")
        
        # Level 3: Response challenger filtering (verification filter)
        # Uses shared Tools class from hindsight.core.llm.tools.tools for OpenAI-compatible tool execution
        if self.response_challenger and self.response_challenger.is_available() and filtered_issues:
            self.logger.info("UnifiedIssueFilter: Applying Level 3 (Response Challenger) filtering...")
            level2_count = len(filtered_issues)

            # Pass function context to Level 3 filter for better analysis
            if function_context:
                self.logger.debug("UnifiedIssueFilter: Passing function context to Level 3 filter")
            else:
                self.logger.debug("UnifiedIssueFilter: No function context available for Level 3 filter")

            filtered_issues = self.response_challenger.challenge_issues(filtered_issues, function_context)

            self.level3_dropped_count = level2_count - len(filtered_issues)
            if self.level3_dropped_count > 0:
                self.logger.info(f"UnifiedIssueFilter: Level 3 dropped {self.level3_dropped_count} issues, {len(filtered_issues)} remaining")
        else:
            if filtered_issues:
                self.logger.info("UnifiedIssueFilter: Skipping Level 3 (Response Challenger) filtering - not available or disabled")
        
        total_dropped = original_count - len(filtered_issues)
        self.logger.info(f"UnifiedIssueFilter: Filtering complete - dropped {total_dropped} total issues, {len(filtered_issues)} final issues")
        
        return filtered_issues
    
    def get_filtering_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the filtering system.
        
        Returns:
            Dictionary with filtering statistics and configuration
        """
        stats = {
            "level1_enabled": True,
            "level1_allowed_categories": list(self.category_filter.get_allowed_categories()),
            "level1_dropped_count": getattr(self, 'level1_dropped_count', 0),
            "level2_enabled": self.enable_llm_filtering,
            "level2_available": self.llm_filter.is_available() if self.llm_filter else False,
            "level2_dropped_count": getattr(self, 'level2_dropped_count', 0),
            "level3_enabled": True,  # Always enabled
            "level3_available": self.response_challenger.is_available() if self.response_challenger else False,
            "level3_dropped_count": getattr(self, 'level3_dropped_count', 0),
            "evidence_capture_enabled": self.capture_evidence,
            "api_key_provided": bool(self.api_key)
        }
        
        # Add LLM filter stats if available
        if self.llm_filter:
            stats["level2_stats"] = self.llm_filter.get_filter_stats()
        
        # Add Response Challenger stats if available
        if self.response_challenger:
            stats["level3_stats"] = self.response_challenger.get_challenger_stats()
            stats["level3_stats"]["capture_evidence"] = self.capture_evidence
        
        return stats
    
    def is_category_filtered(self, category: str) -> bool:
        """
        Check if a specific category would be filtered out by Level 1 filtering.
        
        Args:
            category: The category to check
            
        Returns:
            True if the category would be filtered out, False otherwise
        """
        return self.category_filter.is_category_filtered(category)
    
    def add_filtered_category(self, category: str) -> None:
        """
        Add a category to the Level 1 filtered list.
        
        Args:
            category: Category name to add to the filtered list
        """
        self.category_filter.add_filtered_category(category)
    
    def remove_filtered_category(self, category: str) -> None:
        """
        Remove a category from the Level 1 filtered list.
        
        Args:
            category: Category name to remove from the filtered list
        """
        self.category_filter.remove_filtered_category(category)


def create_unified_filter(api_key: str, config: dict,
                         additional_allowed_categories: List[str] = None,
                         dropped_issues_dir: Optional[str] = None,
                         enable_llm_filtering: bool = True,
                         capture_evidence: bool = True,
                         file_content_provider=None,
                         directory_tree_util=None,
                         repo_path: Optional[str] = None,
                         artifacts_dir: Optional[str] = None) -> UnifiedIssueFilter:
    """
    Factory function to create a UnifiedIssueFilter instance.
    
    This is the recommended way for analyzers to create their issue filter.
    
    Args:
        api_key: API key for LLM provider
        config: Configuration dictionary
        additional_allowed_categories: Additional categories to allow beyond defaults (logicBug, performance)
        dropped_issues_dir: Directory to save dropped issues (optional)
        enable_llm_filtering: Whether to enable Level 2 LLM and Level 3 Response Challenger filtering (default: True)
        capture_evidence: Whether to capture validation evidence (default: True)
        file_content_provider: FileContentProvider instance for file resolution (optional)
        directory_tree_util: DirectoryTreeUtil instance for directory listing (optional)
        repo_path: Path to the repository (optional, for runTerminalCmd tool)
        artifacts_dir: Path to artifacts directory (optional, for code insights)
        
    Returns:
        Configured UnifiedIssueFilter instance
    """
    return UnifiedIssueFilter(
        api_key=api_key,
        config=config,
        additional_allowed_categories=additional_allowed_categories,
        dropped_issues_dir=dropped_issues_dir,
        enable_llm_filtering=enable_llm_filtering,
        capture_evidence=capture_evidence,
        file_content_provider=file_content_provider,
        directory_tree_util=directory_tree_util,
        repo_path=repo_path,
        artifacts_dir=artifacts_dir
    )


def main(results_directory: str, config_path: str, repo_directory: str, output_file: str = None) -> str:
    """
    Main function to reprocess code analysis results and generate HTML report.
    
    This function reads existing code analysis results, applies unified issue filtering
    (2-level filtering: Category + LLM), and generates a new HTML report with filtered results.
    
    Args:
        results_directory: Directory containing code analysis results
                          (e.g., /path/to/artifacts/results/code_analysis)
        config_path: Path to JSON configuration file for LLM setup
        repo_directory: Path to the repository directory
        output_file: Optional output HTML file path. If None, generates timestamped filename.
        
    Returns:
        Path to the generated HTML report file
        
    Raises:
        FileNotFoundError: If results_directory or config_path doesn't exist
        ValueError: If no analysis results found or configuration is invalid
    """
    import json
    import os
    from datetime import datetime
    from pathlib import Path
    
    # Import required modules
    from ..report.report_generator import read_llm_output_files, generate_html_report
    from ..utils.config_util import load_and_validate_config, get_api_key_from_config
    from ..utils.log_util import get_logger
    
    logger = get_logger(__name__)
    
    # Validate input arguments
    if not os.path.exists(results_directory):
        raise FileNotFoundError(f"Results directory not found: {results_directory}")
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
    if not os.path.exists(repo_directory):
        raise FileNotFoundError(f"Repository directory not found: {repo_directory}")
    
    logger.info(f"Starting unified issue filter reprocessing...")
    logger.info(f"Results directory: {results_directory}")
    logger.info(f"Config file: {config_path}")
    logger.info(f"Repository: {repo_directory}")
    
    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = load_and_validate_config(config_path)
        api_key = get_api_key_from_config(config)
        
        if not api_key:
            logger.warning("No API key found in configuration - LLM filtering will be disabled")
        
        # Read existing analysis results
        logger.info("Reading existing analysis results...")
        all_issues = read_llm_output_files(results_directory, "_analysis.json")
        
        if not all_issues:
            raise ValueError(f"No analysis results found in directory: {results_directory}")
        
        original_count = len(all_issues)
        logger.info(f"Found {original_count} issues in existing analysis results")
        
        # Initialize unified issue filter
        logger.info("Initializing unified issue filter...")
        unified_filter = create_unified_filter(
            api_key=api_key or "dummy-key",  # Use dummy key if no real API key available
            config=config,
            enable_llm_filtering=bool(api_key)  # Only enable LLM filtering if we have a real API key
        )
        
        # Apply unified filtering
        logger.info("Applying unified issue filtering (Category + LLM)...")
        filtered_issues = unified_filter.filter_issues(all_issues)
        
        filtered_count = len(filtered_issues)
        dropped_count = original_count - filtered_count
        
        logger.info(f"Filtering completed:")
        logger.info(f"  Original issues: {original_count}")
        logger.info(f"  Filtered issues: {filtered_count}")
        logger.info(f"  Dropped issues: {dropped_count}")
        
        # Get filtering statistics
        filter_stats = unified_filter.get_filtering_stats()
        logger.info(f"Filter configuration:")
        logger.info(f"  Level 1 (Category): {'enabled' if filter_stats['level1_enabled'] else 'disabled'}")
        logger.info(f"  Level 2 (LLM): {'enabled' if filter_stats['level2_available'] else 'disabled'}")
        logger.info(f"  Allowed categories: {filter_stats['level1_allowed_categories']}")
        
        # Generate output filename if not provided
        if output_file is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            repo_name = os.path.basename(repo_directory.rstrip('/'))
            output_file = f"/tmp/filtered_analysis_{repo_name}_{timestamp}.html"
        
        # Ensure output directory exists
        output_dir = os.path.dirname(os.path.abspath(output_file))
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate HTML report with filtered results
        logger.info(f"Generating HTML report: {output_file}")
        project_name = config.get('project_name') or os.path.basename(repo_directory.rstrip('/'))
        
        report_file = generate_html_report(
            issues=filtered_issues,
            output_file=output_file,
            project_name=project_name,
            analysis_type="Filtered Code Analysis"
        )
        
        logger.info(f"HTML report generated successfully: {report_file}")
        logger.info(f"Report contains {filtered_count} filtered issues (dropped {dropped_count} issues)")
        
        return report_file
        
    except Exception as e:
        logger.error(f"Error during reprocessing: {e}")
        raise


if __name__ == "__main__":
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(
        description="Reprocess code analysis results with unified issue filtering and generate HTML report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
========
# Basic usage
python -m hindsight.issue_filter.unified_issue_filter \\
    --results /path/to/artifacts/results/code_analysis \\
    --config /path/to/config.json \\
    --repo /path/to/repo

# Using short options
python -m hindsight.issue_filter.unified_issue_filter \\
    -r /path/to/artifacts/results/code_analysis \\
    -c /path/to/config.json \\
    --repo /path/to/repo

# With custom output file
python -m hindsight.issue_filter.unified_issue_filter \\
    -r /path/to/artifacts/results/code_analysis \\
    -c /path/to/config.json \\
    --repo /path/to/repo \\
    -o filtered_report.html

# Example with the provided path
python -m hindsight.issue_filter.unified_issue_filter \\
    -r /Users/sgurivireddy/hindsight_artifacts/almanac/results/code_analysis \\
    -c /path/to/config.json \\
    --repo /path/to/almanac/repo
        """
    )
    
    parser.add_argument(
        "--results", "-r",
        required=True,
        help="Directory containing code analysis results (e.g., /path/to/artifacts/results/code_analysis)"
    )
    
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="Path to JSON configuration file for LLM setup"
    )
    
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to the repository directory"
    )
    
    parser.add_argument(
        "--output", "-o",
        help="Output HTML file path (default: auto-generated with timestamp)"
    )
    
    args = parser.parse_args()
    
    # Use the shortcut arguments directly
    results_directory = args.results
    config_path = args.config
    repo_directory = args.repo
    
    try:
        report_file = main(
            results_directory=results_directory,
            config_path=config_path,
            repo_directory=repo_directory,
            output_file=args.output
        )
        
        print(f"\n✅ Reprocessing completed successfully!")
        print(f"📊 HTML report generated: {report_file}")
        print(f"\nOpen the HTML file in your browser to view the filtered results.")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)