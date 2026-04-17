#!/usr/bin/env python3
"""
Git Simple Commit Analyzer
Analyzes code changes between two git commits by generating diffs and running LLM analysis
on the changes. Generates AST only for changed files to provide enhanced context.

Usage:
  git_simple_commit_analyzer.py --repo /path/to/repo --config config.json --out_dir /tmp/diff --c1 abc123 --c2 def456
  git_simple_commit_analyzer.py --repo /path/to/repo --config config.json --out_dir /tmp/diff --c1 abc123

"""

import os
import sys
import json
import argparse
import subprocess
import tempfile
import shutil
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from .base_diff_analyzer import BaseDiffAnalyzer
from .commit_additional_context_provider import CommitExtendedContextProvider
from .affected_function_detector import AffectedFunctionDetector, extract_changed_lines_per_file
from ..analyzers.analysis_runner_mixins import UnifiedIssueFilterMixin, ReportGeneratorMixin
from ..issue_filter.unified_issue_filter import create_unified_filter
from ..core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from ..core.llm.diff_analysis import DiffAnalysis, DiffAnalysisConfig
from ..core.constants import MAX_CHARACTERS_PER_DIFF_ANALYSIS, DEFAULT_NUM_BLOCKS_TO_ANALYZE, DEFAULT_LLM_MODEL, MAX_FILES_PER_DIFF_CHUNK, DEFAULT_LLM_API_END_POINT, MAX_SUPPORTED_FILE_COUNT, MAX_FUNCTION_BODY_LENGTH
from ..core.errors import AnalyzerErrorCode, AnalysisResult
from ..core.proj_util.file_or_directory_summary_generator import FileOrDirectorySummaryGenerator
from ..core.prompts.prompt_builder import PromptBuilder
from ..report.report_generator import generate_html_report
from ..utils.file_util import clear_directory_contents
from ..utils.log_util import setup_default_logging, get_logger
from ..utils.config_util import get_api_key_from_config, get_llm_provider_type
from ..utils.output_directory_provider import OutputDirectoryProvider
from ..core.errors import AnalyzerErrorCode, AnalysisResult

# Import publisher-subscriber classes
from ..results_store.code_analysis_publisher import CodeAnalysisResultsPublisher
from ..results_store.code_analysys_results_local_fs_subscriber import CodeAnalysysResultsLocalFSSubscriber


