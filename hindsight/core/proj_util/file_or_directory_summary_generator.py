#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
File or Directory Summary Generator
Provides an API to generate English summaries of files using LLM providers
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# Local imports - ordered alphabetically
from hindsight.core.constants import DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT
from hindsight.core.llm.llm import Claude, ClaudeConfig
from hindsight.core.llm.tools import Tools
from hindsight.utils.config_util import load_and_validate_config, get_llm_provider_type, get_api_key_from_config
from hindsight.utils.directory_tree_util import DirectoryTreeUtil
from hindsight.utils.file_content_provider import FileContentProvider
from hindsight.utils.log_util import get_logger, setup_default_logging
from hindsight.utils.output_directory_provider import get_output_directory_provider

# Initialize logging
setup_default_logging()
logger = get_logger(__name__)


def load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_file = project_root / "hindsight" / "core" / "prompts" / "fileSummaryPrompt.md"
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            content = f.read()
        # Extract the content after the title
        lines = content.split('\n')
        # Skip the title line and return the rest
        prompt_content = '\n'.join(lines[1:]).strip()
        return prompt_content
    except Exception as e:
        # Use basic logging if logger not available yet
        logger.warning(f"Could not load system prompt from {prompt_file}: {e}")
        # Fallback to a basic prompt
        return """You are a code analysis expert. Analyze the provided file and generate a 2-3 line summary of what it does, its key components, and its role in the codebase. Use the available tools (readFile, runTerminalCmd, list_files) to understand the file before generating your summary."""


