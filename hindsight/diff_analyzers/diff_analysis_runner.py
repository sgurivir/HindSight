#!/usr/bin/env python3
"""
Diff Analysis Runner
Main runner class for git diff analysis using GitSimpleCommitAnalyzer.
Follows the same pattern as CodeAnalysisRunner for consistent API integration.
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from .git_simple_diff_analyzer import GitSimpleCommitAnalyzer
from ..analyzers.analysis_runner import AnalysisRunner
from ..analyzers.token_tracker import TokenTracker
from ..core.constants import DEFAULT_NUM_BLOCKS_TO_ANALYZE
from ..results_store.code_analysis_publisher import CodeAnalysisResultsPublisher
from ..utils.log_util import get_logger, setup_default_logging
from ..utils.config_util import ConfigValidationError, load_and_validate_config, get_llm_provider_type

# Initialize logging at module level
setup_default_logging()
logger = get_logger(__name__)


class DiffAnalysisRunner(AnalysisRunner):
    """Main runner class for git diff analysis."""

    def __init__(self):
        """Initialize the diff analysis runner."""
        super().__init__()
        self.analyzer_instance = None
        
        # Token tracking
        self.token_tracker = None
        
        # Subscribers for results collection
        self._subscribers = []
        
        # Publisher-subscriber system - initialize early so it's available when subscribers are added
        self.results_publisher = CodeAnalysisResultsPublisher()
        self.logger.info("Publisher initialized for Diff Analysis Runner")
        
        # Store pending user prompts until analyzer is created
        self._pending_user_prompts = []

    def run(self,
            config_dict: Dict[str, Any],
            repo_dir: str,
            out_dir: str,
            c1: Optional[str] = None,
            c2: Optional[str] = None,
            branch1: Optional[str] = None,
            branch2: Optional[str] = None,
            branch: Optional[str] = None,
            num_blocks_to_analyze: int = DEFAULT_NUM_BLOCKS_TO_ANALYZE,
            artifacts_dir: Optional[str] = None) -> str:
        """
        Run git diff analysis with the specified parameters.
        
        This method follows the same pattern as CodeAnalysisRunner.run() to allow
        consistent API integration.

        Args:
            config_dict: Configuration dictionary
            repo_dir: Directory where the repository is already checked out
            out_dir: Output directory for diff results
            c1: First commit hash (optional if using branches)
            c2: Second commit hash (optional if using branches)
            branch1: First branch name (optional if using commits)
            branch2: Second branch name (optional if using commits)
            branch: Branch to checkout from origin (optional - defaults to current branch)
            num_blocks_to_analyze: Maximum number of blocks (files) to analyze (default: 25)
            artifacts_dir: Optional custom artifacts directory for API usage (if not provided, uses default)

        Returns:
            Path to the generated analysis report
        """
        self.logger.info("Starting Diff Analysis Runner")
        self.logger.info(f"Arguments passed to runner.run:")
        self.logger.info(f"  config_dict: {config_dict}")
        self.logger.info(f"  repo_dir: {repo_dir}")
        self.logger.info(f"  out_dir: {out_dir}")
        self.logger.info(f"  c1: {c1}")
        self.logger.info(f"  c2: {c2}")
        self.logger.info(f"  branch1: {branch1}")
        self.logger.info(f"  branch2: {branch2}")
        self.logger.info(f"  branch: {branch}")
        self.logger.info(f"  num_blocks_to_analyze: {num_blocks_to_analyze}")
        self.logger.info(f"  artifacts_dir: {artifacts_dir}")

        try:
            # Configure OutputDirectoryProvider if artifacts_dir is provided (for API usage)
            if artifacts_dir:
                from pathlib import Path
                from ..utils.output_directory_provider import OutputDirectoryProvider
                
                # Ensure artifacts directory exists
                artifacts_path = Path(artifacts_dir)
                artifacts_path.mkdir(parents=True, exist_ok=True)
                
                # Configure OutputDirectoryProvider with custom directory
                output_provider = OutputDirectoryProvider()
                output_provider.configure(
                    repo_path=repo_dir,
                    custom_base_dir=str(artifacts_path)
                )
                self.logger.info(f"Configured custom artifacts directory: {artifacts_dir}")
            
            # Initialize centralized token tracker if not already set (similar to CodeAnalysisRunner)
            if not self.token_tracker:
                llm_provider_type = get_llm_provider_type(config_dict)
                self.token_tracker = TokenTracker(llm_provider_type)
                self.logger.info(f"Auto-initialized centralized token tracker for provider: {llm_provider_type}")
            
            # Initialize publisher-subscriber system (similar to CodeAnalysisRunner)
            self._initialize_publisher_subscriber(config_dict, out_dir)
            
            # Create and configure the analyzer
            analyzer = GitSimpleCommitAnalyzer(
                repo_dir=repo_dir,
                config=config_dict,
                out_dir=out_dir,
                c1=c1,
                c2=c2,
                branch1=branch1,
                branch2=branch2,
                branch=branch
            )
            
            # Set token tracker on analyzer (similar to CodeAnalysisRunner)
            if hasattr(analyzer, 'set_token_tracker') and self.token_tracker:
                analyzer.set_token_tracker(self.token_tracker)
            
            # Register prior result stores with analyzer (similar to CodeAnalysisRunner)
            if hasattr(analyzer, 'results_publisher') and self.results_publisher:
                # Transfer prior result stores from runner to analyzer's publisher
                for store in self.results_publisher._prior_result_stores:
                    if hasattr(analyzer, 'register_prior_result_store'):
                        analyzer.register_prior_result_store(store)
                    elif hasattr(analyzer.results_publisher, 'register_prior_result_store'):
                        analyzer.results_publisher.register_prior_result_store(store)
            
            # Add subscribers to analyzer (similar to CodeAnalysisRunner)
            for subscriber in self._subscribers:
                if hasattr(analyzer, 'add_results_subscriber'):
                    analyzer.add_results_subscriber(subscriber)
            
            # Apply pending user prompts if any (similar to CodeAnalysisRunner)
            if hasattr(self, '_pending_user_prompts') and self._pending_user_prompts:
                if hasattr(analyzer, 'set_user_provided_prompts'):
                    analyzer.set_user_provided_prompts(self._pending_user_prompts)
                    self.logger.info(f"Applied {len(self._pending_user_prompts)} pending user-provided prompts to analyzer")
                    self._pending_user_prompts = []  # Clear after applying
            
            self.analyzer_instance = analyzer
            
            # Run the analysis with the specified block limit
            report_path = analyzer.run(num_blocks_to_analyze=num_blocks_to_analyze)
            
            # Log token usage summary (similar to CodeAnalysisRunner)
            if self.token_tracker:
                self.token_tracker.log_summary()
            
            self.logger.info(f"Diff analysis completed successfully: {report_path}")
            return report_path
            
        except Exception as e:
            self.logger.error(f"Diff analysis failed: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            raise

    def set_token_tracker(self, token_tracker) -> None:
        """
        Set the token tracker for this diff analysis runner.
        Similar to CodeAnalysisRunner.set_token_tracker()

        Args:
            token_tracker: TokenTracker instance to use for tracking token usage
        """
        self.token_tracker = token_tracker
        self.logger.info(f"Token tracker set: {type(token_tracker).__name__}")

    def get_token_tracker(self):
        """
        Get the current token tracker.
        Similar to CodeAnalysisRunner.get_token_tracker()

        Returns:
            TokenTracker instance or None if not set
        """
        return self.token_tracker

    def add_results_subscriber(self, subscriber) -> None:
        """
        Add a subscriber to receive diff analysis results.
        Similar to CodeAnalysisRunner.add_results_subscriber()
        This should be called before running analysis.

        Args:
            subscriber: A subscriber implementing results subscriber interface
        """
        self._subscribers.append(subscriber)
        self.results_publisher.subscribe(subscriber)
        self.logger.info(f"Added subscriber: {type(subscriber).__name__} (registered with publisher)")

    def set_user_provided_prompts(self, user_prompts: list) -> None:
        """
        Set multiple user-provided prompts to be included in the system prompt for diff analysis.
        Similar to CodeAnalysisRunner.set_user_provided_prompts()
        
        Args:
            user_prompts: List of user-specific instructions for analysis
        """
        if hasattr(self, 'analyzer_instance') and self.analyzer_instance:
            if hasattr(self.analyzer_instance, 'set_user_provided_prompts'):
                self.analyzer_instance.set_user_provided_prompts(user_prompts)
                self.logger.info(f"Set {len(user_prompts)} user-provided prompts on analyzer")
            else:
                self.logger.warning("Analyzer does not support user-provided prompts")
        else:
            # Store for later when analyzer is created
            self._pending_user_prompts = user_prompts
            self.logger.info(f"Stored {len(user_prompts)} user-provided prompts for later application")

    def register_prior_result_store(self, store) -> None:
        """
        Register a prior result store for duplicate checking.
        Similar to CodeAnalysisRunner.register_prior_result_store()
        
        Args:
            store: A prior result store implementing the required interface
        """
        self.results_publisher.register_prior_result_store(store)
        self.logger.info(f"Registered prior result store: {type(store).__name__}")

    def _initialize_publisher_subscriber(self, config: dict, output_base_dir: str) -> None:
        """
        Initialize the publisher-subscriber system for diff analysis results.
        Similar to CodeAnalysisRunner._initialize_publisher_subscriber()

        Args:
            config: Configuration dictionary
            output_base_dir: Base output directory
        """
        self.results_publisher.initialize(output_base_dir)

        # All subscribers should already be registered since publisher was available when they were added
        # But double-check to ensure consistency
        registered_count = 0
        for subscriber in self._subscribers:
            if subscriber not in getattr(self.results_publisher, '_subscribers', []):
                self.results_publisher.subscribe(subscriber)
                registered_count += 1
                self.logger.info(f"Late-registered subscriber: {type(subscriber).__name__}")

        if registered_count == 0:
            self.logger.info(f"All {len(self._subscribers)} subscribers were already registered with publisher")
        
        self.logger.info(f"Initialized publisher-subscriber system for diff analysis")

    def get_analyzed_diff(self) -> Optional[str]:
        """
        Get the diff content that was analyzed by the runner.
        
        This is needed for PR commenting to ensure line numbers match exactly
        between analysis and GitHub API calls.
        
        Returns:
            The diff content that was analyzed, or None if not available
        """
        if hasattr(self, 'analyzer_instance') and self.analyzer_instance:
            if hasattr(self.analyzer_instance, 'diff_content'):
                return self.analyzer_instance.diff_content
            else:
                self.logger.warning("Analyzer instance has no diff_content attribute")
        else:
            self.logger.warning("No analyzer instance available")
        
        return None


def main():
    """Main entry point for the diff analysis runner."""
    parser = argparse.ArgumentParser(
        description="Diff Analysis Runner - Analyzes git diffs using LLM analysis with selective AST generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config config.json --repo /path/to/repo --out_dir /tmp/diff --c1 abc123 --c2 def456
  %(prog)s --config config.json --repo /path/to/repo --out_dir /tmp/diff --branch1 main --branch2 feature-branch
  %(prog)s --config config.json --repo /path/to/repo --out_dir /tmp/diff --c1 abc123 --branch develop
        """
    )

    parser.add_argument(
        "--config",
        required=True,
        help="Path to JSON configuration file (similar format as CodeAnalysisRunner)"
    )

    parser.add_argument(
        "--repo",
        required=True,
        help="Directory where the git repository is already checked out"
    )

    parser.add_argument(
        "--out_dir",
        required=True,
        help="Output directory for diff analysis results"
    )

    parser.add_argument(
        "--c1",
        help="First commit hash (required if not using branches)"
    )

    parser.add_argument(
        "--c2",
        help="Second commit hash (optional - if not provided, will use parent of c1)"
    )

    parser.add_argument(
        "--branch1",
        help="First branch name (required if not using commits)"
    )

    parser.add_argument(
        "--branch2",
        help="Second branch name (required if not using commits)"
    )

    parser.add_argument(
        "--branch",
        help="Branch to checkout from origin (optional - defaults to current branch)"
    )

    parser.add_argument(
        "--num-blocks-to-analyze",
        type=int,
        default=DEFAULT_NUM_BLOCKS_TO_ANALYZE,
        help=f"Maximum number of blocks (files) to analyze (default: {DEFAULT_NUM_BLOCKS_TO_ANALYZE})"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--user-prompt",
        action="append",
        help="Optional user-provided prompt to be included in the system prompt for diff analysis. Can be specified multiple times to add multiple prompts. Each will be appended to the standard system prompt."
    )

    parser.add_argument(
        "--artifacts-dir",
        help="Optional custom artifacts directory for analysis outputs (default: ~/hindsight_diff_artifacts)"
    )

    args = parser.parse_args()

    try:
        # Set up artifacts directory (default or custom)
        from pathlib import Path
        from ..utils.output_directory_provider import OutputDirectoryProvider
        
        if args.artifacts_dir:
            # Use custom artifacts directory
            artifacts_dir = Path(args.artifacts_dir)
        else:
            # Use default artifacts directory
            artifacts_dir = Path.home() / "hindsight_diff_artifacts"
        
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        # Configure OutputDirectoryProvider
        output_provider = OutputDirectoryProvider()
        output_provider.configure(
            repo_path=args.repo,
            custom_base_dir=str(artifacts_dir)
        )
        
        print(f"📁 Using artifacts directory: {artifacts_dir}")

        # Load configuration
        config = load_and_validate_config(args.config)

        # Create and run the runner
        runner = DiffAnalysisRunner()
        
        # Auto-create and set TokenTracker (similar to CodeAnalysisRunner)
        llm_provider_type = get_llm_provider_type(config)
        token_tracker = TokenTracker(llm_provider_type)
        runner.set_token_tracker(token_tracker)
        logger.info(f"Auto-created TokenTracker for provider: {llm_provider_type}")

        # Set user-provided prompts if provided (similar to CodeAnalysisRunner)
        if args.user_prompt:
            runner.set_user_provided_prompts(args.user_prompt)

        report_path = runner.run(
            config_dict=config,
            repo_dir=args.repo,
            out_dir=args.out_dir,
            c1=args.c1,
            c2=args.c2,
            branch1=args.branch1,
            branch2=args.branch2,
            branch=args.branch,
            num_blocks_to_analyze=args.num_blocks_to_analyze,
            artifacts_dir=str(artifacts_dir)
        )

        # Print token usage summary after analysis (similar to CodeAnalysisRunner)
        if runner.get_token_tracker():
            input_tokens, output_tokens = runner.get_token_tracker().get_total_token_usage()
            total_tokens = input_tokens + output_tokens
            if total_tokens > 0:
                print(f"\n=== TOKEN USAGE SUMMARY ===")
                print(f"Input Tokens:  {input_tokens:,}")
                print(f"Output Tokens: {output_tokens:,}")
                print(f"Total Tokens:  {total_tokens:,}")
                print(f"Provider:      {runner.get_token_tracker().llm_provider_type}")
                print("=" * 27)

        print(f"\n✅ Diff analysis completed successfully!")
        print(f"📊 Analysis report generated: {report_path}")
        print(f"\nOpen the HTML file in your browser to view the results.")

    except ConfigValidationError as e:
        print(f"\n❌ Configuration validation failed: {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\n❌ Configuration file not found: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"\n❌ Invalid JSON configuration: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        if args.verbose:
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