class GitSimpleCommitAnalyzer(UnifiedIssueFilterMixin, ReportGeneratorMixin, BaseDiffAnalyzer):
    """Main class for analyzing git diffs using LLM analysis with selective AST generation for changed files.
    
    Uses UnifiedIssueFilterMixin for shared issue filter initialization.
    Uses ReportGeneratorMixin for shared report generation functionality.
    """

    def __init__(self, repo_dir: str, config: dict, out_dir: str,
                 c1: Optional[str] = None, c2: Optional[str] = None,
                 branch1: Optional[str] = None, branch2: Optional[str] = None,
                 branch: Optional[str] = None):
        """
        Initialize the Git Simple Commit Analyzer.

        Args:
            repo_dir: Directory where the repository is already checked out
            config: Configuration dictionary (similar to CodeAnalysisRunner format)
            out_dir: Output directory for diff results
            c1: First commit hash (optional if using branches)
            c2: Second commit hash (optional if using branches)
            branch1: First branch name (optional if using commits)
            branch2: Second branch name (optional if using commits)
            branch: Branch to checkout from origin (optional - defaults to current branch)
        """
        super().__init__(repo_dir, config, out_dir, c1, c2, branch1, branch2, branch)
        
        # Additional attributes specific to simple commit analyzer
        self.diff_content = ""
        self.unified_issue_filter = None
        self.file_diff_stats = {}  # Dictionary to store file -> {lines_changed, chars_changed}
        self.num_blocks_to_analyze = DEFAULT_NUM_BLOCKS_TO_ANALYZE  # Default value, can be overridden in run() - preference for number of chunks (size limits always enforced)
        
        # Extract force_in_process_ast parameter from config (similar to CodeAnalyzer)
        self.force_in_process_ast = config.get('force_in_process_ast', False)
        if self.force_in_process_ast:
            self.logger.info("AST generation will run in-process (force_in_process_ast=True)")
        else:
            self.logger.info("AST generation will use default behavior (force_in_process_ast=False)")
        
        # Initialize publisher-subscriber system
        self.results_publisher = None
        self._subscribers = []
        
        # Token tracking (similar to CodeAnalysisRunner)
        self.token_tracker = None
        
        # User-provided prompts (similar to CodeAnalysisRunner)
        self.user_provided_prompts = []
        
        # Additional context provider for enhanced diff analysis
        self.context_provider = None
        
        # File summary generator for generating file summaries before diff analysis
        # Initialize once and reuse (similar to CodeAnalyzer pattern)
        self.file_summary_generator = None
        
        # Cache for file summaries to avoid regenerating them
        self.file_summaries_cache = {}


    def generate_diff(self, output_path: str) -> str:
        """
        Generate unified diff between the two commits and save to file.

        Args:
            output_path: Path where the diff file will be saved

        Returns:
            String containing the unified diff
        """
        self.logger.info(f"Generating diff between {self.old_commit_hash} and {self.new_commit_hash}")

        try:
            # Generate unified diff with context
            result = subprocess.run(
                ['git', 'diff', '--unified=7', self.old_commit_hash, self.new_commit_hash],
                cwd=self.repo_checkout_dir,
                capture_output=True,
                text=True,
                check=True
            )
            
            diff_content = result.stdout
            self.logger.info(f"Generated diff with {len(diff_content)} characters")
            
            # Also get the list of changed files
            files_result = subprocess.run(
                ['git', 'diff', '--name-only', self.old_commit_hash, self.new_commit_hash],
                cwd=self.repo_checkout_dir,
                capture_output=True,
                text=True,
                check=True
            )
            
            all_changed_files = files_result.stdout.strip().split('\n') if files_result.stdout.strip() else []
            self.logger.info(f"Found {len(all_changed_files)} changed files")
            
            # Filter files by supported extensions
            self.changed_files = self._filter_files_by_extensions(all_changed_files)
            self.logger.info(f"After extension filtering: {len(self.changed_files)} files remain")
            
            # Apply exclude_directories filtering using base class method
            self.changed_files = self._filter_files_by_exclude_directories(self.changed_files)
            self.logger.info(f"After applying exclude_directories filter: {len(self.changed_files)} files remain")
            
            # Filter diff content to only include supported files
            filtered_diff = self._filter_diff_by_files(diff_content, self.changed_files)
            
            # SAVE ORIGINAL DIFF FOR DEBUGGING
            original_diff_path = output_path.replace('.diff', '_original.diff')
            original_output_file = Path(original_diff_path)
            original_output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(original_output_file, 'w', encoding='utf-8') as f:
                f.write(filtered_diff)
            
            self.logger.info(f"Original diff saved to file: {original_diff_path}")
            
            # DISABLED: Expand diff context to include whole functions using AST information
            # expanded_diff = self._expand_diff_context_with_ast(filtered_diff)
            
            # Save original diff to file (no expansion)
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(filtered_diff)
            
            self.logger.info(f"Original diff saved to file: {output_path}")
            
            # Use original diff content for LLM analysis
            self.diff_content = filtered_diff
            
            return self.diff_content
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to generate diff: {e}")
            raise

    def _filter_files_by_extensions(self, files: List[str]) -> List[str]:
        """
        Filter files to only include those with supported extensions.

        Args:
            files: List of file paths

        Returns:
            List of file paths with supported extensions
        """
        filtered_files = []
        ignored_count = 0

        for file_path in files:
            # Get file extension
            _, ext = os.path.splitext(file_path)
            ext = ext.lower()

            if ext in ALL_SUPPORTED_EXTENSIONS:
                filtered_files.append(file_path)
            else:
                ignored_count += 1
                self.logger.debug(f"Ignoring file with unsupported extension: {file_path} ({ext})")

        if ignored_count > 0:
            self.logger.info(f"Filtered out {ignored_count} files with unsupported extensions")
            self.logger.info(f"Supported extensions: {ALL_SUPPORTED_EXTENSIONS}")

        return filtered_files


    def _filter_diff_by_files(self, diff_content: str, allowed_files: List[str]) -> str:
        """
        Filter diff content to only include changes for allowed files.

        Args:
            diff_content: Full diff content
            allowed_files: List of files to include

        Returns:
            Filtered diff content
        """
        if not allowed_files:
            return ""

        lines = diff_content.split('\n')
        filtered_lines = []
        current_file = None
        include_current_section = False

        for line in lines:
            # Check for file headers
            if line.startswith('diff --git'):
                # Extract file path from diff header
                # Format: diff --git a/path/to/file b/path/to/file
                parts = line.split()
                if len(parts) >= 4:
                    file_a = parts[2][2:]  # Remove 'a/' prefix
                    file_b = parts[3][2:]  # Remove 'b/' prefix
                    current_file = file_b  # Use the new file path
                    include_current_section = current_file in allowed_files
                else:
                    include_current_section = False
            elif line.startswith('---') or line.startswith('+++'):
                # File path headers, check if we should include this file
                if line.startswith('+++'):
                    file_path = line[4:].strip()  # Remove '+++ ' prefix
                    if file_path.startswith('b/'):
                        file_path = file_path[2:]  # Remove 'b/' prefix
                    current_file = file_path
                    include_current_section = current_file in allowed_files

            # Include line if we're in an allowed file section
            if include_current_section:
                filtered_lines.append(line)

        return '\n'.join(filtered_lines)

    def _expand_diff_context_with_ast(self, diff_content: str) -> str:
        """
        Expand diff context to include whole functions using the fixed diff enhancement utility.
        
        Args:
            diff_content: Original diff content
            
        Returns:
            Expanded diff content with full function context
        """
        if not self.context_provider:
            self.logger.warning("No AST context provider available - keeping original diff context")
            return diff_content
            
        try:
            # Use the fixed diff enhancement utility
            from ..utils.diff_enhancement_util import DiffContextExpander
            
            # Get the merged functions file path from context provider
            merged_functions_path = self._get_merged_functions_path()
            if not merged_functions_path:
                self.logger.warning("No merged functions file available - keeping original diff context")
                return diff_content
            
            # Create a temporary diff file
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False) as temp_diff:
                temp_diff.write(diff_content)
                temp_diff_path = temp_diff.name
            
            try:
                # Create a temporary output file
                with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False) as temp_output:
                    temp_output_path = temp_output.name
                
                # Use the fixed diff expansion utility
                success = DiffContextExpander.expand_diff_with_function_context(
                    repo_path=str(self.repo_checkout_dir),
                    file_content_provider=self.get_file_content_provider(),
                    diff_file_path=temp_diff_path,
                    merged_functions_path=merged_functions_path,
                    output_file_path=temp_output_path
                )
                
                if success:
                    # Read the expanded diff
                    with open(temp_output_path, 'r', encoding='utf-8') as f:
                        expanded_diff = f.read()
                    
                    self.logger.info("Successfully expanded diff context using fixed diff enhancement utility")
                    return expanded_diff
                else:
                    self.logger.warning("Diff expansion failed - keeping original diff context")
                    return diff_content
                    
            finally:
                # Clean up temporary files
                import os
                try:
                    os.unlink(temp_diff_path)
                    os.unlink(temp_output_path)
                except:
                    pass
            
        except Exception as e:
            self.logger.warning(f"Failed to expand diff context with fixed utility: {e}")
            return diff_content

    def _get_merged_functions_path(self) -> Optional[str]:
        """
        Get the path to the merged_functions.json file from the context provider.
        
        Returns:
            Path to merged_functions.json file or None if not available
        """
        try:
            if not self.context_provider:
                return None
                
            # Try to get AST artifacts to find the merged functions file
            from pathlib import Path
            target_files = [Path(self.repo_checkout_dir) / f for f in self.changed_files[:1]]  # Use first file to trigger AST generation
            exclude_dirs = self.config.get('exclude_directories', [])
            clang_args = self.config.get('clang_args', [])
            
            ast_artifacts = self.context_provider._get_or_generate_ast_artifacts(target_files, clang_args, self.changed_files, self.code_insights_dir, use_subprocess=not self.force_in_process_ast)
            
            if ast_artifacts and 'merged_functions_file' in ast_artifacts:
                return ast_artifacts['merged_functions_file']
            
            self.logger.warning("Could not find merged functions file from AST artifacts")
            return None
            
        except Exception as e:
            self.logger.warning(f"Error getting merged functions path: {e}")
            return None

    def _initialize_file_summary_generator(self) -> None:
        """
        Initialize the FileOrDirectorySummaryGenerator once and reuse it.
        This follows the same pattern as CodeAnalyzer to avoid repeated Tools initialization.
        """
        if not self.file_summary_generator:
            try:
                llm_provider = get_llm_provider_type(self.config)
                
                # Ensure the config has the necessary values for FileOrDirectorySummaryGenerator
                enhanced_config = self.config.copy()
                
                # Add exclude_directories if not present (needed for Tools initialization)
                if 'exclude_directories' not in enhanced_config:
                    enhanced_config['exclude_directories'] = []
                
                self.file_summary_generator = FileOrDirectorySummaryGenerator(llm_provider, enhanced_config)
                self.logger.info(f"Initialized FileOrDirectorySummaryGenerator once with provider: {llm_provider}")
            except Exception as e:
                self.logger.error(f"Failed to initialize FileOrDirectorySummaryGenerator: {e}")
                self.file_summary_generator = None

    def _generate_file_summaries(self, changed_files: List[str]) -> Dict[str, str]:
        """
        Generate summaries for all changed files using FileOrDirectorySummaryGenerator.
        
        Args:
            changed_files: List of file paths that were changed
            
        Returns:
            Dictionary mapping file_path -> summary
        """
        if not changed_files:
            return {}
            
        self.logger.info(f"Generating summaries for {len(changed_files)} changed files")
        
        try:
            # Ensure file summary generator is initialized (done once)
            if not self.file_summary_generator:
                self._initialize_file_summary_generator()
            
            if not self.file_summary_generator:
                self.logger.warning("FileOrDirectorySummaryGenerator not available - skipping file summaries")
                return {}
            
            summaries = {}
            
            for file_path in changed_files:
                # Check cache first
                if file_path in self.file_summaries_cache:
                    summaries[file_path] = self.file_summaries_cache[file_path]
                    self.logger.debug(f"Using cached summary for {file_path}")
                    continue
                
                try:
                    # Generate summary for this file
                    self.logger.debug(f"Generating summary for {file_path}")
                    summary = self.file_summary_generator.get_summary_of_file(
                        root=str(self.repo_checkout_dir),
                        relative_path=file_path
                    )
                    
                    summaries[file_path] = summary
                    self.file_summaries_cache[file_path] = summary
                    self.logger.debug(f"Generated summary for {file_path}: {len(summary)} characters")
                    
                except Exception as e:
                    self.logger.warning(f"Failed to generate summary for {file_path}: {e}")
                    summaries[file_path] = f"Error generating summary: {str(e)}"
            
            self.logger.info(f"Successfully generated summaries for {len(summaries)} files")
            return summaries
            
        except Exception as e:
            self.logger.error(f"Failed to generate file summaries: {e}")
            return {}

    def _calculate_total_characters_changed(self, file_stats: Dict[str, Dict[str, int]]) -> int:
        """
        Calculate total characters changed across all files.
        
        Args:
            file_stats: Dictionary mapping file_path -> {lines_changed, chars_changed}
            
        Returns:
            Total characters changed
        """
        return sum(stats['chars_changed'] for stats in file_stats.values())

    def _check_diff_file_count_limit(self) -> Optional[AnalysisResult]:
        """
        Check if diff has too many changed files with supported extensions.
        
        This check is performed after filtering to ensure the diff is within
        analyzable limits.
        
        Returns:
            AnalysisResult with error if limit exceeded, None if within limit
        """
        file_count = len(self.changed_files)
        
        self.logger.info("Checking file count limit for diff analysis...")
        self.logger.info(f"Changed files: {file_count}")
        self.logger.info(f"Limit: {MAX_SUPPORTED_FILE_COUNT} files")
        
        if file_count > MAX_SUPPORTED_FILE_COUNT:
            error_msg = (
                f"Diff has too many changed files ({file_count} files with supported extensions). "
                f"Maximum allowed: {MAX_SUPPORTED_FILE_COUNT}. "
                f"Please reduce the diff scope or use exclude_directories to filter files."
            )
            self.logger.error(error_msg)
            
            return AnalysisResult.error(
                code=AnalyzerErrorCode.ERROR_REPOSITORY_TOO_MANY_FILES,
                message=error_msg,
                details={
                    'file_count': file_count,
                    'max_allowed': MAX_SUPPORTED_FILE_COUNT,
                    'old_commit': self.old_commit_hash,
                    'new_commit': self.new_commit_hash
                },
                recoverable=True,
                user_action="Reduce diff scope or use exclude_directories configuration"
            )
        
        self.logger.info(f"✓ File count check passed ({file_count}/{MAX_SUPPORTED_FILE_COUNT})")
        return None

    # _initialize_unified_issue_filter is now provided by UnifiedIssueFilterMixin
    # Note: The diff analyzer calls it with just api_key, so we need a wrapper
    def _initialize_unified_issue_filter_for_diff(self, api_key: str) -> None:
        """
        Wrapper to initialize unified issue filter for diff analysis.
        Calls the mixin method with the config from self.config.
        
        Args:
            api_key: API key for LLM provider
        """
        # Call the mixin method with config
        super()._initialize_unified_issue_filter(api_key, self.config)

    # ==================== FUNCTION-LEVEL DIFF ANALYSIS METHODS ====================
    
    def _build_ast_for_changed_files(self) -> Dict[str, Any]:
        """
        Build AST artifacts for changed files using the context provider.
        
        Returns:
            Dictionary containing AST artifacts:
                - functions: function_to_location mapping
                - call_graph: call graph with invoked_by relationships
                - data_types: data type definitions
        """
        if not self.context_provider:
            self.logger.warning("No context provider available for AST generation")
            return {}
            
        try:
            # Convert changed files to absolute paths
            target_files = [Path(self.repo_checkout_dir) / f for f in self.changed_files if (Path(self.repo_checkout_dir) / f).exists()]
            
            if not target_files:
                self.logger.warning("No valid target files for AST generation")
                return {}
            
            clang_args = self.config.get('clang_args', [])
            
            # Use the context provider to generate AST artifacts
            ast_artifacts = self.context_provider._get_or_generate_ast_artifacts(
                target_files,
                clang_args,
                self.changed_files,
                self.code_insights_dir,
                use_subprocess=not self.force_in_process_ast
            )
            
            self.logger.info(f"Generated AST artifacts: {list(ast_artifacts.keys()) if ast_artifacts else 'None'}")
            return ast_artifacts or {}
            
        except Exception as e:
            self.logger.error(f"Failed to build AST for changed files: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return {}

    def _analyze_affected_functions(self, affected_functions: List[Dict[str, Any]],
                                     all_changed_files: List[str],
                                     changed_lines_per_file: Dict[str, Dict[str, List[int]]],
                                     ast_artifacts: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Analyze each affected function using LLM with diff context.
        
        Similar to CodeAnalysisRunner._run_code_analysis() but with diff-specific prompts.
        
        Args:
            affected_functions: List of affected function info dicts
            all_changed_files: List of all changed files in the commit
            changed_lines_per_file: Changed lines per file from diff parsing
            ast_artifacts: AST artifacts for context
            
        Returns:
            List of all issues found across all functions
        """
        all_issues = []
        total_functions = len(affected_functions)
        
        # Get API key
        api_key = get_api_key_from_config(self.config)
        if not api_key:
            self.logger.error("No API key available for function analysis")
            return []
        
        # Initialize unified issue filter
        self._initialize_unified_issue_filter_for_diff(api_key)
        
        # Create diff analysis config
        llm_provider_type = get_llm_provider_type(self.config)
        api_url = self.config.get('api_url') or self.config.get('api_end_point', DEFAULT_LLM_API_END_POINT)
        
        diff_config = DiffAnalysisConfig(
            api_key=api_key,
            api_url=api_url,
            model=self.config.get('model', DEFAULT_LLM_MODEL),
            repo_path=str(self.repo_checkout_dir),
            output_file="",  # Not used in two-stage flow
            max_tokens=self.config.get('max_tokens', 64000),
            temperature=0.0,
            config=self.config,
            file_content_provider=getattr(self, 'file_content_provider', None),
        )

        for i, func_info in enumerate(affected_functions, 1):
            func_name = func_info.get('function', 'unknown')
            file_path = func_info.get('file', 'unknown')
            
            self.logger.info(f"Analyzing function {i}/{total_functions}: {func_name} in {file_path}")
            
            # Start issue-specific prompt logging for this function
            # This creates a numbered subdirectory (e.g., prompts_sent/1/, prompts_sent/2/)
            # to prevent prompts from being overwritten when analyzing multiple functions
            from ..core.llm.llm import Claude
            Claude.start_issue_logging(i)
            
            try:
                # Build function-specific prompt data
                prompt_data = self._build_function_diff_prompt(
                    func_info,
                    all_changed_files,
                    changed_lines_per_file,
                    ast_artifacts
                )
                
                if not prompt_data:
                    self.logger.warning(f"Could not build prompt for function {func_name}")
                    continue
                
                # Create a new DiffAnalysis instance for each function
                diff_analyzer = DiffAnalysis(diff_config)
                self._last_analysis = diff_analyzer

                # Stage Da: Collect diff context
                diff_context_bundle = diff_analyzer.run_diff_context_collection(prompt_data)
                if diff_context_bundle is None:
                    self.logger.warning(f"Stage Da failed for {func_name} - skipping Stage Db")
                    continue

                # Stage Db: Analyze from context
                issues = diff_analyzer.run_diff_analysis_from_context(diff_context_bundle)
                
                # Record token usage
                if self.token_tracker and hasattr(diff_analyzer, 'get_token_totals'):
                    try:
                        input_tokens, output_tokens = diff_analyzer.get_token_totals()
                        if input_tokens > 0 or output_tokens > 0:
                            self.token_tracker.add_token_usage(input_tokens, output_tokens)
                            self.logger.debug(f"Function {func_name} token usage: {input_tokens} input, {output_tokens} output")
                    except Exception as e:
                        self.logger.warning(f"Failed to record token usage for {func_name}: {e}")
                
                if issues:
                    # Apply issue filter
                    if self.unified_issue_filter:
                        filtered_issues = self.unified_issue_filter.filter_issues(issues)
                        if len(filtered_issues) != len(issues):
                            self.logger.info(f"Function {func_name}: filtered {len(issues) - len(filtered_issues)} issues")
                        issues = filtered_issues
                    
                    all_issues.extend(issues)
                    self.logger.info(f"Function {func_name}: found {len(issues)} issues")
                else:
                    self.logger.info(f"Function {func_name}: no issues found")
                    
            except Exception as e:
                self.logger.error(f"Error analyzing function {func_name}: {e}")
                self.logger.error(f"Full traceback: {traceback.format_exc()}")
                continue
            finally:
                # End issue-specific prompt logging for this function
                # This resets the issue tracking so the next function gets its own directory
                Claude.end_issue_logging()
        
        self.logger.info(f"Function-level analysis complete: {len(all_issues)} total issues from {total_functions} functions")
        return all_issues

    def _build_function_diff_prompt(self, func_info: Dict[str, Any],
                                     all_changed_files: List[str],
                                     changed_lines_per_file: Dict[str, Dict[str, List[int]]],
                                     ast_artifacts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Build prompt data for analyzing a single function in diff context.
        
        Args:
            func_info: Function information dict
            all_changed_files: List of all changed files
            changed_lines_per_file: Changed lines per file
            ast_artifacts: AST artifacts for context
            
        Returns:
            Dict with prompt data or None if function code cannot be retrieved
        """
        func_name = func_info.get('function', '')
        file_path = func_info.get('file', '')
        start_line = func_info.get('start', 0)
        end_line = func_info.get('end', 0)
        affected_reason = func_info.get('affected_reason', 'modified')
        changed_lines = func_info.get('changed_lines', [])
        
        # Get function code with diff markers
        function_code = self._get_function_code_with_diff_markers(
            file_path, start_line, end_line, changed_lines_per_file
        )
        
        if not function_code:
            self.logger.warning(f"Could not retrieve code for function {func_name}")
            return None
        
        # Check function size limit
        code_lines = function_code.count('\n') + 1
        if code_lines > MAX_FUNCTION_BODY_LENGTH:
            self.logger.warning(f"Function {func_name} has {code_lines} lines, exceeds limit of {MAX_FUNCTION_BODY_LENGTH}")
            return None
        
        # Get call context from AST
        call_context = self._get_function_call_context(func_name, ast_artifacts)
        
        # Get data types and constants used
        data_types_used = call_context.get('data_types_used', [])
        constants_used = call_context.get('constants_used', {})
        
        # Get related functions with their code
        invoked_functions = self._get_related_functions_with_code(
            call_context.get('functions_invoked', []),
            changed_lines_per_file,
            ast_artifacts
        )
        
        invoking_functions = self._get_related_functions_with_code(
            call_context.get('invoked_by', []),
            changed_lines_per_file,
            ast_artifacts
        )
        
        return {
            'function': func_name,
            'file_path': file_path,
            'code': function_code,
            'start_line': start_line,
            'end_line': end_line,
            'changed_lines': changed_lines,
            'affected_reason': affected_reason,
            'data_types_used': data_types_used,
            'constants_used': constants_used,
            'invoked_functions': invoked_functions,
            'invoking_functions': invoking_functions,
            'diff_context': {
                'all_changed_files': all_changed_files,
                'is_part_of_wider_change': len(all_changed_files) > 1,
                'total_files_changed': len(all_changed_files)
            }
        }

    def _get_function_code_with_diff_markers(self, file_path: str, start_line: int,
                                              end_line: int,
                                              changed_lines_per_file: Dict[str, Dict[str, List[int]]]) -> Optional[str]:
        """
        Get function code with line numbers and +/- markers for changed lines.
        
        Args:
            file_path: Path to the file
            start_line: Function start line
            end_line: Function end line
            changed_lines_per_file: Changed lines per file
            
        Returns:
            Formatted code string with line numbers and diff markers
        """
        try:
            # Get file content using the file content provider
            file_content_provider = self.get_file_content_provider()
            if not file_content_provider:
                self.logger.warning("No file content provider available")
                return None
            
            # Read the file content
            full_path = Path(self.repo_checkout_dir) / file_path
            if not full_path.exists():
                self.logger.warning(f"File not found: {full_path}")
                return None
            
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            # Get changed lines for this file
            file_changes = changed_lines_per_file.get(file_path, {})
            added_lines = set(file_changes.get('added', []))
            removed_lines = set(file_changes.get('removed', []))
            
            # Build formatted code
            formatted_lines = []
            for line_num in range(start_line, min(end_line + 1, len(lines) + 1)):
                if line_num <= len(lines):
                    line_content = lines[line_num - 1].rstrip('\n\r')
                    
                    # Determine marker
                    if line_num in added_lines:
                        marker = '+'
                    elif line_num in removed_lines:
                        marker = '-'
                    else:
                        marker = ' '
                    
                    # Format: "  45 | +   code here"
                    formatted_lines.append(f"{line_num:4d} | {marker} {line_content}")
            
            return '\n'.join(formatted_lines)
            
        except Exception as e:
            self.logger.error(f"Error getting function code: {e}")
            return None

    def _get_function_call_context(self, func_name: str, ast_artifacts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get call context for a function from AST artifacts.
        
        Args:
            func_name: Function name
            ast_artifacts: AST artifacts
            
        Returns:
            Dict with functions_invoked, invoked_by, data_types_used, constants_used
        """
        result = {
            'functions_invoked': [],
            'invoked_by': [],
            'data_types_used': [],
            'constants_used': {}
        }
        
        if not ast_artifacts:
            return result
        
        call_graph = ast_artifacts.get('call_graph', {})
        call_graph_list = call_graph.get('call_graph', [])
        
        for file_entry in call_graph_list:
            functions = file_entry.get('functions', [])
            for func_entry in functions:
                if func_entry.get('function') == func_name:
                    result['functions_invoked'] = func_entry.get('functions_invoked', [])
                    result['invoked_by'] = func_entry.get('invoked_by', [])
                    result['data_types_used'] = func_entry.get('data_types_used', [])
                    result['constants_used'] = func_entry.get('constants_used', {})
                    return result
        
        return result

    def _get_related_functions_with_code(self, function_entries: List[Any],
                                          changed_lines_per_file: Dict[str, Dict[str, List[int]]],
                                          ast_artifacts: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get related functions with their code and modification status.
        
        Args:
            function_entries: List of function entries - can be either:
                - List of strings (function names) - used by invoked_by
                - List of dicts with {"function": name, ...} - used by functions_invoked
            changed_lines_per_file: Changed lines per file
            ast_artifacts: AST artifacts
            
        Returns:
            List of dicts with function info, code, and is_modified flag
        """
        result = []
        
        if not ast_artifacts:
            return result
        
        functions_data = ast_artifacts.get('functions', {})
        function_to_location = functions_data.get('function_to_location', {})
        
        for func_entry in function_entries[:10]:  # Limit to 10 related functions
            # Handle both formats:
            # - functions_invoked contains dicts with {"function": name, "context": {...}}
            # - invoked_by contains plain strings (function names)
            if isinstance(func_entry, dict):
                func_name = func_entry.get('function', '')
            elif isinstance(func_entry, str):
                func_name = func_entry
            else:
                self.logger.warning(f"Unexpected function entry type: {type(func_entry)}")
                continue
            locations = function_to_location.get(func_name, [])
            if not locations:
                continue
            
            # Use first location
            loc = locations[0] if isinstance(locations, list) else locations
            file_path = loc.get('file_name', '')
            start_line = loc.get('start', 0)
            end_line = loc.get('end', 0)
            
            # Check if this function was modified
            file_changes = changed_lines_per_file.get(file_path, {})
            added_lines = set(file_changes.get('added', []))
            is_modified = any(start_line <= line <= end_line for line in added_lines)
            
            # Get function code with markers
            code = self._get_function_code_with_diff_markers(
                file_path, start_line, end_line, changed_lines_per_file
            )
            
            if code:
                result.append({
                    'name': func_name,
                    'file': file_path,
                    'start': start_line,
                    'end': end_line,
                    'code': code,
                    'is_modified': is_modified
                })
        
        return result

    def _initialize_publisher_subscriber(self, output_base_dir: str) -> None:
        """
        Initialize the publisher-subscriber system for diff analysis results.

        Args:
            output_base_dir: Base output directory
        """
        # Extract repository name from directory
        repo_name = self.repo_checkout_dir.name
        
        # Initialize publisher
        self.results_publisher = CodeAnalysisResultsPublisher()
        self.results_publisher.initialize(output_base_dir)

        # CRITICAL FIX: Register all previously added subscribers with the publisher
        # These are custom subscribers added via add_results_subscriber() before initialization
        # (e.g., DatabaseResultsCache, DiffAnalysisPostgreSQLSubscriber from API)
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

    def _initialize_publisher_subscriber_for_report(self, output_base_dir: str) -> None:
        """
        Initialize the publisher-subscriber system for report generation from existing issues.
        Unlike _initialize_publisher_subscriber(), this method loads results directly into
        the publisher's results collection so they are available via get_results().

        Args:
            output_base_dir: Base output directory
        """
        # Extract repository name from directory
        repo_name = self.repo_checkout_dir.name
        
        # Initialize publisher
        self.results_publisher = CodeAnalysisResultsPublisher()
        self.results_publisher.initialize(output_base_dir)

        # Register all previously added subscribers with the publisher
        for subscriber in self._subscribers:
            self.results_publisher.subscribe(subscriber)
            self.logger.info(f"Registered previously added subscriber: {type(subscriber).__name__}")

        # Create and add default file system subscriber
        default_subscriber = CodeAnalysysResultsLocalFSSubscriber(output_base_dir)
        default_subscriber.set_repo_name(repo_name)
        self.results_publisher.subscribe(default_subscriber)
        self._subscribers.append(default_subscriber)

        # Load existing results directly into the publisher's results collection for report generation
        # This is different from _initialize_publisher_subscriber which only indexes for cache lookups
        if hasattr(default_subscriber, 'load_existing_results_for_report'):
            loaded_count = default_subscriber.load_existing_results_for_report(repo_name, self.results_publisher)
            if loaded_count > 0:
                self.logger.info(f"Loaded {loaded_count} existing analysis results for report generation")

        self.logger.info(f"Initialized publisher-subscriber system for report generation: {repo_name}")

    def set_token_tracker(self, token_tracker) -> None:
        """
        Set the token tracker for this diff analyzer.
        Similar to CodeAnalyzer.set_token_tracker()

        Args:
            token_tracker: TokenTracker instance to use for tracking token usage
        """
        self.token_tracker = token_tracker
        self.logger.info(f"Token tracker set: {type(token_tracker).__name__}")

    def get_token_tracker(self):
        """
        Get the current token tracker.
        Similar to CodeAnalyzer.get_token_tracker()

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
        if self.results_publisher:
            self.results_publisher.subscribe(subscriber)
            self.logger.info(f"Added subscriber: {type(subscriber).__name__} (registered with publisher)")
        else:
            self.logger.info(f"Added subscriber: {type(subscriber).__name__} (will be registered when publisher is initialized)")

    def set_user_provided_prompts(self, user_prompts: list) -> None:
        """
        Set multiple user-provided prompts to be included in the system prompt for diff analysis.
        Similar to CodeAnalysisRunner.set_user_provided_prompts()
        
        Args:
            user_prompts: List of user-specific instructions for analysis
        """
        self.user_provided_prompts = []
        if user_prompts and isinstance(user_prompts, list):
            for prompt in user_prompts:
                if prompt and prompt.strip():
                    self.user_provided_prompts.append(prompt.strip())
            self.logger.info(f"Set {len(self.user_provided_prompts)} user-provided prompts")
        else:
            self.logger.info("User-provided prompts cleared")

    def save_results(self, issues: List[Dict[str, Any]]) -> str:
        """
        Save analysis results using publisher-subscriber pattern and generate HTML report.

        Args:
            issues: List of analysis issues

        Returns:
            Path to the generated HTML report
        """
        self.logger.info(f"Saving {len(issues)} analysis results using publisher-subscriber pattern")

        # Deduplicate issues before saving and report generation
        if self.config.get('enable_issue_deduplication', True) and issues:
            try:
                from ..dedupers.issue_deduper import IssueDeduper
                from ..utils.output_directory_provider import get_output_directory_provider
                
                # Get the repository artifacts directory
                output_provider = get_output_directory_provider()
                artifacts_dir = output_provider.get_repo_artifacts_dir()
                
                # Initialize deduper with artifacts directory
                deduper = IssueDeduper(
                    artifacts_dir=artifacts_dir,
                    threshold=self.config.get('dedupe_threshold', 0.85)
                )
                
                original_count = len(issues)
                issues = deduper.dedupe(issues)
                
                dedupe_stats = deduper.get_stats()
                self.logger.info(
                    f"Deduplication: {dedupe_stats['total_input']} issues -> "
                    f"{dedupe_stats['unique_output']} unique "
                    f"({dedupe_stats['duplicates_removed']} duplicates removed: "
                    f"{dedupe_stats['exact_matches']} exact, "
                    f"{dedupe_stats['semantic_matches']} semantic)"
                )
                self.logger.info(f"Vector DB stored at: {dedupe_stats['db_path']}")
                
                # Cleanup deduper resources
                deduper.cleanup()
                
            except Exception as e:
                self.logger.warning(f"Issue deduplication failed, continuing with all issues: {e}")

        # Extract repository name from directory
        repo_name = self.repo_checkout_dir.name

        # Publish results using the publisher-subscriber system
        if self.results_publisher:
            # Create a single result entry for the diff analysis
            # We'll treat this as a "function" analysis where the function name represents the commit range
            function_name = f"diff_{self.old_commit_hash[:8]}_to_{self.new_commit_hash[:8]}"
            file_path = "diff_analysis"  # Virtual file path for diff analysis
            
            # Create a checksum based on the commit hashes
            import hashlib
            checksum_input = f"{self.old_commit_hash}_{self.new_commit_hash}_{len(self.changed_files)}"
            function_checksum = hashlib.md5(checksum_input.encode()).hexdigest()[:16]

            # Use the existing publisher to save results
            result_id = self.results_publisher.add_result(
                repo_name=repo_name,
                file_path=file_path,
                function=function_name,
                function_checksum=function_checksum,
                results=issues
            )
            
            if result_id:
                self.logger.info(f"Results published successfully with ID: {result_id}")
            else:
                self.logger.warning("Failed to publish results")

        # Use the results directory from the new structure
        results_dir = self.results_dir
        results_dir.mkdir(parents=True, exist_ok=True)

        # Generate HTML report
        try:
            project_name = self.config.get('project_name', 'Git Diff Analysis')
            report_title = f"{project_name} - Diff Analysis ({self.old_commit_hash[:8]} → {self.new_commit_hash[:8]})"
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            html_filename = f"diff_analysis_report_{timestamp}.html"
            html_path = results_dir / html_filename

            report_file = generate_html_report(
                issues,
                output_file=str(html_path),
                project_name=report_title,
                analysis_type="Diff Analysis"
            )

            self.logger.info(f"HTML report generated: {report_file}")
            return report_file

        except Exception as e:
            self.logger.error(f"Failed to generate HTML report: {e}")
            # Return a default path if HTML generation fails
            return str(results_dir / "diff_analysis_failed.html")

    def generate_report_from_existing_issues(self, config_dict: Dict[str, Any], repo_dir: str, out_dir: str) -> bool:
        """
        Generate HTML report from existing diff analysis files without running analysis.
        
        Args:
            config_dict: Configuration dictionary
            repo_dir: Repository directory path
            out_dir: Output directory path
            
        Returns:
            bool: True if report generation succeeded, False otherwise
        """
        try:
            self.logger.info("Starting report generation from existing diff analysis issues...")
            
            # Set up the basic configuration
            self.repo_checkout_dir = Path(repo_dir)
            self.config = config_dict
            
            # Initialize output directories
            from ..utils.output_directory_provider import OutputDirectoryProvider
            output_provider = OutputDirectoryProvider()
            output_provider.configure(repo_dir, out_dir)
            
            # Set up analysis and results directories
            self.analysis_dir = Path(output_provider.get_repo_artifacts_dir()) / "analysis"
            self.results_dir = Path(output_provider.get_repo_artifacts_dir()) / "results"
            
            self.logger.info(f"Repository directory: {self.repo_checkout_dir}")
            self.logger.info(f"Analysis directory: {self.analysis_dir}")
            self.logger.info(f"Results directory: {self.results_dir}")
            
            # Initialize publisher-subscriber system to load existing results for report generation
            self._initialize_publisher_subscriber_for_report(str(self.analysis_dir))
            
            # Check if we have any existing results
            if not self.results_publisher:
                self.logger.error("Publisher not available - cannot load existing results")
                return False
                
            # Extract repository name for results lookup
            repo_name = self.repo_checkout_dir.name
            existing_results = self.results_publisher.get_results(repo_name)
            
            if not existing_results:
                self.logger.warning("No existing diff analysis results found")
                # Check if there are any result files in the expected locations
                diff_results_dir = self.results_dir / "diff_analysis"
                if diff_results_dir.exists():
                    result_files = list(diff_results_dir.glob("*.json"))
                    if result_files:
                        self.logger.info(f"Found {len(result_files)} result files, but they may not be loaded properly")
                    else:
                        self.logger.info("No result files found in diff analysis directory")
                else:
                    self.logger.info("Diff analysis results directory does not exist")
                return False
            
            # Convert results to issues format
            all_issues = []
            for result in existing_results:
                if 'results' in result and isinstance(result['results'], list):
                    all_issues.extend(result['results'])
                elif isinstance(result, dict):
                    # Single result format
                    all_issues.append(result)
            
            self.logger.info(f"Found {len(all_issues)} total issues from existing results")
            
            if not all_issues:
                self.logger.warning("No issues found in existing results")
                return False
            
            # Generate HTML report
            try:
                project_name = self.config.get('project_name', 'Git Diff Analysis')
                
                # Create a descriptive report title
                report_title = f"{project_name} - Diff Analysis Report (Existing Results)"
                
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                html_filename = f"diff_analysis_existing_report_{timestamp}.html"
                html_path = self.results_dir / html_filename
                
                # Ensure results directory exists
                self.results_dir.mkdir(parents=True, exist_ok=True)
                
                report_file = generate_html_report(
                    all_issues,
                    output_file=str(html_path),
                    project_name=report_title,
                    analysis_type="Diff Analysis"
                )
                
                self.logger.info(f"HTML report generated successfully: {report_file}")
                self.logger.info(f"Report contains {len(all_issues)} issues from existing analysis")
                return True
                
            except Exception as e:
                self.logger.error(f"Failed to generate HTML report: {e}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error during report generation from existing issues: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return False


    def run(self, num_blocks_to_analyze: int = DEFAULT_NUM_BLOCKS_TO_ANALYZE) -> str:
        """
        Run the complete git diff analysis workflow.
        
        This method follows the same pattern as CodeAnalysisRunner.run() to allow
        consistent API integration.

        Args:
            num_blocks_to_analyze: Preferred number of chunks to analyze (default: 10).
                                 Size limits are always enforced for safety. If more chunks
                                 are needed to process all files, analysis will continue.

        Returns:
            Path to the generated analysis report
        """
        # Set the preference for this analysis run (similar to CodeAnalysisRunner pattern)
        self.num_blocks_to_analyze = num_blocks_to_analyze
        self.logger.info(f"Starting Git Simple Commit Analysis with chunk preference: {num_blocks_to_analyze} (size limits always enforced)")
        
        return self.run_analysis()

    def run_analysis(self) -> str:
        """
        Run the complete git diff analysis workflow.

        Returns:
            Path to the generated analysis report
        """
        try:
            self.logger.info("Starting Git Simple Commit Analysis")

            # Step 0: Clear existing contents in diff output directory
            self._clear_diff_output_directory()

            # Step 1: Setup repository (already checked out)
            self.setup_repository()

            # Step 2: Determine commit order
            self.determine_commit_order()

            # Step 2.5: Configure OutputDirectoryProvider for trivial issue filter
            # For diff analysis, we want prompts to go directly to analysis_dir/prompts_sent
            # We configure with the parent directory so that when repo name is appended, it points to analysis_dir
            output_provider = OutputDirectoryProvider()
            output_provider.configure(
                repo_path=str(self.analysis_dir.name),  # Use analysis dir name as repo name
                custom_base_dir=str(self.analysis_dir.parent)  # Use parent so repo name appending works correctly
            )
            self.logger.info(f"Configured OutputDirectoryProvider for diff analysis - prompts will be saved to: {self.analysis_dir}/prompts_sent")

            # Step 2.5.0: Setup conversation logging directory EARLY (before DirectoryClassifier uses LLM)
            # This must happen before any LLM calls to ensure conversation logging is available
            from ..core.llm.llm import Claude
            Claude.setup_prompts_logging()
            Claude.clear_older_prompts()
            self.logger.info("Setup conversation logging directory and cleared older prompts (early setup before DirectoryClassifier)")

            # Step 2.5.1: Run DirectoryClassifier and check file count limit BEFORE diff generation
            self.logger.info("\n\n=== DIRECTORY CLASSIFICATION & FILE COUNT CHECK ===")
            
            # Get enhanced exclude directories using analysis_runner's method
            from ..analyzers.analysis_runner import AnalysisRunner
            runner = AnalysisRunner()
            
            try:
                enhanced_exclude_dirs = runner.get_enhanced_exclude_directories(
                    repo_path=str(self.repo_checkout_dir),
                    config=self.config,
                    user_provided_include_list=self.config.get('include_directories', []),
                    user_provided_exclude_list=self.config.get('exclude_directories', [])
                )
                
                self.logger.info(f"DirectoryClassifier complete:")
                self.logger.info(f"  User-provided exclusions: {len(self.config.get('exclude_directories', []))}")
                self.logger.info(f"  Enhanced exclusions (static + LLM): {len(enhanced_exclude_dirs)}")
                
                if enhanced_exclude_dirs:
                    self.logger.info(f"  Directories to exclude: {sorted(enhanced_exclude_dirs)[:10]}{'...' if len(enhanced_exclude_dirs) > 10 else ''}")
                
                # Update config with enhanced exclusions for use in diff generation
                self.config['exclude_directories'] = enhanced_exclude_dirs
                self.logger.info("Updated config with enhanced exclude directories")
                
            except Exception as e:
                self.logger.warning(f"DirectoryClassifier failed, using user-provided exclusions: {e}")
                # Continue with user-provided exclusions
                enhanced_exclude_dirs = self.config.get('exclude_directories', [])
            
            # Now check file count with the enhanced exclusions BEFORE generating diff
            self.logger.info("\nChecking file count limit before diff generation...")
            
            try:
                from ..utils.filtered_file_finder import FilteredFileFinder
                
                file_count = FilteredFileFinder.count_files_with_supported_extensions(
                    repo_dir=str(self.repo_checkout_dir),
                    include_directories=self.config.get('include_directories', []),
                    exclude_directories=enhanced_exclude_dirs
                )
                
                self.logger.info(f"Found {file_count} files with supported extensions")
                self.logger.info(f"Limit: {MAX_SUPPORTED_FILE_COUNT} files")
                
                if file_count > MAX_SUPPORTED_FILE_COUNT:
                    error_msg = (
                        f"Repository has too many files ({file_count} files with supported extensions). "
                        f"Maximum allowed: {MAX_SUPPORTED_FILE_COUNT}. "
                        f"Please use include_directories or exclude_directories to reduce the scope."
                    )
                    self.logger.error(error_msg)
                    
                    error_result = AnalysisResult.error(
                        code=AnalyzerErrorCode.ERROR_REPOSITORY_TOO_MANY_FILES,
                        message=error_msg,
                        details={
                            'file_count': file_count,
                            'max_allowed': MAX_SUPPORTED_FILE_COUNT,
                            'include_directories': self.config.get('include_directories', []),
                            'exclude_directories': enhanced_exclude_dirs
                        },
                        recoverable=True,
                        user_action="Reduce repository scope using include_directories or exclude_directories configuration"
                    )
                    
                    error_code = error_result.code
                    self.logger.error(f"[{error_code.value}] {error_result.message}")
                    raise RuntimeError(f"{error_code.value}: {error_result.message}")
                
                self.logger.info(f"✓ File count check passed ({file_count}/{MAX_SUPPORTED_FILE_COUNT})")
                
            except RuntimeError:
                # Re-raise RuntimeError from file count check
                raise
            except Exception as e:
                self.logger.error(f"Error during file count check: {e}")
                # Don't fail analysis on count error, just log warning
                self.logger.warning("Proceeding with analysis despite file count check error")

            # Step 2.5.2: Initialize additional context provider with commit hash for caching
            try:
                exclude_dirs = self.config.get('exclude_directories', [])
                self.context_provider = CommitExtendedContextProvider(
                    repo_path=str(self.repo_checkout_dir),
                    exclude_directories=exclude_dirs,
                    commit_hash=self.new_commit_hash  # Enable AST caching by providing commit hash
                )
                self.logger.info(f"Initialized CommitExtendedContextProvider with caching enabled for commit {self.new_commit_hash[:8]}")
            except Exception as e:
                self.logger.warning(f"Failed to initialize context provider: {e}")
                self.context_provider = None

            # Step 2.5.2: Initialize FileOrDirectorySummaryGenerator ONCE per analysis session
            # This prevents repeated Tools initialization for each chunk
            self._initialize_file_summary_generator()

            # Step 2.6: Initialize publisher-subscriber system
            self._initialize_publisher_subscriber(str(self.analysis_dir))

            # Note: Conversation logging was already setup in Step 2.5.0 (before DirectoryClassifier)

            # Step 3: Generate diff and save to file
            diff_file_path = self.analysis_dir / f"diff_{self.old_commit_hash[:8]}_to_{self.new_commit_hash[:8]}.diff"
            diff_content = self.generate_diff(str(diff_file_path))

            if not diff_content.strip():
                self.logger.warning("No diff content generated - no changes between commits")
                # Create empty results
                empty_results = []
                return self.save_results(empty_results)

            # Step 4: Extract changed lines per file for function-level analysis
            self.logger.info("Extracting changed lines per file for function-level analysis...")
            changed_lines_per_file = extract_changed_lines_per_file(diff_content)
            self.logger.info(f"Extracted changes for {len(changed_lines_per_file)} files")
            
            # Step 5: Build AST for changed files
            self.logger.info("Building AST for changed files...")
            ast_artifacts = self._build_ast_for_changed_files()
            
            if not ast_artifacts:
                self.logger.warning("No AST artifacts generated - cannot perform function-level analysis")
                return self.save_results([])
            
            # Step 6: Identify affected functions using AffectedFunctionDetector
            self.logger.info("Identifying affected functions...")
            detector = AffectedFunctionDetector(
                call_graph=ast_artifacts.get('call_graph', {}),
                functions=ast_artifacts.get('functions', {}),
                changed_lines_per_file=changed_lines_per_file,
                repo_path=str(self.repo_checkout_dir)
            )
            
            affected_functions = detector.get_affected_functions(
                include_callers=True,
                include_callees=True,
                max_depth=1
            )
            
            self.logger.info(f"Found {len(affected_functions)} affected functions")

            if not affected_functions:
                self.logger.warning("No affected functions found in the diff")
                return self.save_results([])

            # Step 7: Analyze each affected function with LLM (function-level analysis)
            self.logger.info("Analyzing affected functions with LLM...")
            issues = self._analyze_affected_functions(
                affected_functions=affected_functions,
                all_changed_files=self.changed_files,
                changed_lines_per_file=changed_lines_per_file,
                ast_artifacts=ast_artifacts
            )

            # Step 8: Save results and generate report
            report_path = self.save_results(issues)

            self.logger.info(f"Function-level diff analysis completed successfully: {report_path}")
            return report_path

        except Exception as e:
            self.logger.error(f"Git simple commit analysis failed: {e}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            raise


def main():
    """Main entry point for the git simple commit analyzer."""
    parser = argparse.ArgumentParser(
        description="Analyze code changes between git commits using LLM analysis with selective AST generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --repo /path/to/repo --config config.json --out_dir /tmp/diff --c1 abc123 --c2 def456
  %(prog)s --repo /path/to/repo --config config.json --out_dir /tmp/diff --branch1 main --branch2 feature-branch
  %(prog)s --repo /path/to/repo --config config.json --out_dir /tmp/diff --c1 abc123 --branch develop
        """
    )

    parser.add_argument(
        "--repo",
        required=True,
        help="Directory where the git repository is already checked out"
    )

    parser.add_argument(
        "--config",
        required=True,
        help="Path to JSON configuration file (similar format as CodeAnalysisRunner)"
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
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--num-chunks-to-analyze",
        type=int,
        default=DEFAULT_NUM_BLOCKS_TO_ANALYZE,
        help=f"Maximum number of chunks to analyze (default: {DEFAULT_NUM_BLOCKS_TO_ANALYZE}). Size limits always enforced for safety."
    )

    parser.add_argument(
        "--generate-report-from-existing-issues",
        action="store_true",
        help="Generate HTML report from existing analysis files without running analysis. Requires --config to locate artifacts."
    )

    args = parser.parse_args()

    try:
        # Set up default artifacts directory for main() usage
        import os
        from pathlib import Path
        from ..utils.output_directory_provider import OutputDirectoryProvider
        
        # Default artifacts directory is ~/hindsight_diff_artifacts
        default_artifacts_dir = Path.home() / "hindsight_diff_artifacts"
        default_artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        # Configure OutputDirectoryProvider with default directory
        output_provider = OutputDirectoryProvider()
        output_provider.configure(
            repo_path=args.repo,
            custom_base_dir=str(default_artifacts_dir)
        )
        
        print(f"📁 Using artifacts directory: {default_artifacts_dir}")

        # Load configuration
        from ..utils.config_util import load_and_validate_config, get_llm_provider_type
        from ..analyzers.token_tracker import TokenTracker
        
        config = load_and_validate_config(args.config)

        # Check if user wants to generate report from existing issues only
        if args.generate_report_from_existing_issues:
            if not args.config:
                print(f"\n❌ Error: --config is required when using --generate-report-from-existing-issues")
                sys.exit(1)

            # Create analyzer instance for report generation
            analyzer = GitSimpleCommitAnalyzer(
                repo_dir=args.repo,
                config=config,
                out_dir=args.out_dir
            )
            
            success = analyzer.generate_report_from_existing_issues(
                config_dict=config,
                repo_dir=args.repo,
                out_dir=args.out_dir
            )
            
            if success:
                print(f"\n✅ Report generation from existing issues completed successfully!")
                print(f"\nOpen the HTML file in your browser to view the results.")
            else:
                print(f"\n❌ Report generation failed. Check the logs for details.")
            
            sys.exit(0 if success else 1)

        # Use the DiffAnalysisRunner for consistent pattern
        from .diff_analysis_runner import DiffAnalysisRunner
        
        runner = DiffAnalysisRunner()
        
        # Auto-create and set TokenTracker (similar to CodeAnalysisRunner)
        llm_provider_type = get_llm_provider_type(config)
        token_tracker = TokenTracker(llm_provider_type)
        runner.set_token_tracker(token_tracker)
        print(f"🔧 Auto-created TokenTracker for provider: {llm_provider_type}")
        
        report_path = runner.run(
            config_dict=config,
            repo_dir=args.repo,
            out_dir=args.out_dir,
            c1=args.c1,
            c2=args.c2,
            branch=args.branch,
            num_blocks_to_analyze=args.num_chunks_to_analyze
        )

        # Print token usage summary after analysis (similar to CodeAnalysisRunner)
        if runner.get_token_tracker():
            input_tokens, output_tokens = runner.get_token_tracker().get_total_token_usage()
            total_tokens = input_tokens + output_tokens
            print(f"\n=== TOKEN USAGE DEBUG ===")
            print(f"Token tracker exists: {runner.get_token_tracker() is not None}")
            print(f"Input Tokens:  {input_tokens:,}")
            print(f"Output Tokens: {output_tokens:,}")
            print(f"Total Tokens:  {total_tokens:,}")
            print(f"Provider:      {runner.get_token_tracker().llm_provider_type}")
            if total_tokens > 0:
                print(f"\n=== TOKEN USAGE SUMMARY ===")
                print(f"Input Tokens:  {input_tokens:,}")
                print(f"Output Tokens: {output_tokens:,}")
                print(f"Total Tokens:  {total_tokens:,}")
                print(f"Provider:      {runner.get_token_tracker().llm_provider_type}")
                print("=" * 27)
            else:
                print("No tokens recorded - this indicates the token tracking is not working properly")
            print("=" * 27)

        print(f"\n✅ Git simple commit analysis completed successfully!")
        print(f"📊 Analysis report generated: {report_path}")
        print(f"\nOpen the HTML file in your browser to view the results.")

    except FileNotFoundError as e:
        error_code = AnalyzerErrorCode.ERROR_ANALYSIS_INVALID_CONFIG
        print(f"\n❌ Analysis failed with error code: {error_code.value}")
        print(f"Configuration file not found: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        error_code = AnalyzerErrorCode.ERROR_ANALYSIS_INVALID_CONFIG
        print(f"\n❌ Analysis failed with error code: {error_code.value}")
        print(f"Invalid JSON configuration: {e}")
        sys.exit(1)
    except Exception as e:
        error_code = AnalyzerErrorCode.ERROR_INTERNAL_UNKNOWN
        print(f"\n❌ Analysis failed with error code: {error_code.value}")
        print(f"Error: {e}")
        if args.verbose:
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()