#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Mixins for analysis runners providing shared initialization functionality.

This module extracts duplicated initialization methods from:
- CodeAnalysisRunner (code_analyzer.py)
- TraceAnalysisRunner (trace_analyzer.py)
- GitSimpleCommitAnalyzer (git_simple_diff_analyzer.py)

These mixins provide common implementations for:
1. Unified issue filter initialization
2. Publisher-subscriber system initialization
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..utils.log_util import get_logger


class UnifiedIssueFilterMixin:
    """
    Mixin providing unified issue filter initialization.
    
    This mixin extracts the common `_initialize_unified_issue_filter()` method
    that was duplicated across CodeAnalysisRunner, TraceAnalysisRunner, and
    GitSimpleCommitAnalyzer.
    
    Requirements for using this mixin:
    - Class must have a `logger` attribute
    - Class must have a `unified_issue_filter` attribute (initialized to None)
    - Class must have a `get_file_content_provider()` method
    """
    
    def _initialize_unified_issue_filter(self, api_key: str, config: Dict[str, Any], enable_llm_filtering: bool = True) -> None:
        """
        Initialize the unified issue filter if not already initialized.
        
        This method provides a common implementation for initializing the
        three-level issue filter (Category + LLM + Response Challenger) used
        across all analyzers.
        
        Args:
            api_key: API key for LLM provider
            config: Configuration dictionary containing LLM settings
            enable_llm_filtering: Whether to enable Level 2 LLM and Level 3 Response Challenger filtering (default: True)
        """
        # Ensure we have the required attributes
        if not hasattr(self, 'unified_issue_filter'):
            self.unified_issue_filter = None
        
        if not hasattr(self, 'logger'):
            self.logger = get_logger(__name__)
        
        if not self.unified_issue_filter and api_key:
            try:
                # Import here to avoid circular imports
                from ..issue_filter import create_unified_filter
                
                self.logger.info("Initializing unified three-level issue filter (Category + LLM + Response Challenger)")
                
                # Get file content provider if available
                file_content_provider = None
                if hasattr(self, 'get_file_content_provider'):
                    try:
                        file_content_provider = self.get_file_content_provider()
                    except RuntimeError:
                        self.logger.debug("FileContentProvider not available yet")
                
                # Get dropped issues directory from output directory provider
                dropped_issues_dir = None
                try:
                    from ..utils.output_directory_provider import get_output_directory_provider
                    output_provider = get_output_directory_provider()
                    artifacts_dir = output_provider.get_repo_artifacts_dir()
                    dropped_issues_dir = os.path.join(artifacts_dir, "dropped_issues")
                except Exception as e:
                    self.logger.debug(f"Could not get dropped issues directory from output provider: {e}")
                    # Fallback to analysis_dir if available
                    if hasattr(self, 'analysis_dir') and self.analysis_dir:
                        dropped_issues_dir = str(self.analysis_dir) if hasattr(self.analysis_dir, '__str__') else self.analysis_dir
                        dropped_issues_dir = os.path.join(dropped_issues_dir, "dropped_issues")
                
                # Get directory tree util if available
                directory_tree_util = None
                if hasattr(self, 'directory_tree_util'):
                    directory_tree_util = self.directory_tree_util
                
                # Get repo_path if available (from repo_checkout_dir or repo_path attribute)
                repo_path = None
                if hasattr(self, 'repo_checkout_dir') and self.repo_checkout_dir:
                    repo_path = str(self.repo_checkout_dir)
                elif hasattr(self, 'repo_path') and self.repo_path:
                    repo_path = str(self.repo_path)
                
                # Get artifacts_dir if available
                artifacts_dir = None
                if artifacts_dir is None:
                    try:
                        artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"
                    except Exception:
                        pass
                
                self.unified_issue_filter = create_unified_filter(
                    api_key=api_key,
                    config=config,
                    dropped_issues_dir=dropped_issues_dir,
                    enable_llm_filtering=enable_llm_filtering,
                    file_content_provider=file_content_provider,
                    directory_tree_util=directory_tree_util,
                    repo_path=repo_path,
                    artifacts_dir=artifacts_dir
                )
                
                # Log filter configuration
                stats = self.unified_issue_filter.get_filtering_stats()
                self.logger.info("Unified issue filter initialized successfully")
                self.logger.info(f"Level 1 (Category) filtering: enabled")
                self.logger.info(f"Level 2 (LLM) filtering: {'enabled' if stats.get('level2_available', False) else 'disabled'}")
                self.logger.info(f"Level 3 (Response Challenger) filtering: {'enabled' if stats.get('level3_available', False) else 'disabled'}")
                self.logger.info(f"Allowed categories: {stats.get('level1_allowed_categories', [])}")
                
            except Exception as e:
                self.logger.error(f"Failed to initialize unified issue filter: {e}")
                self.logger.error(f"Config keys available: {list(config.keys())}")
                self.unified_issue_filter = None
        elif not api_key:
            self.logger.warning("No API key available - unified issue filter will be disabled")
        else:
            self.logger.debug("Unified issue filter already initialized, skipping")


