#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Trace Analysis Prompt Builder
Specialized prompt builder for trace analysis that uses trace-specific system prompts
"""

import pkgutil
from typing import Tuple

from ..lang_util.code_context_pruner import CodeContextPruner
from ...utils.log_util import get_logger

logger = get_logger(__name__)

def _read_package_file(filename: str) -> str:
    """
    Read a file from the hindsight.core.prompts package using pkgutil.get_data.
    """
    try:
        data = pkgutil.get_data('hindsight.core.prompts', filename)
        if data is not None:
            return data.decode('utf-8')
        else:
            logger.warning(f"Package file {filename} not found")
            return ""
    except Exception as e:
        logger.warning(f"Could not read package file {filename}: {e}")
        return ""

class TracePromptBuilder:
    """
    Builds system prompt and user prompt specifically for trace analysis.
    """

    @staticmethod
    def build_system_prompt() -> str:
        """
        Build the system prompt for trace analysis using systemPromptTrace.md.

        Returns:
            str: System prompt content
        """
        try:
            system_prompt = _read_package_file("systemPromptTrace.md")
            if system_prompt:
                logger.info(f"Loaded trace system prompt: {len(system_prompt)} characters")
                return system_prompt
            else:
                logger.error("Failed to load systemPromptTrace.md")
                raise FileNotFoundError("systemPromptTrace.md not found in package")
        except Exception as e:
            logger.error(f"Error loading trace system prompt: {e}")
            return "You are a senior software engineer analyzing callstack traces for performance optimization opportunities."

    @staticmethod
    def build_user_prompt(prompt_content: str) -> str:
        """
        Build the user prompt for trace analysis using analyzeTrace.md template.

        Args:
            prompt_content: The trace analysis prompt content

        Returns:
            str: Complete user prompt
        """
        try:
            # Apply CodeContextPruner to clean up code content by default
            pruned_content = CodeContextPruner.prune_comments_simple(prompt_content)
            logger.info("Applied CodeContextPruner to trace prompt content")

            # Read the trace analysis template
            template_content = _read_package_file("analyzeTrace.md")
            if template_content:
                # Replace placeholder with actual prompt content (using pruned content)
                user_prompt = template_content.replace("{json_content}", pruned_content)
                logger.info(f"Built trace user prompt: {len(user_prompt)} characters")
                return user_prompt
            else:
                logger.error("Failed to load analyzeTrace.md")
                raise FileNotFoundError("analyzeTrace.md not found in package")
        except Exception as e:
            logger.error(f"Error building trace user prompt: {e}")
            # Use pruned content even in error case
            try:
                pruned_content = CodeContextPruner.prune_comments_simple(prompt_content)
                return f"Analyze this callstack trace:\n\n{pruned_content}"
            except:
                return f"Analyze this callstack trace:\n\n{prompt_content}"

    @staticmethod
    def build_output_requirements() -> str:
        """
        Build the output requirements section with schema for trace analysis.

        Returns:
            str: Output requirements with schema
        """
        try:
            # Read output schema
            schema_content = _read_package_file("outputSchema.json")
            if schema_content:
                output_requirements = f"""

    ## Output Requirements

    Return a valid JSON array of issues found in the trace analysis. Each issue must follow this exact schema:

    ```json
    {schema_content}
    ```

    **CRITICAL**: Your entire response must be valid JSON starting with `[` and ending with `]`. Any other text will cause system failure.

    **Required Fields**:
    - `severity`: Must be one of "critical", "high", "medium", "low"
    - `file`: Path to the file containing the issue
    - `file_path`: Complete path of the file relative to the repository
    - `functionName`: Exact name of the function with the issue
    - `line`: Line number or range where the issue occurs
    - `issue`: Clear, specific description of the problem found
    - `category`: Must be one of "performance", "memoryManagement", "concurrency", "errorHandling", "resourceManagement", "codeQuality", "logicBug", "minorOptimizationConsiderations"
    - `issueType`: The specific type of issue (use the category value if no more specific type applies)
    - `impact`: Description of the potential impact or consequences
    - `potentialSolution`: Specific, actionable solution or recommendation

    **CRITICAL HTML FORMATTING INSTRUCTIONS**:
    
    For the `issue`, `impact`, and `potentialSolution` fields:
    - When listing multiple items, use HTML line breaks for proper formatting
    - Start each numbered item on a new line using `<br>` tags
    - Format numbered lists as: "1) First item<br>2) Second item<br>3) Third item"
    - This ensures proper display in HTML reports
    
    **Example of proper formatting**:
    ```
    "potentialSolution": "Consider these optimizations:<br>1) In file MyClass.cpp at line 45, replace the loop with a more efficient algorithm<br>2) Modify MyHeader.h lines 12-15 to use inline functions<br>3) Update MyFile.m line 78 by adding caching"
    ```

    **MUST INSTRUCTION FOR POTENTIAL SOLUTION**:
    The "potentialSolution" field MUST ALWAYS include specific file names and line numbers where the changes should be made. Format examples:
    - "In file MyClass.cpp at line 45, replace the loop with..."
    - "Modify MyHeader.h lines 12-15 to change..."
    - "Update MyFile.m line 78 by adding..."

    Focus on performance optimization opportunities in the callstack trace. Only report issues where there is a clear potential for optimization.
    """
                return output_requirements
            else:
                logger.error("Failed to load outputSchema.json")
                raise FileNotFoundError("outputSchema.json not found in package")
        except Exception as e:
            logger.error(f"Error building output requirements: {e}")
            return "\n\n## Output Requirements\n\nReturn valid JSON array of issues with potentialSolution field."

    @staticmethod
    def build_complete_prompt(prompt_content: str, extracted_file_paths: list = None) -> Tuple[str, str]:
        """
        Build complete system and user prompts for trace analysis.
        
        IMPORTANT: System prompt is kept constant across all traces to enable TTL caching.
        Trace-specific information (like extracted file paths) goes in the user prompt.

        Args:
            prompt_content: The trace analysis prompt content
            extracted_file_paths: Optional list of file paths extracted from the trace

        Returns:
            Tuple[str, str]: (system_prompt, user_prompt)
        """
        # Build system prompt (keep it constant across all traces for TTL caching)
        system_prompt = TracePromptBuilder.build_system_prompt()

        # Build user prompt
        user_prompt = TracePromptBuilder.build_user_prompt(prompt_content)
        
        # Add extracted file paths information to USER prompt (not system prompt)
        # This keeps the system prompt consistent while still providing file context
        if extracted_file_paths:
            # Filter out empty, None, and whitespace-only paths to prevent empty readFile tool calls
            valid_file_paths = [path for path in extracted_file_paths if path and isinstance(path, str) and path.strip()]
            
            if valid_file_paths:
                file_paths_info = f"""

## Available Files from Trace Analysis

The following files have been identified in the trace and are available in the repository for analysis:

{chr(10).join(f"- {path}" for path in valid_file_paths)}

These files are likely relevant to the trace being analyzed and may contain the functions mentioned in the callstack.

"""
                user_prompt += file_paths_info

        # Add output requirements to user prompt
        output_requirements = TracePromptBuilder.build_output_requirements()
        complete_user_prompt = user_prompt + output_requirements

        return system_prompt, complete_user_prompt