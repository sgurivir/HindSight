#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Code Analysis Module
Handles the main code analysis logic and orchestrates the analysis process
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, List

from .llm import Claude, ClaudeConfig
from .tools import Tools
from .iterative import ContextCollectionAnalyzer, CodeAnalysisAnalyzer
from ..constants import MAX_TOOL_ITERATIONS
from ..prompts.prompt_builder import PromptBuilder
from ..ast_index import RepoAstIndex
from ...utils.directory_tree_util import DirectoryTreeUtil
from ...utils.file_util import read_json_file, write_json_file, write_file, get_artifacts_temp_file_path
from ...utils.json_util import validate_and_format_json, clean_json_response
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)


@dataclass
class AnalysisConfig:
    """Configuration for code analysis"""
    json_file_path: str
    api_key: str
    api_url: str
    model: str
    repo_path: str
    output_file: str
    max_tokens: int = 64000
    temperature: float = 0.1
    processed_cache_file: Optional[str] = None  # Legacy cache system removed
    config: Dict[str, Any] = None  # Store the full configuration dict
    file_content_provider: Any = None  # FileContentProvider instance for file resolution
    file_filter: List[str] = None  # Optional list of files to limit analysis to
    min_function_body_length: int = 7  # Minimum number of lines for a function to be analyzed


