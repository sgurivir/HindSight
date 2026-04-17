#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Project Summary Generator Module
Generates individual file summaries and maintains a summary index database
"""

import os
import shelve
import time
import json
import re
import pickle
import shutil
import argparse
import sys
import tempfile
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from .reasoning_order_generator import ReasoningOrderGenerator
from ..llm.llm import Claude, ClaudeConfig
from ..constants import DEFAULT_LLM_MODEL, DEFAULT_LLM_API_END_POINT
from ...utils.api_key_util import get_api_key
from ...utils.artifacts import get_repo_artifacts_dir
from ...utils.config_util import load_config_tolerant
from ...utils.file_util import write_file, read_file
from ...utils.log_util import get_logger
from ...utils.output_directory_provider import get_output_directory_provider
from ...utils.hash_util import HashUtil
from ...report.issue_directory_organizer import RepositoryDirHierarchy



logger = get_logger(__name__)

# Constants
MAX_FILE_SIZE_MB = 10
SUPPORTED_EXTENSIONS = {
    '.java', '.cpp', '.c', '.h', '.hpp', '.cc', '.cxx',
    '.m', '.mm', '.swift', '.go', '.kt'
}

EXCLUDE_PATTERNS = {
    '.git', '.svn', '.hg', '__pycache__', 'node_modules', '.DS_Store',
    '.pyc', '.class', '.o', '.obj', '.exe', '.dll', '.so', '.dylib',
    '.tmp', '.temp', '.swp', '.log', 'build', 'dist', 'target'
}


@dataclass
class SummaryConfig:
    """Configuration for project summarization"""
    repo_path: str
    summary_dir: str
    api_key: str
    api_url: str
    model: str
    ignore_dirs: set = None
    max_tokens: int = 64000
    temperature: float = 0.1
    merged_functions_json_path: str = None


class ProjectSummaryGenerator:
    """
    Generates individual file summaries on-demand and maintains a summary index database.
    No longer generates summaries on startup - summaries are created when requested by LLM.
    Implemented as a singleton to ensure consistent configuration across the application.
    """

    _instance = None
    _initialized = False

    def __new__(cls, config: SummaryConfig = None):
        if cls._instance is None:
            cls._instance = super(ProjectSummaryGenerator, cls).__new__(cls)
        return cls._instance

    def __init__(self, config: SummaryConfig = None):
        # Only initialize once
        if self._initialized:
            return

        if config is None:
            raise ValueError("ProjectSummaryGenerator requires SummaryConfig for first initialization")
        """
        Initialize ProjectSummaryGenerator with configuration.

        Args:
            config: Summary configuration including summary_dir
        """
        self.config = config
        self.repo_path = Path(config.repo_path).resolve()
        self.summary_dir = Path(config.summary_dir).resolve()

        # Create summary directory if it doesn't exist
        self.summary_dir.mkdir(parents=True, exist_ok=True)

        # Store Claude config but don't create client yet (created per-session)
        self.claude_config = ClaudeConfig(
            api_key=config.api_key,
            api_url=config.api_url,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            provider_type=getattr(config, 'provider_type', 'aws_bedrock')
        )

        # Set default ignore directories if not provided
        if config.ignore_dirs is None:
            self.ignore_dirs = {
                'Tools', 'Tests', 'Test', 'External', 'protobufs', 'bin', 'scripts',
                'ProtocolBuffers', 'ProtobufDefs', 'NewUIKitTests', 'CarPlayArtwork',
                'UIKitMacHelper', 'Artwork', 'Documents', '.git', '__pycache__',
                'node_modules', 'build', 'dist', 'target'
            }
        else:
            self.ignore_dirs = config.ignore_dirs

        # Path to summary index database (shelve will add .db extension automatically)
        self.index_db_path = self.summary_dir / "summary_index"

        # Load directory structure for file finding
        self.structure_pkl_path = self._get_structure_pkl_path()

        # Load function context from merged_functions.json if provided
        self.function_context = {}
        if config.merged_functions_json_path and os.path.exists(config.merged_functions_json_path):
            self.function_context = self._load_function_context(config.merged_functions_json_path)
            logger.info(f"Loaded function context for {len(self.function_context)} files from {config.merged_functions_json_path}")
        else:
            logger.info("No merged_functions.json provided or file not found - proceeding without function context")

        # Mark as initialized
        self._initialized = True

        logger.info(f"Initialized ProjectSummaryGenerator singleton for repo: {self.repo_path}")
        logger.info(f"Summary directory: {self.summary_dir}")
        logger.info(f"Structure pkl path: {self.structure_pkl_path}")

    @classmethod
    def get_instance(cls):
        """
        Get the singleton instance of ProjectSummaryGenerator.

        Returns:
            ProjectSummaryGenerator: The singleton instance

        Raises:
            RuntimeError: If the singleton has not been initialized yet
        """
        if cls._instance is None or not cls._instance._initialized:
            raise RuntimeError(
                "ProjectSummaryGenerator singleton has not been initialized. "
                "Call ProjectSummaryGenerator(config) first or use initialize_singleton()."
            )
        return cls._instance

    @classmethod
    def initialize_singleton(cls, config: SummaryConfig):
        """
        Initialize the ProjectSummaryGenerator singleton with configuration.

        Args:
            config: SummaryConfig instance with all necessary configuration

        Returns:
            ProjectSummaryGenerator: The initialized singleton instance
        """
        if cls._instance is not None and cls._instance._initialized:
            logger.warning("ProjectSummaryGenerator singleton already initialized, returning existing instance")
            return cls._instance

        return cls(config)

    @classmethod
    def is_initialized(cls):
        """
        Check if the singleton has been initialized.

        Returns:
            bool: True if initialized, False otherwise
        """
        return cls._instance is not None and cls._instance._initialized

    def _get_structure_pkl_path(self) -> str:
        """
        Get the path to the structure.pkl file for directory structure lookup.

        Returns:
            str: Path to structure.pkl file
        """
        # Use the OutputDirectoryProvider singleton to get the correct artifacts directory
        output_provider = get_output_directory_provider()
        artifacts_dir = output_provider.get_repo_artifacts_dir()
        structure_pkl_path = f"{artifacts_dir}/directory_structure/structure.pkl"
        return structure_pkl_path

    def _find_file_in_structure(self, filename: str) -> Optional[Path]:
        """
        Find a file in the repository using the structure.pkl file.
        LLM may not have guessed the path correctly, so we search for the file.

        Args:
            filename: Name of the file to find (may include partial path)

        Returns:
            Path: Full path to the file if found, None otherwise
        """
        try:
            # Extract just the filename if a path was provided
            if '/' in filename:
                search_filename = filename.split('/')[-1]
            elif '\\' in filename:
                search_filename = filename.split('\\')[-1]
            else:
                search_filename = filename

            # Try to load the structure.pkl file
            if os.path.exists(self.structure_pkl_path):
                try:
                    with open(self.structure_pkl_path, 'rb') as f:
                        structure_data = pickle.load(f)

                    # Search through the structure for the file
                    found_paths = []
                    self._search_structure_recursive(structure_data, search_filename, found_paths)

                    if len(found_paths) == 1:
                        # Single match found
                        return Path(found_paths[0])
                    elif len(found_paths) > 1:
                        # Multiple matches - try to disambiguate using original filename
                        logger.warning(f"Multiple matches found for {search_filename}: {found_paths}")
                        # If original filename had path info, try to match it
                        if filename != search_filename:
                            for path in found_paths:
                                if filename in path:
                                    return Path(path)
                        # Return first match if no disambiguation possible
                        return Path(found_paths[0])
                    else:
                        logger.warning(f"File {search_filename} not found in structure.pkl")

                except Exception as e:
                    logger.error(f"Error reading structure.pkl: {e}")
            else:
                logger.warning(f"Structure.pkl not found at {self.structure_pkl_path}")

            # Fallback: search filesystem directly
            return self._search_filesystem_for_file(search_filename)

        except Exception as e:
            logger.error(f"Error finding file {filename}: {e}")
            return None

    def _search_structure_recursive(self, structure_data: dict, filename: str, found_paths: list, current_path: str = ""):
        """
        Recursively search through structure data for a filename.

        Args:
            structure_data: Dictionary containing directory structure
            filename: Filename to search for
            found_paths: List to append found paths to
            current_path: Current path being searched
        """
        if isinstance(structure_data, dict):
            # Check if this level has files
            if 'files' in structure_data:
                for file_info in structure_data['files']:
                    if isinstance(file_info, str):
                        file_name = file_info
                    elif isinstance(file_info, dict):
                        file_name = file_info.get('name', '')
                    else:
                        continue

                    if file_name == filename:
                        full_path = os.path.join(current_path, file_name) if current_path else file_name
                        found_paths.append(str(self.repo_path / full_path))

            # Recursively search subdirectories
            for key, value in structure_data.items():
                if key != 'files' and isinstance(value, dict):
                    new_path = os.path.join(current_path, key) if current_path else key
                    self._search_structure_recursive(value, filename, found_paths, new_path)

    def _search_filesystem_for_file(self, filename: str) -> Optional[Path]:
        """
        Fallback method to search filesystem directly for a file.

        Args:
            filename: Name of the file to search for

        Returns:
            Path: Full path to the file if found, None otherwise
        """
        try:
            found_paths = []
            for root, dirs, files in os.walk(self.repo_path):
                # Skip ignored directories
                dirs[:] = [d for d in dirs if d not in self.ignore_dirs]

                if filename in files:
                    found_paths.append(Path(root) / filename)

            if len(found_paths) == 1:
                return found_paths[0]
            elif len(found_paths) > 1:
                logger.warning(f"Multiple filesystem matches found for {filename}: {found_paths}")
                return found_paths[0]  # Return first match
            else:
                logger.error(f"File {filename} not found in filesystem")
                return None

        except Exception as e:
            logger.error(f"Error searching filesystem for {filename}: {e}")
            return None

    def _get_directory_structure_for_file(self, file_path: Path) -> str:
        """
        Get directory structure tree for a file's location.

        Args:
            file_path: Path to the file

        Returns:
            str: Directory structure as a tree string
        """
        try:

            # Create hierarchy and get structure for the file's directory
            hierarchy = RepositoryDirHierarchy(str(self.repo_path))

            # Get relative path from repo root
            try:
                rel_path = file_path.relative_to(self.repo_path)
                dir_path = rel_path.parent if rel_path.parent != Path('.') else Path('')

                if dir_path == Path(''):
                    # File is in root directory
                    return hierarchy.get_tree_structure(max_depth=3)
                else:
                    # Get structure for the specific directory
                    dir_structure = hierarchy.get_directory_hierarchy_by_path(str(dir_path))
                    if dir_structure:
                        return dir_structure
                    else:
                        # Fallback to full structure
                        return hierarchy.get_tree_structure(max_depth=3)

            except ValueError:
                # File path is not relative to repo path
                return hierarchy.get_tree_structure(max_depth=3)

        except Exception as e:
            logger.error(f"Error getting directory structure for {file_path}: {e}")
            return f"Error getting directory structure: {e}"

    def _should_process_file(self, file_path: Path) -> bool:
        """
        Check if a file should be processed for summarization.

        Args:
            file_path: Path to the file

        Returns:
            bool: True if file should be processed
        """
        # Check if file is in ignored directory
        if any(part in self.ignore_dirs for part in file_path.parts):
            return False

        # Check file extension
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS and file_path.name not in SUPPORTED_EXTENSIONS:
            return False

        # Check for excluded patterns
        if any(pattern in file_path.name for pattern in EXCLUDE_PATTERNS):
            return False

        # Check file size
        try:
            if file_path.stat().st_size > MAX_FILE_SIZE_MB * 1024 * 1024:
                logger.warning(f"Skipping large file: {file_path} ({file_path.stat().st_size / (1024*1024):.1f}MB)")
                return False
        except OSError:
            return False

        return True

    def _load_function_context(self, merged_functions_json_path: str) -> Dict[str, List[Dict]]:
        """
        Load function context from merged_functions.json file.

        Args:
            merged_functions_json_path: Path to the merged_functions.json file

        Returns:
            Dict mapping file paths to list of function contexts
        """
        try:
            with open(merged_functions_json_path, 'r', encoding='utf-8') as f:
                functions_data = json.load(f)

            # Group functions by file path
            file_functions = {}
            for func_data in functions_data:
                if 'context' in func_data and 'file' in func_data['context']:
                    file_path = func_data['context']['file']
                    if file_path not in file_functions:
                        file_functions[file_path] = []

                    # Extract function information
                    func_info = {
                        'name': func_data.get('name', ''),
                        'start_line': func_data['context'].get('start', 0),
                        'end_line': func_data['context'].get('end', 0),
                        'type': func_data.get('type', 'function')
                    }
                    file_functions[file_path].append(func_info)

            logger.info(f"Loaded function context for {len(file_functions)} files")
            return file_functions

        except Exception as e:
            logger.error(f"Error loading function context from {merged_functions_json_path}: {e}")
            return {}

    def _sanitize_function_name(self, function_name: str) -> str:
        """
        Sanitize function name to be safe for SQLite table names.

        Args:
            function_name: Original function name

        Returns:
            str: Sanitized function name safe for SQLite
        """
        if not function_name:
            return "unknown_function"

        # Replace problematic characters with underscores
        # Keep alphanumeric characters, underscores, and basic punctuation
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', function_name)

        # Ensure it doesn't start with a number
        if sanitized and sanitized[0].isdigit():
            sanitized = f"func_{sanitized}"

        # Limit length to reasonable size
        if len(sanitized) > 100:
            sanitized = sanitized[:100]

        return sanitized or "unknown_function"

    def _get_file_functions(self, file_path: Path) -> List[Dict]:
        """
        Get function context for a specific file.

        Args:
            file_path: Path to the file

        Returns:
            List of function context dictionaries
        """
        try:
            # Try both absolute and relative paths
            abs_path = str(file_path.resolve())

            # Handle potential path resolution issues with symlinks
            try:
                rel_path = str(file_path.relative_to(self.repo_path))
            except ValueError:
                # If relative_to fails, try with resolved paths
                try:
                    # Try to get relative path without resolving symbolic links first
                    rel_path = str(file_path.relative_to(self.repo_path))
                except ValueError:
                    # If that fails, try with resolved paths
                    try:
                        rel_path = str(file_path.resolve().relative_to(self.repo_path.resolve()))
                    except ValueError:
                        # If still fails, use just the filename
                        rel_path = file_path.name

            # Check both paths in function context
            functions = self.function_context.get(abs_path, [])
            if not functions:
                functions = self.function_context.get(rel_path, [])

            # Also try with forward slashes (in case of path separator differences)
            if not functions:
                rel_path_forward = rel_path.replace('\\', '/')
                functions = self.function_context.get(rel_path_forward, [])

            return functions

        except Exception as e:
            logger.debug(f"Error getting file functions for {file_path}: {e}")
            return []

    def _get_files_in_order(self) -> List[Path]:
        """
        Get files in dependency-aware order using ReasoningOrderGenerator.

        Returns:
            List[Path]: Ordered list of files to process
        """
        logger.info("Generating dependency-aware file order...")

        try:
            # Use ReasoningOrderGenerator to get ordered files
            ordered_files = ReasoningOrderGenerator.ordered_files(
                self.repo_path,
                ignore_dirs=self.ignore_dirs
            )

            # If no files found with default extensions, try with all supported extensions
            if not ordered_files:
                logger.info("No files found with default extensions, trying with all supported extensions...")
                # Create a set of extensions that ReasoningOrderGenerator can handle
                reasoning_exts = set()
                for ext in SUPPORTED_EXTENSIONS:
                    if ext.startswith('.'):
                        reasoning_exts.add(ext)

                # Build graph with all supported extensions
                graph = ReasoningOrderGenerator.build_graph(
                    self.repo_path,
                    exts=reasoning_exts,
                    ignore_dirs=self.ignore_dirs
                )

                if graph:
                    comps = ReasoningOrderGenerator.scc_tarjan(graph)
                    ordered_groups = ReasoningOrderGenerator.topo_of_components(graph, comps)
                    ordered_files = [f for group in ordered_groups for f in sorted(group)]

            # Filter files that should be processed
            processable_files = [f for f in ordered_files if self._should_process_file(f)]

            logger.info(f"Found {len(processable_files)} files to process (from {len(ordered_files)} total)")
            return processable_files

        except Exception as e:
            logger.error(f"Error generating file order: {e}")
            # Fallback to simple directory traversal
            return self._get_files_simple()

    def _get_files_simple(self) -> List[Path]:
        """
        Fallback method to get files using simple directory traversal.

        Returns:
            List[Path]: List of files to process
        """
        logger.info("Using simple file traversal as fallback...")
        files = []

        for root, dirs, filenames in os.walk(self.repo_path):
            # Remove ignored directories from dirs list to prevent traversal
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs]

            for filename in filenames:
                file_path = Path(root) / filename
                if self._should_process_file(file_path):
                    files.append(file_path)

        return sorted(files)

    def _calculate_md5(self, file_path: Path) -> str:
        """
        Calculate MD5 hash of a file.

        Args:
            file_path: Path to the file

        Returns:
            str: MD5 hash of the file
        """
        result = HashUtil.hash_for_file_md5(file_path)
        if not result:
            logger.error(f"Error calculating MD5 for {file_path}")
        return result

    def _generate_summary_filename(self, file_path: Path) -> str:
        """
        Generate summary filename based on the new naming convention.

        Args:
            file_path: Original file path

        Returns:
            str: Summary filename
        """
        # Get relative path from repo root
        rel_path = file_path.relative_to(self.repo_path)

        # Split path components
        path_parts = list(rel_path.parts[:-1])  # All parts except filename
        filename = rel_path.parts[-1]

        # Get file extension
        if '.' in filename:
            name, ext = filename.rsplit('.', 1)
            ext_part = ext
        else:
            name = filename
            ext_part = ""

        # Build summary filename: summary_x_y_z_a_ext.txt
        if path_parts:
            path_str = "_".join(path_parts)
            if ext_part:
                summary_name = f"summary_{path_str}_{name}_{ext_part}.txt"
            else:
                summary_name = f"summary_{path_str}_{name}.txt"
        else:
            if ext_part:
                summary_name = f"summary_{name}_{ext_part}.txt"
            else:
                summary_name = f"summary_{name}.txt"

        return summary_name

    def _load_prompt_template(self, prompt_name: str) -> str:
        """
        Load prompt template from prompts directory.

        Args:
            prompt_name: Name of the prompt file (without .md extension)

        Returns:
            str: Prompt template content
        """
        try:
            # Get the path to the hindsight module's prompts directory
            current_file = Path(__file__)
            prompts_dir = current_file.parent.parent / "prompts"
            prompt_path = prompts_dir / f"{prompt_name}.md"

            if prompt_path.exists():
                return read_file(str(prompt_path))
            else:
                logger.warning(f"Prompt template not found: {prompt_path}")
                return ""
        except Exception as e:
            logger.error(f"Error loading prompt template {prompt_name}: {e}")
            return ""

    def _generate_file_summary(self, file_path: Path) -> Optional[str]:
        """
        Generate enhanced summary for a single file using LLM with new Claude session.
        Creates a new Claude session for each summary generation and closes it afterward.

        Args:
            file_path: Path to the file to summarize

        Returns:
            str: Generated summary or None on error
        """
        claude_session = None
        try:
            # Read file content
            file_content = read_file(str(file_path))
            if not file_content:
                directory_structure = self._get_directory_structure_for_file(file_path)
                return f"# Enhanced Summary for {file_path.name}\n\n## Overall Functionality\nEmpty file with no content\n\n## Functions and Methods\n```json\n{{\"functions\": []}}\n```\n\n## Key Components\n- No components found\n\n## Dependencies and Imports\n- No imports found\n\n## Directory Structure Context\n```\n{directory_structure}\n```\n\n## Role in Codebase\nPlaceholder or unused file"

            # Create new Claude session for this summary
            claude_session = Claude(self.claude_config)
            logger.debug(f"Created new Claude session for {file_path.name}")

            # Load enhanced prompt template
            prompt_template = self._load_prompt_template("generateEnhancedFileSummary")

            # Get directory structure for this file
            directory_structure = self._get_directory_structure_for_file(file_path)

            # Create messages for Claude
            system_prompt = prompt_template if prompt_template else "You are a code analysis assistant. Generate comprehensive file summaries with function details in JSON format."

            # Get relative path for context
            try:
                rel_path = file_path.relative_to(self.repo_path)
            except ValueError:
                rel_path = file_path.name

            user_prompt = f"""Please analyze this file and generate an enhanced summary:

