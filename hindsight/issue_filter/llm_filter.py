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
from ..core.constants import LLM_FILTER_BATCH_SIZE, DEFAULT_LLM_API_END_POINT, DEFAULT_LLM_MODEL, DEFAULT_MAX_TOKENS

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
        """LLM-based filtering using the trivial issue filter prompt.

        Drives `stage_trivial_filter` from the new async LLM stack via the
        `SyncStageRunner` bridge — `filter_issues` stays sync at the API
        level so callers (UnifiedIssueFilter, pipelines via `to_thread`) need
        no change.

        Per-issue failures and JSON parse errors keep the issue (no verdict
        → don't drop). Matches the legacy behavior exactly.
        """
        from hindsight.llm import (
            SyncStageRunner,
            make_client_config_from_dict,
            stage_trivial_filter,
        )

        original_count = len(issues)
        self.logger.info(
            f"LLMBasedFilter: trivial filtering {original_count} issues via stage_trivial_filter"
        )

        prompt_path = Path(__file__).parent.parent / "core" / "prompts" / "trivialIssueFilterPrompt.md"
        try:
            with open(prompt_path, "r", encoding="utf-8") as fh:
                system_prompt = fh.read()
        except Exception as exc:
            self.logger.error(f"Failed to load trivial issue filter prompt: {exc}")
            return self._dummy_filter_issues(issues)

        try:
            client_config = make_client_config_from_dict(
                api_key=self.api_key,
                config=self.config,
                default_api_url=DEFAULT_LLM_API_END_POINT,
                default_model=DEFAULT_LLM_MODEL,
                default_max_tokens=DEFAULT_MAX_TOKENS,
            )
        except Exception as exc:
            self.logger.error(f"Failed to build client config: {exc}")
            return self._dummy_filter_issues(issues)

        # Build one user prompt per issue.
        user_prompts: List[str] = []
        for issue in issues:
            user_prompts.append(json.dumps({
                "issue": issue.get("issue", ""),
                "category": issue.get("category", ""),
                "issueType": issue.get("issueType", issue.get("issue_type", "")),
                "severity": issue.get("severity", ""),
                "description": issue.get("description", ""),
                "suggestion": issue.get("suggestion", ""),
            }, indent=2))

        try:
            runner = SyncStageRunner(client_config)
            verdicts = runner.run_many(
                stage_trivial_filter(system_prompt, max_iterations=3),
                user_prompts,
                max_iterations=3,
            )
        except Exception as exc:
            self.logger.error(f"SyncStageRunner failed; keeping all issues: {exc}")
            return list(issues)

        filtered_issues: List[Dict[str, Any]] = []
        dropped_count = 0
        for idx, (issue, verdict) in enumerate(zip(issues, verdicts), start=1):
            if isinstance(verdict, dict) and verdict.get("result") is True:
                dropped_count += 1
                preview = issue.get("issue", "")[:100]
                self.logger.info(f"Issue {idx} marked as trivial by LLM: {preview}...")
                self._save_dropped_issue(issue, "Marked as trivial by LLM")
            else:
                # No verdict / verdict says keep → preserve the issue.
                if verdict is None:
                    self.logger.debug(f"No verdict for issue {idx}; keeping it")
                filtered_issues.append(issue)

        filtered_count = len(filtered_issues)
        if dropped_count > 0:
            self.logger.info(
                f"LLMBasedFilter: LLM filtered {dropped_count} trivial issues, "
                f"keeping {filtered_count}"
            )
        else:
            self.logger.info(
                f"LLMBasedFilter: LLM found no trivial issues, keeping all {filtered_count}"
            )
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