class PublisherSubscriberMixin:
    """
    Mixin providing publisher-subscriber system initialization.
    
    This mixin extracts the common `_initialize_publisher_subscriber()` method
    that was duplicated across CodeAnalysisRunner, TraceAnalysisRunner, and
    GitSimpleCommitAnalyzer.
    
    Requirements for using this mixin:
    - Class must have a `logger` attribute
    - Class must have a `results_publisher` attribute (initialized to None)
    - Class must have a `_subscribers` attribute (initialized to [])
    """
    
    def _initialize_code_analysis_publisher_subscriber(self, config: dict, output_base_dir: str) -> None:
        """
        Initialize the publisher-subscriber system for code analysis results.
        
        This is the common implementation used by CodeAnalysisRunner.
        
        Args:
            config: Configuration dictionary
            output_base_dir: Base output directory
        """
        # Import here to avoid circular imports
        from results_store.code_analysis_publisher import CodeAnalysisResultsPublisher
        
        # Ensure we have the required attributes
        if not hasattr(self, 'results_publisher'):
            self.results_publisher = None
        if not hasattr(self, '_subscribers'):
            self._subscribers = []
        if not hasattr(self, 'logger'):
            self.logger = get_logger(__name__)
        
        # Extract repository name from path
        repo_path = config.get('path_to_repo', '')
        repo_name = os.path.basename(repo_path.rstrip('/'))
        
        # Initialize publisher only if not already initialized, or preserve existing stores
        if not self.results_publisher:
            self.results_publisher = CodeAnalysisResultsPublisher()
        else:
            # Publisher already exists with registered stores - preserve them
            store_count = len(self.results_publisher._prior_result_stores) if hasattr(self.results_publisher, '_prior_result_stores') else 0
            self.logger.info(f"Publisher already initialized with {store_count} prior result stores - preserving existing stores")
        
        self.results_publisher.initialize(output_base_dir)
        
        # Subscribe all registered subscribers to the publisher
        for subscriber in self._subscribers:
            self.results_publisher.subscribe(subscriber)
            self.logger.info(f"Subscribed {type(subscriber).__name__} to publisher")
        
        # If we have a file system subscriber, load existing results for caching
        for subscriber in self._subscribers:
            if hasattr(subscriber, 'load_existing_results'):
                loaded_count = subscriber.load_existing_results(repo_name, self.results_publisher)
                if loaded_count > 0:
                    self.logger.info(f"Loaded {loaded_count} existing analysis results for checksum-based caching via {type(subscriber).__name__}")
        
        self.logger.info(f"Initialized publisher-subscriber system for repository: {repo_name}")
    
    def _initialize_trace_analysis_publisher_subscriber(self) -> None:
        """
        Initialize the publisher-subscriber system for trace analysis results.
        
        This is the common implementation used by TraceAnalysisRunner.
        """
        # Import here to avoid circular imports
        from results_store.trace_analysis_publisher import TraceAnalysisResultsPublisher
        
        # Ensure we have the required attributes
        if not hasattr(self, 'results_publisher'):
            self.results_publisher = None
        if not hasattr(self, '_subscribers'):
            self._subscribers = []
        if not hasattr(self, 'logger'):
            self.logger = get_logger(__name__)
        if not hasattr(self, 'repo_path'):
            raise RuntimeError("repo_path must be set before initializing publisher-subscriber system")
        
        try:
            # Initialize the publisher
            self.results_publisher = TraceAnalysisResultsPublisher()
            self.logger.info("Initialized TraceAnalysisResultsPublisher")
            
            # Subscribe all registered subscribers to the publisher
            for subscriber in self._subscribers:
                self.results_publisher.subscribe(subscriber)
                self.logger.info(f"Subscribed {type(subscriber).__name__} to publisher")
            
            # If we have a file system subscriber, load existing results for caching
            repo_name = os.path.basename(self.repo_path.rstrip('/'))
            for subscriber in self._subscribers:
                if hasattr(subscriber, 'load_existing_results'):
                    loaded_count = subscriber.load_existing_results(repo_name, self.results_publisher)
                    if loaded_count > 0:
                        self.logger.info(f"Loaded {loaded_count} existing trace analysis results for caching via {type(subscriber).__name__}")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize publisher-subscriber system: {e}")
            raise RuntimeError(f"Publisher-subscriber system initialization failed: {e}")
    
    def _initialize_diff_analysis_publisher_subscriber(self, output_base_dir: str) -> None:
        """
        Initialize the publisher-subscriber system for diff analysis results.
        
        This is the common implementation used by GitSimpleCommitAnalyzer.
        
        Args:
            output_base_dir: Base output directory
        """
        # Import here to avoid circular imports
        from results_store.code_analysis_publisher import CodeAnalysisResultsPublisher
        from results_store.code_analysys_results_local_fs_subscriber import CodeAnalysysResultsLocalFSSubscriber
        
        # Ensure we have the required attributes
        if not hasattr(self, 'results_publisher'):
            self.results_publisher = None
        if not hasattr(self, '_subscribers'):
            self._subscribers = []
        if not hasattr(self, 'logger'):
            self.logger = get_logger(__name__)
        if not hasattr(self, 'repo_checkout_dir'):
            raise RuntimeError("repo_checkout_dir must be set before initializing publisher-subscriber system")
        
        # Extract repository name from directory
        repo_name = self.repo_checkout_dir.name if hasattr(self.repo_checkout_dir, 'name') else os.path.basename(str(self.repo_checkout_dir))
        
        # Initialize publisher
        self.results_publisher = CodeAnalysisResultsPublisher()
        self.results_publisher.initialize(output_base_dir)
        
        # Register all previously added subscribers with the publisher
        previously_added_count = 0
        for subscriber in self._subscribers:
            self.results_publisher.subscribe(subscriber)
            previously_added_count += 1
            self.logger.info(f"Registered previously added subscriber: {type(subscriber).__name__}")
        
        # Create and add default file system subscriber
        default_subscriber = CodeAnalysysResultsLocalFSSubscriber(output_base_dir)
        default_subscriber.set_repo_name(repo_name)
        self.results_publisher.subscribe(default_subscriber)
        self._subscribers.append(default_subscriber)
        
        self.logger.info(f"Initialized publisher-subscriber system for repository: {repo_name}")
        if previously_added_count > 0:
            self.logger.info(f"Registered {previously_added_count} previously added subscribers with publisher")


