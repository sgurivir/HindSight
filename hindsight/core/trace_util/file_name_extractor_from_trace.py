#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
FileNameExtractorFromTrace - Service for extracting file names from trace data using LLM
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Dict

from ..llm.llm import Claude, ClaudeConfig
from ..constants import DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT
from ...utils.config_util import load_config_tolerant, get_api_key_from_config, get_llm_provider_type
from ...utils.file_util import read_file
from ...utils.log_util import get_logger
from ...utils.file_content_provider import FileContentProvider

logger = get_logger(__name__)


class FileNameExtractorFromTrace:
    """
    Service for extracting file names from trace data using LLM.
    Supports both AWS Bedrock and Claude API providers.
    """

    def __init__(self, config: dict, repo_path: str = None):
        """
        Initialize the FileNameExtractorFromTrace with LLM configuration.

        Args:
            config: Configuration dictionary containing LLM settings
            repo_path: Path to the repository for file searching
        """
        self.config = config
        self.repo_path = repo_path
        self.file_content_provider = None
        
        # Extract LLM configuration
        api_key = get_api_key_from_config(config)
        if not api_key:
            raise ValueError("API key not found in configuration")
        
        # Create Claude configuration
        claude_config = ClaudeConfig(
            api_key=api_key,
            api_url=config.get('api_end_point', DEFAULT_LLM_API_END_POINT),
            model=config.get('model', DEFAULT_LLM_MODEL),
            max_tokens=config.get('max_tokens', 64000),
            temperature=config.get('temperature', 0.1),
            provider_type=get_llm_provider_type(config)
        )
        
        # Initialize Claude client
        self.claude = Claude(claude_config)
        
        # Load system prompt
        self.system_prompt = self._load_system_prompt()
        
        # Initialize FileContentProvider if repo_path is provided
        if self.repo_path:
            self._initialize_file_content_provider()
        
        logger.info(f"Initialized FileNameExtractorFromTrace with provider: {claude_config.provider_type}")

    def _load_system_prompt(self) -> str:
        """
        Load the system prompt for file name extraction.
        
        Returns:
            str: System prompt content
        """
        try:
            # Get the path to the system prompt file
            prompt_file = Path(__file__).parent.parent / "prompts" / "systemPromptFileNameExtraction.md"
            
            if not prompt_file.exists():
                logger.error(f"System prompt file not found: {prompt_file}")
                return "You are a file name extraction assistant. Extract all file names from the given trace data and return them as a JSON array."
            
            content = read_file(str(prompt_file))
            if content:
                logger.info("Successfully loaded system prompt for file name extraction")
                return content
            else:
                logger.warning("Failed to read system prompt file, using fallback")
                return "You are a file name extraction assistant. Extract all file names from the given trace data and return them as a JSON array."
                
        except Exception as e:
            logger.error(f"Error loading system prompt: {e}")
            return "You are a file name extraction assistant. Extract all file names from the given trace data and return them as a JSON array."

    def extract_file_names(self, trace_content: str) -> List[str]:
        """
        Extract file names from trace content using LLM.
        
        Args:
            trace_content: The trace data as a string
            
        Returns:
            List[str]: List of extracted file names
        """
        try:
            logger.info(f"Extracting file names from trace content ({len(trace_content)} characters)")
            
            # Start conversation tracking
            self.claude.start_conversation("file_name_extraction", "trace_analysis")
            
            # Prepare user message
            user_message = f"Extract all file names from the following trace data:\n\n{trace_content}"
            
            # Send message to LLM
            response = self.claude.send_message_with_system(
                system_prompt=self.system_prompt,
                messages=[{"role": "user", "content": user_message}],
                enable_system_cache=True,
                cache_ttl="1h"
            )
            
            if not response or "error" in response:
                logger.error("Failed to get response from LLM")
                return []
            
            # Extract response content
            response_content = ""
            if "content" in response and isinstance(response.get("content"), list):
                # Claude native format
                for block in response.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        response_content += block.get("text", "")
            elif "choices" in response:
                # AWS Bedrock format
                choices = response.get("choices", [])
                if choices:
                    assistant_message = choices[0].get("message", {})
                    response_content = assistant_message.get("content", "")
            
            if not response_content:
                logger.error("No content in LLM response")
                return []
            
            # Parse JSON response - handle markdown code blocks
            try:
                # Clean the response content to extract JSON from markdown code blocks
                cleaned_content = self._extract_json_from_response(response_content)
                
                file_names = json.loads(cleaned_content)
                if isinstance(file_names, list):
                    # Validate that all items are strings and filter out empty/whitespace-only names
                    validated_names = []
                    for name in file_names:
                        if isinstance(name, str) and name.strip():
                            validated_names.append(name.strip())
                        elif name:  # Log non-string or empty entries for debugging
                            logger.debug(f"Filtered out invalid file name: {repr(name)} (type: {type(name)})")
                    
                    logger.info(f"Successfully extracted {len(validated_names)} file names (filtered from {len(file_names)} total)")
                    return validated_names
                else:
                    logger.error("LLM response is not a JSON array")
                    return []
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM response as JSON: {e}")
                logger.debug(f"Raw response: {response_content}")
                logger.debug(f"Cleaned content: {self._extract_json_from_response(response_content)}")
                return []
            
        except Exception as e:
            logger.error(f"Error extracting file names: {e}")
            return []
        finally:
            # Log conversation
            try:
                self.claude.log_complete_conversation()
            except Exception as e:
                logger.warning(f"Failed to log conversation: {e}")

    def _extract_json_from_response(self, response_content: str) -> str:
        """
        Extract JSON content from LLM response, handling markdown code blocks.
        
        Args:
            response_content: Raw response content from LLM
            
        Returns:
            str: Cleaned JSON content
        """
        content = response_content.strip()
        
        # Check if content is wrapped in markdown code blocks
        if content.startswith('```'):
            lines = content.split('\n')
            
            # Find the start and end of the code block
            start_idx = 0
            end_idx = len(lines)
            
            # Skip the opening ```json or ``` line
            if lines[0].startswith('```'):
                start_idx = 1
            
            # Find the closing ``` line
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == '```':
                    end_idx = i
                    break
            
            # Extract content between code block markers
            json_lines = lines[start_idx:end_idx]
            content = '\n'.join(json_lines).strip()
        
        return content

    def _initialize_file_content_provider(self):
        """
        Initialize FileContentProvider for the repository.
        """
        try:
            # Try to get existing FileContentProvider singleton
            try:
                self.file_content_provider = FileContentProvider.get()
                logger.info("Using existing FileContentProvider singleton")
                return
            except RuntimeError:
                pass
            
            # Create new FileContentProvider from repository using simplified API
            logger.info(f"Initializing FileContentProvider for repository: {self.repo_path}")
            self.file_content_provider = FileContentProvider.from_repo(self.repo_path)
            logger.info("FileContentProvider initialized successfully")
            
        except Exception as e:
            logger.warning(f"Failed to initialize FileContentProvider: {e}")
            self.file_content_provider = None

    def find_files_in_repository(self, file_names: List[str]) -> Dict[str, List[str]]:
        """
        Find the extracted file names in the repository and return their relative paths.
        
        Args:
            file_names: List of file names extracted from trace
            
        Returns:
            Dict mapping file names to list of relative paths where they were found
        """
        found_files = {}
        
        if not self.file_content_provider:
            logger.warning("FileContentProvider not available, cannot search repository")
            return found_files
        
        for file_name in file_names:
            try:
                # Get all candidates for this file name
                candidates = FileContentProvider.all_candidates_for(file_name)
                
                if candidates:
                    # Convert absolute paths to relative paths
                    relative_paths = []
                    for abs_path in candidates:
                        try:
                            if self.repo_path:
                                from pathlib import Path
                                repo_path_obj = Path(self.repo_path).resolve()
                                abs_path_obj = Path(abs_path).resolve()
                                
                                # Try to get relative path
                                try:
                                    rel_path = abs_path_obj.relative_to(repo_path_obj)
                                    relative_paths.append(str(rel_path))
                                except ValueError:
                                    # Path is not relative to repo, use absolute path
                                    relative_paths.append(abs_path)
                            else:
                                relative_paths.append(abs_path)
                        except Exception as e:
                            logger.debug(f"Error converting path {abs_path} to relative: {e}")
                            relative_paths.append(abs_path)
                    
                    found_files[file_name] = relative_paths
                    logger.info(f"Found {len(relative_paths)} instance(s) of '{file_name}' in repository")
                else:
                    logger.info(f"File '{file_name}' not found in repository")
                    
            except Exception as e:
                logger.error(f"Error searching for file '{file_name}': {e}")
        
        return found_files

    def get_all_file_paths(self, trace_content: str) -> List[str]:
        """
        Extract file names from trace and return all relative paths found in repository.
        This is the main method that combines LLM extraction with FileContentProvider filtering.
        
        Args:
            trace_content: The trace data as a string
            
        Returns:
            List[str]: List of all relative paths to files found in repository
        """
        # Step 1: Extract file names using LLM
        file_names = self.extract_file_names(trace_content)
        
        if not file_names:
            logger.info("No file names extracted from trace")
            return []
        
        # Step 2: Find files in repository using FileContentProvider
        found_files = self.find_files_in_repository(file_names)
        
        # Step 3: Flatten to single list of all relative paths
        all_paths = []
        for file_name, paths in found_files.items():
            all_paths.extend(paths)
        
        logger.info(f"Found {len(all_paths)} total file paths in repository from {len(file_names)} extracted file names")
        return all_paths

