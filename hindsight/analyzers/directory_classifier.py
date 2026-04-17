#!/usr/bin/env python3
"""
Directory analysis helper for analyzers.
Contains functionality for determining include and exclude directories for analysis.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from ..core.constants import DEFAULT_EXCLUDE_DIRECTORY_NAMES


class DirectoryClassifier:
    """
    Helper class for analyzing directories and determining include/exclude patterns.
    This class provides functionality for scanning repositories and determining
    which directories should be included or excluded from analysis.
    """
    
    DEFAULT_EXTS = ALL_SUPPORTED_EXTENSIONS

    @staticmethod
    def get_include_and_exclude_directories(repo_path: str,
                                            user_provided_include_list: Optional[List[str]] = None,
                                            user_provided_exclude_list: Optional[List[str]] = None) -> Tuple[Set[str], Set[str]]:
        """
        Scan the repository and return sets of directories to include and exclude.
        
        Args:
            repo_path: Path to the repository root
            user_provided_include_list: Optional list of directory names or relative paths to include.
                                      If provided, only directories matching these will be included.
                                      Empty lists are treated as None (no filtering applied).
                                      Supports both directory names (e.g., "core") and relative paths (e.g., "hindsight/core").
            user_provided_exclude_list: Optional list of directory names to exclude.
                                      If provided, will be combined with DEFAULT_EXCLUDE_DIRECTORY_NAMES.
                                      Empty lists are treated as None (only default excludes applied).
            
        Returns:
            tuple: (include_directories, exclude_directories)
                - include_directories: set of relative paths from repo_path
                - exclude_directories: set of relative paths from repo_path to ignore
        """
        # Treat empty lists as None for graceful handling
        if user_provided_include_list is not None and len(user_provided_include_list) == 0:
            user_provided_include_list = None
        if user_provided_exclude_list is not None and len(user_provided_exclude_list) == 0:
            user_provided_exclude_list = None
        
        # Initialize directory names to ignore (case-insensitive)
        # Combine default exclude names with user-provided exclude list if provided
        if user_provided_exclude_list is not None:
            exclude_names = list(DEFAULT_EXCLUDE_DIRECTORY_NAMES) + list(user_provided_exclude_list)
        else:
            exclude_names = DEFAULT_EXCLUDE_DIRECTORY_NAMES
        exclude_names_lower = [name.lower() for name in exclude_names]
        
        # Initialize sets
        include_directories = set()
        exclude_directories = set()
        
        # Convert repo_path to Path object and resolve it
        repo_root = Path(repo_path).expanduser().resolve()
        
        if not repo_root.exists() or not repo_root.is_dir():
            raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")
        
        # Get supported extensions for comparison
        supported_extensions = set(DirectoryClassifier.DEFAULT_EXTS)
        
        # Process user_provided_include_list to support both directory names and relative paths
        user_include_paths = set()
        user_include_names = set()
        user_provided_include_set = set()  # Normalized set of all user-provided include items
        
        if user_provided_include_list is not None:
            for item in user_provided_include_list:
                item_path = Path(item)
                normalized_item = item.replace('\\', '/')
                user_provided_include_set.add(normalized_item)
                
                if '/' in item or '\\' in item:
                    # This is a relative path
                    user_include_paths.add(normalized_item)
                else:
                    # This is just a directory name
                    user_include_names.add(item.lower())
        
        # Walk through all directories in the repository
        for root, dirs, files in os.walk(repo_root):
            current_dir = Path(root)
            
            # Get relative path from repo_root
            try:
                relative_path = current_dir.relative_to(repo_root)
                relative_path_str = str(relative_path) if relative_path != Path('.') else '.'
            except ValueError:
                # Skip if we can't get relative path
                continue
            
            # Count files with supported extensions in current directory
            supported_file_count = 0
            for file in files:
                file_path = Path(file)
                if file_path.suffix.lower() in supported_extensions:
                    supported_file_count += 1
            
            # Count subdirectories in current directory
            subdirectory_count = len(dirs)
            
            # Get directory name for checking against default ignore list
            dir_name = current_dir.name
            
            # Determine if directory should be excluded
            should_exclude = False
            
            # Check if this directory is in the user_provided_include_list
            # If it is, it should NEVER be excluded, regardless of other rules
            is_user_included = False
            if user_provided_include_list is not None:
                # Check if directory matches by name
                if dir_name.lower() in user_include_names:
                    is_user_included = True
                # Check if directory matches by relative path
                elif relative_path_str in user_provided_include_set:
                    is_user_included = True
                # Check if this directory is a child of any user-included path
                else:
                    for include_item in user_provided_include_set:
                        if relative_path_str.startswith(include_item + '/') or relative_path_str == include_item:
                            is_user_included = True
                            break
            
            # Only apply exclusion rules if the directory is NOT in user_provided_include_list
            if not is_user_included:
                # Check if directory name matches exclude names (case-insensitive)
                if dir_name.lower() in exclude_names_lower:
                    should_exclude = True
                    exclude_directories.add(relative_path_str)
                # Check if directory has no supported files AND no subdirectories
                elif supported_file_count == 0 and subdirectory_count == 0:
                    should_exclude = True
                    exclude_directories.add(relative_path_str)
            
            # If not excluded, add to include set ONLY if it has supported files
            # But first check if any parent directory is in the exclude list
            if not should_exclude and supported_file_count > 0:
                # Check if any parent of this directory is excluded
                is_child_of_excluded = False
                if relative_path_str != '.':
                    path_parts = relative_path_str.split('/')
                    for i in range(1, len(path_parts)):
                        parent_path = '/'.join(path_parts[:i])
                        # Check if parent directory name matches exclude patterns
                        parent_dir_name = Path(parent_path).name
                        if parent_dir_name.lower() in exclude_names_lower:
                            is_child_of_excluded = True
                            break
                
                # Check if user provided include list and if so, filter by it
                should_include = True
                if user_provided_include_list is not None:
                    should_include = False
                    # Check if directory matches by name
                    if dir_name.lower() in user_include_names:
                        should_include = True
                    # Check if directory matches by relative path
                    elif relative_path_str in user_include_paths:
                        should_include = True
                    # Check if any parent path matches the include paths
                    else:
                        for include_path in user_include_paths:
                            if relative_path_str.startswith(include_path + '/') or relative_path_str == include_path:
                                should_include = True
                                break
                
                if not is_child_of_excluded and should_include:
                    include_directories.add(relative_path_str)
        
        # Ensure ALL user_provided_include_list items are in include_directories
        # This guarantees that user-specified directories are always included
        if user_provided_include_list is not None:
            for item in user_provided_include_list:
                normalized_item = item.replace('\\', '/')
                include_directories.add(normalized_item)
        
        # Remove any user_provided_include_list items from exclude_directories
        # This ensures user-specified directories are never excluded
        if user_provided_include_list is not None:
            for item in user_provided_include_list:
                normalized_item = item.replace('\\', '/')
                exclude_directories.discard(normalized_item)
                
                # Also remove any child directories of user-included directories from excludes
                exclude_directories_to_remove = set()
                for exclude_path in exclude_directories:
                    if exclude_path.startswith(normalized_item + '/'):
                        exclude_directories_to_remove.add(exclude_path)
                exclude_directories -= exclude_directories_to_remove
        
        # Apply sophisticated exclusion logic: exclude parent directories when all children should be excluded
        # and parent has no supported files
        exclude_directories = DirectoryClassifier._optimize_exclusions(exclude_directories, repo_root, supported_extensions, user_provided_include_set)
        
        return include_directories, exclude_directories

    @staticmethod
    def _remove_redundant_children(exclude_directories: Set[str]) -> Set[str]:
        """
        Remove child directories from exclude list if their parent is already excluded.
        
        Args:
            exclude_directories: Set of relative paths to exclude
            
        Returns:
            Set of relative paths with redundant children removed
        """
        # Convert to sorted list for processing (shorter paths first)
        sorted_excludes = sorted(exclude_directories)
        result = set()
        
        for path in sorted_excludes:
            # Check if any parent of this path is already in the result
            is_child_of_excluded = False
            path_parts = path.split('/')
            
            # Check all possible parent paths
            for i in range(1, len(path_parts)):
                parent_path = '/'.join(path_parts[:i])
                if parent_path in result:
                    is_child_of_excluded = True
                    break
            
            # Only add if it's not a child of an already excluded directory
            if not is_child_of_excluded:
                result.add(path)
        
        return result

    @staticmethod
    def _optimize_exclusions(exclude_directories: Set[str], repo_root: Path, supported_extensions: Set[str], user_provided_include_set: Set[str]) -> Set[str]:
        """
        Optimize exclusions by excluding parent directories when all children should be excluded
        and parent has no supported files.
        
        This implements the logic:
        - If A/B/C and A/B/D should be excluded, and A/B has no supported files, exclude A/B instead
        - If A and A/B should be included, but A/B/C should be excluded, only A/B/C is returned
        - If directory or its sub-directory is in included_directories, it should never be excluded
        
        Args:
            exclude_directories: Set of relative paths to exclude
            repo_root: Path to repository root
            supported_extensions: Set of supported file extensions
            user_provided_include_set: Set of user-provided include directories
            
        Returns:
            Set of optimized relative paths to exclude
        """
        # First, remove redundant children (existing logic)
        optimized_exclusions = DirectoryClassifier._remove_redundant_children(exclude_directories)
        
        # Get all directories in the repository and check which have supported files
        all_directories = set()
        directories_with_supported_files = set()
        
        for root, dirs, files in os.walk(repo_root):
            current_dir = Path(root)
            try:
                relative_path = current_dir.relative_to(repo_root)
                relative_path_str = str(relative_path) if relative_path != Path('.') else '.'
            except ValueError:
                continue
                
            if relative_path_str == '.':
                continue
                
            all_directories.add(relative_path_str)
            
            # Check if directory has supported files
            has_supported_files = False
            for file in files:
                file_path = Path(file)
                if file_path.suffix.lower() in supported_extensions:
                    has_supported_files = True
                    break
            
            if has_supported_files:
                directories_with_supported_files.add(relative_path_str)
        
        # Process directories by depth (shallowest first) to find optimization opportunities
        # This ensures we check parents before children
        sorted_dirs = sorted(all_directories, key=lambda x: x.count('/'))
        
        for parent_dir in sorted_dirs:
            # Skip if parent is already excluded (would be redundant)
            if parent_dir in optimized_exclusions:
                continue
                
            # Skip if parent is user-included
            if DirectoryClassifier._is_directory_or_parent_included(parent_dir, user_provided_include_set):
                continue
                
            # Skip if parent has supported files
            if parent_dir in directories_with_supported_files:
                continue
            
            # Find all descendant directories of this parent
            descendant_dirs = [d for d in all_directories if d.startswith(parent_dir + '/')]
            
            if not descendant_dirs:
                # No descendants, this directory should be excluded if it has no supported files
                if parent_dir not in directories_with_supported_files:
                    optimized_exclusions.add(parent_dir)
                continue
            
            # Check if all descendants should be excluded
            all_descendants_should_be_excluded = True
            for desc_dir in descendant_dirs:
                # Descendant should be excluded if:
                # 1. It's already in the exclude list, OR
                # 2. It has no supported files AND is not user-included
                desc_should_be_excluded = (
                    desc_dir in exclude_directories or
                    (desc_dir not in directories_with_supported_files and
                     not DirectoryClassifier._is_directory_or_parent_included(desc_dir, user_provided_include_set))
                )
                
                if not desc_should_be_excluded:
                    all_descendants_should_be_excluded = False
                    break
            
            # If all descendants should be excluded, exclude the parent instead
            if all_descendants_should_be_excluded:
                optimized_exclusions.add(parent_dir)
                # Remove all descendants from exclusions since parent is now excluded
                descendants_to_remove = [d for d in optimized_exclusions if d.startswith(parent_dir + '/')]
                for desc_to_remove in descendants_to_remove:
                    optimized_exclusions.discard(desc_to_remove)
        
        # Apply redundant children removal one more time after optimization
        return DirectoryClassifier._remove_redundant_children(optimized_exclusions)
    
    @staticmethod
    def _is_directory_or_parent_included(directory_path: str, user_provided_include_set: Set[str]) -> bool:
        """
        Check if a directory or any of its parent directories is in the included set.
        
        Args:
            directory_path: Directory path to check
            user_provided_include_set: Set of user-provided include directories
            
        Returns:
            bool: True if directory or any parent is included
        """
        if not user_provided_include_set:
            return False
            
        # Check exact match
        if directory_path in user_provided_include_set:
            return True
            
        # Check if any included directory is a parent of this directory
        for included_dir in user_provided_include_set:
            if directory_path.startswith(included_dir):
                return True
                
        # Check if this directory is a parent of any included directory
        for included_dir in user_provided_include_set:
            if included_dir.startswith(directory_path):
                return True
                
        return False

    @staticmethod
    def get_recommended_exclude_directories(repo_path: str,
                                            user_provided_include_list: Optional[List[str]] = None,
                                            user_provided_exclude_list: Optional[List[str]] = None) -> Set[str]:
        """
        Get recommended directories to exclude from analysis.
        
        Args:
            repo_path: Path to the repository root
            user_provided_include_list: Optional list of directory names or relative paths to include
            user_provided_exclude_list: Optional list of directory names to exclude in addition to defaults
            
        Returns:
            Set of relative paths that should be excluded from analysis
        """
        _, exclude_directories = DirectoryClassifier.get_include_and_exclude_directories(
            repo_path, user_provided_include_list, user_provided_exclude_list
        )
        return exclude_directories

    @staticmethod
    def get_recommended_exclude_directories_safe(repo_path: str,
                                                 user_provided_include_list: Optional[List[str]] = None,
                                                 user_provided_exclude_list: Optional[List[str]] = None) -> List[str]:
        """
        Safely get recommended directories to exclude from analysis with error handling.
        
        Args:
            repo_path: Path to the repository root
            user_provided_include_list: Optional list of directory names or relative paths to include
            user_provided_exclude_list: Optional list of directory names to exclude in addition to defaults
            
        Returns:
            List of relative paths that should be excluded from analysis (empty list on error)
        """
        try:
            exclude_directories = DirectoryClassifier.get_recommended_exclude_directories(
                repo_path, user_provided_include_list, user_provided_exclude_list
            )
            return list(exclude_directories)
        except Exception:
            # Return empty list on any error - let the caller handle logging
            return []

    @staticmethod
    def print_directory_analysis(repo_path: str,
                                 user_provided_include_list: Optional[List[str]] = None,
                                 user_provided_exclude_list: Optional[List[str]] = None) -> None:
        """
        Print the directory analysis results in a formatted way.
        
        Args:
            repo_path: Path to the repository root
            user_provided_include_list: Optional list of directory names or relative paths to include
            user_provided_exclude_list: Optional list of directory names to exclude in addition to defaults
        """
        try:
            include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(
                repo_path, user_provided_include_list, user_provided_exclude_list)
            
            print("=== DIRECTORY ANALYSIS RESULTS ===")
            print(f"Repository: {repo_path}")
            if user_provided_include_list:
                print(f"Include filter: {user_provided_include_list}")
            if user_provided_exclude_list:
                print(f"Additional excludes: {user_provided_exclude_list}")
            print()
            
            print("INCLUDE DIRECTORIES:")
            if include_dirs:
                for dir_path in sorted(include_dirs):
                    print(f"  + {dir_path}")
            else:
                print("  (no directories to include)")
            
            print()
            print("EXCLUDE DIRECTORIES:")
            if exclude_dirs:
                for dir_name in sorted(exclude_dirs):  # Already a set, just sort
                    print(f"  - {dir_name}")
            else:
                print("  (no directories to exclude)")
                
        except Exception as e:
            print(f"Error analyzing directories: {e}")




class LLMBasedDirectoryClassifier(DirectoryClassifier):
    """
    Helper class which uses power of LLM to detect directories which are not central
    to the logic of a given repository. It can identify test, third_party and generated
    code.
    """
    
    def __init__(self, api_key: str, api_url: str = None, model: str = None, provider_type: str = "aws_bedrock"):
        """
        Initialize LLMBasedDirectoryClassifier with LLM configuration using centralized factory.

        Args:
            api_key: API key for LLM provider
            api_url: API endpoint URL (optional, uses default if not provided)
            model: Model name (optional, uses default if not provided)
            provider_type: LLM provider type ("aws_bedrock")
        """
        from ..core.llm.llm import Claude, ClaudeConfig, create_llm_provider
        from ..core.constants import DEFAULT_LLM_API_END_POINT, DEFAULT_LLM_MODEL
        
        # Set defaults if not provided
        self.api_url = api_url or DEFAULT_LLM_API_END_POINT
        self.model = model or DEFAULT_LLM_MODEL
        self.provider_type = provider_type
        
        # Create Claude configuration
        self.claude_config = ClaudeConfig(
            api_key=api_key,
            api_url=self.api_url,
            model=self.model,
            max_tokens=64000,
            temperature=0.1,
            timeout=120,
            provider_type=provider_type
        )
        
        # Use centralized factory to create provider
        provider = create_llm_provider(self.claude_config)
        
        # Initialize Claude client with factory-created provider
        self.claude = Claude(self.claude_config)
        self.claude.provider = provider  # Use the factory-created provider
    
    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'LLMBasedDirectoryClassifier':
        """
        Create LLMBasedDirectoryClassifier from configuration dictionary (same format as repo_analyzer config).
        
        Args:
            config: Configuration dictionary containing LLM settings
            
        Returns:
            LLMBasedDirectoryClassifier: Configured analyzer instance
        """
        # Get API key with fallback to Apple Connect token for AWS Bedrock
        from ..utils.config_util import get_llm_provider_type, get_api_key_from_config
        provider_type = get_llm_provider_type(config)
        config_api_key = get_api_key_from_config(config)

        # For AWS Bedrock, use get_api_key() to handle Apple Connect fallback
        from ..utils.api_key_util import get_api_key
        api_key = get_api_key(config_api_key)
        
        return cls(
            api_key=api_key,
            api_url=config.get("api_end_point"),
            model=config.get("model"),
            provider_type=provider_type
        )
        
    def _create_system_prompt(self) -> str:
        """
        Create system prompt for directory analysis.
        
        Returns:
            str: System prompt for LLM, or empty string if prompt file not found
        """
        # Load prompt from markdown file
        prompt_file = Path(__file__).parent.parent / "core" / "prompts" / "directoryAnalysis.md"
        with open(prompt_file, 'r', encoding='utf-8') as f:
            content = f.read()
            # Extract content after the header (skip the "# Directory Analysis System Prompt" line)
            lines = content.split('\n')
            # Find the first non-empty line after the header
            start_idx = 0
            for i, line in enumerate(lines):
                if line.strip() and not line.startswith('#'):
                    start_idx = i
                    break
            return '\n'.join(lines[start_idx:]).strip()

    def _create_user_prompt(self, subdirectories: List[str], 
                            directory_contents: Dict[str, List[str]], 
                            already_excluded_directories: Optional[List[str]] = None) -> str:
        """
        Create user prompt with directory information.
        
        Args:
            subdirectories: List of subdirectory paths
            directory_contents: Dictionary mapping directory paths to lists of files
            already_excluded_directories: List of directories already excluded by human analysis
            
        Returns:
            str: User prompt for LLM
        """
        prompt = "Please analyze the following repository directories and determine which should be excluded from code analysis:\n\n"
        
        # Add information about already excluded directories if provided
        if already_excluded_directories:
            prompt += f"**Note**: The following directories have already been excluded by human analysis and should be ignored:\n"
            for excluded_dir in already_excluded_directories:
                prompt += f"  - {excluded_dir} (already excluded)\n"
            prompt += "\nPlease analyze only the directories listed below and recommend additional exclusions:\n\n"
        
        for directory in subdirectories:
            prompt += f"## Directory: {directory}\n"
            
            files = directory_contents.get(directory, [])
            if files:
                # Limit to first 10 files to avoid token limits
                display_files = files[:10]
                prompt += "Files:\n"
                for file in display_files:
                    prompt += f"  - {file}\n"
                
                if len(files) > 10:
                    prompt += f"  ... and {len(files) - 10} more files\n"
            else:
                prompt += "  (no files or empty directory)\n"
            
            prompt += "\n"
        
        prompt += "\nBased on this analysis, return a JSON array of directory paths that should be EXCLUDED from code analysis"
        if already_excluded_directories:
            prompt += " (in addition to the already excluded directories mentioned above)"
        prompt += ":"
        
        return prompt

    def analyze_directories(self, repo_path: str,
                            subdirectories: List[str] = None,
                            already_excluded_directories: Optional[List[str]] = None,
                            user_provided_include_list: Optional[List[str]] = None) -> List[str]:
        """
        Analyze repository directory structure using LLM to identify directories to exclude.
        Now uses complete directory tree structure instead of just first-level directories.
        
        Args:
            repo_path: Path to the repository root
            subdirectories: List of subdirectory paths to analyze (optional, will discover all if not provided)
            already_excluded_directories: List of directories already excluded by human analysis (will be skipped)
            user_provided_include_list: List of directories that must NOT be excluded (user explicitly wants them included)
            
        Returns:
            List[str]: List of directory paths that should be excluded (in addition to already_excluded_directories)
        """
        import json
        from pathlib import Path
        from ..utils.log_util import get_logger
        
        logger = get_logger(__name__)
        
        try:
            # Validate connection first
            if not self.claude.validate_connection():
                logger.error("Failed to validate LLM connection")
                return []
            
            # Build complete directory tree structure
            logger.info("Building complete directory tree structure...")
            tree_structure, all_directory_paths = self._build_directory_tree(repo_path, max_depth=12)
            
            # Filter out already excluded directories
            if already_excluded_directories:
                already_excluded_set = set(already_excluded_directories)
                logger.info(f"Excluding {len(already_excluded_directories)} already excluded directories from analysis")
            else:
                already_excluded_set = set()
            
            # Create prompts with tree structure
            system_prompt = self._create_system_prompt()
            user_prompt = self._create_tree_based_user_prompt(tree_structure, already_excluded_directories, user_provided_include_list)
            
            # Check token limits and truncate if necessary
            if not self.claude.check_token_limit(system_prompt, user_prompt):
                logger.warning("Prompt exceeds token limits, using shallower tree structure")
                # Try with shallower tree
                tree_structure, all_directory_paths = self._build_directory_tree(repo_path, max_depth=8)
                user_prompt = self._create_tree_based_user_prompt(tree_structure, already_excluded_directories, user_provided_include_list)
                
                # If still too long, chunk the analysis
                if not self.claude.check_token_limit(system_prompt, user_prompt):
                    logger.warning("Tree structure still too large, chunking analysis")
                    return self._analyze_directories_in_chunks(repo_path, already_excluded_set, user_provided_include_list)
            
            # Start conversation
            self.claude.start_conversation("directory_tree_analysis", f"Analyzing complete directory tree structure")
            
            # Create payload manually to exclude tools (directory classifier doesn't need tools)
            messages = [{"role": "user", "content": user_prompt}]
            full_messages = [
                {"role": "system", "content": system_prompt}
            ] + messages
            
            # Create payload without tools
            payload = self.claude.provider.create_payload(
                full_messages,
                stream=False,
                enable_system_cache=True,
                cache_ttl="1h"
            )
            
            # Remove tools from payload to prevent LLM from trying to use them
            if "tools" in payload:
                del payload["tools"]
            
            # Store messages for conversation logging
            self.claude.conversation_messages.append(full_messages.copy())
            
            # Make request directly
            response = self.claude.provider.make_request(payload)
            
            # Store response for conversation logging
            self.claude.conversation_responses.append(response.copy() if response else {"error": "No response"})
            
            if response is None:
                logger.error("No response from LLM")
                return []
            
            # Handle error responses
            if "error" in response:
                logger.error(f"LLM API error: {response.get('error')}")
                return []
            
            # Extract response content - handle both Claude native and AWS Bedrock formats
            content = ""
            
            # Check for Claude native format first
            if "content" in response and isinstance(response.get("content"), list):
                # Claude native format
                content_blocks = response.get("content", [])
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        content = block.get("text", "")
                        break
            
            # Check for AWS Bedrock format
            elif "choices" in response:
                # AWS Bedrock format
                choices = response.get("choices", [])
                if choices:
                    assistant_message = choices[0].get("message", {})
                    content = assistant_message.get("content", "")
            
            if not content:
                logger.error("Empty content in LLM response")
                logger.debug(f"Response format: {list(response.keys())}")
                return []
            
            # Parse JSON response
            try:
                # Clean up markdown formatting if present
                if "```json" in content:
                    start = content.find("```json") + 7
                    end = content.find("```", start)
                    if end != -1:
                        content = content[start:end].strip()
                elif "```" in content:
                    start = content.find("```") + 3
                    end = content.find("```", start)
                    if end != -1:
                        content = content[start:end].strip()
                
                excluded_dirs = json.loads(content)
                
                if not isinstance(excluded_dirs, list):
                    logger.error(f"Expected JSON array, got {type(excluded_dirs)}")
                    return []
                
                # Validate that returned directories exist in the repository
                valid_excluded_dirs = []
                logger.debug(f"Total directories collected by tree builder: {len(all_directory_paths)}")
                logger.debug(f"Sample of collected directories: {sorted(list(all_directory_paths))[:10]}")
                
                for dir_path in excluded_dirs:
                    if isinstance(dir_path, str):
                        # Normalize path separators
                        normalized_path = dir_path.replace('\\', '/')
                        logger.debug(f"Checking if '{normalized_path}' exists in collected directories")
                        if normalized_path in all_directory_paths:
                            valid_excluded_dirs.append(normalized_path)
                            logger.debug(f"✓ Found: {normalized_path}")
                        else:
                            # Check if directory actually exists on filesystem as fallback
                            from pathlib import Path
                            repo_root = Path(repo_path).resolve()
                            actual_dir_path = repo_root / normalized_path
                            if actual_dir_path.exists() and actual_dir_path.is_dir():
                                logger.info(f"Directory exists on filesystem but not in tree collection, adding anyway: {normalized_path}")
                                valid_excluded_dirs.append(normalized_path)
                            else:
                                logger.warning(f"Directory not found in repository: {dir_path}")
                                # Debug: show similar paths
                                similar_paths = [p for p in all_directory_paths if dir_path.split('/')[-1] in p]
                                if similar_paths:
                                    logger.debug(f"Similar paths found: {similar_paths[:5]}")
                    else:
                        logger.warning(f"Invalid excluded directory type: {type(dir_path)}")
                
                logger.info(f"LLM analysis complete: {len(valid_excluded_dirs)} directories recommended for exclusion")
                
                # Log conversation
                self.claude.log_complete_conversation(
                    final_result=json.dumps(valid_excluded_dirs, indent=2)
                )
                
                return valid_excluded_dirs
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM response as JSON: {e}")
                logger.debug(f"Raw response: {content}")
                return []
            
        except Exception as e:
            logger.error(f"Error in LLM directory analysis: {e}")
            return []

    def _build_directory_tree(self, repo_path: str, max_depth: int = 12) -> tuple[str, set]:
        """
        Build a compact directory tree structure for the repository with file limits.
        Only includes directories that contain at least one file with supported extensions.
        
        Args:
            repo_path: Path to the repository root
            max_depth: Maximum depth to traverse
            
        Returns:
            tuple: (tree_structure_string, set_of_all_directory_paths)
        """
        from pathlib import Path
        
        repo_root = Path(repo_path).resolve()
        all_directory_paths = set()
        supported_extensions = set(DirectoryClassifier.DEFAULT_EXTS)
        
        def has_supported_files_recursive(dir_path: Path) -> bool:
            """Check if directory or any subdirectory contains supported files."""
            try:
                for item in dir_path.iterdir():
                    if item.is_file():
                        if item.suffix.lower() in supported_extensions:
                            return True
                    elif item.is_dir():
                        if has_supported_files_recursive(item):
                            return True
                return False
            except (PermissionError, OSError):
                return False
        
        def build_tree_recursive(current_path: Path, prefix: str = "", depth: int = 0) -> List[str]:
            lines = []
            if depth > max_depth:
                return lines
                
            try:
                # Get relative path from repo root
                rel_path = current_path.relative_to(repo_root)
                rel_path_str = str(rel_path) if rel_path != Path('.') else '.'
                
                # Only include directories that have supported files (recursively)
                if not has_supported_files_recursive(current_path):
                    return lines
                
                all_directory_paths.add(rel_path_str)
                
                # Debug logging for specific problematic directories
                if any(problem_dir in rel_path_str for problem_dir in ['src/test', 'src/main', 'protowire']):
                    from ..utils.log_util import get_logger
                    debug_logger = get_logger(__name__)
                    debug_logger.debug(f"Tree builder collected: '{rel_path_str}' at depth {depth}")
                
                # Get directory contents
                subdirs = []
                files = []
                
                for item in current_path.iterdir():
                    if item.is_dir():
                        # Only include subdirectories that have supported files
                        if has_supported_files_recursive(item):
                            subdirs.append(item)
                    elif item.is_file():
                        files.append(item)
                
                # Sort for consistent output
                subdirs.sort(key=lambda x: x.name.lower())
                files.sort(key=lambda x: x.name.lower())
                
                # Add current directory
                if depth == 0:
                    lines.append(f"{current_path.name}/")
                else:
                    lines.append(f"{prefix}├── {current_path.name}/")
                
                # Add files (limit to first 3 to keep prompt concise)
                file_count = len(files)
                display_files = files[:3]
                
                for i, file_item in enumerate(display_files):
                    is_last_file = (i == len(display_files) - 1) and len(subdirs) == 0 and file_count <= 3
                    file_prefix = f"{prefix}{'└──' if is_last_file else '├──'}"
                    lines.append(f"{file_prefix} {file_item.name}")
                
                if file_count > 3:
                    more_prefix = f"{prefix}{'└──' if len(subdirs) == 0 else '├──'}"
                    lines.append(f"{more_prefix} ... ({file_count - 3} more files)")
                
                # Add subdirectories recursively (limit to first 10 subdirs per level)
                display_subdirs = subdirs[:10]
                for i, subdir in enumerate(display_subdirs):
                    is_last = (i == len(display_subdirs) - 1) and len(subdirs) <= 10
                    new_prefix = prefix + ("    " if is_last else "│   ")
                    subdir_lines = build_tree_recursive(subdir, new_prefix, depth + 1)
                    lines.extend(subdir_lines)
                
                if len(subdirs) > 10:
                    more_prefix = f"{prefix}└── ... ({len(subdirs) - 10} more subdirectories)"
                    lines.append(more_prefix)
                    
            except (PermissionError, OSError):
                # Skip directories we can't read
                pass
                
            return lines
        
        tree_lines = build_tree_recursive(repo_root)
        tree_structure = "\n".join(tree_lines)
        
        return tree_structure, all_directory_paths

    def _create_tree_based_user_prompt(self, tree_structure: str,
                                       already_excluded_directories: Optional[List[str]] = None,
                                       user_provided_include_list: Optional[List[str]] = None) -> str:
        """
        Create user prompt with complete directory tree structure.
        
        Args:
            tree_structure: Complete directory tree as a string
            already_excluded_directories: List of directories already excluded by human analysis
            user_provided_include_list: List of directories that must NOT be excluded (user explicitly wants them included)
            
        Returns:
            str: User prompt for LLM
        """
        prompt = "Analyze this repository directory tree and identify directories to exclude from code analysis:\n\n"
        
        prompt += "**NOTE**: This tree only shows directories containing at least one file with supported extensions "
        prompt += "(.cpp, .cc, .c, .mm, .m, .h, .swift, .kt, .kts, .java, .go). "
        prompt += "Directories without supported files have already been filtered out.\n\n"
        
        # Add information about already excluded directories if provided
        if already_excluded_directories:
            prompt += f"**Already excluded by human analysis:**\n"
            for excluded_dir in already_excluded_directories:
                prompt += f"  - {excluded_dir}\n"
            prompt += "\nAnalyze the tree below for additional exclusions:\n\n"
        
        # Add information about user-provided include directories (MUST NOT be excluded)
        if user_provided_include_list:
            prompt += f"**IMPORTANT - DO NOT EXCLUDE these directories (user explicitly wants them included):**\n"
            for include_dir in user_provided_include_list:
                prompt += f"  - {include_dir} (PROTECTED - must be included)\n"
            prompt += "\nThese directories and their subdirectories MUST NOT appear in your exclusion list.\n\n"
        
        prompt += "## Directory Tree:\n"
        prompt += "```\n"
        prompt += tree_structure
        prompt += "\n```\n\n"
        
        prompt += "Identify directories to EXCLUDE (return JSON array of relative paths):\n"
        prompt += "- Tests: test/, tests/, spec/, __tests__/, etc.\n"
        prompt += "- Build/Generated: build/, dist/, target/, generated/, out/, etc.\n"
        prompt += "- Compiler-Generated: directories with .pb.go/.pb.cc/.pb.h/*_pb2.py/*_pb.js files (protobuf), gen-*/, __generated__/, generated thrift/grpc/openapi code\n"
        prompt += "- External: vendor/, node_modules/, external/, third_party/, etc.\n"
        prompt += "- Docs: docs/, documentation/, examples/ (non-core)\n"
        prompt += "- Config/Tools: .git/, .vscode/, scripts/ (non-business logic)\n\n"
        
        prompt += "Return JSON array of relative directory paths to exclude:"
        
        return prompt

    def _analyze_directories_in_chunks(self, repo_path: str, already_excluded_set: set,
                                       user_provided_include_list: Optional[List[str]] = None) -> List[str]:
        """
        Analyze directories in chunks when the full tree is too large for token limits.
        Only includes directories that contain at least one file with supported extensions.
        
        Args:
            repo_path: Path to the repository root
            already_excluded_set: Set of already excluded directories
            user_provided_include_list: List of directories that must NOT be excluded
            
        Returns:
            List[str]: Combined list of directories to exclude from all chunks
        """
        from pathlib import Path
        from ..utils.log_util import get_logger
        import json
        
        logger = get_logger(__name__)
        
        # Get first-level directories for chunking
        repo_root = Path(repo_path).resolve()
        first_level_dirs = []
        supported_extensions = set(DirectoryClassifier.DEFAULT_EXTS)
        
        def has_supported_files_recursive(dir_path: Path) -> bool:
            """Check if directory or any subdirectory contains supported files."""
            try:
                for item in dir_path.iterdir():
                    if item.is_file():
                        if item.suffix.lower() in supported_extensions:
                            return True
                    elif item.is_dir():
                        if has_supported_files_recursive(item):
                            return True
                return False
            except (PermissionError, OSError):
                return False
        
        try:
            for item in repo_root.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    rel_path = str(item.relative_to(repo_root))
                    if rel_path not in already_excluded_set:
                        # Only include directories that have supported files
                        if has_supported_files_recursive(item):
                            first_level_dirs.append(rel_path)
        except (PermissionError, OSError):
            logger.error(f"Cannot read repository directory: {repo_path}")
            return []
        
        # Process in chunks of 8 directories to keep prompts manageable
        chunk_size = 8
        all_excluded = []
        
        for i in range(0, len(first_level_dirs), chunk_size):
            chunk = first_level_dirs[i:i + chunk_size]
            logger.info(f"Analyzing chunk {i//chunk_size + 1}: {len(chunk)} directories")
            
            # Build compact tree for this chunk only
            chunk_tree_lines = [f"{repo_root.name}/"]
            chunk_paths = set()
            
            for j, dir_name in enumerate(chunk):
                dir_path = repo_root / dir_name
                is_last_dir = (j == len(chunk) - 1)
                
                chunk_tree_lines.append(f"{'└──' if is_last_dir else '├──'} {dir_name}/")
                chunk_paths.add(dir_name)
                
                # Add limited subdirectories for context (max 2 levels, max 5 items per level)
                try:
                    subdirs = [item for item in dir_path.iterdir() if item.is_dir()][:5]
                    files = [item for item in dir_path.iterdir() if item.is_file()][:2]
                    
                    # Show files first
                    for k, file_item in enumerate(files):
                        file_prefix = f"{'    ' if is_last_dir else '│   '}├── {file_item.name}"
                        chunk_tree_lines.append(file_prefix)
                    
                    # Show subdirectories
                    for k, subitem in enumerate(subdirs):
                        is_last_sub = (k == len(subdirs) - 1) and len(files) == 0
                        subdir_rel = f"{dir_name}/{subitem.name}"
                        sub_prefix = f"{'    ' if is_last_dir else '│   '}{'└──' if is_last_sub else '├──'} {subitem.name}/"
                        chunk_tree_lines.append(sub_prefix)
                        chunk_paths.add(subdir_rel)
                        
                        # One more level (very limited)
                        try:
                            subsubdirs = [item for item in subitem.iterdir() if item.is_dir()][:2]
                            for l, subsubitem in enumerate(subsubdirs):
                                subsubdir_rel = f"{dir_name}/{subitem.name}/{subsubitem.name}"
                                subsub_prefix = f"{'    ' if is_last_dir else '│   '}{'    ' if is_last_sub else '│   '}├── {subsubitem.name}/"
                                chunk_tree_lines.append(subsub_prefix)
                                chunk_paths.add(subsubdir_rel)
                        except (PermissionError, OSError):
                            pass
                            
                except (PermissionError, OSError):
                    pass
            
            chunk_tree = "\n".join(chunk_tree_lines)
            
            # Analyze this chunk
            try:
                system_prompt = self._create_system_prompt()
                user_prompt = self._create_tree_based_user_prompt(chunk_tree, list(already_excluded_set), user_provided_include_list)
                
                # Create payload manually to exclude tools (directory classifier doesn't need tools)
                messages = [{"role": "user", "content": user_prompt}]
                full_messages = [
                    {"role": "system", "content": system_prompt}
                ] + messages
                
                # Create payload without tools
                payload = self.claude.provider.create_payload(
                    full_messages,
                    stream=False,
                    enable_system_cache=True,
                    cache_ttl="1h"
                )
                
                # Remove tools from payload to prevent LLM from trying to use them
                if "tools" in payload:
                    del payload["tools"]
                
                # Make request directly
                response = self.claude.provider.make_request(payload)
                
                if response:
                    # Extract and parse response (same logic as main method)
                    content = ""
                    if "content" in response and isinstance(response.get("content"), list):
                        content_blocks = response.get("content", [])
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get("type") == "text":
                                content = block.get("text", "")
                                break
                    elif "choices" in response:
                        choices = response.get("choices", [])
                        if choices:
                            assistant_message = choices[0].get("message", {})
                            content = assistant_message.get("content", "")
                    
                    if content:
                        # Clean up and parse JSON
                        if "```json" in content:
                            start = content.find("```json") + 7
                            end = content.find("```", start)
                            if end != -1:
                                content = content[start:end].strip()
                        elif "```" in content:
                            start = content.find("```") + 3
                            end = content.find("```", start)
                            if end != -1:
                                content = content[start:end].strip()
                        
                        try:
                            excluded_dirs = json.loads(content)
                            if isinstance(excluded_dirs, list):
                                for dir_path in excluded_dirs:
                                    if isinstance(dir_path, str) and dir_path in chunk_paths:
                                        all_excluded.append(dir_path)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse JSON response for chunk {i//chunk_size + 1}")
                            
            except Exception as e:
                logger.warning(f"Error analyzing chunk {i//chunk_size + 1}: {e}")
                continue
        
        logger.info(f"Chunked analysis complete: {len(all_excluded)} directories recommended for exclusion")
        return all_excluded

    def _get_directory_contents(self, repo_path: str, subdirectories: List[str]) -> Dict[str, List[str]]:
        """
        Get file listings for subdirectories.
        
        Args:
            repo_path: Path to the repository root
            subdirectories: List of subdirectory paths
            
        Returns:
            Dict[str, List[str]]: Dictionary mapping directory paths to file lists
        """
        from pathlib import Path
        
        directory_contents = {}
        repo_root = Path(repo_path).resolve()
        
        for subdir in subdirectories:
            dir_path = repo_root / subdir
            files = []
            
            if dir_path.exists() and dir_path.is_dir():
                try:
                    # Get files in the directory (not recursive)
                    for item in dir_path.iterdir():
                        if item.is_file():
                            files.append(item.name)
                    
                    # Sort files for consistent output
                    files.sort()
                    
                except (PermissionError, OSError):
                    # Skip directories we can't read
                    pass
            
            directory_contents[subdir] = files
        
        return directory_contents


def main():
    """
    Main function to test LLMBasedDirectoryClassifier functionality.
    """
    import argparse
    import json
    import os
    from pathlib import Path
    
    parser = argparse.ArgumentParser(description="Analyze repository directories using LLM to identify exclude patterns")
    parser.add_argument("--repo", "-r", required=True, help="Path to the repository to analyze")
    
    # Configuration options - either use config file or individual parameters
    config_group = parser.add_mutually_exclusive_group(required=True)
    config_group.add_argument("--config", help="Path to JSON configuration file (same format as repo_analyzer)")
    config_group.add_argument("--api-key", help="API key for LLM provider (use with individual parameters)")
    
    # Individual LLM parameters (used when --api-key is provided instead of --config)
    parser.add_argument("--api-url", help="API endpoint URL (optional, used with --api-key)")
    parser.add_argument("--model", help="Model name (optional, used with --api-key)")
    parser.add_argument("--provider", default="aws_bedrock", choices=["aws_bedrock"],
                       help="LLM provider type (default: aws_bedrock, used with --api-key)")
    
    # Analysis options
    parser.add_argument("--subdirs", nargs="*", help="Specific subdirectories to analyze (optional)")
    parser.add_argument("--max-dirs", type=int, default=20, help="Maximum number of directories to analyze (default: 20)")
    parser.add_argument("--already-excluded", nargs="*", help="Directories already excluded by human analysis (will be skipped)")
    
    args = parser.parse_args()
    
    # Validate repository path
    repo_path = Path(args.repo).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        print(f"Error: Repository path does not exist or is not a directory: {args.repo}")
        return 1
    
    try:
        # Initialize the LLM-based analyzer
        print("Initializing LLM-based directory analyzer...")
        
        if args.config:
            # Load configuration from JSON file
            print(f"Loading configuration from: {args.config}")
            try:
                with open(args.config, 'r') as f:
                    config = json.load(f)
                
                analyzer = LLMBasedDirectoryClassifier.from_config(config)
                
                # Use exclude_directories from config as already_excluded if not provided via command line
                if not args.already_excluded and "exclude_directories" in config:
                    args.already_excluded = config["exclude_directories"]
                    print(f"Using exclude_directories from config: {args.already_excluded}")
                
            except FileNotFoundError:
                print(f"Error: Configuration file not found: {args.config}")
                return 1
            except json.JSONDecodeError as e:
                print(f"Error: Invalid JSON in configuration file: {e}")
                return 1
            except KeyError as e:
                print(f"Error: Missing required configuration key: {e}")
                return 1
        else:
            # Use individual parameters
            analyzer = LLMBasedDirectoryClassifier(
                api_key=args.api_key,
                api_url=args.api_url,
                model=args.model,
                provider_type=args.provider
            )
        
        # Get subdirectories to analyze
        if args.subdirs:
            subdirectories = args.subdirs
            print(f"Analyzing specified subdirectories: {subdirectories}")
        else:
            # Get all subdirectories in the repository
            print("Discovering subdirectories...")
            subdirectories = []
            for item in repo_path.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    relative_path = str(item.relative_to(repo_path))
                    subdirectories.append(relative_path)
            
            # Limit to max_dirs to avoid token limits
            if len(subdirectories) > args.max_dirs:
                print(f"Found {len(subdirectories)} directories, limiting to {args.max_dirs} for analysis")
                subdirectories = subdirectories[:args.max_dirs]
            
            print(f"Found {len(subdirectories)} subdirectories to analyze")
        
        if not subdirectories:
            print("No subdirectories found to analyze")
            return 0
        
        # Perform LLM analysis
        print("Performing LLM analysis...")
        excluded_dirs = analyzer.analyze_directories(str(repo_path), subdirectories, args.already_excluded)
        
        # Display results
        print("\n" + "="*60)
        print("DIRECTORY ANALYSIS RESULTS")
        print("="*60)
        
        # Calculate filtered directories (excluding already excluded ones)
        if args.already_excluded:
            already_excluded_set = set(args.already_excluded)
            filtered_subdirs = [d for d in subdirectories if d not in already_excluded_set]
        else:
            filtered_subdirs = subdirectories
            already_excluded_set = set()
        
        print(f"\nTotal directories found: {len(subdirectories)}")
        if args.already_excluded:
            print(f"Already excluded by human: {len(args.already_excluded)}")
            print(f"Analyzed by LLM: {len(filtered_subdirs)}")
        else:
            print(f"Analyzed by LLM: {len(filtered_subdirs)}")
        print(f"Additional exclusions recommended by LLM: {len(excluded_dirs)}")
        
        # Show already excluded directories
        if args.already_excluded:
            print(f"\nDirectories ALREADY EXCLUDED by human analysis ({len(args.already_excluded)}):")
            for dir_path in sorted(args.already_excluded):
                print(f"  - {dir_path} (human)")
        
        # Show LLM recommended exclusions
        if excluded_dirs:
            print(f"\nDirectories recommended for EXCLUSION by LLM ({len(excluded_dirs)}):")
            for dir_path in sorted(excluded_dirs):
                print(f"  - {dir_path} (LLM)")
        
        # Calculate directories that would be included (for JSON output only, not displayed)
        all_excluded = already_excluded_set.union(set(excluded_dirs))
        included_dirs = [d for d in subdirectories if d not in all_excluded]
        
        # Output JSON for programmatic use
        all_excluded_dirs = list(already_excluded_set.union(set(excluded_dirs)))
        result = {
            "repository": str(repo_path),
            "total_directories": subdirectories,
            "already_excluded_directories": args.already_excluded or [],
            "llm_recommended_exclusions": excluded_dirs,
            "all_excluded_directories": all_excluded_dirs,
            "included_directories": included_dirs,
            "analysis_summary": {
                "total_found": len(subdirectories),
                "already_excluded_count": len(args.already_excluded) if args.already_excluded else 0,
                "llm_analyzed_count": len(filtered_subdirs),
                "llm_excluded_count": len(excluded_dirs),
                "total_excluded_count": len(all_excluded_dirs),
                "included_count": len(included_dirs)
            }
        }
        
        output_file = repo_path / "llm_directory_analysis.json"
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        print(f"\nDetailed results saved to: {output_file}")
        
        return 0
        
    except KeyboardInterrupt:
        print("\nAnalysis interrupted by user")
        return 1
    except Exception as e:
        print(f"Error during analysis: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())