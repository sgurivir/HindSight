#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Trace Code Analysis Module
Specialized code analysis for trace analysis that uses trace-specific prompts
"""

import os
import json
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple

from ..constants import MAX_TOOL_ITERATIONS
from ..llm.llm import Claude, ClaudeConfig
from ..llm.tools import Tools
from .trace_prompt_builder import TracePromptBuilder
from .trace_result_repository import TraceAnalysisResultRepository, TraceAnalysisResult
from .file_name_extractor_from_trace import FileNameExtractorFromTrace

# Import AnalysisRunner conditionally to avoid circular imports
from ...utils.directory_tree_util import DirectoryTreeUtil
from ...utils.file_util import read_file
from ...utils.json_util import validate_and_format_json, clean_json_response
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)

@dataclass
class TraceAnalysisConfig:
    """Configuration for trace analysis"""
    prompt_file_path: str
    api_key: str
    api_url: str
    model: str
    repo_path: str
    output_file: str
    max_tokens: int = 64000
    temperature: float = 0.1
    config: Dict[str, Any] = None  # Store the full configuration dict

class TraceCodeAnalysis:
    """
    Specialized code analysis orchestrator for trace analysis.
    Uses trace-specific prompts and handles trace analysis workflow.
    """

    def __init__(self, config: TraceAnalysisConfig):
        """
        Initialize TraceCodeAnalysis with configuration.

        Args:
            config: Trace analysis configuration
        """
        self.config = config

        # Initialize Claude client
        claude_config = ClaudeConfig(
            api_key=config.api_key,
            api_url=config.api_url,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            provider_type=config.config.get('llm_provider_type', 'claude') if config.config else 'claude'
        )
        self.claude = Claude(claude_config)

        # Initialize tools with temp directory configuration and ignore directories
        # Use the output directory from the singleton instead of JSON config
        try:
            output_provider = get_output_directory_provider()
            output_base_dir = output_provider.get_custom_base_dir()
        except RuntimeError:
            # Fallback if singleton not configured
            output_base_dir = None

        # Get ignore directories from config and create case variants
        ignore_dirs = set()
        if config.config and config.config.get('exclude_directories'):
            base_dirs_to_ignore = config.config.get('exclude_directories', [])
            for dir_name in base_dirs_to_ignore:
                ignore_dirs.add(dir_name)
                ignore_dirs.add(dir_name.upper())
                ignore_dirs.add(dir_name.lower())

        # Get FileContentProvider instance if available from TraceAnalysisResultRepository
        file_content_provider = None
        try:
            trace_result_repository = TraceAnalysisResultRepository.get_instance()
            if hasattr(trace_result_repository, 'file_content_provider'):
                file_content_provider = trace_result_repository.file_content_provider
        except Exception:
            pass  # FileContentProvider not available, continue without it

        # Get the artifacts directory path (code_insights subdirectory)
        output_provider = get_output_directory_provider()
        artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"

        # Get DirectoryTreeUtil instance from AnalysisRunner if available
        directory_tree_util = None
        try:
            # Import AnalysisRunner here to avoid circular import
            from ...analyzers.analysis_runner import AnalysisRunner
            directory_tree_util = AnalysisRunner().directory_tree_util
        except Exception as e:
            logger.warning(f"Could not get DirectoryTreeUtil from AnalysisRunner: {e}")
            # Create a new DirectoryTreeUtil instance
            try:
                directory_tree_util = DirectoryTreeUtil()
                logger.info("Created new DirectoryTreeUtil instance")
            except Exception as e2:
                logger.error(f"Could not create DirectoryTreeUtil instance: {e2}")

        self.tools = Tools(config.repo_path, output_base_dir, file_content_provider, artifacts_dir, directory_tree_util, ignore_dirs)

        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        logger.info("Initialized TraceCodeAnalysis with provided config")

    def _extract_and_log_token_usage(self, response: Dict[str, Any], iteration: int) -> None:
        """
        Extract token usage from API response and log it.

        Args:
            response: API response from Claude
            iteration: Current iteration number
        """
        try:
            # Extract token usage from response
            usage = response.get("usage", {})

            input_tokens = (
                usage.get("input_tokens", 0) or
                usage.get("prompt_tokens", 0)
            )
            output_tokens = (
                usage.get("output_tokens", 0) or
                usage.get("completion_tokens", 0)
            )

            if input_tokens > 0 or output_tokens > 0:
                # Log current API call token usage
                logger.info(f"Iteration {iteration} - Input tokens: {input_tokens:,}, Output tokens: {output_tokens:,}")

                # Update totals
                self.total_input_tokens += input_tokens
                self.total_output_tokens += output_tokens

                # Log running totals
                logger.info(f"Running totals - Input tokens: {self.total_input_tokens:,}, Output tokens: {self.total_output_tokens:,}")
            else:
                logger.warning(f"Iteration {iteration} - No token usage information found in API response")

        except Exception as e:
            logger.error(f"Error extracting token usage from API response: {e}")

    def _log_final_token_summary(self) -> None:
        """Log final token usage summary."""
        try:
            total_tokens = self.total_input_tokens + self.total_output_tokens
            logger.info(f"Token consumed: Input: {self.total_input_tokens:,}, Output: {self.total_output_tokens:,}, Used: {total_tokens:,}")
        except Exception as e:
            logger.error(f"Error logging final token summary: {e}")

    def get_token_totals(self) -> tuple:
        """
        Get the total input and output tokens used by this analysis.

        Returns:
            tuple: (total_input_tokens, total_output_tokens)
        """
        return self.total_input_tokens, self.total_output_tokens

    def _process_analysis_result(self, result: str) -> Tuple[bool, str, bool]:
        """
        Process and clean the analysis result, with optional double-check validation.

        Args:
            result: Raw analysis result from Claude

        Returns:
            Tuple[bool, str, bool]: (success, processed_result, is_double_check_drop)
        """
        try:
            logger.info(f"Processing result: {len(result)} characters")

            cleaned_result = clean_json_response(result)

            # Validate and format JSON
            is_valid, final_output = validate_and_format_json(cleaned_result)

            if is_valid:
                logger.info("Result is valid JSON - cleaned and formatted")
            else:
                logger.warning("Result is not valid JSON after cleanup - saving as-is")
                final_output = cleaned_result

            return True, final_output, False

        except Exception as e:
            logger.error(f"Error processing result: {e}")
            return False, result, False

    def _get_original_context_for_validation(self) -> str:
        """
        Get the original context from the prompt file being analyzed for validation purposes.

        Returns:
            str: Original context as JSON string
        """
        try:
            # Read the original prompt file that was analyzed
            with open(self.config.prompt_file_path, 'r', encoding='utf-8') as f:
                prompt_content = f.read()

            # Extract the callstack section from the prompt for context
            # Find the callstack section between markers
            start_marker = "Analyze this callstack\n======"
            end_marker = "\n====Use this additional context if needed==="

            start_idx = prompt_content.find(start_marker)
            if start_idx != -1:
                start_idx += len(start_marker)
                end_idx = prompt_content.find(end_marker, start_idx)
                if end_idx != -1:
                    callstack_section = prompt_content[start_idx:end_idx].strip()
                    return json.dumps({"callstack": callstack_section}, ensure_ascii=False)

            # Fallback: return the entire prompt content as context
            return json.dumps({"prompt_content": prompt_content}, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"Could not read original context from {self.config.prompt_file_path}: {e}")
            # Fallback to extracting context from analysis result
            from ...analyzers.analysis_runner import AnalysisRunner
            runner = AnalysisRunner()
            return runner._extract_original_context_from_analysis("")


    def _save_result(self, result: str) -> bool:
        """
        Save the analysis result to output file using TraceResultRepository.

        Args:
            result: Processed analysis result

        Returns:
            bool: True if successful
        """
        try:

            # Get the singleton instance
            trace_result_repository = TraceAnalysisResultRepository.get_instance()

            # Save the result using the repository
            return trace_result_repository.save_trace_result(
                output_file=self.config.output_file,
                results_data=result,
                metadata={
                    'prompt_file_path': self.config.prompt_file_path,
                    'repo_path': self.config.repo_path,
                    'timestamp': time.time(),
                    'analysis_type': 'individual_trace'
                }
            )

        except Exception as e:
            logger.error(f"Error saving result using TraceResultRepository: {e}")
            # Fallback to static method
            logger.info("Falling back to static save method...")

            return TraceAnalysisResult.save_result(
                result=result,
                output_file=self.config.output_file,
                prompt_file_path=self.config.prompt_file_path,
                repo_path=self.config.repo_path
            )

    def run_analysis(self) -> bool:
        """
        Run the complete trace analysis process with iterative tool usage.

        Returns:
            bool: True if analysis completed successfully
        """
        logger.info("Starting trace analysis...")
        start_time = time.time()

        # Start conversation tracking
        context_info = os.path.basename(self.config.prompt_file_path)
        self.claude.start_conversation("trace_analysis", context_info)

        try:
            # Load prompt content
            prompt_content = read_file(self.config.prompt_file_path)
            if not prompt_content:
                logger.error(f"Failed to read prompt file: {self.config.prompt_file_path}")
                return False

            logger.info(f"Successfully loaded prompt: {self.config.prompt_file_path}")
            logger.info(f"Prompt content length: {len(prompt_content)} characters")

            # Step 1: Extract file names from trace using FileNameExtractorFromTrace
            extracted_file_paths = []
            try:
                logger.info("Extracting file names from trace content...")
                file_name_extractor = FileNameExtractorFromTrace(
                    config=self.config.config,
                    repo_path=self.config.repo_path
                )
                
                # Extract all file paths from the trace content
                extracted_file_paths = file_name_extractor.get_all_file_paths(prompt_content)
                
                if extracted_file_paths:
                    logger.info(f"Extracted {len(extracted_file_paths)} file paths from trace: {extracted_file_paths[:5]}{'...' if len(extracted_file_paths) > 5 else ''}")
                else:
                    logger.info("No file paths extracted from trace content")
                    
            except Exception as e:
                logger.warning(f"Failed to extract file names from trace: {e}")
                logger.info("Continuing with trace analysis without extracted file paths")

            # Step 2: Build prompts using TracePromptBuilder with extracted file paths
            system_prompt, user_prompt = TracePromptBuilder.build_complete_prompt(prompt_content, extracted_file_paths)

            # Check token limits AFTER the prompt content is inserted
            if not self.claude.check_token_limit(system_prompt, user_prompt):
                total_chars = len(system_prompt + user_prompt)
                estimated_tokens = self.claude.estimate_tokens(system_prompt + user_prompt)
                max_input_tokens = self.claude.config.max_tokens - 5000

                logger.error(f"CRITICAL: Input exceeds token limits - ABORTING analysis to prevent API error")
                logger.error(f"System prompt: {len(system_prompt):,} chars")
                logger.error(f"User prompt: {len(user_prompt):,} chars")
                logger.error(f"Total content: {total_chars:,} chars")
                logger.error(f"Estimated tokens: {estimated_tokens:,}")
                logger.error(f"Max input tokens allowed: {max_input_tokens:,}")
                logger.error(f"Model limit: {self.claude.config.max_tokens:,}")
                logger.error(f"Prompt file: {self.config.prompt_file_path}")

                # Log final token usage summary even for failed analysis due to token limits
                self._log_final_token_summary()
                return False

            logger.info(f"Token limit check passed - estimated tokens: {self.claude.estimate_tokens(system_prompt + user_prompt):,}")

            # Run iterative analysis with tool usage using unified method
            analysis_result = self.claude.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_executor=self,  # Pass self so tools can be accessed via self.tools
                supported_tools=[
                    "readFile", "runTerminalCmd", "getImplementation", "getSummaryOfFile",
                    "inspectDirectoryHierarchy", "list_files",
                    "getFileContentByLines", "getFileContent", "checkFileSize"
                ],
                context_guidance_template="""
