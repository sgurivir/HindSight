# Created by Sridhar Gurivireddy

import json
import os
import pkgutil
import re
from typing import Any, Dict, List, Optional, Tuple

from ..lang_util.code_context_pruner import CodeContextPruner
from ..lang_util.call_tree_section_generator import generate_call_tree_section_for_function
from ..llm.llm import Claude, ClaudeConfig, create_llm_provider
from ..constants import (
    CALL_TREE_MAX_ANCESTOR_DEPTH,
    CALL_TREE_MAX_DESCENDANT_DEPTH,
    CALL_TREE_MAX_CHILDREN_PER_NODE,
    CALL_TREE_MAX_TOKENS,
    CALL_TREE_ENABLED
)
from ...utils.file_content_provider import FileContentProvider
from ...utils.file_util import (get_artifacts_temp_file_path,
                                read_json_file, write_file, write_json_file)
from ...utils.json_util import clean_json_response
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider

def _read_package_file(filename: str) -> Optional[str]:
    """
    Read a file from the hindsight.core.prompts package using pkgutil.get_data.
    This works both in development and when installed via pip.
    """
    try:
        data = pkgutil.get_data('hindsight.core.prompts', filename)
        if data is not None:
            return data.decode('utf-8')
        else:
            logger.warning(f"Package file {filename} not found")
            return None
    except Exception as e:
        logger.warning(f"Could not read package file {filename}: {e}")
        return None

# File names for package resources
SYSTEM_PROMPT_FILE = "systemPrompt.md"
ANALYZE_SPECIFIC_FUNCTION_FILE = "analyzeSpecificFunctionSimple.md"
ANALYZE_ENTIRE_FILE_FILE = "analyzeEntireFile.md"
OUTPUT_REQUIREMENTS_FILE = "outputRequirements.md"
OUTPUT_SCHEMA_FILE = "outputSchema.json"
GENERIC_ISSUE_CATEGORY_FILE = "genericIssueCategory.md"
DETAILED_ANALYSIS_PROCESS_FILE = "detailedAnalysisProcess.md"
JSON_OUTPUT_GUIDANCE_FILE = "jsonOutputGuidance.md"
CONTEXT_COLLECTION_PROCESS_FILE = "contextCollectionProcess.md"
ANALYSIS_PROCESS_FILE = "analysisProcess.md"
DIFF_CONTEXT_COLLECTION_PROCESS_FILE = "diffContextCollectionProcess.md"
DIFF_ANALYSIS_PROCESS_FILE = "diffAnalysisProcess.md"

# Get logger using logUtil
logger = get_logger(__name__)

