#!/usr/bin/env python3
"""
Generate a repository configuration JSON file for Hindsight analysis.

This script automatically creates a config JSON for any repository by:
1. Extracting project name from the repository path
2. Using LLM to identify test/third-party/generated directories to exclude
3. Generating a complete config JSON with sensible defaults

Usage:
    python -m dev.generate_repo_config --repo ~/src/my-project
    python -m dev.generate_repo_config --repo ~/src/my-project --output my-project.json
    python -m dev.generate_repo_config --repo ~/src/my-project --model "aws:anthropic.claude-sonnet-4-5-20250929-v1:0"
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from hindsight.analyzers.directory_classifier import DirectoryClassifier, LLMBasedDirectoryClassifier
from hindsight.utils.config_util import get_api_key_from_config, get_llm_provider_type
from hindsight.utils.api_key_util import get_api_key
from hindsight.utils.log_util import setup_default_logging, get_logger

# Initialize logging
setup_default_logging()
logger = get_logger(__name__)

# Default LLM configuration (from almanacapps.json)
DEFAULT_API_ENDPOINT = "https://floodgate.g.apple.com/api/openai/v1/chat/completions"
DEFAULT_MODEL = "aws:anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_LLM_PROVIDER_TYPE = "aws_bedrock"
DEFAULT_MIN_FUNCTION_BODY_LENGTH = 7
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Desktop")


def get_project_name_from_repo(repo_path: str) -> str:
    """
    Extract a human-readable project name from the repository path.
    
    Args:
        repo_path: Path to the repository
        
    Returns:
        str: Human-readable project name
    """
    # Get the base directory name
    repo_name = os.path.basename(repo_path.rstrip('/'))
    
    # Convert common naming conventions to readable format
    # e.g., "my-project" -> "My Project", "my_project" -> "My Project"
    readable_name = repo_name.replace('-', ' ').replace('_', ' ')
    
    # Title case the name
    readable_name = readable_name.title()
    
    return readable_name


def get_static_exclude_directories(repo_path: str, 
                                   include_directories: Optional[List[str]] = None) -> List[str]:
    """
    Get static exclude directories using DirectoryClassifier (no LLM).
    
    Args:
        repo_path: Path to the repository
        include_directories: Optional list of directories to include
        
    Returns:
        List[str]: List of directories to exclude
    """
    try:
        exclude_dirs = DirectoryClassifier.get_recommended_exclude_directories_safe(
            repo_path,
            user_provided_include_list=include_directories,
            user_provided_exclude_list=None
        )
        return exclude_dirs
    except Exception as e:
        logger.warning(f"Failed to get static exclude directories: {e}")
        return []


def get_llm_exclude_directories(repo_path: str,
                                api_url: str,
                                model: str,
                                llm_provider_type: str,
                                static_excludes: List[str],
                                include_directories: Optional[List[str]] = None) -> List[str]:
    """
    Use LLM to identify additional directories to exclude.
    
    Args:
        repo_path: Path to the repository
        api_url: LLM API endpoint
        model: LLM model identifier
        llm_provider_type: LLM provider type
        static_excludes: Already identified static exclusions
        include_directories: Optional list of directories to include
        
    Returns:
        List[str]: Additional directories to exclude (from LLM analysis)
    """
    try:
        # Create config dict for LLMBasedDirectoryClassifier
        config = {
            "api_end_point": api_url,
            "model": model,
            "llm_provider_type": llm_provider_type,
            "credentials": ""  # Will use Apple Connect token fallback
        }
        
        # Create LLM-based classifier
        classifier = LLMBasedDirectoryClassifier.from_config(config)
        
        # Analyze directories, passing static excludes as already excluded
        llm_excludes = classifier.analyze_directories(
            repo_path,
            subdirectories=None,  # Will discover all directories
            already_excluded_directories=static_excludes,
            user_provided_include_list=include_directories
        )
        
        return llm_excludes
        
    except Exception as e:
        logger.warning(f"LLM directory analysis failed: {e}")
        logger.info("Falling back to static exclusions only")
        return []


def generate_config(repo_path: str,
                    api_url: str = DEFAULT_API_ENDPOINT,
                    model: str = DEFAULT_MODEL,
                    llm_provider_type: str = DEFAULT_LLM_PROVIDER_TYPE,
                    include_directories: Optional[List[str]] = None,
                    skip_llm: bool = False) -> Dict[str, Any]:
    """
    Generate a complete repository configuration.
    
    Args:
        repo_path: Path to the repository
        api_url: LLM API endpoint
        model: LLM model identifier
        llm_provider_type: LLM provider type
        include_directories: Optional list of directories to include
        skip_llm: If True, skip LLM analysis and use only static exclusions
        
    Returns:
        Dict[str, Any]: Complete configuration dictionary
    """
    # Resolve and validate repo path
    repo_path = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(repo_path):
        raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")
    
    logger.info(f"Generating config for repository: {repo_path}")
    
    # Get project name
    project_name = get_project_name_from_repo(repo_path)
    logger.info(f"Project name: {project_name}")
    
    # Step 1: Get static exclude directories
    logger.info("Step 1: Analyzing directory structure (static analysis)...")
    static_excludes = get_static_exclude_directories(repo_path, include_directories)
    logger.info(f"Static analysis found {len(static_excludes)} directories to exclude")
    
    # Step 2: Use LLM to identify additional exclusions
    all_excludes = list(static_excludes)
    
    if not skip_llm:
        logger.info("Step 2: Using LLM to identify test/third-party/generated directories...")
        llm_excludes = get_llm_exclude_directories(
            repo_path,
            api_url,
            model,
            llm_provider_type,
            static_excludes,
            include_directories
        )
        
        if llm_excludes:
            logger.info(f"LLM analysis found {len(llm_excludes)} additional directories to exclude")
            # Combine static and LLM exclusions (avoid duplicates)
            all_excludes = list(set(static_excludes) | set(llm_excludes))
        else:
            logger.info("LLM analysis did not find additional directories to exclude")
    else:
        logger.info("Step 2: Skipping LLM analysis (--skip-llm flag)")
    
    # Sort exclude directories for consistent output
    all_excludes.sort()
    
    logger.info(f"Total directories to exclude: {len(all_excludes)}")
    
    # Build configuration dictionary
    config = {
        "project_name": project_name,
        "description": "",
        
        "api_end_point": api_url,
        "model": model,
        "llm_provider_type": llm_provider_type,
        "credentials": "",
        
        "exclude_directories": all_excludes,
        "include_directories": include_directories or [],
        "exclude_files": [],
        
        "min_function_body_length": DEFAULT_MIN_FUNCTION_BODY_LENGTH,
        
        "user_prompts": []
    }
    
    return config


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate a repository configuration JSON file for Hindsight analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate config for a repository (saves to ~/Desktop/my-project.json)
    python -m dev.generate_repo_config --repo ~/src/my-project
    
    # Generate config and save to a specific file
    python -m dev.generate_repo_config --repo ~/src/my-project --output /path/to/my-project.json
    
    # Use a different model
    python -m dev.generate_repo_config --repo ~/src/my-project --model "aws:anthropic.claude-sonnet-4-5-20250929-v1:0"
    
    # Skip LLM analysis (faster, uses only static directory analysis)
    python -m dev.generate_repo_config --repo ~/src/my-project --skip-llm
    
    # Specify directories to include (will not be excluded)
    python -m dev.generate_repo_config --repo ~/src/my-project --include-directories src lib
        """
    )
    
    parser.add_argument(
        "--repo", "-r",
        required=True,
        help="Path to the repository to generate config for"
    )
    
    parser.add_argument(
        "--output", "-o",
        help=f"Output file path for the generated config JSON. If not specified, saves to ~/Desktop/<repo_name>.json"
    )
    
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_ENDPOINT,
        help=f"LLM API endpoint URL (default: {DEFAULT_API_ENDPOINT})"
    )
    
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LLM model identifier (default: {DEFAULT_MODEL})"
    )
    
    parser.add_argument(
        "--llm-provider-type",
        default=DEFAULT_LLM_PROVIDER_TYPE,
        choices=["aws_bedrock", "claude", "dummy"],
        help=f"LLM provider type (default: {DEFAULT_LLM_PROVIDER_TYPE})"
    )
    
    parser.add_argument(
        "--include-directories",
        nargs="+",
        help="List of directories that must be included (will not be excluded)"
    )
    
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip LLM analysis and use only static directory analysis (faster but less accurate)"
    )
    
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress informational output (only print the JSON config)"
    )
    
    args = parser.parse_args()
    
    # Suppress logging if quiet mode
    if args.quiet:
        import logging
        logging.getLogger().setLevel(logging.ERROR)
    
    try:
        # Generate configuration
        config = generate_config(
            repo_path=args.repo,
            api_url=args.api_url,
            model=args.model,
            llm_provider_type=args.llm_provider_type,
            include_directories=args.include_directories,
            skip_llm=args.skip_llm
        )
        
        # Format JSON output
        json_output = json.dumps(config, indent=4)
        
        # Determine output path
        if args.output:
            output_path = os.path.abspath(args.output)
        else:
            # Default to ~/Desktop/<repo_name>.json
            repo_name = os.path.basename(args.repo.rstrip('/'))
            output_path = os.path.join(DEFAULT_OUTPUT_DIR, f"{repo_name}.json")
        
        # Write to file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json_output)
            f.write('\n')
        
        # Always print the path where config was saved
        print(f"\nConfiguration saved to: {output_path}")
        
        return 0
        
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
