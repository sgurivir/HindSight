#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Dummy LLM Provider
Provides mock responses without making actual API calls
"""

import time
from typing import Optional, Dict, Any, List
from .base_provider import BaseLLMProvider, LLMConfig
from ....utils.log_util import get_logger

logger = get_logger(__name__)


class DummyProvider(BaseLLMProvider):
    """
    Dummy LLM provider that returns mock responses without making API calls.
    Useful for testing and development without incurring API costs.
    """

    def __init__(self, config: LLMConfig):
        """
        Initialize the dummy provider.

        Args:
            config: LLM configuration (used for compatibility but no actual API calls made)
        """
        super().__init__(config)
        logger.info(f"Initialized DummyProvider with model: {config.model}")

    def make_request(self, payload: Dict[str, Any], max_retries: int = 3) -> Optional[Dict[str, Any]]:
        """
        Make a mock request that returns a dummy response without calling any API.

        Args:
            payload: Request payload (contains messages with diff context)
            max_retries: Maximum number of retries (ignored in dummy mode)

        Returns:
            Dict: Mock response in the expected format
        """
        logger.info("DummyProvider: Returning mock response (no API call made)")

        # Simulate some processing time
        time.sleep(0.1)

        # Extract context from payload to generate realistic responses
        context_info = self._extract_context_from_payload(payload)

        # Return a mock response that matches the expected Claude API format
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": self._generate_mock_analysis_response(context_info)
                    }
                }
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 200,
                "prompt_tokens": 100,
                "completion_tokens": 200
            },
            "model": self.config.model,
            "dummy_mode": True
        }

        return mock_response

    def create_payload(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        enable_system_cache: bool = False,
        cache_ttl: str = "1h",
        enable_tools: bool = False
    ) -> Dict[str, Any]:
        """
        Create a mock payload (no actual payload needed for dummy mode).

        Args:
            messages: List of message dictionaries
            stream: Whether to stream the response (ignored)
            enable_system_cache: Whether to enable caching (ignored)
            cache_ttl: TTL for cache control (ignored)
            enable_tools: Whether to enable tools (ignored in dummy mode)

        Returns:
            Dict: Mock payload
        """
        logger.debug("DummyProvider: Creating mock payload")

        return {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "stream": stream,
            "dummy_mode": True
        }

    def validate_connection(self) -> bool:
        """
        Mock connection validation (always returns True for dummy mode).

        Returns:
            bool: Always True for dummy provider
        """
        logger.info("DummyProvider: Mock connection validation (always successful)")
        return True

    def _extract_context_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract file paths, function names, and other context from the payload messages.
        
        Args:
            payload: Request payload containing messages
            
        Returns:
            Dict containing extracted context information
        """
        context = {
            "files": [],
            "functions": [],
            "lines": [],
            "repo_path": None
        }
        
        try:
            messages = payload.get("messages", [])
            
            # Combine all message content to search for context
            all_content = ""
            for message in messages:
                if isinstance(message, dict):
                    content = message.get("content", "")
                    if isinstance(content, str):
                        all_content += content + "\n"
                    elif isinstance(content, list):
                        # Handle content blocks format
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                all_content += block.get("text", "") + "\n"
            
            # Extract file paths (look for common patterns)
            import re
            
            # Look for file paths in diff format (e.g., "--- a/path/to/file.swift", "+++ b/path/to/file.swift")
            file_patterns = [
                r'[+-]{3}\s+[ab]/([^\s]+)',  # Git diff format
                r'diff --git a/([^\s]+)',    # Git diff header
                r'@@.*@@\s*([^\s]+)',       # Diff hunk header with file
                r'([a-zA-Z0-9_/.-]+\.[a-zA-Z]{1,4})',  # General file pattern
            ]
            
            for pattern in file_patterns:
                matches = re.findall(pattern, all_content)
                context["files"].extend(matches)
            
            # Look for function names (Swift, Objective-C, etc.)
            function_patterns = [
                r'func\s+([a-zA-Z_][a-zA-Z0-9_]*)',  # Swift functions
                r'-\s*\([^)]+\)\s*([a-zA-Z_][a-zA-Z0-9_]*)',  # Objective-C methods
                r'\+\s*\([^)]+\)\s*([a-zA-Z_][a-zA-Z0-9_]*)',  # Objective-C class methods
                r'def\s+([a-zA-Z_][a-zA-Z0-9_]*)',   # Python functions
                r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)',  # JavaScript functions
            ]
            
            for pattern in function_patterns:
                matches = re.findall(pattern, all_content)
                context["functions"].extend(matches)
            
            # Look for line numbers in diff context
            line_matches = re.findall(r'@@\s*-(\d+),?\d*\s*\+(\d+),?\d*\s*@@', all_content)
            for old_line, new_line in line_matches:
                context["lines"].extend([int(old_line), int(new_line)])
            
            # Remove duplicates and clean up
            context["files"] = list(set([f for f in context["files"] if f and len(f) > 0]))[:5]  # Limit to 5 files
            context["functions"] = list(set([f for f in context["functions"] if f and len(f) > 0]))[:5]  # Limit to 5 functions
            context["lines"] = list(set(context["lines"]))[:10]  # Limit to 10 line numbers
            
            logger.debug(f"Extracted context: {len(context['files'])} files, {len(context['functions'])} functions, {len(context['lines'])} lines")
            
        except Exception as e:
            logger.warning(f"Error extracting context from payload: {e}")
        
        return context

    def _generate_mock_analysis_response(self, context_info: Dict[str, Any] = None) -> str:
        """
        Generate a mock analysis response that looks realistic using extracted context.
        Returns a JSON array of issues as expected by the diff analysis system.

        Args:
            context_info: Extracted context information from the payload

        Returns:
            str: Mock JSON analysis response as array
        """
        if not context_info:
            context_info = {"files": [], "functions": [], "lines": []}
        
        # Use real files and functions if available, otherwise fall back to defaults
        files = context_info.get("files", [])
        functions = context_info.get("functions", [])
        lines = context_info.get("lines", [])
        
        # Default fallbacks if no context extracted
        if not files:
            files = ["SignalServiceKit/src/Messages/OWSMessageSender.swift", "Signal/src/ViewControllers/ConversationView/ConversationViewController.swift"]
        if not functions:
            functions = ["sendMessage", "viewDidLoad", "configureCell", "handleUserAction"]
        if not lines:
            lines = [42, 123, 89, 156]
        
        # Generate realistic issues using extracted context
        import random
        
        issues = []
        categories = ["performance", "memoryManagement", "concurrency", "errorHandling", "codeQuality", "security"]
        severities = ["low", "medium", "high"]
        
        # Generate 1-3 issues
        num_issues = random.randint(1, min(3, len(files)))
        
        for i in range(num_issues):
            file = files[i % len(files)]
            function = functions[i % len(functions)] if functions else f"function_{i+1}"
            line = lines[i % len(lines)] if lines else random.randint(10, 200)
            category = random.choice(categories)
            severity = random.choice(severities)
            
            issue = {
                "severity": severity,
                "confidence": round(random.uniform(0.7, 0.95), 2),
                "description": f"Mock analysis detected potential {category} issue in {function}",
                "category": category,
                "issueType": f"mock{category.capitalize()}Issue",
                "recommendation": f"Consider reviewing the {category} aspects of {function}",
                "codeSnippet": f"// Code snippet from {file} around line {line}",
                "explanation": f"This is a simulated {severity} severity {category} issue found in {function} at line {line}. Generated by DummyProvider for testing.",
                "suggestedFix": f"Review and optimize the {category} implementation in {function}",
                "file": file,
                "line": line,
                "function": function
            }
            issues.append(issue)
        
        import json
        return json.dumps(issues, indent=2)