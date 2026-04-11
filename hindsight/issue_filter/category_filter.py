#!/usr/bin/env python3
"""
Category-based Issue Filter (Level 1 Filtering)

This module provides hard filtering based on issue categories.
It ensures certain categories like 'codeQuality' are never shown to users.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Set, Optional
from ..utils.log_util import get_logger
from ..utils.output_directory_provider import get_output_directory_provider

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class CategoryBasedFilter:
    """
    Level 1 filter that removes issues based on their category.
    This is a hard filter - only issues in ALLOWED categories are shown to users.
    All other categories are filtered out.
    
    The LLM is instructed to use specific categories for certain issue types that
    we want to filter out. This approach is more reliable than trying to suppress
    these issues in the prompt, as the LLM may still report them under different
    category names. By giving them explicit categories, we can reliably filter them.
    
    Intentionally filtered categories (defined in systemPrompt.md but not allowed here):
    - divisionByZero: Division by zero, sqrt of negative values, mathematical domain errors
    - nilAccess: Null/nil pointer access, missing null checks, out-of-bounds access
    - memory: Memory management issues (filtered to reduce noise)
    - concurrency: Threading issues (filtered unless explicitly enabled)
    - general: Catch-all category (filtered to reduce noise)
    - noIssue: No issues found (filtered as it's not a real issue)
    - inputNotValidated: Input validation issues (filtered as often speculative)
    - errorHandling: Missing or incomplete error handling (filtered to reduce noise)
    """
    
    # Categories that are ALLOWED (only these will be shown to users)
    # All other categories defined in systemPrompt.md will be filtered out
    ALLOWED_CATEGORIES: Set[str] = {
        'logicBug',
        'performance'
    }
    
    def __init__(self, additional_allowed_categories: List[str] = None,
                 dropped_issues_dir: Optional[str] = None):
        """
        Initialize the category-based filter.
        
        Args:
            additional_allowed_categories: Additional categories to allow beyond the defaults (logicBug, performance)
            dropped_issues_dir: Directory to save dropped issues (optional, auto-created if not provided)
        """
        self.logger = get_logger(__name__)
        self.dropped_issues_dir = dropped_issues_dir
        
        # Start with default allowed categories
        self.allowed_categories = self.ALLOWED_CATEGORIES.copy()
        
        # Add any additional allowed categories
        if additional_allowed_categories:
            self.allowed_categories.update(additional_allowed_categories)
            self.logger.info(f"Added {len(additional_allowed_categories)} additional allowed categories")
        
        # Setup dropped issues directory
        self._setup_dropped_issues_directory()
        
        self.logger.info(f"CategoryBasedFilter initialized - ONLY allowing {len(self.allowed_categories)} categories: {sorted(self.allowed_categories)}")
        self.logger.info(f"All other categories will be filtered out")
    
    def _setup_dropped_issues_directory(self) -> None:
        """Setup the dropped_issues directory under output folder."""
        if self.dropped_issues_dir:
            # Use provided directory
            try:
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
                self.logger.info(f"Level 1 dropped issues directory: {self.dropped_issues_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create Level 1 dropped issues directory: {e}")
                self.dropped_issues_dir = None
        else:
            # Auto-create directory using output provider
            try:
                output_provider = get_output_directory_provider()
                output_base_dir = output_provider.get_repo_artifacts_dir()
                self.dropped_issues_dir = os.path.join(output_base_dir, "dropped_issues", "level1_category_filter")
                os.makedirs(self.dropped_issues_dir, exist_ok=True)
                self.logger.info(f"Level 1 dropped issues directory created: {self.dropped_issues_dir}")
            except Exception as e:
                self.logger.debug(f"Could not auto-create Level 1 dropped issues directory: {e}")
                self.dropped_issues_dir = None
    
    def _save_dropped_issue(self, issue: Dict[str, Any], reason: str) -> None:
        """
        Save a dropped issue to a JSON file in the dropped_issues directory.
        
        Args:
            issue: The original issue that was dropped
            reason: The reason why the issue was dropped
        """
        if not self.dropped_issues_dir:
            return
            
        try:
            # Create a unique filename based on timestamp and issue content
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            
            # Create a safe filename from issue content
            issue_text = issue.get('issue', 'unknown_issue')[:50]
            safe_issue_text = "".join(c for c in issue_text if c.isalnum() or c in ('_', '-', ' ')).replace(' ', '_')
            
            filename = f"level1_dropped_issue_{timestamp}_{safe_issue_text}.json"
            filepath = os.path.join(self.dropped_issues_dir, filename)
            
            # Create the dropped issue record
            dropped_record = {
                "timestamp": datetime.now().isoformat(),
                "filter_level": "Level 1 - Category Filter",
                "filter_type": "Category-based filtering",
                "reason": reason,
                "original_issue": issue
            }
            
            # Save to file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(dropped_record, f, indent=2, ensure_ascii=False)
                
            self.logger.debug(f"Level 1 dropped issue saved to: {filepath}")
            
        except Exception as e:
            self.logger.error(f"Failed to save Level 1 dropped issue: {e}")
    
    def filter_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter issues to only keep those in ALLOWED categories.
        All other categories are dropped and saved to dropped_issues directory.
        
        Args:
            issues: List of issue dictionaries
            
        Returns:
            List of issues with only allowed categories (logicBug, performance)
        """
        if not issues:
            return issues
        
        original_count = len(issues)
        filtered_issues = []
        dropped_by_category = {}
        kept_by_category = {}
        
        for issue in issues:
            # Defensive check: ensure issue is a dictionary
            if not isinstance(issue, dict):
                self.logger.warning(f"Skipping non-dictionary issue object: {type(issue)} - {issue}")
                dropped_by_category['invalid_type'] = dropped_by_category.get('invalid_type', 0) + 1
                continue
                
            category = issue.get('category', '').strip()
            
            # ALLOWLIST APPROACH: Only keep issues in allowed categories
            if category and category in self.allowed_categories:
                filtered_issues.append(issue)
                kept_by_category[category] = kept_by_category.get(category, 0) + 1
            else:
                # Track dropped issues by category for logging
                drop_reason = category if category else 'no_category'
                dropped_by_category[drop_reason] = dropped_by_category.get(drop_reason, 0) + 1
                
                # Save dropped issue to file
                reason = f"Category '{drop_reason}' not in allowed list: {sorted(self.allowed_categories)}"
                self._save_dropped_issue(issue, reason)
        
        dropped_count = original_count - len(filtered_issues)
        
        self.logger.info(f"CategoryBasedFilter: Kept {len(filtered_issues)} issues in allowed categories")
        for category, count in kept_by_category.items():
            self.logger.info(f"  ✓ {category}: {count} issues kept")
        
        if dropped_count > 0:
            self.logger.info(f"CategoryBasedFilter: Dropped {dropped_count} issues from {len(dropped_by_category)} categories")
            for category, count in sorted(dropped_by_category.items()):
                self.logger.info(f"  ✗ {category}: {count} issues dropped")
        
        return filtered_issues
    
    def is_category_allowed(self, category: str) -> bool:
        """
        Check if a specific category is allowed (will be kept).
        
        Args:
            category: The category to check
            
        Returns:
            True if the category is allowed, False otherwise
        """
        return category.strip() in self.allowed_categories if category else False
    
    def is_category_filtered(self, category: str) -> bool:
        """
        Check if a specific category is filtered out (will be dropped).
        
        Args:
            category: The category to check
            
        Returns:
            True if the category should be filtered out, False otherwise
        """
        return not self.is_category_allowed(category)
    
    def get_allowed_categories(self) -> Set[str]:
        """
        Get the set of categories that are allowed (will be kept).
        
        Returns:
            Set of allowed category names
        """
        return self.allowed_categories.copy()
    
    def get_filtered_categories(self) -> Set[str]:
        """
        Get the set of categories that are being filtered out.
        This is maintained for backward compatibility.
        
        Returns:
            Set of allowed category names (for backward compatibility)
        """
        return self.allowed_categories.copy()
    
    def add_allowed_category(self, category: str) -> None:
        """
        Add a category to the allowed list.
        
        Args:
            category: Category name to add to the allowed list
        """
        if category and category.strip():
            self.allowed_categories.add(category.strip())
            self.logger.info(f"Added '{category}' to allowed categories")
    
    def add_filtered_category(self, category: str) -> None:
        """
        Remove a category from the allowed list (backward compatibility method).
        
        Args:
            category: Category name to remove from allowed list
        """
        if category and category.strip() in self.allowed_categories:
            self.allowed_categories.discard(category.strip())
            self.logger.info(f"Removed '{category}' from allowed categories (filtered out)")
    
    def remove_filtered_category(self, category: str) -> None:
        """
        Add a category to the allowed list (backward compatibility method).
        
        Args:
            category: Category name to add to allowed list
        """
        self.add_allowed_category(category)