class CodeAnalysis:
    """
    Main code analysis orchestrator.
    Handles the complete analysis process from input to output.
    """

    def __init__(self, config: AnalysisConfig):
        """
        Initialize CodeAnalysis with configuration.

        Args:
            config: Analysis configuration
        """
        self.config = config

        # Store file filter for use in analysis
        self.file_filter = config.file_filter or []

        # Publisher for result checking and publishing (set by runner)
        self.publisher = None

        # Initialize centralized AST index for lazy loading
        self.ast_index = RepoAstIndex()
        self._load_merged_data()

        # Initialize Claude client
        claude_config = ClaudeConfig(
            api_key=config.api_key,
            api_url=config.api_url,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            provider_type=config.config.get('llm_provider_type', 'aws_bedrock') if config.config else 'aws_bedrock'
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

        # Get ignore directories from config and create case variants (same as main.py)
        ignore_dirs = set()
        if config.config and config.config.get('exclude_directories'):
            base_dirs_to_ignore = config.config.get('exclude_directories', [])
            for dir_name in base_dirs_to_ignore:
                # Add original case (given case)
                ignore_dirs.add(dir_name)
                # Add uppercase variant
                ignore_dirs.add(dir_name.upper())
                # Add lowercase variant
                ignore_dirs.add(dir_name.lower())

        # Pass FileContentProvider to Tools if available
        file_content_provider = config.file_content_provider if hasattr(config, 'file_content_provider') else None

        # Get the artifacts directory path (code_insights subdirectory)
        output_provider = get_output_directory_provider()
        artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"

        # Get DirectoryTreeUtil instance from AnalysisRunner if available
        directory_tree_util = None
        try:
            # Lazy import to avoid circular dependency
            from ...analyzers.analysis_runner import AnalysisRunner
            directory_tree_util = AnalysisRunner().directory_tree_util
        except Exception as e:
            logger.warning(f"Could not get DirectoryTreeUtil from AnalysisRunner: {e}")
            # Create a new DirectoryTreeUtil instance as fallback
            try:
                directory_tree_util = DirectoryTreeUtil()
                logger.info("Created new DirectoryTreeUtil instance as fallback")
            except Exception as e2:
                logger.error(f"Could not create DirectoryTreeUtil instance: {e2}")

        self.tools = Tools(config.repo_path, output_base_dir, file_content_provider, artifacts_dir, directory_tree_util, ignore_dirs)

        # Cache for processed files
        self.processed_cache_file = config.processed_cache_file

        # Token tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        logger.info("Initialized CodeAnalysis with provided config")

    def set_publisher(self, publisher) -> None:
        """
        Set the publisher for result checking and publishing.

        Args:
            publisher: The CodeAnalysisResultsPublisher instance
        """
        self.publisher = publisher
        logger.debug("Publisher set for code analysis")

    def should_analyze_function(self, file_path: str, function_name: str, checksum: str) -> bool:
        """
        Check if function should be analyzed by looking for existing results.

        Args:
            file_path: Path to the file containing the function
            function_name: Name of the function
            checksum: Function checksum

        Returns:
            True if analysis is needed, False if result already exists
        """
        if not self.publisher:
            return True  # No publisher, analyze everything

        existing_result = self.publisher.check_existing_result(file_path, function_name, checksum)
        if existing_result:
            logger.info(f"Skipping analysis for {function_name} in {file_path} - result already exists (checksum: {checksum[:8]}...)")
            return False

        return True

    def _load_merged_data(self) -> None:
        """
        Initialize AST index for centralized loading.
        The actual loading is now handled lazily by RepoAstIndex.
        """
        try:
            # Validate that AST has been built before analysis
            self.ast_index.validate_ast_built()
            logger.debug("AST index initialized and validated")
        except RuntimeError as e:
            logger.warning(f"AST validation failed: {e}")
            # Don't fail initialization - let individual property access handle missing files

    def _should_analyze_function_data(self, function_data: Dict[str, Any]) -> bool:
        """
        Check if a function should be analyzed based on the file filter.

        Args:
            function_data: The function data from JSON

        Returns:
            bool: True if the function should be analyzed, False otherwise
        """
        # If no file filter is set or empty, analyze everything
        if not self.file_filter:
            return True

        # Extract file path from the function data
        file_path = None

        # Check different possible structures in the function data
        if 'file' in function_data:
            file_path = function_data['file']
        elif 'context' in function_data and isinstance(function_data['context'], dict):
            file_path = function_data['context'].get('file')

        if not file_path:
            # If we can't determine the file path, don't filter it out
            return True

        # Normalize file paths for comparison (remove leading ./ and handle relative paths)
        normalized_file_path = file_path.lstrip('./')

        # Check if the file is in our filter list
        for filter_file in self.file_filter:
            normalized_filter_file = filter_file.lstrip('./')
            if normalized_file_path == normalized_filter_file or normalized_file_path.endswith('/' + normalized_filter_file):
                return True

        return False

    def _filter_json_content(self, json_content: str) -> str:
        """
        Filter JSON content to only include functions/classes from files in the file filter.

        Args:
            json_content: Original JSON content as string

        Returns:
            str: Filtered JSON content as string
        """
        # If no file filter is set or empty, return original content (analyze everything)
        if not self.file_filter:
            return json_content

        try:
            # Parse the JSON content
            data = json.loads(json_content)

            # Handle different JSON structures
            if isinstance(data, dict):
                # Check if this is a single function/file entry
                if self._should_analyze_function_data(data):
                    return json_content
                else:
                    # This function/file should be filtered out
                    logger.info(f"Filtering out function/file due to file filter")
                    return json.dumps({"filtered": True, "reason": "File not in filter list"})
            elif isinstance(data, list):
                # Filter the list of functions/files
                filtered_data = []
                for item in data:
                    if self._should_analyze_function_data(item):
                        filtered_data.append(item)

                if filtered_data:
                    return json.dumps(filtered_data, ensure_ascii=False)
                else:
                    logger.info(f"All functions/files filtered out due to file filter")
                    return json.dumps({"filtered": True, "reason": "No functions in filter list"})

            return json_content

        except json.JSONDecodeError as e:
            logger.warning(f"Could not parse JSON for filtering: {e}, proceeding with original content")
            return json_content
        except Exception as e:
            logger.warning(f"Error filtering JSON content: {e}, proceeding with original content")
            return json_content


    def _build_prompts(self, json_content: str, user_provided_prompts: List[str] = None) -> Tuple[str, str]:
        """
        Build system and user prompts for analysis.

        Args:
            json_content: JSON content to analyze
            user_provided_prompts: Optional list of user-provided prompts to include in system prompt

        Returns:
            Tuple[str, str]: (system_prompt, user_prompt)
        """
        try:
            # Determine analysis type
            analysis_type = PromptBuilder.determine_analysis_type(json_content)
            logger.info(f"Determined analysis type: {analysis_type}")

            # Build complete prompts with AST index data and user-provided prompts
            system_prompt, user_prompt = PromptBuilder.build_complete_prompt(
                json_content,
                analysis_type=analysis_type,
                config=self.config.config,
                merged_functions_data=self.ast_index.merged_functions,
                merged_data_types_data=self.ast_index.merged_types,
                merged_call_graph_data=self.ast_index.merged_call_graph,
                user_provided_prompts=user_provided_prompts
            )

            logger.info(f"Built prompts - System: {len(system_prompt)} chars, User: {len(user_prompt)} chars")
            if user_provided_prompts:
                total_prompt_chars = sum(len(prompt) for prompt in user_provided_prompts)
                logger.info(f"User-provided prompts included: {len(user_provided_prompts)} prompts, {total_prompt_chars} total chars")
            return system_prompt, user_prompt

        except Exception as e:
            logger.error(f"Error building prompts: {e}")
            # Return basic prompts as fallback
            return (
                "You are a senior software engineer conducting code analysis.",
                f"Analyze this code:\n{json_content}"
            )

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
            logger.info(f"TOKEN USAGE SUMMARY - Input: {self.total_input_tokens:,}, Output: {self.total_output_tokens:,}, Total: {total_tokens:,}")
        except Exception as e:
            logger.error(f"Error logging final token summary: {e}")

    def get_token_totals(self) -> tuple:
        """
        Get the total input and output tokens used by this analysis.

        Returns:
            tuple: (total_input_tokens, total_output_tokens)
        """
        return self.total_input_tokens, self.total_output_tokens

    def _write_current_full_prompt(self, system_prompt: str, user_prompt: str) -> None:
        """
        Write the current full prompt to currentFullPrompt.txt for debugging.

        Args:
            system_prompt: System prompt text
            user_prompt: User prompt text
        """
        try:
            full_prompt = f"# SYSTEM PROMPT\n\n{system_prompt}\n\n# USER PROMPT\n\n{user_prompt}"

            # Write to temp directory instead of current working directory
            repo_path = self.config.repo_path
            # Use the output directory from the singleton instead of JSON config

            try:
                output_provider = get_output_directory_provider()
                output_base_dir = output_provider.get_custom_base_dir()
            except RuntimeError:
                # Fallback if singleton not configured
                output_base_dir = None
            prompt_file_path = get_artifacts_temp_file_path(repo_path, "currentFullPrompt.txt", output_base_dir)
            success = write_file(prompt_file_path, full_prompt)
            if not success:
                logger.warning("Failed to write current full prompt")

        except Exception as e:
            logger.error(f"Error writing current full prompt: {e}")

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
        Get the original context from the JSON file being analyzed for validation purposes.

        Returns:
            str: Original context as JSON string
        """
        try:
            # Read the original JSON file that was analyzed
            with open(self.config.json_file_path, 'r', encoding='utf-8') as f:
                original_data = f.read()
            return original_data
        except Exception as e:
            logger.warning(f"Could not read original context from {self.config.json_file_path}: {e}")
            # Fallback to extracting context from analysis result
            # Lazy import to avoid circular dependency
            from ...analyzers.analysis_runner import AnalysisRunner
            runner = AnalysisRunner()
            return runner._extract_original_context_from_analysis("")

    def _save_result(self, result: str) -> bool:
        """
        Save the analysis result to output file.

        Args:
            result: Processed analysis result

        Returns:
            bool: True if successful
        """
        try:
            success = write_file(self.config.output_file, result)
            if success:
                logger.info(f"Results saved to {self.config.output_file}")
            return success
        except Exception as e:
            logger.error(f"Error saving result: {e}")
            return False

    def _load_processed_cache(self) -> Dict[str, Dict]:
        """Load the cache of processed files"""
        if not self.processed_cache_file:
            logger.debug("No processed cache file configured, starting fresh")
            return {}

        try:
            # Check if processed_cache_file is None (legacy cache system removed)
            if self.processed_cache_file is None:
                logger.debug("Processed cache file is None (legacy cache system removed), starting fresh")
                return {}

            cache_data = read_json_file(self.processed_cache_file)
            if cache_data:
                logger.debug(f"Loaded processed cache with {len(cache_data)} entries")
                return cache_data
            else:
                logger.debug("No processed cache found or empty, starting fresh")
                return {}
        except Exception as e:
            logger.warning(f"Error loading processed cache: {e}")
            return {}

    def _update_processed_cache(self, success: bool, execution_time: float) -> None:
        """Update the processed cache in real time"""
        if not self.processed_cache_file:
            logger.debug("No processed cache file configured, skipping cache update")
            return

        try:
            # Check if processed_cache_file is None (legacy cache system removed)
            if self.processed_cache_file is None:
                logger.debug("Processed cache file is None (legacy cache system removed), skipping cache update")
                return

            # Load existing cache
            cache = self._load_processed_cache()

            # Get file info
            file_name = os.path.basename(self.config.json_file_path)
            output_size = 0
            if success and os.path.exists(self.config.output_file):
                output_size = os.path.getsize(self.config.output_file)

            # Update cache entry
            cache[file_name] = {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'input_file': self.config.json_file_path,
                'output_file': self.config.output_file,
                'output_size': output_size,
                'success': success,
                'execution_time': round(execution_time, 2),
                'model': self.config.model,
                'total_input_tokens': self.total_input_tokens,
                'total_output_tokens': self.total_output_tokens,
                'total_tokens': self.total_input_tokens + self.total_output_tokens
            }

            # Save cache immediately using utility function
            success_write = write_json_file(self.processed_cache_file, cache)

            if success_write:
                logger.debug(f"Updated processed cache for {file_name}")
            else:
                logger.warning(f"Failed to update processed cache for {file_name}")

        except Exception as e:
            logger.error(f"Error updating processed cache: {e}")

    def run_context_collection(self, json_data: dict, checksum: str) -> Optional[dict]:
        """
        Context Collection: Collect code context for the given function.

        Args:
            json_data: Function record dict (already parsed)
            checksum: Function checksum for caching

        Returns:
            Context bundle dict on success, None on failure
        """
        logger.info(f"Context Collection: Starting for checksum {checksum[:8]}...")
        start_time = time.time()

        # Check for existing context bundle on disk (retry optimization)
        try:
            output_provider = get_output_directory_provider()
            context_bundles_dir = f"{output_provider.get_repo_artifacts_dir()}/context_bundles"
            bundle_path = f"{context_bundles_dir}/{checksum[:8]}.json"

            if os.path.exists(bundle_path):
                with open(bundle_path, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                # A valid context bundle must contain primary_function.  Malformed
                # bundles lacking this key are discarded so Stage 4a re-runs and
                # produces a proper bundle.
                if isinstance(cached, dict) and 'primary_function' in cached:
                    logger.info(f"Context Collection: Found existing bundle at {bundle_path}, skipping collection")
                    return cached
                else:
                    logger.warning(
                        f"Context Collection: Cached bundle at {bundle_path} is malformed "
                        f"(missing 'primary_function'). Deleting and re-running Stage 4a."
                    )
                    try:
                        os.remove(bundle_path)
                    except OSError as del_err:
                        logger.warning(f"Context Collection: Could not delete malformed bundle: {del_err}")
        except Exception as e:
            logger.warning(f"Context Collection: Could not check for existing bundle: {e}")
            bundle_path = None
            context_bundles_dir = None

        # Start conversation tracking
        context_info = os.path.basename(self.config.json_file_path)
        self.claude.start_conversation("context_collection", context_info)

        try:
            json_content = json.dumps(json_data, ensure_ascii=False)

            # Build context collection prompts
            user_provided_prompts = None
            if self.config.config and isinstance(self.config.config, dict):
                user_provided_prompts = self.config.config.get('user_provided_prompts')

            system_prompt, user_prompt = PromptBuilder.build_context_collection_prompt(
                json_content=json_content,
                config=self.config.config,
                merged_functions_data=self.ast_index.merged_functions,
                merged_data_types_data=self.ast_index.merged_types,
                merged_call_graph_data=self.ast_index.merged_call_graph,
                user_provided_prompts=user_provided_prompts
            )

            # Check token limits
            if not self.claude.check_token_limit(system_prompt, user_prompt):
                logger.error("Context Collection: Input exceeds token limits - aborting")
                return None

            # Run iterative analysis with FULL tool set (including knowledge tools)
            # Using stage-specific ContextCollectionAnalyzer for proper JSON extraction
            available_tools = [
                "readFile", "runTerminalCmd", "getSummaryOfFile",
                "inspectDirectoryHierarchy", "list_files", "getFileContentByLines",
                "getFileContent", "checkFileSize"
            ]

            # Use ContextCollectionAnalyzer for stage-specific JSON extraction
            analyzer = ContextCollectionAnalyzer(self.claude)
            raw_result = analyzer.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_executor=self,
                supported_tools=available_tools,
                context_guidance_template="""
Based on the tool results above, continue gathering context. Remember:
1. Include ALL relevant callers, callees, data types, and constants
2. Preserve original source-file line numbers in ALL code snippets
3. Your output MUST be a valid JSON object — your response MUST start with `{{` and end with `}}` — no markdown, no arrays, no prose

{user_prompt}
""",
                token_usage_callback=self._extract_and_log_token_usage
            )

            end_time = time.time()
            logger.info(f"Context Collection: Completed in {end_time - start_time:.2f}s")

            if not raw_result:
                logger.error("Context Collection: No result from LLM")
                return None

            # Parse context bundle - the analyzer already extracted the correct JSON
            try:
                context_bundle = json.loads(raw_result)
            except json.JSONDecodeError as e:
                logger.warning(f"Context Collection: Invalid JSON from analyzer, retrying: {e}")
                # Retry once with fix request using the same analyzer
                retry_analyzer = ContextCollectionAnalyzer(self.claude)
                retry_result = retry_analyzer.run_iterative_analysis(
                    system_prompt=system_prompt,
                    user_prompt=f"Your previous response was not valid JSON. Please return ONLY the JSON context bundle with no markdown or prose. Error: {e}",
                    tools_executor=self,
                    supported_tools=available_tools,
                    token_usage_callback=self._extract_and_log_token_usage
                )
                if not retry_result:
                    logger.error("Context Collection: Retry failed - no result")
                    return None
                try:
                    context_bundle = json.loads(retry_result)
                except json.JSONDecodeError:
                    logger.error("Context Collection: Retry also returned invalid JSON")
                    return None

            if isinstance(context_bundle, list):
                # LLM wrapped the bundle in an array — unwrap if it contains exactly one dict bundle
                candidate = next((item for item in context_bundle if isinstance(item, dict) and 'primary_function' in item), None)
                if candidate:
                    logger.warning("Context Collection: LLM returned a list; unwrapped single context bundle object")
                    context_bundle = candidate
                else:
                    logger.error(f"Context Collection: LLM returned a list with no valid context bundle inside")
                    return None
            if not isinstance(context_bundle, dict):
                logger.error(f"Context Collection: Expected dict context bundle, got {type(context_bundle)}")
                return None

            # Save context bundle to disk
            if context_bundles_dir and bundle_path:
                try:
                    os.makedirs(context_bundles_dir, exist_ok=True)
                    with open(bundle_path, 'w', encoding='utf-8') as f:
                        json.dump(context_bundle, f, indent=2, ensure_ascii=False)
                    logger.info(f"Context Collection: Bundle saved to {bundle_path}")
                except Exception as e:
                    logger.warning(f"Context Collection: Could not save bundle: {e}")

            self.claude.log_complete_conversation(final_result=json.dumps(context_bundle))
            logger.info(f"Context Collection: Successful")
            return context_bundle

        except Exception as e:
            logger.error(f"Context Collection: Unexpected error: {e}")
            self.claude.log_complete_conversation(final_result=f"Error: {e}")
            return None

    def run_analysis_from_context(self, context_bundle: dict) -> Optional[list]:
        """
        Analysis: Perform analysis using the gathered context bundle.

        Args:
            context_bundle: Context bundle dict from Context Collection

        Returns:
            List of issue dicts on success, None on failure
        """
        logger.info("Analysis: Starting from context bundle...")
        start_time = time.time()

        # Start conversation tracking
        func_name = context_bundle.get("primary_function", {}).get("name", "unknown")
        self.claude.start_conversation("analysis", func_name)

        try:
            # Build analysis prompts
            system_prompt, user_prompt = PromptBuilder.build_analysis_from_context_prompt(
                context_bundle=context_bundle,
                config=self.config.config
            )

            # Use analysis tool set (reduced); using stage-specific CodeAnalysisAnalyzer for proper JSON extraction
            stage_b_supported_tools = ["readFile", "runTerminalCmd"]

            # Use CodeAnalysisAnalyzer for stage-specific JSON extraction (expects array of issue dicts)
            analyzer = CodeAnalysisAnalyzer(self.claude)
            raw_result = analyzer.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_executor=self,
                supported_tools=stage_b_supported_tools,
                context_guidance_template="""
Based on the tool results above, complete your analysis. Remember:
1. Your response MUST be ONLY a valid JSON array starting with [ and ending with ]
2. If no issues found, return exactly: []

{user_prompt}
""",
                token_usage_callback=self._extract_and_log_token_usage
            )

            end_time = time.time()
            logger.info(f"Analysis: Completed in {end_time - start_time:.2f}s")

            if not raw_result:
                logger.error("Analysis: No result from LLM")
                return None

            # Parse issues list - the analyzer already extracted the correct JSON
            from ...utils.json_util import validate_and_format_json
            is_valid, processed = validate_and_format_json(raw_result)

            try:
                issues = json.loads(processed if is_valid else raw_result)
            except json.JSONDecodeError as e:
                logger.error(f"Analysis: Failed to parse result as JSON: {e}")
                self.claude.log_complete_conversation(final_result=f"JSON parse failed: {e}")
                return None

            if not isinstance(issues, list):
                # Might be a dict envelope with 'results' key
                if isinstance(issues, dict) and 'results' in issues:
                    issues = issues['results']
                elif isinstance(issues, dict):
                    # LLM returned a single issue object instead of a one-element array
                    logger.warning("Analysis: LLM returned a single dict; wrapping in list")
                    issues = [issues]
                else:
                    logger.warning(f"Analysis: Expected list, got {type(issues)}; treating as empty")
                    issues = []

            valid_issues = [i for i in issues if isinstance(i, dict)]
            if len(valid_issues) != len(issues):
                logger.warning(f"Analysis: Filtered out {len(issues) - len(valid_issues)} invalid issues")

            self.claude.log_complete_conversation(final_result=json.dumps(valid_issues))
            self.tools.log_tool_usage_summary()
            self._log_final_token_summary()

            logger.info(f"Analysis: Found {len(valid_issues)} issues")
            return valid_issues

        except Exception as e:
            logger.error(f"Analysis: Unexpected error: {e}")
            self.claude.log_complete_conversation(final_result=f"Error: {e}")
            return None

    # Removed _run_iterative_analysis and _execute_claude_tool_use - now using unified methods in llm.py