class PromptBuilder:
    """
    Builds system prompt and final user prompt for code analysis,
    similar to claude.py functionality.
    
    ARCHITECTURAL PRINCIPLE: When updating prompts, ensure consistent JSON syntax
    for responses. All JSON-based response formatting should follow the same
    patterns defined in the output schema and requirements.
    """

    @staticmethod
    def _load_file_summary() -> Optional[str]:
        """
        Load file summary - functionality removed.

        Returns:
            None: Summary functionality removed
        """
        # Summary functionality removed - ProjectSummaryGenerator no longer used
        return None

    @staticmethod
    def _load_directory_summary(file_path: str) -> Optional[str]:
        """
        Load directory summary using ProjectSummaryGenerator

        Args:
            file_path: Path to the file being analyzed (to determine directory)

        Returns:
            str: Directory summary content or None if not found
        """
        try:
            # Directory summaries are not currently supported by ProjectSummaryGenerator
            logger.debug(f"Directory summary functionality removed for {file_path}")
            return None

        except Exception as e:
            logger.debug(f"Could not load directory summary for {file_path}: {e}")
            return None

    @staticmethod
    def _extract_file_path_from_json(json_content: str) -> Optional[str]:
        """
        Extract file path from JSON content being analyzed.

        Args:
            json_content: JSON content string

        Returns:
            str: File path or None if not found
        """
        try:
            data = json.loads(json_content)

            # Try different possible keys for file path
            file_path_keys = ['file', 'filePath', 'file_path', 'path', 'fileName', 'file_name']

            for key in file_path_keys:
                if key in data and data[key]:
                    return data[key]

            # Check in context if it exists (standard format)
            if 'context' in data and isinstance(data['context'], dict):
                for key in file_path_keys:
                    if key in data['context'] and data['context'][key]:
                        return data['context'][key]

            # Check in function_context if it exists (new schema)
            if 'function_context' in data and isinstance(data['function_context'], dict):
                for key in file_path_keys:
                    if key in data['function_context'] and data['function_context'][key]:
                        return data['function_context'][key]

            # Check in fileContext if it exists
            if 'fileContext' in data and isinstance(data['fileContext'], dict):
                for key in file_path_keys:
                    if key in data['fileContext'] and data['fileContext'][key]:
                        return data['fileContext'][key]

            return None

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Could not extract file path from JSON: {e}")
            return None

    @staticmethod
    def _extract_invoking_file_paths(json_content: str) -> List[str]:
        """
        Extract all file paths from invoking functions in the JSON content.

        Args:
            json_content: JSON content string

        Returns:
            List[str]: List of unique file paths from invoking functions
        """
        try:
            data = json.loads(json_content)
            file_paths = set()

            def extract_paths_recursive(obj):
                """Recursively extract file paths from nested invoking structures."""
                if isinstance(obj, dict):
                    # Check for functions_invoked in new format - no recursive processing needed since it's just function names
                    if 'functions_invoked' in obj and isinstance(obj['functions_invoked'], list):
                        # functions_invoked is just a list of function names, no nested context to extract
                        pass

                    # Check for function_context with file path
                    if 'function_context' in obj and isinstance(obj['function_context'], dict):
                        file_path = obj['function_context'].get('file')
                        if file_path:
                            file_paths.add(file_path)

                    # Recursively check all values
                    for value in obj.values():
                        if isinstance(value, (dict, list)):
                            extract_paths_recursive(value)

                elif isinstance(obj, list):
                    for item in obj:
                        extract_paths_recursive(item)

            extract_paths_recursive(data)
            return list(file_paths)

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Could not extract invoking file paths from JSON: {e}")
            return []

    @staticmethod
    def _build_summary_context(file_summary: str = None, dir_summary: str = None, invoking_dir_summaries: Dict[str, str] = None) -> str:
        """
        Build summary context section for the prompt.

        Args:
            file_summary: File summary content
            dir_summary: Directory summary content for the primary file
            invoking_dir_summaries: Dictionary mapping directory names to their summaries

        Returns:
            str: Formatted summary context
        """
        if not file_summary and not dir_summary and not invoking_dir_summaries:
            return ""

        context = "\n\n## CONTEXTUAL SUMMARIES\n\n"
        context += "The following summaries provide context about the files and directories being analyzed:\n\n"

        if file_summary:
            context += "### File Summary\n\n"
            context += f"{file_summary}\n\n"

        if dir_summary:
            context += "### Primary Directory Summary\n\n"
            context += f"{dir_summary}\n\n"

        if invoking_dir_summaries:
            context += "### Related Directory Summaries\n\n"
            context += "The following directories contain functions that are invoked by the code being analyzed:\n\n"

            for dir_name, summary in invoking_dir_summaries.items():
                context += f"#### Directory: {dir_name}\n\n"
                context += f"{summary}\n\n"

        context += "Use this contextual information to better understand the code's purpose, dependencies, and relationships across the codebase.\n\n"
        context += "---\n"

        return context

    @staticmethod
    def _load_project_summary() -> Optional[str]:
        """
        Load project summary - functionality removed.

        Returns:
            None: Summary functionality removed
        """
        # Summary functionality removed - ProjectSummaryGenerator no longer used
        return None

    @staticmethod
    def build_system_prompt(config: Dict[str, Any], user_provided_prompts: List[str] = None) -> str:
        """
        Build the system prompt by reading from systemPrompt.md

        Args:
            config: Configuration dictionary containing all settings including project information
            user_provided_prompts: Optional list of user-provided prompts to append to the system prompt

        Returns:
            str: Complete system prompt with project context, analysis scope, and user-provided prompts
        """
        try:
            # Extract analysis settings from config
            project_name = config.get("project_name")
            project_description = config.get("description")

            system_content = _read_package_file(SYSTEM_PROMPT_FILE)
            system_prompt = system_content if system_content is not None else ""

            # Project summary functionality removed - no longer using ProjectSummaryGenerator

            system_prompt += "\n\n## Project Context\n\n"
            system_prompt += f"**Project Name**: {project_name}\n\n"
            system_prompt += f"**Project Description**: {project_description}\n\n"
            system_prompt += "Use this project context to better understand the codebase and provide more relevant analysis."
            system_prompt += "\n\n## Analysis Scope\n"

            # Add generic issue categories
            generic_content = _read_package_file(GENERIC_ISSUE_CATEGORY_FILE)
            if generic_content is not None:
                system_prompt += f"\n\n{generic_content}\n\n"
            else:
                logger.warning(f"{GENERIC_ISSUE_CATEGORY_FILE} not found")

            # Add user-provided prompts if available
            if user_provided_prompts and any(prompt.strip() for prompt in user_provided_prompts):
                system_prompt += "\n\n## Additional User Instructions\n\n"
                for i, prompt in enumerate(user_provided_prompts, 1):
                    if prompt.strip():
                        system_prompt += f"{i}. {prompt.strip()}\n\n"
                system_prompt += "Please incorporate these additional instructions into your analysis while maintaining the standard analysis quality and format."

            return system_prompt

        except FileNotFoundError:
            return "# Code Analysis Task\n\nYou are a senior software engineer conducting code analysis."

    @staticmethod
    def _convert_json_to_comment_format(json_content: str, merged_functions_data: Optional[Dict[str, Any]] = None, merged_data_types_data: Optional[Dict[str, Any]] = None, merged_call_graph_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Convert JSON content to comment-based format.

        For small functions (<300 lines): Send the function body
        For large functions (>300 lines): Send relative path, start line, and end line numbers

        Args:
            json_content: JSON content string
            merged_functions_data: Pre-loaded merged functions data (optional)
            merged_data_types_data: Pre-loaded merged data types data (optional)

        Returns:
            str: Comment-based format with code blocks and line numbers
        """
        try:
            data = json.loads(json_content)
            result = []

            # Handle main function/code
            if isinstance(data, dict):
                # Extract main function information
                function_name = data.get('function', 'Unknown')
                file_path = data.get('file', 'Unknown')

                if file_path == 'Unknown' and 'context' in data:
                    file_path = data['context'].get('file', 'Unknown')


                # Get code content from multiple possible locations in the data structure
                code_content = data.get('code', '')
                if not code_content and 'context' in data:
                    code_content = data['context'].get('function_context', '')
                if not code_content and 'context' in data:
                    code_content = data['context'].get('code', '')
                if not code_content:
                    # Try other possible keys for code content
                    code_content = data.get('function_body', '')
                if not code_content:
                    code_content = data.get('body', '')

                start_line = data.get('startLine')
                end_line = data.get('endLine')
                if not start_line and 'lines' in data:
                    start_line = data['lines'].get('start')
                    end_line = data['lines'].get('end')
                if not start_line and 'context' in data:
                    # Fix: Look for both 'start'/'end' and 'startLine'/'endLine' in context
                    start_line = data['context'].get('start') or data['context'].get('startLine')
                    end_line = data['context'].get('end') or data['context'].get('endLine')


                # Check function size (300 lines threshold)
                function_line_count = len(code_content.split('\n')) if code_content else 0


                # Add main function header
                result.append(f"// Function - {function_name}")
                result.append(f"// file : {file_path}")

                # Always include the main function code if available
                if code_content:
                    # For functions > 300 lines, show only file path and line numbers
                    if function_line_count > 300:
                        if start_line is not None:
                            result.append(f"// start line number: {start_line}")
                        if end_line is not None:
                            result.append(f"// end line number: {end_line}")
                        result.append("")
                        result.append("// LARGE FUNCTION - Use tools to read the actual code")
                        result.append("")
                    else:
                        # For small functions, include the full function body
                        result.append("")
                        # Apply code context pruning and add line numbers with original line numbers
                        pruned_code = CodeContextPruner.prune_comments_simple(code_content)
                        pruned_lines = pruned_code.split('\n')
                        pruned_first_line = pruned_lines[0] if pruned_lines else ""
                        if not re.match(r'^\s*\d+\s*\|', pruned_first_line):
                            # Use the same mechanism as file_util.py to preserve original line numbers
                            if start_line is not None:
                                numbered_lines = [f"{start_line+i:4d} | {line}"
                                                for i, line in enumerate(pruned_lines)]
                                pruned_code = '\n'.join(numbered_lines)
                            else:
                                # Fallback to sequential numbering if start_line not available
                                pruned_code = CodeContextPruner.add_line_numbers(pruned_code, 1)
                        result.append(pruned_code)
                        result.append("")
                else:
                    # If no code content found, add explicit message for debugging
                    result.append("")
                    result.append("// ERROR: Main function code content is missing!")
                    result.append("// This is a critical bug - the function code should be included here")
                    if start_line is not None and end_line is not None:
                        result.append(f"// Expected code from lines {start_line} to {end_line}")
                    result.append("// Use tools to read the actual function code")
                    result.append("")

                # Handle invoking functions
                functions_invoked = data.get('functions_invoked', [])

                if functions_invoked:
                    result.append("// == Additional context when analyzing above function")
                    result.append(f"// == {function_name}() invokes the following function(s)")
                    result.append("")

                    for func_name in functions_invoked:
                        result.append("//====================================================")
                        result.append("")

                        # Try to look up function body for invoked functions
                        func_context = PromptBuilder._lookup_function_body(func_name, merged_functions_data, merged_call_graph_data)

                        if func_context and func_context.get('code'):
                            # Found function body - include it if < 300 lines
                            func_code = func_context['code']
                            func_file_path = func_context.get('file', 'Unknown')
                            func_start_line = func_context.get('start_line')
                            func_end_line = func_context.get('end_line')

                            function_line_count = len(func_code.split('\n')) if func_code else 0

                            result.append(f"// Function - {func_name}")
                            result.append(f"// file : {func_file_path}")

                            if function_line_count > 300:
                                # Large function - show only file path and line numbers
                                if func_start_line is not None:
                                    result.append(f"// start line number: {func_start_line}")
                                if func_end_line is not None:
                                    result.append(f"// end line number: {func_end_line}")
                                result.append("")
                            else:
                                # Small function - include full body
                                result.append("")
                                if func_code:
                                    # Apply code context pruning and add line numbers with original line numbers
                                    pruned_code = CodeContextPruner.prune_comments_simple(func_code)
                                    if not re.match(r'^\s*\d+\s*\|', pruned_code.split('\n')[0]):
                                        # Use the same mechanism as file_util.py to preserve original line numbers
                                        lines = pruned_code.split('\n')
                                        if func_start_line is not None:
                                            numbered_lines = [f"{func_start_line+i:4d} | {line}"
                                                            for i, line in enumerate(lines)]
                                            pruned_code = '\n'.join(numbered_lines)
                                        else:
                                            # Fallback to sequential numbering if start_line not available
                                            pruned_code = CodeContextPruner.add_line_numbers(pruned_code, 1)
                                    result.append(pruned_code)
                                    result.append("")
                        else:
                            # Function body not found - show fallback message
                            result.append(f"// Function - {func_name}")
                            result.append("// file : Function name only (no context available - re-lookup required)")
                            result.append("")

                            # Add line with function context note
                            result.append(f"   1 | Function: {func_name} (context not available - re-lookup required)")
                            result.append("")

                # Handle caller functions (invoked_by)
                invoked_by = data.get('invoked_by', [])

                if invoked_by:
                    result.append("// == Caller context when analyzing above function")
                    result.append(f"// == {function_name}() is called by the following function(s)")
                    result.append("")

                    for caller_name in invoked_by:
                        result.append("//====================================================")
                        result.append("")

                        # invoked_by contains just function names (strings)
                        # Look up the full function details using the function name
                        caller_name = str(caller_name)

                        # Try to look up function body for caller functions
                        caller_context = PromptBuilder._lookup_function_body(caller_name, merged_functions_data, merged_call_graph_data)

                        if caller_context and caller_context.get('code'):
                            # Found caller function body - include it if < 300 lines
                            caller_code = caller_context['code']
                            caller_file_path = caller_context.get('file', 'Unknown')
                            caller_start_line = caller_context.get('start_line')
                            caller_end_line = caller_context.get('end_line')

                            function_line_count = len(caller_code.split('\n')) if caller_code else 0

                            result.append(f"// CALLER Function - {caller_name}")
                            result.append(f"// file : {caller_file_path}")
                            # Note: We no longer have the specific call line since invoked_by is simplified
                            # The function body will show where the call happens

                            if function_line_count > 300:
                                # Large function - show only file path and line numbers
                                if caller_start_line is not None:
                                    result.append(f"// start line number: {caller_start_line}")
                                if caller_end_line is not None:
                                    result.append(f"// end line number: {caller_end_line}")
                                result.append("")
                            else:
                                # Small function - include full body
                                result.append("")
                                if caller_code:
                                    # Apply code context pruning and add line numbers with original line numbers
                                    pruned_code = CodeContextPruner.prune_comments_simple(caller_code)
                                    if not re.match(r'^\s*\d+\s*\|', pruned_code.split('\n')[0]):
                                        # Use the same mechanism as file_util.py to preserve original line numbers
                                        lines = pruned_code.split('\n')
                                        if caller_start_line is not None:
                                            numbered_lines = [f"{caller_start_line+i:4d} | {line}"
                                                            for i, line in enumerate(lines)]
                                            pruned_code = '\n'.join(numbered_lines)
                                        else:
                                            # Fallback to sequential numbering if start_line not available
                                            pruned_code = CodeContextPruner.add_line_numbers(pruned_code, 1)
                                    result.append(pruned_code)
                                    result.append("")
                        else:
                            # Caller function body not found - show fallback message
                            result.append(f"// CALLER Function - {caller_name}")
                            result.append("// file : Function name only (no context available - re-lookup required)")
                            result.append("")

                            # Add line with caller context note
                            result.append(f"   1 | CALLER Function: {caller_name} (context not available - re-lookup required)")
                            result.append("")

                # Handle data types used
                data_types_used = data.get('data_types_used', [])

                if data_types_used:
                    result.append("// == Data types used by above function")
                    result.append("")

                    for data_type_name in data_types_used:
                        result.append("//====================================================")
                        result.append("")

                        # Try to look up data type body for used data types
                        data_type_context = PromptBuilder._lookup_data_type_body(data_type_name, merged_data_types_data)

                        if data_type_context and data_type_context.get('code'):
                            # Found data type body - include it if < 300 lines
                            data_type_code = data_type_context['code']
                            data_type_file_path = data_type_context.get('file', 'Unknown')
                            data_type_start_line = data_type_context.get('start_line')
                            data_type_end_line = data_type_context.get('end_line')

                            data_type_line_count = len(data_type_code.split('\n')) if data_type_code else 0

                            result.append(f"// Data Type - {data_type_name}")
                            result.append(f"// file : {data_type_file_path}")

                            if data_type_line_count > 300:
                                # Large data type - show only file path and line numbers
                                if data_type_start_line is not None:
                                    result.append(f"// start line number: {data_type_start_line}")
                                if data_type_end_line is not None:
                                    result.append(f"// end line number: {data_type_end_line}")
                                result.append("")
                            else:
                                # Small data type - include full body
                                result.append("")
                                if data_type_code:
                                    # Apply code context pruning and add line numbers with original line numbers
                                    pruned_code = CodeContextPruner.prune_comments_simple(data_type_code)
                                    if not re.match(r'^\s*\d+\s*\|', pruned_code.split('\n')[0]):
                                        # Use the same mechanism as file_util.py to preserve original line numbers
                                        lines = pruned_code.split('\n')
                                        if data_type_start_line is not None:
                                            numbered_lines = [f"{data_type_start_line+i:4d} | {line}"
                                                            for i, line in enumerate(lines)]
                                            pruned_code = '\n'.join(numbered_lines)
                                        else:
                                            # Fallback to sequential numbering if start_line not available
                                            pruned_code = CodeContextPruner.add_line_numbers(pruned_code, 1)
                                    result.append(pruned_code)
                                    result.append("")
                        else:
                            # Data type body not found - show fallback message
                            result.append(f"// Data Type - {data_type_name}")
                            result.append("// file : Data type name only (no context available - re-lookup required)")
                            result.append("")

                            # Add line with data type context note
                            result.append(f"   1 | Data Type: {data_type_name} (context not available - re-lookup required)")
                            result.append("")

                # Handle constants used
                constants_used = data.get('constants_used', {})

                if constants_used:
                    result.append(f"// === Constants used by function {function_name}() ===")
                    for constant_name, constant_value in constants_used.items():
                        result.append(f"{constant_name} = {constant_value}")
                    result.append("")

                # Add call tree context section if enabled and call graph data is available
                logger.info(f"Call tree generation check: "
                            f"CALL_TREE_ENABLED={CALL_TREE_ENABLED}, "
                            f"merged_call_graph_data={'present' if merged_call_graph_data else 'None'}, "
                            f"function_name={function_name}")
                
                if CALL_TREE_ENABLED and merged_call_graph_data and function_name != 'Unknown':
                    try:
                        call_tree_section = generate_call_tree_section_for_function(
                            call_graph_data=merged_call_graph_data,
                            function_name=function_name,
                            max_ancestor_depth=CALL_TREE_MAX_ANCESTOR_DEPTH,
                            max_descendant_depth=CALL_TREE_MAX_DESCENDANT_DEPTH,
                            max_children_per_node=CALL_TREE_MAX_CHILDREN_PER_NODE,
                            max_tokens=CALL_TREE_MAX_TOKENS
                        )
                        if call_tree_section:
                            result.append("")
                            result.append(call_tree_section)
                            result.append("")
                            logger.info(f"Added call tree context section for function: {function_name}")
                        else:
                            logger.warning(f"No call tree context generated for function: {function_name}")
                    except Exception as e:
                        logger.error(f"Error generating call tree section for {function_name}: {e}")
                else:
                    logger.debug(f"Skipping call tree generation: "
                                f"CALL_TREE_ENABLED={CALL_TREE_ENABLED}, "
                                f"has_data={merged_call_graph_data is not None}, "
                                f"func_name={function_name}")

            return '\n'.join(result)

        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Error converting JSON to comment format: {e}")
            # Fallback to original content
            return json_content

    @staticmethod
    def _apply_code_context_pruning(content: str) -> str:
        """
        Apply CodeContextPruner to clean up code content by removing comments.

        Args:
            content: Content to prune (can be JSON string or plain text)

        Returns:
            str: Pruned content with comments removed
        """
        try:
            # Try to parse as JSON first to see if it contains code content
            try:
                data = json.loads(content)
                # If it's JSON, we need to prune code content within it
                # Look for common code content fields and prune them
                if isinstance(data, dict):
                    # Common fields that might contain code
                    code_fields = ['code', 'content', 'fileContent', 'sourceCode', 'body']
                    for field in code_fields:
                        if field in data and isinstance(data[field], str):
                            # Apply pruning to code content
                            data[field] = CodeContextPruner.prune_comments_simple(data[field])

                    # Also check nested structures
                    def prune_nested_code(obj):
                        if isinstance(obj, dict):
                            for key, value in obj.items():
                                if isinstance(value, str) and any(keyword in key.lower() for keyword in ['code', 'content', 'source', 'body']):
                                    obj[key] = CodeContextPruner.prune_comments_simple(value)
                                elif isinstance(value, (dict, list)):
                                    prune_nested_code(value)
                        elif isinstance(obj, list):
                            for item in obj:
                                if isinstance(item, (dict, list)):
                                    prune_nested_code(item)

                    prune_nested_code(data)
                    return json.dumps(data, ensure_ascii=False, indent=2)
                else:
                    # If it's not a dict, return as-is
                    return content
            except json.JSONDecodeError:
                # If it's not JSON, treat as plain text and prune if it looks like code
                if any(indicator in content for indicator in ['#include', 'import ', 'function ', 'class ', '{', '}', '//', '/*']):
                    return CodeContextPruner.prune_comments_simple(content)
                else:
                    return content
        except Exception as e:
            logger.debug(f"Error applying code context pruning: {e}")
            return content

    @staticmethod
    def build_user_prompt(
        json_content: str,
        analysis_type: str = "specific_function",
        find_generic_issues: bool = True,
        repo_path: str = None,
        merged_functions_data: Optional[Dict[str, Any]] = None,
        merged_data_types_data: Optional[Dict[str, Any]] = None,
        merged_call_graph_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build the user prompt for analysis

        Args:
            json_content: JSON content to analyze
            analysis_type: Type of analysis ("specific_function" or "entire_file")
            find_generic_issues: Whether to find generic issues
            repo_path: Repository path to determine temp directory location
            merged_functions_data: Pre-loaded merged functions data (optional)
            merged_data_types_data: Pre-loaded merged data types data (optional)

        Returns:
            str: Complete user prompt
        """
        try:
            # Apply CodeContextPruner to clean up code content by default
            pruned_json_content = PromptBuilder._apply_code_context_pruning(json_content)

            # Large file handling removed - no longer using summaries
            # Just use the pruned content as-is

            # Convert JSON to comment-based format
            comment_based_content = PromptBuilder._convert_json_to_comment_format(pruned_json_content, merged_functions_data, merged_data_types_data, merged_call_graph_data)

            # Extract file path from JSON to load summaries
            file_path = PromptBuilder._extract_file_path_from_json(pruned_json_content)
            file_summary = None
            dir_summary = None
            invoking_dir_summaries = {}

            if file_path:
                logger.info(f"Extracted file path from JSON: {file_path}")
            else:
                logger.warning(f"Could not extract file path from JSON - summaries will not be loaded. JSON content: {pruned_json_content}")

            # Summary functionality removed - no longer loading file summaries
            file_summary = None
            dir_summary = None
            invoking_dir_summaries = {}

            # Determine which template to use
            if analysis_type == "entire_file":
                template_file = ANALYZE_ENTIRE_FILE_FILE
            else:
                template_file = ANALYZE_SPECIFIC_FUNCTION_FILE

            # Read the template
            template_content = _read_package_file(template_file)
            if template_content is None:
                raise FileNotFoundError(f"Template file not found: {template_file}")

            # Build summary context with invoking directory summaries
            summary_context = PromptBuilder._build_summary_context(file_summary, dir_summary, invoking_dir_summaries)

            # Replace placeholder with comment-based content instead of JSON
            user_prompt = template_content.replace("{json_content}", comment_based_content)

            # Insert summary context before the code content if available
            if summary_context:
                # Find where to insert the summary context (before the code section)
                code_marker = "// Code to ANALYZE"
                if code_marker in user_prompt:
                    user_prompt = user_prompt.replace(code_marker, summary_context + code_marker)
                    logger.info(f"Inserted summary context into prompt before '{code_marker}' marker")
                else:
                    # Fallback: add at the beginning
                    user_prompt = summary_context + user_prompt
                    logger.info("Inserted summary context at the beginning of prompt (fallback)")
            else:
                logger.info("No summary context to insert - proceeding without contextual summaries")

            # Enhanced prompts functionality removed - no scope limitation needed

            return user_prompt

        except FileNotFoundError as e:
            logger.error(f"Template file not found: {e}")
            # Use comment-based format even in fallback
            pruned_content = PromptBuilder._apply_code_context_pruning(json_content)
            comment_based_fallback = PromptBuilder._convert_json_to_comment_format(pruned_content, merged_functions_data, merged_data_types_data, merged_call_graph_data)
            return f"// Code to ANALYZE\n\n{comment_based_fallback}"

    @staticmethod
    def build_output_requirements() -> str:
        """
        Build the output requirements section with schema replacement

        Returns:
            str: Output requirements with schema
        """
        try:
            # Read output requirements template
            output_template = _read_package_file(OUTPUT_REQUIREMENTS_FILE)
            if output_template is None:
                raise FileNotFoundError(f"Output requirements file not found: {OUTPUT_REQUIREMENTS_FILE}")

            # Read output schema
            schema_content = _read_package_file(OUTPUT_SCHEMA_FILE)
            if schema_content is None:
                raise FileNotFoundError(f"Output schema file not found: {OUTPUT_SCHEMA_FILE}")

            # Replace placeholder with actual schema
            output_requirements = output_template.replace("{output_schema}", schema_content)

            return output_requirements

        except FileNotFoundError as e:
            logger.error(f"Output requirements file not found: {e}")
            return "## Output Requirements\n\nReturn valid JSON array of issues."

    @staticmethod
    def build_json_output_guidance() -> str:
        """
        Build the JSON output guidance message for when LLM fails to provide valid JSON.
        This is used as a follow-up prompt to enforce strict JSON output.

        Returns:
            str: JSON output guidance prompt
        """
        try:
            guidance_content = _read_package_file(JSON_OUTPUT_GUIDANCE_FILE)
            if guidance_content is None:
                raise FileNotFoundError(f"JSON output guidance file not found: {JSON_OUTPUT_GUIDANCE_FILE}")
            return guidance_content
        except FileNotFoundError as e:
            logger.error(f"JSON output guidance file not found: {e}")
            return "Please provide your analysis results as a valid JSON array starting with [ and ending with ]."

    @staticmethod
    def build_complete_prompt(
        json_content: str,
        analysis_type: str = "specific_function",
        config: Dict[str, Any] = None,
        merged_functions_data: Optional[Dict[str, Any]] = None,
        merged_data_types_data: Optional[Dict[str, Any]] = None,
        merged_call_graph_data: Optional[Dict[str, Any]] = None,
        user_provided_prompts: List[str] = None
    ) -> tuple[str, str]:
        """
        Build complete system and user prompts for analysis

        Args:
            json_content: JSON content to analyze
            analysis_type: Type of analysis ("specific_function" or "entire_file")
            config: Configuration dictionary containing all settings including project information
            merged_functions_data: Pre-loaded merged functions data (optional)
            merged_data_types_data: Pre-loaded merged data types data (optional)
            merged_call_graph_data: Pre-loaded merged call graph data (optional)
            user_provided_prompts: Optional list of user-provided prompts to include in system prompt

        Returns:
            tuple[str, str]: (system_prompt, user_prompt)
        """
        # Build system prompt with user-provided prompts
        system_prompt = PromptBuilder.build_system_prompt(config, user_provided_prompts)

        # Add output requirements to system prompt
        output_requirements = PromptBuilder.build_output_requirements()

        # Build user prompt
        find_generic_issues = True  # Always find generic issues by default
        repo_path = config.get('path_to_repo', os.getcwd()) if config else os.getcwd()

        # Enhanced prompts functionality removed - no scope limitation needed

        user_prompt = PromptBuilder.build_user_prompt(json_content, analysis_type, find_generic_issues, repo_path, merged_functions_data, merged_data_types_data, merged_call_graph_data)
        complete_user_prompt = user_prompt + "\n\n" + output_requirements

        return system_prompt, complete_user_prompt


    @staticmethod
    def determine_analysis_type(json_content: str) -> str:
        """
        Determine analysis type based on JSON content structure

        Args:
            json_content: JSON content to analyze

        Returns:
            str: "entire_file" or "specific_function"
        """
        try:
            data = json.loads(json_content)
            # If it has fileContext but no function, it's entire file analysis
            if "fileContext" in data and "function" not in data:
                return "entire_file"
            else:
                return "specific_function"
        except json.JSONDecodeError:
            return "specific_function"

    @staticmethod
    def build_context_collection_prompt(
        json_content: str,
        config: Dict[str, Any] = None,
        merged_functions_data: Optional[Dict[str, Any]] = None,
        merged_data_types_data: Optional[Dict[str, Any]] = None,
        merged_call_graph_data: Optional[Dict[str, Any]] = None,
        user_provided_prompts: List[str] = None
    ) -> Tuple[str, str]:
        """
        Build system and user prompts for Stage 4a context collection.

        Args:
            json_content: JSON content of the function to collect context for
            config: Configuration dictionary
            merged_functions_data: Pre-loaded merged functions data (optional)
            merged_data_types_data: Pre-loaded merged data types data (optional)
            merged_call_graph_data: Pre-loaded merged call graph data (optional)
            user_provided_prompts: Optional list of user-provided prompts

        Returns:
            Tuple[str, str]: (system_prompt, user_prompt)
        """
        try:
            # Load Stage 4a process prompt
            process_content = _read_package_file(CONTEXT_COLLECTION_PROCESS_FILE)
            if not process_content:
                logger.warning(f"{CONTEXT_COLLECTION_PROCESS_FILE} not found, using fallback")
                process_content = "You are a context-gathering agent. Collect all code context needed to analyze the primary function. Output a JSON context bundle."

            # Build system prompt
            system_prompt = process_content

            # Add project context if available
            if config:
                project_name = config.get("project_name", "")
                project_description = config.get("description", "")
                if project_name or project_description:
                    system_prompt += f"\n\n## Project Context\n\n"
                    if project_name:
                        system_prompt += f"**Project Name**: {project_name}\n\n"
                    if project_description:
                        system_prompt += f"**Project Description**: {project_description}\n\n"

            # Add user-provided prompts if available
            if user_provided_prompts and any(p.strip() for p in user_provided_prompts):
                system_prompt += "\n\n## Additional Collection Instructions\n\n"
                for i, prompt in enumerate(user_provided_prompts, 1):
                    if prompt.strip():
                        system_prompt += f"{i}. {prompt.strip()}\n\n"

            # Build user prompt: the function data in comment format + instruction
            analysis_type = PromptBuilder.determine_analysis_type(json_content)
            code_comment_format = PromptBuilder._convert_json_to_comment_format(
                json_content,
                merged_functions_data=merged_functions_data,
                merged_data_types_data=merged_data_types_data,
                merged_call_graph_data=merged_call_graph_data
            )

            user_prompt = f"## Function to Analyze\n\n{code_comment_format}\n\n"
            user_prompt += "Collect all context needed to analyze this function and return a JSON context bundle as described in the system prompt. Your response MUST start with `{` and end with `}` — return a JSON object, not an array."

            logger.info(f"Built Stage 4a prompts - System: {len(system_prompt)} chars, User: {len(user_prompt)} chars")
            return system_prompt, user_prompt

        except Exception as e:
            logger.error(f"Error building context collection prompt: {e}")
            return (
                "You are a context-gathering agent. Collect all code context needed to analyze the primary function.",
                f"Collect context for:\n{json_content}"
            )

    @staticmethod
    def build_analysis_from_context_prompt(
        context_bundle: Dict[str, Any],
        config: Dict[str, Any] = None
    ) -> Tuple[str, str]:
        """
        Build system and user prompts for Stage 4b analysis from a context bundle.

        Args:
            context_bundle: Context bundle dict from Stage 4a
            config: Configuration dictionary

        Returns:
            Tuple[str, str]: (system_prompt, user_prompt)
        """
        try:
            # Load Stage 4b analysis process prompt
            process_content = _read_package_file(ANALYSIS_PROCESS_FILE)
            if not process_content:
                logger.warning(f"{ANALYSIS_PROCESS_FILE} not found, using fallback")
                process_content = "You are a senior software engineer. Analyze the provided context bundle and identify bugs and performance issues."

            # Build system prompt
            system_prompt = process_content

            # Add project context if available
            if config:
                project_name = config.get("project_name", "")
                project_description = config.get("description", "")
                if project_name or project_description:
                    system_prompt += f"\n\n## Project Context\n\n"
                    if project_name:
                        system_prompt += f"**Project Name**: {project_name}\n\n"
                    if project_description:
                        system_prompt += f"**Project Description**: {project_description}\n\n"

            # Load output schema requirements
            output_requirements = PromptBuilder.build_output_requirements()

            # Build user prompt: context bundle + output schema
            primary_func_name = context_bundle.get("primary_function", {}).get("name", "unknown")
            primary_func_file = context_bundle.get("primary_function", {}).get("file_path", "unknown")

            user_prompt = f"## Context Bundle for Analysis\n\n"
            user_prompt += f"**Function**: `{primary_func_name}` in `{primary_func_file}`\n\n"
            user_prompt += "The following context bundle contains all code needed for your analysis. Line numbers in source fields are original source-file line numbers — use them directly in your output.\n\n"
            user_prompt += "```json\n"
            user_prompt += json.dumps(context_bundle, indent=2, ensure_ascii=False)
            user_prompt += "\n```\n\n"
            user_prompt += output_requirements

            logger.info(f"Built Stage 4b prompts - System: {len(system_prompt)} chars, User: {len(user_prompt)} chars")
            return system_prompt, user_prompt

        except Exception as e:
            logger.error(f"Error building analysis from context prompt: {e}")
            return (
                "You are a senior software engineer. Analyze the provided context bundle and identify bugs and performance issues. Return a JSON array of issues.",
                f"Analyze this context bundle:\n{json.dumps(context_bundle, indent=2)}"
            )

    # Large file handling and nested content handling removed - ProjectSummaryGenerator no longer used

    @staticmethod
    def _lookup_function_body(function_name: str, merged_functions_data: Optional[Dict[str, Any]] = None, merged_call_graph_data: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        """
        Look up function body from merged AST files and FileContentProvider.
        Falls back to call graph data if merged_functions_data lookup fails.

        Args:
            function_name: Name of the function to look up
            merged_functions_data: Pre-loaded merged functions data (optional, will load from file if not provided)
            merged_call_graph_data: Pre-loaded merged call graph data (optional, used as fallback)

        Returns:
            dict: Function context with 'code', 'file', 'start_line', 'end_line' or None if not found
        """
        try:
            # Try to get FileContentProvider instance
            try:
                file_provider = FileContentProvider.get()
            except RuntimeError:
                logger.debug("FileContentProvider not available for function lookup")
                return None

            # Use provided data or load from file
            if merged_functions_data is None:
                # Load merged functions data from file
                output_provider = get_output_directory_provider()
                artifacts_dir = output_provider.get_repo_artifacts_dir()
                merged_functions_file = f"{artifacts_dir}/code_insights/merged_functions.json"

                if not os.path.exists(merged_functions_file):
                    logger.debug(f"Merged functions file not found: {merged_functions_file}")
                    # Don't return None yet - try call graph fallback below
                else:
                    # Load function definitions
                    merged_functions_data = read_json_file(merged_functions_file)

            # Build function lookup map
            function_lookup = {}

            # Handle new format: direct function_to_location_and_checksum structure
            if merged_functions_data and isinstance(merged_functions_data, dict):
                for func_name, func_info in merged_functions_data.items():
                    if isinstance(func_info, dict) and 'code' in func_info:
                        code_locations = func_info['code']
                        if isinstance(code_locations, list) and code_locations:
                            # Take the first location if multiple exist
                            location = code_locations[0]
                            if isinstance(location, dict):
                                # Convert to the expected context format
                                context_info = {
                                    'file': location.get('file_name', ''),
                                    'start': location.get('start', 0),
                                    'end': location.get('end', 0)
                                }
                                function_lookup[func_name] = context_info
                                # Also store normalized version (remove parentheses and parameters)
                                normalized_name = PromptBuilder._normalize_function_name(func_name)
                                function_lookup[normalized_name] = context_info

            # Find function context from merged_functions_data
            func_context = PromptBuilder._find_function_context_in_lookup(function_name, function_lookup)

            # If not found in merged_functions_data, try call graph data as fallback
            if not func_context and merged_call_graph_data:
                logger.debug(f"Function context not found in merged_functions_data for: {function_name}, trying call graph fallback")
                func_context = PromptBuilder._lookup_function_from_call_graph(function_name, merged_call_graph_data)

            if not func_context:
                logger.debug(f"Function context not found for: {function_name}")
                return None

            # Extract file information
            file_path = func_context.get('file')
            start_line = func_context.get('start')
            end_line = func_context.get('end')

            if not file_path or start_line is None or end_line is None:
                logger.debug(f"Incomplete function context for {function_name}: file={file_path}, start={start_line}, end={end_line}")
                return None

            # Get file content using FileContentProvider
            filename = os.path.basename(file_path)
            resolved_path = file_provider.guess_path(filename)

            if not resolved_path:
                resolved_path = file_provider.resolve_file_path(filename)

            if not resolved_path:
                logger.debug(f"Could not resolve file path for: {filename}")
                return None

            # Read file content
            full_content = file_provider.read_text(resolved_path)
            if not full_content:
                logger.debug(f"Could not read content from: {resolved_path}")
                return None

            # Extract function body using line numbers
            lines = full_content.split('\n')

            # Validate line numbers
            if start_line < 1 or end_line < 1 or start_line > len(lines) or end_line > len(lines):
                logger.debug(f"Invalid line numbers for {function_name}: start={start_line}, end={end_line}, total_lines={len(lines)}")
                return None

            # Extract the function body (convert to 0-based indexing)
            function_lines = lines[start_line-1:end_line]
            function_code = '\n'.join(function_lines)

            return {
                'code': function_code,
                'file': file_path,
                'start_line': start_line,
                'end_line': end_line
            }

        except Exception as e:
            logger.debug(f"Error looking up function body for {function_name}: {e}")
            return None

    @staticmethod
    def _normalize_function_name(func_name: str) -> str:
        """
        Normalize function name to handle mismatches like 'CLGnssProvider::stopLocation()' vs 'CLGnssProvider::stopLocation'
        """
        if not func_name:
            return func_name

        # Remove parentheses and everything after them (parameters)
        if '(' in func_name:
            func_name = func_name.split('(')[0]

        # Strip whitespace
        return func_name.strip()

    @staticmethod
    def _find_function_context_in_lookup(function_name: str, function_lookup: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Find function context in the lookup map, handling name variations.

        Args:
            function_name: Function name to search for
            function_lookup: Dictionary mapping function names to context info

        Returns:
            dict: Function context with file, start, end or None
        """
        if not function_name or not function_lookup:
            return None

        # Try exact match first
        if function_name in function_lookup:
            return function_lookup[function_name]

        # Try normalized version
        normalized_name = PromptBuilder._normalize_function_name(function_name)
        if normalized_name in function_lookup:
            return function_lookup[normalized_name]

        # Try partial matching - look for functions that contain the normalized name
        for func_name, context in function_lookup.items():
            if normalized_name in func_name or func_name in normalized_name:
                return context

        return None

    @staticmethod
    def _lookup_function_from_call_graph(function_name: str, merged_call_graph_data: Any) -> Optional[Dict[str, Any]]:
        """
        Look up function context from call graph data as a fallback.
        
        The call graph data structure is:
        [
            {
                "file": "path/to/file.m",
                "functions": [
                    {
                        "function": "ClassName::methodName",
                        "context": {"file": "...", "start": N, "end": M},
                        "functions_invoked": [
                            {
                                "function": "OtherClass::otherMethod",
                                "context": {"file": "...", "start": N, "end": M}
                            },
                            ...
                        ]
                    },
                    ...
                ]
            },
            ...
        ]
        
        Args:
            function_name: Name of the function to look up
            merged_call_graph_data: Call graph data structure (list of file entries)
            
        Returns:
            dict: Function context with 'file', 'start', 'end' or None if not found
        """
        if not merged_call_graph_data or not function_name:
            return None
            
        try:
            normalized_search_name = PromptBuilder._normalize_function_name(function_name)
            
            # Handle both list format and dict format
            file_entries = merged_call_graph_data
            if isinstance(merged_call_graph_data, dict):
                # If it's a dict, it might have a wrapper key
                if 'call_graph' in merged_call_graph_data:
                    file_entries = merged_call_graph_data['call_graph']
                elif 'files' in merged_call_graph_data:
                    file_entries = merged_call_graph_data['files']
                else:
                    # Assume the dict values are the file entries
                    file_entries = list(merged_call_graph_data.values())
            
            if not isinstance(file_entries, list):
                logger.debug(f"Call graph data is not a list: {type(file_entries)}")
                return None
            
            # Search through all file entries
            for file_entry in file_entries:
                if not isinstance(file_entry, dict):
                    continue
                    
                functions = file_entry.get('functions', [])
                if not isinstance(functions, list):
                    continue
                
                for func_entry in functions:
                    if not isinstance(func_entry, dict):
                        continue
                    
                    # Check if this is the function we're looking for (as a top-level function)
                    func_name = func_entry.get('function', '')
                    normalized_func_name = PromptBuilder._normalize_function_name(func_name)
                    
                    if normalized_search_name == normalized_func_name or \
                       normalized_search_name in normalized_func_name or \
                       normalized_func_name in normalized_search_name:
                        context = func_entry.get('context')
                        if context and isinstance(context, dict):
                            return {
                                'file': context.get('file', ''),
                                'start': context.get('start', 0),
                                'end': context.get('end', 0)
                            }
                    
                    # Also search in functions_invoked - these have embedded context
                    functions_invoked = func_entry.get('functions_invoked', [])
                    if isinstance(functions_invoked, list):
                        for invoked_func in functions_invoked:
                            if isinstance(invoked_func, dict):
                                invoked_name = invoked_func.get('function', '')
                                normalized_invoked_name = PromptBuilder._normalize_function_name(invoked_name)
                                
                                if normalized_search_name == normalized_invoked_name or \
                                   normalized_search_name in normalized_invoked_name or \
                                   normalized_invoked_name in normalized_search_name:
                                    context = invoked_func.get('context')
                                    if context and isinstance(context, dict):
                                        return {
                                            'file': context.get('file', ''),
                                            'start': context.get('start', 0),
                                            'end': context.get('end', 0)
                                        }
            
            logger.debug(f"Function {function_name} not found in call graph data")
            return None
            
        except Exception as e:
            logger.debug(f"Error looking up function in call graph: {e}")
            return None

    @staticmethod
    def _resolve_file_path_with_provider(function_name: str) -> str:
        """
        Resolve file path using FileContentProvider as fallback when path is Unknown.

        Args:
            function_name: Name of the function

        Returns:
            str: Resolved file path or 'Unknown' if resolution fails
        """
        try:
            # Try to get the FileContentProvider instance
            try:
                provider = FileContentProvider.get()
            except RuntimeError:
                # FileContentProvider not initialized
                logger.debug("FileContentProvider not available for file path resolution")
                return 'Unknown'

            # Extract class name from function name if it's a method (e.g., "DataCollector::setupSensors" -> "DataCollector")
            class_name = None
            if '::' in function_name:
                class_name = function_name.split('::')[0]
            elif '.' in function_name and not function_name.endswith('.swift'):
                # Handle cases like "SRSensor.isEnabled" -> "SRSensor"
                class_name = function_name.split('.')[0]

            # Try to resolve file path using different strategies
            resolved_path = None

            # Strategy 1: Try with class name if available
            if class_name:
                resolved_path = provider.resolve_file_path(class_name)
                if resolved_path:
                    logger.debug(f"Resolved file path for {function_name} using class name {class_name}: {resolved_path}")
                    return resolved_path

            # Strategy 2: Try with full function name
            resolved_path = provider.resolve_file_path(function_name)
            if resolved_path:
                logger.debug(f"Resolved file path for {function_name} using function name: {resolved_path}")
                return resolved_path

            # Strategy 3: Try to guess based on function name patterns
            # For Swift functions, try adding .swift extension
            if not function_name.endswith('.swift'):
                swift_filename = f"{class_name or function_name}.swift"
                resolved_path = provider.resolve_file_path(swift_filename)
                if resolved_path:
                    logger.debug(f"Resolved file path for {function_name} using Swift filename {swift_filename}: {resolved_path}")
                    return resolved_path

            # If all strategies fail, return Unknown
            logger.debug(f"Could not resolve file path for function: {function_name}")
            return 'Unknown'

        except Exception as e:
            logger.debug(f"Error resolving file path for {function_name}: {e}")
            return 'Unknown'

    @staticmethod
    def _lookup_data_type_body(data_type_name: str, merged_data_types_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Look up data type body from merged AST files and FileContentProvider.

        Args:
            data_type_name: Name of the data type to look up
            merged_data_types_data: Pre-loaded merged data types data (optional, will load from file if not provided)

        Returns:
            dict: Data type context with 'code', 'file', 'start_line', 'end_line' or None if not found
        """
        try:
            # Try to get FileContentProvider instance
            try:
                file_provider = FileContentProvider.get()
            except RuntimeError:
                logger.debug("FileContentProvider not available for data type lookup")
                return None

            # Use provided data or load from file
            if merged_data_types_data is None:
                # Load merged data types data from file
                output_provider = get_output_directory_provider()
                artifacts_dir = output_provider.get_repo_artifacts_dir()

                # Try multiple possible file names for data types
                possible_data_type_files = [
                    f"{artifacts_dir}/code_insights/merged_defined_classes.json"
                ]

                merged_data_types_file = None
                for file_path in possible_data_type_files:
                    if os.path.exists(file_path):
                        merged_data_types_file = file_path
                        break

                if not merged_data_types_file:
                    logger.debug(f"No data types file found. Tried: {possible_data_type_files}")
                    return None

                # Load data type definitions
                merged_data_types_data = read_json_file(merged_data_types_file)

                if not merged_data_types_data:
                    logger.debug("Failed to load merged data types data")
                    return None

            # Build data type lookup map
            data_type_lookup = {}

            # Handle new format: "data_type_to_location_and_checksum" wrapper
            if isinstance(merged_data_types_data, dict) and 'data_type_to_location_and_checksum' in merged_data_types_data:
                # Navigate the structure: data_type_to_location_and_checksum -> data_type_name -> code
                data_type_entries = merged_data_types_data['data_type_to_location_and_checksum']
                if isinstance(data_type_entries, dict):
                    for dt_name, dt_info in data_type_entries.items():
                        if isinstance(dt_info, dict) and 'code' in dt_info:
                            code_locations = dt_info['code']
                            if isinstance(code_locations, list) and code_locations:
                                # Take the first location if multiple exist
                                location = code_locations[0]
                                if isinstance(location, dict):
                                    # Convert to the expected context format
                                    context_info = {
                                        'file': location.get('file_name', ''),
                                        'start': location.get('start', 0),
                                        'end': location.get('end', 0)
                                    }
                                    data_type_lookup[dt_name] = context_info
                                    # Also store normalized version
                                    normalized_name = PromptBuilder._normalize_data_type_name(dt_name)
                                    data_type_lookup[normalized_name] = context_info

            else:
                logger.debug("Merged data types data has unexpected format - no data type definitions loaded")

            # Find data type context
            dt_context = PromptBuilder._find_data_type_context_in_lookup(data_type_name, data_type_lookup)

            if not dt_context:
                logger.debug(f"Data type context not found for: {data_type_name}")
                return None

            # Extract file information
            file_path = dt_context.get('file')
            start_line = dt_context.get('start')
            end_line = dt_context.get('end')

            if not file_path or start_line is None or end_line is None:
                logger.debug(f"Incomplete data type context for {data_type_name}: file={file_path}, start={start_line}, end={end_line}")
                return None

            # Get file content using FileContentProvider
            filename = os.path.basename(file_path)
            resolved_path = file_provider.guess_path(filename)

            if not resolved_path:
                resolved_path = file_provider.resolve_file_path(filename)

            if not resolved_path:
                logger.debug(f"Could not resolve file path for: {filename}")
                return None

            # Read file content
            full_content = file_provider.read_text(resolved_path)
            if not full_content:
                logger.debug(f"Could not read content from: {resolved_path}")
                return None

            # Extract data type body using line numbers
            lines = full_content.split('\n')

            # Validate line numbers
            if start_line < 1 or end_line < 1 or start_line > len(lines) or end_line > len(lines):
                logger.debug(f"Invalid line numbers for {data_type_name}: start={start_line}, end={end_line}, total_lines={len(lines)}")
                return None

            # Extract the data type body (convert to 0-based indexing)
            data_type_lines = lines[start_line-1:end_line]
            data_type_code = '\n'.join(data_type_lines)

            return {
                'code': data_type_code,
                'file': file_path,
                'start_line': start_line,
                'end_line': end_line
            }

        except Exception as e:
            logger.debug(f"Error looking up data type body for {data_type_name}: {e}")
            return None

    @staticmethod
    def _normalize_data_type_name(data_type_name: str) -> str:
        """
        Normalize data type name to handle mismatches.
        """
        if not data_type_name:
            return data_type_name

        # Remove template parameters and everything after them
        if '<' in data_type_name:
            data_type_name = data_type_name.split('<')[0]

        # Strip whitespace
        return data_type_name.strip()

    @staticmethod
    def _find_data_type_context_in_lookup(data_type_name: str, data_type_lookup: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Find data type context in the lookup map, handling name variations.

        Args:
            data_type_name: Data type name to search for
            data_type_lookup: Dictionary mapping data type names to context info

        Returns:
            dict: Data type context with file, start, end or None
        """
        if not data_type_name or not data_type_lookup:
            return None

        # Try exact match first
        if data_type_name in data_type_lookup:
            return data_type_lookup[data_type_name]

        # Try normalized version
        normalized_name = PromptBuilder._normalize_data_type_name(data_type_name)
        if normalized_name in data_type_lookup:
            return data_type_lookup[normalized_name]

        # Try partial matching - look for data types that contain the normalized name
        for dt_name, context in data_type_lookup.items():
            if normalized_name in dt_name or dt_name in normalized_name:
                return context

        return None