**File**: {file_path.name}
**Path**: {rel_path}

**Content**:
```
{file_content}
```

**Directory Structure Context**:
```
{directory_structure}
```

Generate a comprehensive summary following the format specified in the system prompt. Include:
1. Overall functionality description
2. All functions/methods with exact line numbers in JSON format
3. Key components and dependencies
4. Directory structure context
5. Role in the larger codebase

Make sure the JSON for functions is valid and includes accurate line numbers."""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # Check token limits before sending
            if not claude_session.check_token_limit(system_prompt, user_prompt):
                logger.error(f"Enhanced file summary request exceeds token limits for {file_path}")
                logger.error(f"System prompt: {len(system_prompt):,} chars, User prompt: {len(user_prompt):,} chars")
                return None

            # Send to Claude with system cache enabled
            response = claude_session.send_message(messages, enable_system_cache=True, cache_ttl="1h")
            if response and response.get("choices"):
                content = response["choices"][0].get("message", {}).get("content", "")
                logger.info(f"Successfully generated enhanced summary for {file_path.name}")
                return content.strip()

            logger.error(f"No valid response received for {file_path.name}")
            return None

        except Exception as e:
            logger.error(f"Error generating enhanced summary for {file_path}: {e}")
            return None
        finally:
            # Always close the Claude session
            if claude_session:
                try:
                    # Claude sessions don't have explicit close method, but we can clear references
                    claude_session = None
                    logger.debug(f"Closed Claude session for {file_path.name}")
                except Exception as e:
                    logger.warning(f"Error closing Claude session for {file_path.name}: {e}")

    def _save_file_summary(self, file_path: Path, summary: str) -> Optional[str]:
        """
        Save file summary to summary directory with new naming convention.

        Args:
            file_path: Original file path
            summary: Generated summary

        Returns:
            str: Relative path to summary file if saved successfully, None otherwise
        """
        try:
            # Generate summary filename
            summary_filename = self._generate_summary_filename(file_path)
            summary_path = self.summary_dir / summary_filename

            # Write summary
            success = write_file(str(summary_path), summary)
            if success:
                logger.debug(f"Saved summary: {summary_path}")
                # Return relative path from summary_dir
                return summary_filename

            return None

        except Exception as e:
            logger.error(f"Error saving summary for {file_path}: {e}")
            return None

    def _update_summary_index(self, file_path: Path, md5_hash: str, summary_file: str) -> bool:
        """
        Update the summary index database.

        Args:
            file_path: Original file path
            md5_hash: MD5 hash of the file
            summary_file: Relative path to summary file

        Returns:
            bool: True if updated successfully
        """
        try:
            # Get relative path from repo root as key
            rel_path = str(file_path.relative_to(self.repo_path))

            # Get function context for this file
            functions = self._get_file_functions(file_path)
            function_context = []

            for func in functions:
                sanitized_name = self._sanitize_function_name(func.get('name', ''))
                function_context.append({
                    'function_name': sanitized_name,
                    'original_name': func.get('name', ''),
                    'start_line': func.get('start_line', 0),
                    'end_line': func.get('end_line', 0),
                    'type': func.get('type', 'function')
                })

            # Open shelve database
            with shelve.open(str(self.index_db_path)) as db:
                db[rel_path] = {
                    "md5": md5_hash,
                    "summary": summary_file,
                    "last_commit": "",  # Leave empty for now as requested
                    "functions": function_context
                }

            return True

        except Exception as e:
            logger.error(f"Error updating summary index for {file_path}: {e}")
            return False

    def _file_needs_update(self, file_path: Path) -> bool:
        """
        Check if a file needs summary update based on MD5 hash.

        Args:
            file_path: Path to the file

        Returns:
            bool: True if file needs update
        """
        try:
            rel_path = str(file_path.relative_to(self.repo_path))
            current_md5 = self._calculate_md5(file_path)

            # Open shelve database to check existing entry
            with shelve.open(str(self.index_db_path)) as db:
                if rel_path in db:
                    stored_info = db[rel_path]
                    stored_md5 = stored_info.get("md5", "")
                    summary_file = stored_info.get("summary", "")

                    # Check if MD5 matches and summary file exists
                    if stored_md5 == current_md5:
                        summary_path = self.summary_dir / summary_file
                        if summary_path.exists():
                            return False  # No update needed

            return True  # Update needed

        except Exception as e:
            logger.error(f"Error checking if file needs update {file_path}: {e}")
            return True  # Assume update needed on error
    def get_file_summary(self, relative_file_path: str, include_functions: bool = True) -> Optional[str]:
        """
        Get summary for a specific file by its relative path.

        Args:
            relative_file_path: Relative path from repo root
            include_functions: Whether to include function information in the summary

        Returns:
            str: File summary content with optional function information, or None if not found
        """
        try:
            with shelve.open(str(self.index_db_path)) as db:
                if relative_file_path in db:
                    stored_info = db[relative_file_path]
                    summary_file = stored_info.get("summary", "")
                    functions = stored_info.get("functions", [])

                    if summary_file:
                        summary_path = self.summary_dir / summary_file
                        if summary_path.exists():
                            summary_content = read_file(str(summary_path))

                            # Add function information if requested and available
                            if include_functions and functions:
                                function_info = self._format_function_info(functions)
                                summary_content = f"{summary_content}\n\n**Functions in this file:**\n{function_info}"

                            return summary_content
            return None
        except Exception as e:
            logger.error(f"Error retrieving summary for {relative_file_path}: {e}")
            return None

    def _format_function_info(self, functions: List[Dict]) -> str:
        """
        Format function information for inclusion in summaries.

        Args:
            functions: List of function context dictionaries

        Returns:
            str: Formatted function information
        """
        if not functions:
            return "No functions found."

        function_lines = []
        for func in functions:
            name = func.get('original_name', func.get('function_name', 'unknown'))
            start_line = func.get('start_line', 0)
            end_line = func.get('end_line', 0)
            func_type = func.get('type', 'function')

            if start_line and end_line:
                function_lines.append(f"- {name} ({func_type}): lines {start_line}-{end_line}")
            else:
                function_lines.append(f"- {name} ({func_type})")

        return "\n".join(function_lines)


    def get_file_summary_on_demand(self, filename: str) -> Optional[str]:
        """
        Get summary for a file on-demand. This is the main method called by LLM.
        First finds the file (LLM may not have guessed path correctly), then checks
        if summary exists and is up-to-date based on MD5 hash, and generates new
        summary if needed using a new Claude session.

        Args:
            filename: Name of the file to get summary for (may include partial path)

        Returns:
            str: File summary content or None if generation failed
        """
        try:
            logger.info(f"LLM requested summary for file: {filename}")

            # Step 1: Find the file using structure.pkl or filesystem search
            target_file = self._find_file_in_structure(filename)
            if not target_file:
                logger.error(f"Could not find file: {filename}")
                return None

            logger.info(f"Found file at: {target_file}")

            # Step 2: Check if file should be processed
            if not self._should_process_file(target_file):
                logger.warning(f"File not suitable for processing: {target_file}")
                return f"File {filename} is not suitable for summarization (unsupported type or too large)"

            # Step 3: Check if summary exists and is up-to-date (MD5 hash check)
            if not self._file_needs_update(target_file):
                logger.info(f"Using cached summary for: {target_file.relative_to(self.repo_path)}")
                # Get existing summary from cache
                rel_path = str(target_file.relative_to(self.repo_path))
                cached_summary = self.get_file_summary(rel_path)
                if cached_summary:
                    return cached_summary
                else:
                    logger.warning(f"Cached summary not found, will regenerate")

            # Step 4: Generate new summary with new Claude session
            logger.info(f"Generating new summary for: {target_file.relative_to(self.repo_path)}")

            # Calculate MD5 hash
            md5_hash = self._calculate_md5(target_file)
            if not md5_hash:
                logger.error(f"Failed to calculate MD5 for {target_file}")
                return None

            # Generate summary using new Claude session
            summary = self._generate_file_summary(target_file)
            if not summary:
                logger.warning(f"Failed to generate summary for {target_file}, continuing execution")
                return None

            # Step 5: Save summary and update index
            summary_file = self._save_file_summary(target_file, summary)
            if summary_file:
                # Update index
                if self._update_summary_index(target_file, md5_hash, summary_file):
                    logger.info(f"Successfully generated and cached summary for: {target_file.relative_to(self.repo_path)}")
                    return summary
                else:
                    logger.warning(f"Failed to update index for {target_file}, but returning summary")
                    return summary
            else:
                logger.warning(f"Failed to save summary for {target_file}, but returning generated content")
                return summary

        except Exception as e:
            logger.warning(f"Error generating summary for {filename}: {e}")
            logger.info("Continuing execution despite summary generation failure")
            return None

    def generate_file_summary_by_path(self, file_path: str) -> Optional[str]:
        """
        Generate summary for a specific file by its path.
        If hash of file has changed since last generation, the summary will be regenerated.

        Args:
            file_path: Absolute or relative path to the file

        Returns:
            str: File summary content or None if generation failed
        """
        try:
            # Convert to Path object and resolve
            if os.path.isabs(file_path):
                target_file = Path(file_path)
            else:
                target_file = self.repo_path / file_path

            # Check if file exists
            if not target_file.exists():
                logger.error(f"File not found: {target_file}")
                return None

            # Check if file should be processed
            if not self._should_process_file(target_file):
                logger.warning(f"File not suitable for processing: {target_file}")
                return None

            # Check if file needs update (this handles caching based on MD5 hash)
            if not self._file_needs_update(target_file):
                logger.info(f"Using cached summary for: {target_file.relative_to(self.repo_path)}")
                # Get existing summary from cache
                rel_path = str(target_file.relative_to(self.repo_path))
                return self.get_file_summary(rel_path)

            logger.info(f"Generating summary for: {target_file.relative_to(self.repo_path)}")

            # Calculate MD5 hash
            md5_hash = self._calculate_md5(target_file)
            if not md5_hash:
                logger.error(f"Failed to calculate MD5 for {target_file}")
                return None

            # Generate summary
            summary = self._generate_file_summary(target_file)
            if not summary:
                logger.error(f"Failed to generate summary for {target_file}")
                return None

            # Save summary
            summary_file = self._save_file_summary(target_file, summary)
            if not summary_file:
                logger.error(f"Failed to save summary for {target_file}")
                return None

            # Update index
            if self._update_summary_index(target_file, md5_hash, summary_file):
                logger.info(f"Successfully generated and cached summary for: {target_file.relative_to(self.repo_path)}")
                return summary
            else:
                logger.error(f"Failed to update index for {target_file}")
                return None

        except Exception as e:
            logger.error(f"Error generating summary for {file_path}: {e}")
            return None

    def generate_summaries(self, force_regenerate: bool = False) -> Dict[str, Any]:
        """
        Generate summaries for all files in the repository.

        Args:
            force_regenerate: Force regeneration of all summaries even if they exist

        Returns:
            Dict: Summary of the summarization process
        """
        logger.info("Starting project summarization process...")
        start_time = time.time()

        results = {
            'files_processed': 0,
            'files_successful': 0,
            'files_skipped': 0,
            'total_time': 0,
            'errors': []
        }

        try:
            # Get files in dependency order
            ordered_files = self._get_files_in_order()
            logger.info(f"Generating summary for {len(ordered_files)} files...")

            # Process each file
            for i, file_path in enumerate(ordered_files, 1):
                results['files_processed'] += 1

                try:
                    # Check if file needs update (unless force regeneration)
                    if not force_regenerate and not self._file_needs_update(file_path):
                        logger.info(f"[{i}/{len(ordered_files)}] Skipping {file_path.relative_to(self.repo_path)} (up to date)")
                        results['files_skipped'] += 1
                        results['files_successful'] += 1
                        continue

                    logger.info(f"[{i}/{len(ordered_files)}] Summarizing: {file_path.relative_to(self.repo_path)}")

                    # Calculate MD5 hash
                    md5_hash = self._calculate_md5(file_path)
                    if not md5_hash:
                        logger.warning(f"Failed to calculate MD5 for {file_path}, skipping...")
                        results['errors'].append(f"Failed to calculate MD5 for {file_path}")
                        continue

                    # Generate summary
                    summary = self._generate_file_summary(file_path)
                    if not summary:
                        logger.warning(f"Failed to generate summary for {file_path}, skipping...")
                        results['errors'].append(f"Failed to generate summary for {file_path}")
                        continue

                    # Save summary
                    summary_file = self._save_file_summary(file_path, summary)
                    if not summary_file:
                        logger.warning(f"Failed to save summary for {file_path}, skipping...")
                        results['errors'].append(f"Failed to save summary for {file_path}")
                        continue

                    # Only update index if summary was successfully generated and saved
                    if self._update_summary_index(file_path, md5_hash, summary_file):
                        results['files_successful'] += 1
                        logger.debug(f"Successfully processed and indexed: {file_path.relative_to(self.repo_path)}")
                    else:
                        logger.warning(f"Failed to update index for {file_path}, but continuing...")
                        results['errors'].append(f"Failed to update index for {file_path}")
                        # Still count as successful since summary was generated and saved
                        results['files_successful'] += 1

                except Exception as e:
                    # Catch any unexpected errors and continue processing
                    logger.warning(f"Unexpected error processing {file_path.relative_to(self.repo_path)}: {e}")
                    results['errors'].append(f"Unexpected error processing {file_path}: {e}")
                    continue

            # Calculate total time
            results['total_time'] = time.time() - start_time

            # Log final results
            logger.info("=" * 60)
            logger.info("SUMMARIZATION COMPLETED")
            logger.info("=" * 60)
            logger.info(f"Files processed: {results['files_successful']}/{results['files_processed']}")
            logger.info(f"Files skipped: {results['files_skipped']}")
            logger.info(f"Total time: {results['total_time']:.2f} seconds")
            if results['errors']:
                logger.warning(f"Errors encountered: {len(results['errors'])}")
            logger.info("=" * 60)

            return results

        except Exception as e:
            logger.error(f"Error during summarization process: {e}")
            results['errors'].append(f"Unexpected error: {e}")
            results['total_time'] = time.time() - start_time
            return results

    @staticmethod
    def dump_summary_database(db_path: str) -> None:
        """
        Dump summary database entries to console for inspection.

        Args:
            db_path: Path to the summary index database (without .db extension)
        """
        try:

            db_file = Path(db_path)

            # Check if database exists
            if not db_file.with_suffix('.db').exists():
                print(f"❌ Database not found: {db_file.with_suffix('.db')}")
                return

            print(f"📊 Summary Database: {db_file.with_suffix('.db')}")
            print("=" * 80)

            # Open and read the database
            with shelve.open(str(db_file)) as db:
                if not db:
                    print("📭 Database is empty")
                    return

                print(f"📈 Total entries: {len(db)}")
                print("=" * 80)

                # Sort entries by file path for better readability
                sorted_entries = sorted(db.items())

                for i, (file_path, entry_data) in enumerate(sorted_entries, 1):
                    print(f"\n{i:3d}. 📄 {file_path}")
                    print(f"     🔍 MD5: {entry_data.get('md5', 'N/A')}")
                    print(f"     📝 Summary: {entry_data.get('summary', 'N/A')}")
                    print(f"     🔄 Last Commit: {entry_data.get('last_commit', 'N/A')}")

                    # Display function information if available
                    functions = entry_data.get('functions', [])
                    if functions:
                        print(f"     🔧 Functions: {len(functions)} found")
                        for func in functions[:3]:  # Show first 3 functions
                            name = func.get('original_name', func.get('function_name', 'unknown'))
                            start_line = func.get('start_line', 0)
                            end_line = func.get('end_line', 0)
                            if start_line and end_line:
                                print(f"       • {name}: lines {start_line}-{end_line}")
                            else:
                                print(f"       • {name}")
                        if len(functions) > 3:
                            print(f"       ... and {len(functions) - 3} more functions")
                    else:
                        print(f"     🔧 Functions: None found")

                    # Check if summary file exists
                    if entry_data.get('summary'):
                        summary_dir = db_file.parent
                        summary_file = summary_dir / entry_data['summary']
                        if summary_file.exists():
                            file_size = summary_file.stat().st_size
                            print(f"     ✅ Summary file exists ({file_size} bytes)")
                        else:
                            print(f"     ❌ Summary file missing: {summary_file}")

                print("\n" + "=" * 80)
                print(f"📊 Summary: {len(db)} files tracked in database")

        except Exception as e:
            print(f"❌ Error reading database: {e}")

    @staticmethod
    def inspect_summary_file(summary_dir: str, file_path: str) -> None:
        """
        Inspect a specific summary file content.

        Args:
            summary_dir: Directory containing summary files
            file_path: Relative path of the original file to inspect summary for
        """
        try:
            summary_dir_path = Path(summary_dir)

            # Generate expected summary filename
            rel_path = Path(file_path)
            path_parts = list(rel_path.parts[:-1])  # All parts except filename
            filename = rel_path.parts[-1]

            # Get file extension
            if '.' in filename:
                name, ext = filename.rsplit('.', 1)
                ext_part = ext
            else:
                name = filename
                ext_part = ""

            # Build summary filename: summary_x_y_z_a_ext.txt
            if path_parts:
                path_str = "_".join(path_parts)
                if ext_part:
                    summary_name = f"summary_{path_str}_{name}_{ext_part}.txt"
                else:
                    summary_name = f"summary_{path_str}_{name}.txt"
            else:
                if ext_part:
                    summary_name = f"summary_{name}_{ext_part}.txt"
                else:
                    summary_name = f"summary_{name}.txt"

            summary_file = summary_dir_path / summary_name

            print(f"🔍 Inspecting summary for: {file_path}")
            print(f"📁 Expected summary file: {summary_file}")
            print("=" * 80)

            if summary_file.exists():
                content = read_file(str(summary_file))
                if content:
                    print("📝 Summary Content:")
                    print("-" * 40)
                    print(content)
                    print("-" * 40)
                    print(f"📊 Summary length: {len(content)} characters")
                else:
                    print("❌ Summary file is empty")
            else:
                print("❌ Summary file not found")

        except Exception as e:
            print(f"❌ Error inspecting summary file: {e}")


def generate_file_summary_to_tmp(config_path: str, relative_file_path: str) -> None:
    """
    Generate summary for a specific file and save it to /tmp/summary_<filename>.txt

    Args:
        config_path: Path to the configuration file
        relative_file_path: Relative path of the file to generate summary for
    """
    try:
        print(f"🔍 Generating summary for: {relative_file_path}")
        print(f"📁 Configuration file: {config_path}")

        # Load and validate configuration
        try:
            config = load_config_tolerant(config_path)
        except Exception as e:
            print(f"❌ Failed to load configuration file: {e}")
            return

        # Extract repository path from config
        repo_path = config.get('path_to_repo')
        if not repo_path:
            print("❌ Configuration file missing 'path_to_repo' field")
            return

        # Validate repository path
        repo_path_obj = Path(repo_path).resolve()
        if not repo_path_obj.exists():
            print(f"❌ Repository path does not exist: {repo_path}")
            return

        if not repo_path_obj.is_dir():
            print(f"❌ Repository path is not a directory: {repo_path}")
            return

        # Validate file path
        file_path = repo_path_obj / relative_file_path
        if not file_path.exists():
            print(f"❌ File does not exist: {file_path}")
            return

        if not file_path.is_file():
            print(f"❌ Path is not a file: {file_path}")
            return

        print(f"📁 Repository: {repo_path}")

        # Get API configuration from config file or fallback
        from ...utils.config_util import get_api_key_from_config
        api_key = get_api_key_from_config(config)
        if not api_key:
            # Try environment variable first, then fallback to Apple Connect
            api_key = os.environ.get('ANTHROPIC_API_KEY')
            if not api_key:
                print("🔑 API key not found in config or environment, trying Apple Connect fallback...")
                api_key = get_api_key()

        if not api_key:
            print("❌ API key not found. Please set 'credentials' in config file, ANTHROPIC_API_KEY environment variable, or ensure Apple Connect is configured.")
            return

        # Get API URL and model from config
        api_url = config.get('api_end_point', DEFAULT_LLM_API_END_POINT)
        model = config.get('model', DEFAULT_LLM_MODEL)

        # Get exclude directories from config
        ignore_dirs = set(config.get('exclude_directories', []))

        # Create temporary summary directory for this operation
        temp_summary_dir = tempfile.mkdtemp(prefix="hindsight_summary_")

        try:
            # Create SummaryConfig
            summary_config = SummaryConfig(
                repo_path=str(repo_path_obj),
                summary_dir=temp_summary_dir,
                api_key=api_key,
                api_url=api_url,
                model=model,
                ignore_dirs=ignore_dirs,
                max_tokens=64000,
                temperature=0.1
            )

            # Create temporary ProjectSummaryGenerator (this is a standalone utility function)
            # Note: This bypasses the singleton pattern for this specific utility use case
            generator = ProjectSummaryGenerator.__new__(ProjectSummaryGenerator)
            generator.__init__(summary_config)

            print("🤖 Generating summary using LLM...")

            # Generate summary for the specific file
            summary = generator.generate_file_summary_by_path(relative_file_path)

            if summary:
                # Extract filename for output file
                filename = Path(relative_file_path).name
                output_file = f"/tmp/summary_{filename}.txt"

                # Write summary to /tmp
                success = write_file(output_file, summary)
                if success:
                    print(f"✅ Summary generated successfully!")
                    print(f"📄 Output file: {output_file}")
                    print(f"📊 Summary length: {len(summary)} characters")
                else:
                    print(f"❌ Failed to write summary to {output_file}")
            else:
                print("❌ Failed to generate summary")

        finally:
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_summary_dir)
            except Exception as e:
                print(f"⚠️  Warning: Failed to clean up temporary directory {temp_summary_dir}: {e}")

    except Exception as e:
        print(f"❌ Error generating summary: {e}")
        traceback.print_exc()


def main():
    """
    Main function for command-line usage of ProjectSummaryGenerator.
    Provides utilities to inspect summary databases and files, and generate summaries for specific files.
    """

    parser = argparse.ArgumentParser(
        description="ProjectSummaryGenerator Database Inspector and Summary Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dump all entries from a summary database
  python -m hindsight.core.proj_util.ProjectSummaryGenerator --dump-db <artifacts_dir>/MyProject/project_summary/summary_index

  # Inspect a specific summary file
  python -m hindsight.core.proj_util.ProjectSummaryGenerator --inspect-summary <artifacts_dir>/MyProject/project_summary src/main.cpp

  # Generate summary for a specific file
  python -m hindsight.core.proj_util.ProjectSummaryGenerator --config /path/to/config.json --generate_summary_for src/main.cpp
        """
    )

    parser.add_argument(
        "--dump-db",
        help="Path to summary database (without .db extension) to dump entries"
    )

    parser.add_argument(
        "--inspect-summary",
        nargs=2,
        metavar=("SUMMARY_DIR", "FILE_PATH"),
        help="Inspect summary file content. Args: summary_directory original_file_path"
    )

    parser.add_argument(
        "--config",
        help="Path to configuration file for summary generation"
    )

    parser.add_argument(
        "--generate_summary_for",
        help="Relative path of file to generate summary for (requires --config)"
    )

    args = parser.parse_args()

    if args.dump_db:
        ProjectSummaryGenerator.dump_summary_database(args.dump_db)
    elif args.inspect_summary:
        summary_dir, file_path = args.inspect_summary
        ProjectSummaryGenerator.inspect_summary_file(summary_dir, file_path)
    elif args.config and args.generate_summary_for:
        generate_file_summary_to_tmp(args.config, args.generate_summary_for)
    elif args.config or args.generate_summary_for:
        print("Error: Both --config and --generate_summary_for are required when using summary generation")
        parser.print_help()
        sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