class ReportGeneratorMixin:
    """
    Mixin providing common report generation functionality.
    
    This mixin extracts the common `_generate_report()` patterns that were
    duplicated across CodeAnalysisRunner, TraceAnalysisRunner, and
    GitSimpleCommitAnalyzer.
    
    Requirements for using this mixin:
    - Class must have a `logger` attribute
    - Class must have a `results_publisher` attribute
    - Class must have a `unified_issue_filter` attribute (optional)
    - Class must have a `get_results_directory()` method
    - Class must have a `get_reports_directory()` method
    - Class must have a `get_file_content_provider()` method (optional)
    """
    
    def _get_results_from_publisher(self, repo_name: str) -> List[Dict[str, Any]]:
        """
        Get all results from the publisher for a given repository.
        
        Args:
            repo_name: Name of the repository
            
        Returns:
            List of result dictionaries
        """
        if not hasattr(self, 'logger'):
            self.logger = get_logger(__name__)
            
        if not hasattr(self, 'results_publisher') or not self.results_publisher:
            self.logger.error("Publisher not available for report generation")
            return []
        
        all_results = self.results_publisher.get_results(repo_name)
        
        if not all_results:
            self.logger.warning("No results found in publisher")
            return []
        
        return all_results
    
    def _convert_results_to_issues(self, all_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert publisher results to issues format for report generation.
        
        Args:
            all_results: List of result dictionaries from publisher
            
        Returns:
            List of issue dictionaries
        """
        all_issues = []
        for result in all_results:
            if 'results' in result and isinstance(result['results'], list):
                all_issues.extend(result['results'])
            elif 'issues' in result and isinstance(result['issues'], list):
                all_issues.extend(result['issues'])
            else:
                # Single result format
                all_issues.append(result)
        
        return all_issues
    
    def _organize_issues_by_directory(
        self,
        repo_path: str,
        all_issues: List[Dict[str, Any]],
        exclude_directories: List[str] = None,
        update_file_paths: bool = True,
        create_unknown_directory: bool = True
    ) -> Tuple[Dict[str, int], Any, Any, Any]:
        """
        Organize issues by directory structure using the utility function.
        
        Args:
            repo_path: Path to the repository
            all_issues: List of issue dictionaries
            exclude_directories: List of directories to exclude
            update_file_paths: Whether to update file paths in issues
            create_unknown_directory: Whether to create an Unknown directory for unassigned issues
            
        Returns:
            Tuple of (assignment_stats, repo_hierarchy, issue_organizer, unknown_node)
        """
        # Import here to avoid circular imports
        from ..utils.issue_organizer_util import organize_issues_complete
        
        # Get file content provider if available
        file_content_provider = None
        if hasattr(self, 'get_file_content_provider'):
            try:
                file_content_provider = self.get_file_content_provider()
            except RuntimeError:
                pass
        
        # Get file mapping index path if available
        pickled_index_path = None
        if hasattr(self, '_get_file_mapping_paths'):
            try:
                file_mapping_index, _ = self._get_file_mapping_paths()
                pickled_index_path = file_mapping_index
            except Exception:
                pass
        
        return organize_issues_complete(
            repo_path=repo_path,
            all_issues=all_issues,
            file_content_provider=file_content_provider,
            pickled_index_path=pickled_index_path,
            update_file_paths=update_file_paths,
            create_unknown_directory=create_unknown_directory,
            exclude_directories=exclude_directories or []
        )
    
    def _write_organized_issues_file(
        self,
        output_file: str,
        repo_path: str,
        all_issues: List[Dict[str, Any]],
        assignment_stats: Dict[str, int],
        repo_hierarchy: Any,
        issue_organizer: Any,
        title: str = "REPOSITORY ANALYSIS - ORGANIZED ISSUES BY DIRECTORY"
    ) -> None:
        """
        Write organized issues tree to a file.
        
        Args:
            output_file: Path to the output file
            repo_path: Path to the repository
            all_issues: List of all issues
            assignment_stats: Dictionary with assignment statistics
            repo_hierarchy: Repository directory hierarchy
            issue_organizer: Issue organizer instance
            title: Title for the file
        """
        if not hasattr(self, 'logger'):
            self.logger = get_logger(__name__)
        
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"{title}\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Repository: {repo_path}\n")
            f.write(f"Total Issues: {len(all_issues)}\n")
            f.write(f"Assigned to Directories: {assignment_stats['assigned']}\n")
            f.write(f"Unassigned: {assignment_stats['unassigned']}\n\n")
            
            # Write directory tree with issues
            if hasattr(self, '_write_directory_tree_with_issues'):
                self._write_directory_tree_with_issues(f, repo_hierarchy.get_root_node(), 0)
            
            # Write unassigned issues
            unassigned_issues = issue_organizer.get_unassigned_issues()
            if unassigned_issues:
                f.write("\n" + "=" * 60 + "\n")
                f.write("UNASSIGNED ISSUES\n")
                f.write("=" * 60 + "\n")
                for i, issue in enumerate(unassigned_issues, 1):
                    if isinstance(issue, dict):
                        f.write(f"\n{i}. {issue.get('file', 'Unknown file')} - {issue.get('function_name', 'Unknown function')}\n")
                        f.write(f"   Issue: {issue.get('issue', 'No description')}\n")
                        f.write(f"   Severity: {issue.get('severity', 'unknown')}\n")
                    else:
                        f.write(f"\n{i}. Invalid issue format: {issue}\n")
        
        self.logger.info(f"Organized issues tree saved to: {output_file}")
    
    def _generate_html_report(
        self,
        all_issues: List[Dict[str, Any]],
        report_file_path: str,
        project_name: str,
        analysis_type: str = "Code Analysis"
    ) -> str:
        """
        Generate HTML report from issues.
        
        Args:
            all_issues: List of issue dictionaries
            report_file_path: Path to save the HTML report
            project_name: Name of the project
            analysis_type: Type of analysis (e.g., "Code Analysis", "Trace Analysis", "Diff Analysis")
            
        Returns:
            Path to the generated report file
        """
        # Import here to avoid circular imports
        from ..report.report_generator import generate_html_report
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(report_file_path), exist_ok=True)
        
        return generate_html_report(
            all_issues,
            output_file=report_file_path,
            project_name=project_name,
            analysis_type=analysis_type
        )
    
    def _log_report_statistics(
        self,
        all_issues: List[Dict[str, Any]],
        report_file: str
    ) -> None:
        """
        Calculate and log report statistics.
        
        Args:
            all_issues: List of issue dictionaries
            report_file: Path to the generated report file
        """
        # Import here to avoid circular imports
        from ..report.report_generator import calculate_stats
        
        if not hasattr(self, 'logger'):
            self.logger = get_logger(__name__)
        
        stats = calculate_stats(all_issues)
        self.logger.info(f"Report generated successfully: {report_file}")
        self.logger.info(f"Report statistics:")
        self.logger.info(f"  Total Issues: {stats['total']}")
        
        # Get filtering statistics if unified filter is available
        filter_stats_msg = ""
        if hasattr(self, 'unified_issue_filter') and self.unified_issue_filter:
            try:
                filter_stats = self.unified_issue_filter.get_filtering_stats()
                dropped_category = filter_stats.get('level1_dropped_count', 0)
                dropped_trivial = filter_stats.get('level2_dropped_count', 0)
                dropped_challenge = filter_stats.get('level3_dropped_count', 0)
                
                dropped_parts = []
                if dropped_category > 0:
                    dropped_parts.append(f"Category: {dropped_category}")
                if dropped_trivial > 0:
                    dropped_parts.append(f"Trivial: {dropped_trivial}")
                if dropped_challenge > 0:
                    dropped_parts.append(f"Challenge: {dropped_challenge}")
                
                if dropped_parts:
                    filter_stats_msg = f" (Dropped - {', '.join(dropped_parts)})"
            except Exception as e:
                self.logger.debug(f"Failed to get filter statistics: {e}")
        
        self.logger.info(f"  Critical: {stats['critical']}, High: {stats['high']}, Medium: {stats['medium']}, Low: {stats['low']}{filter_stats_msg}")
    
    def _generate_report_filename(
        self,
        project_name: str,
        analysis_type: str = "repo_analysis"
    ) -> str:
        """
        Generate a report filename with timestamp.
        
        Args:
            project_name: Name of the project
            analysis_type: Type of analysis (e.g., "repo_analysis", "trace_analysis", "diff_analysis")
            
        Returns:
            Generated filename
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if project_name:
            safe_project_name = project_name.replace(' ', '_')
            return f"{analysis_type}_{safe_project_name}_hindsight_{timestamp}.html"
        else:
            return f"{analysis_type}_hindsight_{timestamp}.html"


class AnalysisRunnerMixins(UnifiedIssueFilterMixin, PublisherSubscriberMixin, ReportGeneratorMixin):
    """
    Combined mixin providing all shared initialization functionality.
    
    This class combines UnifiedIssueFilterMixin, PublisherSubscriberMixin, and
    ReportGeneratorMixin for convenience when a class needs all of them.
    
    Usage:
        class MyAnalyzer(AnalysisRunnerMixins, BaseClass):
            def __init__(self):
                super().__init__()
                self.unified_issue_filter = None
                self.results_publisher = None
                self._subscribers = []
    """
    pass