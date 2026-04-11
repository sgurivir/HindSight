#!/usr/bin/env python3
"""
Trace Relevance Filter

This module provides functionality to filter out trace analysis issues that are not
relevant to the original callstack/trace using LLM-based classification. Each issue 
is analyzed in a separate conversation context to determine if it should be dropped.
"""

import json
import os
import tempfile
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path

from ..core.llm.llm import Claude, ClaudeConfig
from ..core.constants import DEFAULT_MAX_TOKENS, DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT
from ..utils.log_util import get_logger
from ..utils.output_directory_provider import get_output_directory_provider
from ..utils.json_util import clean_json_response


class TraceRelevanceFilter:
    """
    Filter for identifying and dropping trace analysis issues that are not relevant
    to the original callstack/trace.
    
    Uses LLM analysis in separate conversation contexts to classify issues
    as relevant or irrelevant based on the original trace context.
    """
    
    def __init__(self, api_key: str, config: Dict[str, Any]):
        """
        Initialize the trace relevance filter.
        
        Args:
            api_key: API key for LLM provider
            config: Configuration dictionary containing LLM settings
        """
        self.logger = get_logger(__name__)
        self.api_key = api_key
        self.config = config
        self.dropped_issues_dir = None
        self._setup_dropped_issues_directory()
        
        # Load the system prompt
        self.system_prompt = self._load_system_prompt()
        
    def _setup_dropped_issues_directory(self) -> None:
        """Setup the dropped_issues directory under output folder."""
        try:
            output_provider = get_output_directory_provider()
            output_base_dir = output_provider.get_repo_artifacts_dir()
            self.dropped_issues_dir = os.path.join(output_base_dir, "trace_dropped_issues")
            os.makedirs(self.dropped_issues_dir, exist_ok=True)
            self.logger.info(f"Trace dropped issues directory created: {self.dropped_issues_dir}")
        except Exception as e:
            self.logger.error(f"Failed to create trace dropped issues directory: {e}")
            self.dropped_issues_dir = None
            
    def _load_system_prompt(self) -> str:
        """Load the trace relevance filter system prompt."""
        try:
            # Get the path to the system prompt file
            current_dir = Path(__file__).parent.parent
            prompt_path = current_dir / "core" / "prompts" / "traceRelevanceFilterPrompt.md"
            
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_content = f.read()
                
            self.logger.debug(f"Loaded trace relevance filter system prompt from: {prompt_path}")
            return prompt_content
            
        except Exception as e:
            self.logger.error(f"Failed to load system prompt: {e}")
            # Fallback prompt if file loading fails
            return """You are a trace analysis issue relevance filter. Determine if an issue is relevant to the original callstack/trace.
            
Respond with JSON only: {"result": true} for relevant issues, {"result": false} for irrelevant issues.

An issue is RELEVANT if it:
1. Relates to functions in the original callstack
2. Affects the execution path shown in the trace
3. Could impact the behavior described in the callstack
4. Is directly connected to the trace context

An issue is IRRELEVANT if it:
1. Relates to unrelated functions not in the callstack
2. Is about general code quality unrelated to the trace
3. Affects code paths not involved in the trace execution"""

    def _create_llm_instance(self) -> Claude:
        """Create a new LLM instance for each conversation context using centralized factory."""
        # Use the centralized create_llm_provider factory to ensure consistent configuration
        # This eliminates code duplication and ensures the trace relevance filter uses the
        # EXACT same provider setup as the main trace analyzer
        
        from ..core.llm.llm import create_llm_provider, ClaudeConfig
        
        # Extract provider_type using the same logic as trace_analyzer.py
        provider_type = self.config.get('llm_provider_type', 'claude') if self.config else 'claude'
        
        # Use api_url if available, otherwise fall back to api_end_point
        api_url = self.config.get('api_url') or self.config.get('api_end_point', DEFAULT_LLM_API_END_POINT)
        
        # Create ClaudeConfig with the same pattern as trace_analyzer
        claude_config = ClaudeConfig(
            api_key=self.api_key,
            api_url=api_url,
            model=self.config.get('model', DEFAULT_LLM_MODEL),
            max_tokens=self.config.get('max_tokens', DEFAULT_MAX_TOKENS),  # Same as original trace analysis prompt
            temperature=self.config.get('temperature', 0.1),
            provider_type=provider_type
        )
        
        # Use the centralized factory to create the provider
        provider = create_llm_provider(claude_config)
        
        # Create Claude instance with the provider
        claude_instance = Claude(claude_config)
        claude_instance.provider = provider  # Use the factory-created provider
        
        self.logger.debug(f"Created LLM instance using centralized factory with provider_type: {provider_type}, api_url: {api_url}")
        return claude_instance
    
    def _extract_response_text(self, response: Dict[str, Any]) -> Optional[str]:
        """
        Extract text content from LLM response, handling different response formats.
        
        Args:
            response: LLM response dictionary
            
        Returns:
            str: Extracted text content or None if not found
        """
        if not response or not isinstance(response, dict):
            return None
            
        # Handle Claude native format
        if "content" in response and isinstance(response.get("content"), list):
            content_blocks = response.get("content", [])
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        
        # Handle AWS Bedrock format
        elif "choices" in response:
            choices = response.get("choices", [])
            if choices:
                assistant_message = choices[0].get("message", {})
                return assistant_message.get("content", "")
        
        # Handle direct text response (fallback)
        elif isinstance(response, str):
            return response
            
        return None
    
    
    def _save_dropped_issue(self, issue: Dict[str, Any], original_trace: List[str], filter_result: Dict[str, Any]) -> None:
        """
        Save a dropped issue to a JSON file in the dropped_issues directory.
        
        Args:
            issue: The original issue that was dropped
            original_trace: The original callstack/trace
            filter_result: The filter analysis result
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
            
            filename = f"dropped_trace_issue_{timestamp}_{safe_issue_text}.json"
            filepath = os.path.join(self.dropped_issues_dir, filename)
            
            # Create the dropped issue record
            dropped_record = {
                "timestamp": datetime.now().isoformat(),
                "original_issue": issue,
                "original_trace": original_trace,
                "filter_result": filter_result,
                "reason": "Issue classified as irrelevant to original trace by LLM filter"
            }
            
            # Save to file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(dropped_record, f, indent=2, ensure_ascii=False)
                
            self.logger.debug(f"Dropped trace issue saved to: {filepath}")
            
        except Exception as e:
            self.logger.error(f"Failed to save dropped trace issue: {e}")
    
    def is_relevant_to_trace(self, issue: Dict[str, Any], original_trace: List[str]) -> bool:
        """
        Determine if an issue is relevant to the original trace using LLM analysis.
        
        Args:
            issue: Dictionary containing the issue details
            original_trace: List of strings representing the original callstack/trace
            
        Returns:
            bool: True if the issue is relevant to the trace, False if irrelevant and should be dropped
        """
        try:
            # Extract issue text for analysis
            issue_text = issue.get('issue', '')
            if not issue_text:
                self.logger.warning("Issue has no 'issue' field, considering it relevant")
                return True
            
            # Prepare the user message with the complete issue object and original trace
            issue_json = json.dumps(issue, indent=2, ensure_ascii=False)
            trace_text = '\n'.join(original_trace) if original_trace else "No trace provided"
            
            user_message = f"""Analyze this trace analysis issue for relevance to the original callstack:

