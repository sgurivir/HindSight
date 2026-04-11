#!/usr/bin/env python3
"""
LLM-based Issue Filter (Level 2 Filtering)

This module provides intelligent filtering using LLM analysis.
It provides smart filtering of issues that pass the category filter.
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from ..utils.log_util import get_logger
from ..utils.output_directory_provider import get_output_directory_provider
from ..core.constants import LLM_FILTER_BATCH_SIZE, DEFAULT_LLM_API_END_POINT

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class LLMBasedFilter:
    """
    Level 2 filter that uses LLM analysis to identify trivial issues.
    This filter operates on issues that have already passed the category filter.
    """
    
    def __init__(self, api_key: str, config: dict, dropped_issues_dir: Optional[str] = None, file_content_provider=None):
        """
        Initialize the LLM-based filter.
        
        Args:
            api_key: API key for LLM provider
            config: Configuration dictionary (same format as used by analyzers)
            dropped_issues_dir: Directory to save dropped issues (optional)
            file_content_provider: FileContentProvider instance for file resolution (optional)
        """
        self.logger = get_logger(__name__)
        self.api_key = api_key
        self.config = config
        self.dropped_issues_dir = dropped_issues_dir
        self.file_content_provider = file_content_provider
        
        # Set up dropped issues directory
        self._setup_dropped_issues_directory()
        
        # Enable LLM filtering if we have any API key (including dummy for testing)
        if api_key:
            self.trivial_filter = True  # Mark as available for LLM filtering
            if api_key == "dummy-key":
                self.logger.info("LLMBasedFilter initialized - LLM filtering enabled (dummy mode)")
            else:
                self.logger.info("LLMBasedFilter initialized - LLM filtering enabled")
        else:
            self.trivial_filter = None
            self.logger.info("LLMBasedFilter initialized - LLM filtering disabled (no API key)")
    
    def _setup_dropped_issues_directory(self) -> None:
        """Setup the dropped_issues directory under output folder."""
        if self.dropped_issues_dir:
            # Use provided directory
            try:
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
                self.logger.info(f"Level 2 dropped issues directory created: {self.dropped_issues_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create Level 2 dropped issues directory: {e}")
                self.dropped_issues_dir = None
        else:
            # Auto-create directory using output provider
            try:
                output_provider = get_output_directory_provider()
                output_base_dir = output_provider.get_repo_artifacts_dir()
                self.dropped_issues_dir = os.path.join(output_base_dir, "dropped_issues", "level2_llm_filter")
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
                self.logger.info(f"Level 2 dropped issues directory created: {self.dropped_issues_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create Level 2 dropped issues directory: {e}")
                self.dropped_issues_dir = None
    
    def _save_dropped_issue(self, issue: Dict[str, Any], reason: str = "Marked as trivial by LLM") -> None:
        """
        Save a dropped issue to a JSON file in the dropped_issues directory.
        
        Args:
            issue: The original issue that was dropped
            reason: The reason why the issue was dropped
        """
        if not self.dropped_issues_dir:
            self.logger.warning("Dropped issues directory not available, cannot save dropped issue")
            return
            
        try:
            # Create a unique filename based on timestamp and issue content
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            
            # Create a safe filename from issue content
            issue_text = issue.get('issue', 'unknown_issue')[:50]
            safe_issue_text = "".join(c for c in issue_text if c.isalnum() or c in ('_', '-', ' ')).replace(' ', '_')
            
            filename = f"level2_dropped_issue_{timestamp}_{safe_issue_text}.json"
            filepath = os.path.join(self.dropped_issues_dir, filename)
            
            # Create the dropped issue record
            dropped_record = {
                "timestamp": datetime.now().isoformat(),
                "filter_level": "Level 2 - LLM Trivial Filter",
                "original_issue": issue,
                "reason": reason,
                "filter_type": "LLM Trivial Issue Detection"
            }
            
            # Save to file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(dropped_record, f, indent=2, ensure_ascii=False)
                
            self.logger.debug(f"Level 2 dropped issue saved to: {filepath}")
            
        except Exception as e:
            self.logger.error(f"Failed to save Level 2 dropped issue: {e}")
    
    def filter_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter issues using LLM analysis to identify trivial ones.
        
        Args:
            issues: List of issue dictionaries (already filtered by category)
            
        Returns:
            List of issues with trivial issues removed
        """
        if not issues:
            return issues
        
        if not self.is_available():
            # LLM filtering not available - return all issues
            original_count = len(issues)
            self.logger.info(f"LLMBasedFilter: LLM filtering not available - returning all {original_count} issues")
            return issues
        
        # Use dummy filtering for dummy API key
        if self.api_key == "dummy-key":
            return self._dummy_filter_issues(issues)
        
        # Real LLM-based filtering
        return self._llm_filter_issues(issues)
    
    def _dummy_filter_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Dummy filtering for testing purposes.
        """
        original_count = len(issues)
        
        # Basic filtering: remove issues with very generic descriptions
        filtered_issues = []
        trivial_keywords = ['todo', 'fixme', 'hack', 'temporary', 'debug', 'test']
        
        for issue in issues:
            issue_text = issue.get('issue', '').lower()
            description = issue.get('description', '').lower()
            
            # Check if issue contains trivial keywords
            is_trivial = any(keyword in issue_text or keyword in description
                           for keyword in trivial_keywords)
            
            if not is_trivial:
                filtered_issues.append(issue)
        
        filtered_count = len(filtered_issues)
        dropped_count = original_count - filtered_count
        
        if dropped_count > 0:
            self.logger.info(f"LLMBasedFilter (dummy): Filtered {dropped_count} trivial issues, keeping {filtered_count} issues")
        else:
            self.logger.info(f"LLMBasedFilter (dummy): No trivial issues found, keeping all {filtered_count} issues")
        
        return filtered_issues
    
    def _llm_filter_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Real LLM-based filtering using the trivial issue filter prompt with structured tools.
        """
        import json
        from ..core.llm.code_analysis import CodeAnalysis, AnalysisConfig
        from ..utils.config_util import get_llm_provider_type
        
        original_count = len(issues)
        self.logger.info(f"LLMBasedFilter: Starting LLM-based filtering of {original_count} issues using structured tools")
        
        # Load the trivial issue filter prompt
        prompt_path = Path(__file__).parent.parent / "core" / "prompts" / "trivialIssueFilterPrompt.md"
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        except Exception as e:
            self.logger.error(f"Failed to load trivial issue filter prompt: {e}")
            # Fallback to dummy filtering
            return self._dummy_filter_issues(issues)
        
        filtered_issues = []
        dropped_count = 0
        
        # Process each issue individually using structured tools approach
        for i, issue in enumerate(issues, 1):
            try:
                # Prepare issue data for LLM
                issue_data = {
                    "issue": issue.get('issue', ''),
                    "category": issue.get('category', ''),
                    "issueType": issue.get('issueType', issue.get('issue_type', '')),
                    "severity": issue.get('severity', ''),
                    "description": issue.get('description', ''),
                    "suggestion": issue.get('suggestion', '')
                }
                
                # Create user message with issue data
                user_message = json.dumps(issue_data, indent=2)
                
                # Create temporary input file for structured analysis
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_file:
                    json.dump(issue_data, temp_file, indent=2)
                    temp_input_path = temp_file.name
                
                # Create temporary output file
                with tempfile.NamedTemporaryFile(mode='w', suffix='_analysis.json', delete=False) as temp_output:
                    temp_output_path = temp_output.name
                
                try:
                    # Get repo_path from config - try multiple possible keys
                    # Even though trivial filtering doesn't need file access, having repo_path
                    # ensures tools work correctly if they're ever used
                    repo_path = self.config.get('repo_path', '') or self.config.get('path_to_repo', '')
                    
                    # If still empty, try to get from file_content_provider
                    if not repo_path and self.file_content_provider:
                        if hasattr(self.file_content_provider, 'repo_path'):
                            repo_path = self.file_content_provider.repo_path or ''
                        elif hasattr(self.file_content_provider, 'get_repo_path'):
                            try:
                                repo_path = self.file_content_provider.get_repo_path() or ''
                            except Exception:
                                pass
                    
                    # Create AnalysisConfig for structured analysis
                    analysis_config = AnalysisConfig(
                        json_file_path=temp_input_path,
                        api_key=self.api_key,
                        api_url=self.config.get('api_end_point', DEFAULT_LLM_API_END_POINT),
                        model=self.config.get('model', 'claude-3-5-sonnet-20241022'),
                        repo_path=repo_path,  # Use resolved repo_path for tool compatibility
                        output_file=temp_output_path,
                        max_tokens=self.config.get('max_tokens', 64000),
                        temperature=self.config.get('temperature', 0.1),
                        processed_cache_file=None,
                        config=self.config,
                        file_content_provider=self.file_content_provider,
                        file_filter=[],
                        min_function_body_length=0
                    )
                    
                    # Create CodeAnalysis instance for structured tool support
                    # Skip AST validation since we don't need AST data for trivial issue filtering
                    code_analysis = CodeAnalysis(analysis_config)
                    # Clear AST validation requirement for trivial filtering
                    code_analysis.ast_index._ast_dir_resolved = "/dev/null"  # Dummy path to skip validation
                    
                    # Use run_iterative_analysis from the Claude client for structured JSON response
                    self.logger.debug(f"Analyzing issue {i}/{original_count} with structured tools")
                    result = code_analysis.claude.run_iterative_analysis(
                        system_prompt=system_prompt,
                        user_prompt=user_message,
                        tools_executor=None,  # No tools needed for trivial filtering
                        supported_tools=None,  # No tools needed for trivial filtering
                        max_iterations=3  # Limit iterations for filtering
                    )
                    
                    # Parse the JSON result returned by run_iterative_analysis
                    parsed_result = None
                    if result:
                        try:
                            # result is a JSON string, parse it to get the dictionary
                            parsed_result = json.loads(result) if isinstance(result, str) else result
                        except json.JSONDecodeError as e:
                            self.logger.warning(f"Failed to parse JSON result for issue {i}: {e}")
                            self.logger.debug(f"Raw result: {result}")
                    
                    if parsed_result and isinstance(parsed_result, dict) and 'result' in parsed_result:
                        is_trivial = parsed_result.get('result', False)
                        
                        if not is_trivial:
                            filtered_issues.append(issue)
                        else:
                            dropped_count += 1
                            issue_summary = issue.get('issue', '')[:100]
                            self.logger.info(f"Issue {i} marked as trivial by LLM: {issue_summary}...")
                            
                            # Save the dropped issue
                            self._save_dropped_issue(issue, "Marked as trivial by LLM")
                    else:
                        self.logger.warning(f"Invalid or empty result from structured analysis for issue {i}")
                        if result:
                            self.logger.debug(f"Raw result received: {result}")
                        # Keep issue if we can't get valid result
                        filtered_issues.append(issue)
                        
                finally:
                    # Clean up temporary files
                    try:
                        import os
                        os.unlink(temp_input_path)
                        os.unlink(temp_output_path)
                    except OSError:
                        pass
                    
            except Exception as e:
                self.logger.error(f"Error processing issue {i} with structured LLM analysis: {e}")
                # Keep issue if error occurs
                filtered_issues.append(issue)
        
        filtered_count = len(filtered_issues)
        
        if dropped_count > 0:
            self.logger.info(f"LLMBasedFilter: LLM filtered {dropped_count} trivial issues, keeping {filtered_count} issues")
        else:
            self.logger.info(f"LLMBasedFilter: LLM found no trivial issues, keeping all {filtered_count} issues")
        
        return filtered_issues
    
    def is_available(self) -> bool:
        """
        Check if LLM-based filtering is available.
        
        Returns:
            True if LLM filtering can be performed, False otherwise
        """
        return self.trivial_filter is not None and bool(self.api_key)
    
    def get_filter_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the LLM filter performance.
        
        Returns:
            Dictionary with filter statistics
        """
        if not self.is_available():
            return {"available": False, "reason": "LLM filtering not available - no valid API key"}
        
        if self.api_key == "dummy-key":
            return {
                "available": True,
                "filter_type": "dummy_keyword_filtering",
                "description": "Dummy LLM filtering using trivial keyword detection"
            }
        else:
            return {
                "available": True,
                "filter_type": "real_llm_filtering",
                "description": "Real LLM filtering using trivial issue filter prompt"
            }