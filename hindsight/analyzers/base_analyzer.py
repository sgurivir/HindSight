from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Protocol, Mapping, Any, Optional, List, Dict, Set
import os
import json
import glob
from collections import Counter

# Import Environment for clang initialization
from hindsight.core.lang_util.Environment import Environment

class AnalyzerProtocol(Protocol):
    """Structural interface analyzers should satisfy (duck-typing friendly)."""

    def name(self) -> str: ...
    def initialize(self, config: Mapping[str, Any]) -> None: ...
    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]: ...
    def finalize(self) -> None: ...
    def pull_results_from_directory(self, artifacts_dir: str) -> Dict[str, Any]: ...

class BaseAnalyzer(ABC):
    """Shared abstract base for analyzers; provides optional defaults."""

    def __init__(self) -> None:
        super().__init__()
        self._initialized = False

    def name(self) -> str:
        return self.__class__.__name__

    def initialize(self, _config: Mapping[str, Any]) -> None:
        """Called once before any analysis; store config, open resources, etc."""
        # Initialize clang before any analysis that might use it
        # The Environment.initialize_libclang() uses dispatch_once pattern, so it's safe to call multiple times
        Environment.initialize_libclang()

        # config parameter is intentionally unused in base implementation
        self._initialized = True

    @abstractmethod
    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """Analyze a single function record and return a result dict or None."""
        raise NotImplementedError

    def finalize(self) -> None:
        """Called once after all analysis; flush metrics, close resources."""
        pass

    def get_recommended_exclude_directories(self, repo_path: str,
                                            user_provided_include_list: Optional[List[str]] = None,
                                            user_provided_exclude_list: Optional[List[str]] = None) -> Set[str]:
        """
        Get recommended directories to exclude from analysis.
        
        This method provides a default implementation using DirectoryClassifier.
        Subclasses can override this method to provide analyzer-specific exclusion logic.
        
        Args:
            repo_path: Path to the repository root
            user_provided_include_list: Optional list of directory names or relative paths to include
            user_provided_exclude_list: Optional list of directory names to exclude in addition to defaults
            
        Returns:
            Set of relative paths that should be excluded from analysis
        """
        # Lazy import to avoid circular dependency
        from .directory_classifier import DirectoryClassifier
        return DirectoryClassifier.get_recommended_exclude_directories_safe(
            repo_path, user_provided_include_list, user_provided_exclude_list
        )

    def get_enhanced_exclude_directories(self, repo_path: str,
                                         config: Dict[str, Any],
                                         user_provided_include_list: Optional[List[str]] = None,
                                         user_provided_exclude_list: Optional[List[str]] = None) -> List[str]:
        """
        Get enhanced directory exclusions using both static analysis and LLM-based recommendations.
        
        This method combines:
        1. User-provided exclude directories (from config and parameters)
        2. LLM-based directory analysis (fault-tolerant)
        3. Static directory classification as fallback
        
        Args:
            repo_path: Path to the repository root
            config: Configuration dictionary containing LLM settings
            user_provided_include_list: Optional list of directory names or relative paths to include
            user_provided_exclude_list: Optional list of directory names to exclude in addition to defaults
            
        Returns:
            List of relative paths that should be excluded from analysis
        """
        from ..utils.log_util import get_logger
        from ..utils.config_util import get_llm_provider_type
        
        logger = get_logger(__name__)
        
        # Start with user-provided exclusions
        base_exclusions = set(user_provided_exclude_list or [])
        
        # Get static directory classification as baseline
        try:
            static_exclusions = self.get_recommended_exclude_directories(
                repo_path, user_provided_include_list, user_provided_exclude_list
            )
            base_exclusions.update(static_exclusions)
            logger.info(f"Static directory analysis found {len(static_exclusions)} directories to exclude")
        except Exception as e:
            logger.warning(f"Static directory analysis failed: {e}")
        
        # Try LLM-based enhancement if provider is not dummy
        llm_provider_type = get_llm_provider_type(config)
        if llm_provider_type != "dummy":
            try:
                logger.info("Attempting LLM-based directory analysis for enhanced exclusions...")
                
                # Lazy import to avoid circular dependency
                from .directory_classifier import LLMBasedDirectoryClassifier
                from ..utils.config_util import get_api_key_from_config
                
                # Get API key
                api_key = get_api_key_from_config(config)
                if not api_key:
                    logger.warning("No API key available for LLM-based directory analysis, using static analysis only")
                    return list(base_exclusions)
                
                # Create LLM classifier
                llm_classifier = LLMBasedDirectoryClassifier.from_config(config)
                
                # Use base exclusions as already excluded directories
                already_excluded = list(base_exclusions)
                
                # Get LLM recommendations
                llm_exclusions = llm_classifier.analyze_directories(
                    repo_path=repo_path,
                    subdirectories=None,  # Let it discover all directories
                    already_excluded_directories=already_excluded
                )
                
                if llm_exclusions:
                    logger.info(f"LLM analysis recommended {len(llm_exclusions)} additional directories for exclusion")
                    base_exclusions.update(llm_exclusions)
                else:
                    logger.info("LLM analysis completed but found no additional directories to exclude")
                    
            except Exception as e:
                logger.warning(f"LLM-based directory analysis failed (using static analysis only): {e}")
                # Continue with static analysis results
        else:
            logger.info("Using dummy LLM provider - skipping LLM-based directory analysis")
        
        final_exclusions = list(base_exclusions)
        logger.info(f"Enhanced directory analysis complete: {len(final_exclusions)} total directories to exclude")
        
        return final_exclusions

    def pull_results_from_directory(self, artifacts_dir: str) -> Dict[str, Any]:
        """
        Pull analysis results from the provided artifacts directory.

        Args:
            artifacts_dir: Path to the artifacts directory containing analysis results

        Returns:
            Dictionary containing:
            - 'results': List of analysis results
            - 'statistics': Dictionary with statistics about the results
            - 'summary': Dictionary with summary information
        """
        # Default implementation that can be overridden by subclasses
        return self._read_analysis_results(artifacts_dir, "_analysis.json")

    def _read_analysis_results(self, directory: str, file_suffix: str = "_analysis.json") -> Dict[str, Any]:
        """
        Read all analysis JSON files from the given directory and return results with statistics.

        Args:
            directory: Directory path to search for analysis files
            file_suffix: File suffix to match (default: "_analysis.json")

        Returns:
            Dictionary containing results, statistics, and summary
        """
        all_issues = []
        file_pattern = os.path.join(directory, f"*{file_suffix}")
        files_processed = 0
        files_with_errors = 0

        if not os.path.exists(directory):
            return {
                'results': [],
                'statistics': self._calculate_statistics([]),
                'summary': {
                    'total_files': 0,
                    'files_processed': 0,
                    'files_with_errors': 0,
                    'total_issues': 0,
                    'directory': directory,
                    'analyzer': self.name()
                }
            }

        for file_path in glob.glob(file_pattern):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if data is not None:
                    if isinstance(data, list):
                        # Filter out any non-dictionary items from the list
                        valid_issues = [item for item in data if isinstance(item, dict)]
                        if len(valid_issues) != len(data):
                            files_with_errors += 1
                        all_issues.extend(valid_issues)
                        files_processed += 1
                    elif isinstance(data, dict):
                        all_issues.append(data)
                        files_processed += 1
                    else:
                        files_with_errors += 1
                        continue
                else:
                    files_with_errors += 1
                    continue

            except (json.JSONDecodeError, IOError, OSError):
                files_with_errors += 1
                continue

        # Calculate statistics
        statistics = self._calculate_statistics(all_issues)

        # Create summary
        total_files = len(glob.glob(file_pattern))
        summary = {
            'total_files': total_files,
            'files_processed': files_processed,
            'files_with_errors': files_with_errors,
            'total_issues': len(all_issues),
            'directory': directory,
            'analyzer': self.name()
        }

        return {
            'results': all_issues,
            'statistics': statistics,
            'summary': summary
        }

    def _calculate_statistics(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate statistics from the issues.

        Args:
            issues: List of issue dictionaries

        Returns:
            Dictionary containing various statistics
        """
        if not issues:
            return {
                'total': 0,
                'by_severity': {},
                'by_category': {},
                'by_file': {},
                'by_function': {}
            }

        # Count by severity (support both 'kind' and 'severity' fields)
        severity_counts = Counter()
        for issue in issues:
            severity = issue.get('kind', issue.get('severity', 'unknown'))
            severity_counts[severity] += 1

        # Count by category/issue type
        category_counts = Counter()
        for issue in issues:
            category = issue.get('category', issue.get('issueType', 'unknown'))
            category_counts[category] += 1

        # Count by file
        file_counts = Counter()
        for issue in issues:
            file_name = issue.get('file_name', issue.get('file', 'unknown'))
            file_counts[file_name] += 1

        # Count by function
        function_counts = Counter()
        for issue in issues:
            function_name = issue.get('function', 'unknown')
            function_counts[function_name] += 1

        return {
            'total': len(issues),
            'by_severity': dict(severity_counts),
            'by_category': dict(category_counts),
            'by_file': dict(file_counts),
            'by_function': dict(function_counts)
        }