class FileOrDirectorySummaryGenerator:
    """
    Generates English summaries of files using LLM providers.
    Supports both Claude and AWS Bedrock providers.
    """
    
    def __init__(self, llm_provider: str, config: Dict[str, Any]):
        """
        Initialize the summary generator with specified LLM provider.
        
        Args:
            llm_provider: LLM provider type ('aws_bedrock')
            config: Configuration dictionary containing LLM settings
        """
        self.llm_provider = llm_provider
        self.config = config
        self.claude_client = None
        self.tools = None
        
        # Cache for reusing Tools instances per repository
        self._tools_cache = {}
        self._current_repo_path = None
        
        # Validate provider
        if llm_provider not in ['aws_bedrock']:
            raise ValueError(f"Unsupported LLM provider: {llm_provider}")

        # Get API key
        self.api_key = get_api_key_from_config(config)
        if not self.api_key:
            raise ValueError(f"No API key available for provider: {llm_provider}")
        
        logger.info(f"Initialized FileOrDirectorySummaryGenerator with provider: {llm_provider}")
    
    def _get_or_create_tools(self, repo_path: str) -> None:
        """
        Get or create Tools instance for the given repository path.
        Reuses existing Tools instances to avoid repeated initialization logging.
        
        Args:
            repo_path: Path to the repository root
        """
        # Check if we already have a Tools instance for this repo path
        if repo_path in self._tools_cache:
            self.tools = self._tools_cache[repo_path]
            logger.debug(f"Reusing existing Tools instance for repository: {repo_path}")
            return
        
        # Configure OutputDirectoryProvider before initializing Tools
        # This is required because Tools class uses get_output_directory_provider()
        # Use the existing configuration if already configured, otherwise use /tmp/ as fallback
        output_provider = get_output_directory_provider()
        if not output_provider.is_configured():
            output_provider.configure(repo_path, "/tmp/")
            logger.debug(f"Configured OutputDirectoryProvider with repo_path: {repo_path}, output_dir: /tmp/")
        else:
            logger.debug(f"OutputDirectoryProvider already configured, using existing configuration")
        
        # Initialize FileContentProvider for efficient file resolution
        file_content_provider = None
        try:
            # Try to get existing FileContentProvider instance
            file_content_provider = FileContentProvider.get()
            logger.debug("Using existing FileContentProvider instance")
        except RuntimeError:
            # FileContentProvider not initialized, create a new one
            try:
                file_content_provider = FileContentProvider.from_repo(repo_path)
                logger.info("Created new FileContentProvider instance")
            except Exception as e:
                logger.warning(f"Failed to create FileContentProvider: {e}")
                file_content_provider = None
        
        # Initialize DirectoryTreeUtil
        directory_tree_util = DirectoryTreeUtil()
        
        # Initialize tools with repository access and providers
        # Extract configuration values for Tools initialization
        override_base_dir = self.config.get('override_base_dir')
        ignore_dirs = set(self.config.get('exclude_directories', []))
        
        # Generate artifacts_dir dynamically like other LLM classes
        # Use code_insights directory where AST files are located (same as code_analyzer)
        artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"
        
        # Create new Tools instance only if not cached
        self.tools = Tools(
            repo_path=repo_path,
            override_base_dir=override_base_dir,
            file_content_provider=file_content_provider,
            artifacts_dir=artifacts_dir,
            directory_tree_util=directory_tree_util,
            ignore_dirs=ignore_dirs
        )
        
        # Cache the Tools instance for reuse
        self._tools_cache[repo_path] = self.tools
        logger.debug(f"Created and cached new Tools instance for repository: {repo_path}")

    def _setup_new_context(self, repo_path: str) -> None:
        """
        Set up a new context window for each summary request.
        Now reuses Tools instances to avoid repeated initialization.
        
        Args:
            repo_path: Path to the repository root
        """
        # Get or create Tools instance (cached to avoid repeated initialization)
        self._get_or_create_tools(repo_path)
        
        # Setup conversation logging (same as working analyzers)
        Claude.setup_prompts_logging()
        
        # Create Claude configuration
        claude_config = ClaudeConfig(
            api_key=self.api_key,
            api_url=self.config.get('api_end_point', DEFAULT_LLM_API_END_POINT),
            model=self.config.get('model', DEFAULT_LLM_MODEL),
            max_tokens=self.config.get('max_tokens', 64000),
            temperature=self.config.get('temperature', 0.1),
            provider_type=self.llm_provider
        )
        
        # Initialize Claude client
        self.claude_client = Claude(claude_config)
        
        # Start a new conversation
        self.claude_client.start_conversation(
            analysis_type="file_summary",
            context_info=f"Repository: {repo_path}"
        )
        
        logger.debug(f"Set up new context for repository: {repo_path}")
    
    def get_summary_of_file(self, root: str, relative_path: str) -> str:
        """
        Generate a 2-3 line English summary of what a file does.
        
        Args:
            root: Root directory path
            relative_path: Relative path to the file from root
            
        Returns:
            str: English summary of the file (2-3 lines)
        """
        try:
            # Set up new context for this summary
            self._setup_new_context(root)
            
            # Construct full file path
            full_path = os.path.join(root, relative_path)
            if not os.path.exists(full_path):
                return f"Error: File not found at {relative_path}"
            
            # Load system prompt
            system_prompt = load_system_prompt()
            
            # Create user prompt requesting file summary
            user_prompt = f"""Please analyze the file '{relative_path}' and provide a 2-3 line summary of what it does.

Use the available tools to read the file contents and understand its purpose. Focus on:
1. What the file's primary function or purpose is
2. Key components it contains (classes, functions, configurations, etc.)
3. How it fits into the broader codebase

File to analyze: {relative_path}"""
            
            # Use structured conversation pattern like working analyzers
            messages = [{"role": "user", "content": user_prompt}]
            
            # Run iterative analysis with proper conversation management using unified method
            final_response = self.claude_client.run_iterative_analysis(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_executor=self,  # Pass self so tools can be accessed via self.tools
                supported_tools=[
                    "readFile", "runTerminalCmd", "getSummaryOfFile",
                    "inspectDirectoryHierarchy", "list_files",
                    "getFileContentByLines", "getFileContent", "checkFileSize"
                ],
                context_guidance_template="""
Based on the tool results above, please continue your file summary analysis. Remember to:
1. Analyze the information provided by the tools
2. Focus on what the file does, its key components, and its role in the codebase
3. Provide your final summary in the required JSON format: {{"summary": "Your 2-3 line summary here"}}

Original summary request: {user_prompt}

Please provide your summary based on all the information gathered so far.
""",
                response_processor=self._extract_summary_from_response
            )
            
            if not final_response:
                logger.error("Failed to get response from iterative analysis")
                return "Error: Failed to generate summary - No response from LLM"
            
            # Extract final summary from response
            summary = self._extract_summary(final_response)
            
            # Log the complete conversation (same as working analyzers)
            self.claude_client.log_complete_conversation(final_result=summary)
            
            logger.info(f"Generated summary for {relative_path}: {len(summary)} characters")
            return summary
            
        except Exception as e:
            error_msg = f"Error generating summary for {relative_path}: {str(e)}"
            logger.error(error_msg)
            return error_msg
    
    def _extract_response_content(self, response: Dict[str, Any]) -> str:
        """
        Extract content from LLM response.
        
        Args:
            response: LLM response dictionary
            
        Returns:
            str: Response content text
        """
        if "content" in response and isinstance(response["content"], list):
            # Claude native format
            content_blocks = response["content"]
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            return "\n".join(text_parts)
        elif "choices" in response:
            # AWS Bedrock format
            choices = response.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
        
        return str(response)
    
    def _extract_summary(self, response_content: str) -> str:
        """
        Extract the final summary from response content.
        
        Args:
            response_content: Full response content from LLM
            
        Returns:
            str: Cleaned summary text
        """
        # Remove any tool requests or JSON blocks
        lines = response_content.split('\n')
        summary_lines = []
        
        in_code_block = False
        in_json_block = False
        
        for line in lines:
            # Skip code blocks and JSON blocks
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                continue
            if line.strip().startswith('{') and '"tool"' in line:
                in_json_block = True
                continue
            if in_json_block and line.strip().endswith('}'):
                in_json_block = False
                continue
            
            if not in_code_block and not in_json_block:
                # Keep lines that look like summary content
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and not stripped.startswith('*'):
                    summary_lines.append(stripped)
        
        # Join and clean up the summary
        summary = ' '.join(summary_lines).strip()
        
        # Limit to reasonable length (approximately 2-3 lines)
        if len(summary) > 300:
            # Find a good breaking point
            sentences = summary.split('. ')
            if len(sentences) >= 2:
                summary = '. '.join(sentences[:2]) + '.'
            else:
                summary = summary[:297] + '...'
        
        return summary if summary else "Unable to generate summary from response."

    def _build_system_prompt_with_tools(self, user_prompt: str) -> str:
        """
        Build system prompt with tool definitions like working analyzers.
        This provides the LLM with structured tool access and requests strict JSON responses.
        
        Args:
            user_prompt: The user's request for file summary
            
        Returns:
            str: Complete system prompt with tool definitions and JSON schema
        """
        # Base system prompt for file summary
        base_prompt = """You are a code analysis expert. Your task is to analyze the provided file and generate a concise 2-3 line summary of what it does, its key components, and its role in the codebase.

Use the available tools to read the file contents and understand its purpose. Focus on:
1. What the file's primary function or purpose is
2. Key components it contains (classes, functions, configurations, etc.)
3. How it fits into the broader codebase

IMPORTANT: You must provide your final response as a JSON object with this exact structure:
{
  "summary": "Your 2-3 line summary here"
}

Available tools:"""

        # Get tool definitions from the Tools class (same as working analyzers)
        tool_definitions = self._get_tool_definitions()
        
        # Combine base prompt with tool definitions
        full_prompt = f"{base_prompt}\n\n{tool_definitions}\n\nRemember to provide your final summary in the required JSON format."
        
        return full_prompt

    def _get_tool_definitions(self) -> str:
        """
        Get tool definitions in the same format as working analyzers.
        This provides structured tool access to the LLM.
        
        Returns:
            str: Tool definitions formatted for the system prompt
        """
        return """
1. readFile - Read file contents
   Usage: {"tool": "readFile", "path": "relative/path/to/file"}

2. runTerminalCmd - Execute terminal commands
   Usage: {"tool": "runTerminalCmd", "command": "command to run", "reason": "why you need this"}

3. getSummaryOfFile - Get file summary
   Usage: {"tool": "getSummaryOfFile", "path": "file.ext", "reason": "why you need this"}

4. list_files - Get directory structure
   Usage: {"tool": "list_files", "path": "directory/path", "reason": "why you need this"}

5. runTerminalCmd with grep - Search for text patterns across files
   Usage: {"tool": "runTerminalCmd", "command": "grep -r -l 'text to find' --include='*.ext' .", "reason": "why you need this"}

Use these tools to gather information about the file before providing your summary.
"""

    def _extract_summary_from_response(self, response_content: str) -> str:
        """
        Extract the final summary from response content with JSON parsing.
        
        This method receives already-cleaned JSON from llm.py's run_iterative_analysis(),
        which has already validated and extracted the JSON. We just need to parse it
        and extract the summary field.
        
        Args:
            response_content: Cleaned JSON string from llm.py (already validated)
            
        Returns:
            str: Cleaned summary text
        """
        # Try to parse the already-cleaned JSON directly
        try:
            # The response_content is already cleaned JSON from llm.py
            parsed_json = json.loads(response_content)
            if "summary" in parsed_json:
                logger.debug("Successfully extracted summary from cleaned JSON")
                return parsed_json["summary"]
            else:
                logger.warning("JSON parsed but no 'summary' field found")
                # Fallback to text extraction
                return self._extract_summary(response_content)
                
        except json.JSONDecodeError as e:
            # If direct parsing fails, the response might not be JSON yet
            # Try the original extraction methods as fallback
            logger.debug(f"Direct JSON parsing failed: {e}, trying fallback methods")
            
            # Try to find JSON wrapped in <json> tags
            json_tag_start = response_content.find('<json>')
            json_tag_end = response_content.find('</json>')
            
            if json_tag_start >= 0 and json_tag_end > json_tag_start:
                json_content = response_content[json_tag_start + 6:json_tag_end].strip()
                try:
                    parsed_json = json.loads(json_content)
                    if "summary" in parsed_json:
                        logger.debug("Successfully extracted JSON summary from <json> tags")
                        return parsed_json["summary"]
                except json.JSONDecodeError:
                    pass
            
            # Try to find the last valid JSON object in the response
            json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            json_matches = re.findall(json_pattern, response_content, re.DOTALL)
            
            for json_match in reversed(json_matches):
                try:
                    parsed_json = json.loads(json_match)
                    if "summary" in parsed_json:
                        logger.debug("Successfully extracted JSON summary from regex match")
                        return parsed_json["summary"]
                except json.JSONDecodeError:
                    continue
            
            # Try to find JSON by looking for balanced braces
            json_start = response_content.find('{')
            if json_start >= 0:
                brace_count = 0
                json_end = json_start
                
                for i, char in enumerate(response_content[json_start:], json_start):
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_end = i + 1
                            break
                
                if brace_count == 0:
                    json_str = response_content[json_start:json_end]
                    try:
                        parsed_json = json.loads(json_str)
                        if "summary" in parsed_json:
                            logger.debug("Successfully extracted JSON summary from balanced braces")
                            return parsed_json["summary"]
                    except json.JSONDecodeError:
                        pass
            
            # Final fallback: use text extraction
            logger.debug("All JSON parsing methods failed, using text extraction fallback")
            return self._extract_summary(response_content)
            
        except Exception as e:
            logger.warning(f"Unexpected error during JSON parsing: {e}, using fallback extraction")
            return self._extract_summary(response_content)

    # Removed _run_iterative_analysis and _execute_claude_tool_use - now using unified methods in llm.py


def main():
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Generate English summaries of files using LLM providers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Summarize a single file
  python -m hindsight.core.proj_util.file_or_directory_summary_generator --root /path/to/repo --config config.json --file src/main.py
  
  # Use AWS Bedrock provider
  python -m hindsight.core.proj_util.file_or_directory_summary_generator --root /path/to/repo --config bedrock_config.json --file README.md
        """
    )
    
    parser.add_argument(
        "--root", "-r",
        required=True,
        help="Path to the repository root directory"
    )
    
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="Path to LLM configuration JSON file"
    )
    
    parser.add_argument(
        "--file", "-f",
        required=True,
        help="Relative path to the file to summarize (from root)"
    )
    
    args = parser.parse_args()
    
    try:
        # Setup prompts logging and clear older prompts for standalone usage
        Claude.setup_prompts_logging()
        Claude.clear_older_prompts()
        
        # Load and validate configuration
        config = load_and_validate_config(args.config)
        
        # Determine LLM provider type
        llm_provider = get_llm_provider_type(config)
        
        # Initialize summary generator
        generator = FileOrDirectorySummaryGenerator(llm_provider, config)
        
        # Generate summary
        summary = generator.get_summary_of_file(args.root, args.file)
        
        # Print summary to stdout
        print(summary)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()