Based on the tool results above, please continue your trace analysis. Remember to:
1. Analyze the callstack and trace information provided by the tools
2. Identify the root cause of issues or performance bottlenecks
3. Provide specific, actionable recommendations
4. Focus on the original trace analysis request: {user_prompt}

Please provide your analysis based on all the information gathered so far.
""",
                token_usage_callback=self._extract_and_log_token_usage
            )

            # Calculate execution time
            end_time = time.time()
            execution_time = end_time - start_time

            # Process results
            if analysis_result:
                # Process and clean the result
                success, processed_result, is_double_check_drop = self._process_analysis_result(analysis_result)

                # Log complete conversation
                self.claude.log_complete_conversation(
                    final_result=processed_result if success else analysis_result
                )

                if success:
                    # Save result
                    save_success = self._save_result(processed_result)

                    if save_success:
                        logger.info("Trace analysis completed successfully!")
                        logger.info(f"Total time taken: {execution_time:.2f} seconds")
                        self.tools.log_tool_usage_summary()
                        # Log final token usage summary
                        self._log_final_token_summary()
                        return True
                    else:
                        logger.error("Failed to save results")
                        return False
                else:
                    if is_double_check_drop:
                        logger.info("Analysis result dropped due to double-check validation")
                        # Log final token usage summary even for double-check drops
                        self._log_final_token_summary()
                        return False  # Still return False but don't log as error
                    else:
                        logger.error("Failed to process results")
                        return False
            else:
                # Log conversation even for failed analysis
                self.claude.log_complete_conversation(
                    final_result="Analysis failed - no result from Claude API"
                )

                logger.error("Analysis failed - no result from Claude API")
                logger.info(f"Total time taken: {execution_time:.2f} seconds")
                # Log final token usage summary even for failed analysis
                self._log_final_token_summary()
                return False

        except Exception as e:
            # Log conversation even for unexpected errors
            self.claude.log_complete_conversation(
                final_result=f"Unexpected error during trace analysis: {e}"
            )

            logger.error(f"Unexpected error during trace analysis: {e}")
            # Log final token usage summary even for unexpected errors
            self._log_final_token_summary()
            return False

    # Removed _run_iterative_analysis - now using unified method in llm.py

