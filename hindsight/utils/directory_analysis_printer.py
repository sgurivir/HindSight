#!/usr/bin/env python3
"""
Directory Analysis Printer
Contains functionality for printing directory analysis results.
Separated from DirectoryTreeUtil to avoid circular imports.
"""

from ..analyzers.directory_classifier import DirectoryClassifier


def print_directory_analysis(repo_path: str) -> None:
    """
    Print the directory analysis results in a formatted way.
    
    Args:
        repo_path: Path to the repository root
    """
    try:
        include_dirs, exclude_dirs = DirectoryClassifier.get_include_and_exclude_directories(repo_path)
        
        print("=== DIRECTORY ANALYSIS RESULTS ===")
        print(f"Repository: {repo_path}")
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


def print_directory_analysis_with_filters(repo_path: str, user_provided_include_list: list[str] = None, user_provided_exclude_list: list[str] = None) -> None:
    """
    Print the directory analysis results with user-provided filters in a formatted way.
    
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