ORIGINAL CALLSTACK/TRACE:
{trace_text}

ISSUE TO EVALUATE:
{issue_json}

Determine if this issue is relevant to the original callstack/trace above."""
            
            self.logger.debug(f"Analyzing issue for trace relevance: {issue_text[:100]}...")
            
            # Create LLM instance for this analysis
            claude = self._create_llm_instance()
            
            # Start conversation tracking
            claude.start_conversation("trace_relevance", f"issue_{hash(issue_text)}")
            
            # Send message to LLM
            response = claude.send_message_with_system(
                system_prompt=self.system_prompt,
                messages=[{"role": "user", "content": user_message}],
                enable_system_cache=True,
                enable_tools=False  # No tools needed for simple relevance check
            )
            
            if not response:
                self.logger.warning("No response from LLM for trace relevance analysis, considering issue relevant")
                return True
            
            # Extract response text
            response_text = self._extract_response_text(response)
            if not response_text:
                self.logger.warning("Could not extract response text from LLM, considering issue relevant")
                return True
            
            # Clean the JSON response using centralized utility
            cleaned_response = clean_json_response(response_text)
            
            # Parse the cleaned JSON response
            try:
                result = json.loads(cleaned_response)
                is_relevant = result.get('result', True)  # Default to relevant if unclear
                
                if not isinstance(is_relevant, bool):
                    self.logger.warning(f"Invalid result type from LLM analysis: {type(is_relevant)}, considering issue relevant")
                    return True
                
                # Log the decision
                if not is_relevant:
                    self.logger.info(f"Issue classified as IRRELEVANT to trace: {issue_text[:100]}...")
                    # Save the dropped issue
                    self._save_dropped_issue(issue, original_trace, result)
                else:
                    self.logger.debug(f"Issue classified as RELEVANT to trace: {issue_text[:100]}...")
                
                return is_relevant
                
            except json.JSONDecodeError as e:
                self.logger.warning(f"Failed to parse LLM response as JSON: {e}, considering issue relevant")
                self.logger.debug(f"Cleaned response was: {cleaned_response}")
                return True
                
        except Exception as e:
            self.logger.error(f"Error during trace relevance analysis: {e}")
            return True  # Default to relevant on error
    
    def filter_issues(self, issues: List[Dict[str, Any]], original_trace: List[str]) -> List[Dict[str, Any]]:
        """
        Filter a list of issues, removing those irrelevant to the original trace.
        
        Args:
            issues: List of issue dictionaries
            original_trace: List of strings representing the original callstack/trace
            
        Returns:
            List[Dict[str, Any]]: Filtered list with irrelevant issues removed
        """
        if not issues:
            return issues
            
        self.logger.info(f"Filtering {len(issues)} issues for trace relevance...")
        
        filtered_issues = []
        dropped_count = 0
        
        for i, issue in enumerate(issues, 1):
            self.logger.debug(f"Processing issue {i}/{len(issues)}")
            
            if self.is_relevant_to_trace(issue, original_trace):
                filtered_issues.append(issue)
            else:
                dropped_count += 1
                self.logger.info(f"Dropping irrelevant issue {i}/{len(issues)}")
        
        self.logger.info(f"Trace relevance filtering complete: {len(filtered_issues)} kept, {dropped_count} dropped")
        
        return filtered_issues