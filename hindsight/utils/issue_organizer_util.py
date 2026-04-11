"""
Utility for organizing issues into directory structure.
Provides a reusable function that can be used by both analyzers and API tasks.
"""
import os
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from ..report.issue_directory_organizer import RepositoryDirHierarchy, DirectoryNode
from ..utils.file_content_provider import FileContentProvider
from ..utils.log_util import get_logger

logger = get_logger(__name__)


def organize_issues_by_directory(
    repo_path: str,
    all_issues: List[Dict[str, Any]],
    file_content_provider: Optional[FileContentProvider] = None,
    pickled_index_path: Optional[str] = None,
    exclude_directories: Optional[List[str]] = None
) -> Tuple[Dict[str, Any], RepositoryDirHierarchy, Any]:
    """
    Organize issues into directory structure using IssueDirectoryOrganizer.
    
    This function provides the same issue organization functionality used by
    CodeAnalysisRunner and TraceAnalysisRunner, but in a reusable form.
    
    Args:
        repo_path: Path to the repository root
        all_issues: List of issue dictionaries to organize
        file_content_provider: Optional FileContentProvider instance
        pickled_index_path: Optional path to pickled index for FileContentProvider
        exclude_directories: Optional list of directory patterns to exclude from organization
        
    Returns:
        Tuple containing:
        - assignment_stats: Dictionary with assignment statistics
        - repo_hierarchy: RepositoryDirHierarchy instance
        - issue_organizer: IssueDirectoryOrganizer instance
    """
    logger.info("Creating repository directory hierarchy for issue organization...")
    
    # Create RepositoryDirHierarchy
    repo_hierarchy = RepositoryDirHierarchy(repo_path, pickled_index_path)
    
    # Create IssueDirectoryOrganizer
    logger.info("Creating issue directory organizer...")
    issue_organizer = repo_hierarchy.create_issue_directory_organizer(file_content_provider)
    
    # Set exclude directories if provided
    if exclude_directories:
        issue_organizer.set_exclude_directories(exclude_directories)
        logger.info(f"Set {len(exclude_directories)} exclude directories for issue organization")
    
    # Assign issues to directories
    logger.info(f"Assigning {len(all_issues)} issues to directories...")
    assignment_stats = issue_organizer.assign_issues_to_directories(all_issues)
    
    logger.info(f"Issue assignment complete: {assignment_stats['assigned']}/{assignment_stats['total_issues']} assigned ({assignment_stats['assignment_rate']:.1f}%)")
    
    return assignment_stats, repo_hierarchy, issue_organizer


def update_issue_file_paths(
    all_issues: List[Dict[str, Any]],
    issue_organizer: Any,
    repo_hierarchy: RepositoryDirHierarchy
) -> None:
    """
    Update file_path for all issues based on their directory assignment.
    
    This replicates the file_path updating logic from CodeAnalysisRunner._generate_report.
    
    Args:
        all_issues: List of issue dictionaries to update
        issue_organizer: IssueDirectoryOrganizer instance
        repo_hierarchy: RepositoryDirHierarchy instance
    """
    logger.info("Updating file_path for issues based on directory assignments...")
    repo_path_str = str(repo_hierarchy.repository_path)
    
    for issue in all_issues:
        # Ensure issue is a dictionary, not a string
        if not isinstance(issue, dict):
            logger.warning(f"Skipping non-dictionary issue: {type(issue)} - {issue}")
            continue
        
        assigned_directory = issue_organizer.get_issue_directory(issue)
        if assigned_directory:
            # Get the directory path for this issue
            directory_path = assigned_directory.get_path()
            # Try multiple possible keys for the filename - preserve original file_path if available
            original_file_path = issue.get('original_file_path', '') or issue.get('file_path', '')
            
            # Extract just the filename from the original file path
            original_file = ''
            if original_file_path:
                if '/' in original_file_path:
                    original_file = original_file_path.split('/')[-1]
                elif '\\' in original_file_path:
                    original_file = original_file_path.split('\\')[-1]
                else:
                    original_file = original_file_path
            
            # Fallback to other possible filename fields if extraction failed
            if not original_file:
                original_file = issue.get('file_name', '') or issue.get('file', '')
            
            # Convert absolute path to relative path from repository root
            if directory_path.startswith(repo_path_str):
                relative_dir = directory_path[len(repo_path_str):].lstrip('/')
                if relative_dir and relative_dir != ".":
                    # Update file_path to include the relative directory path and filename
                    if original_file:
                        issue['file_path'] = f"{relative_dir}/{original_file}"
                    else:
                        # Keep the original file path instead of just the directory
                        issue['file_path'] = original_file_path if original_file_path else relative_dir
                else:
                    # Root directory case
                    issue['file_path'] = original_file if original_file else (original_file_path if original_file_path else 'root')
            else:
                # Fallback to original file if path conversion fails
                issue['file_path'] = original_file if original_file else (original_file_path if original_file_path else 'Unknown')
        else:
            # Unassigned issue - try to construct file_path from available data
            original_file = issue.get('file_name', '') or issue.get('file', '')
            if original_file:
                issue['file_path'] = original_file
            else:
                issue['file_path'] = 'Unknown'


def create_unknown_directory_for_unassigned_issues(
    issue_organizer: Any,
    repo_hierarchy: RepositoryDirHierarchy
) -> Optional[DirectoryNode]:
    """
    Create an "Unknown" directory node for unassigned issues.
    
    This replicates the Unknown directory creation logic from CodeAnalysisRunner._generate_report.
    
    Args:
        issue_organizer: IssueDirectoryOrganizer instance
        repo_hierarchy: RepositoryDirHierarchy instance
        
    Returns:
        DirectoryNode for "Unknown" directory if unassigned issues exist, None otherwise
    """
    # Get unassigned issues
    unassigned_issues = issue_organizer.get_unassigned_issues()
    if not unassigned_issues:
        return None
    
    logger.info(f"Creating 'Unknown' directory for {len(unassigned_issues)} unassigned issues")
    
    # Mark unassigned issues so they can be identified in reports
    for issue in unassigned_issues:
        # Ensure issue is a dictionary, not a string
        if not isinstance(issue, dict):
            logger.warning(f"Skipping non-dictionary unassigned issue: {type(issue)} - {issue}")
            continue
        issue['directory_assigned'] = False
        issue['file_path'] = 'Unknown'  # Set file_path to Unknown for directory categorization
    
    # Create a virtual "Unknown" directory node
    unknown_node = DirectoryNode(name="Unknown", path="Unknown")
    
    # Add all unassigned issues to the Unknown directory
    for issue in unassigned_issues:
        # Only add dictionary issues to the unknown node
        if isinstance(issue, dict):
            unknown_node.add_issue(issue)
    
    # Add the Unknown directory to the root node
    root_node = repo_hierarchy.get_root_node()
    if root_node:
        root_node.add_directory(unknown_node)
        logger.info(f"Added 'Unknown' directory with {len(unassigned_issues)} issues to root directory")
    
    return unknown_node


def organize_issues_complete(
    repo_path: str,
    all_issues: List[Dict[str, Any]],
    file_content_provider: Optional[FileContentProvider] = None,
    pickled_index_path: Optional[str] = None,
    update_file_paths: bool = True,
    create_unknown_directory: bool = True,
    exclude_directories: Optional[List[str]] = None
) -> Tuple[Dict[str, Any], RepositoryDirHierarchy, Any, Optional[DirectoryNode]]:
    """
    Complete issue organization workflow including directory assignment, file path updates, and unknown directory creation.
    
    This is a convenience function that combines all the issue organization steps used by the analyzers.
    
    Args:
        repo_path: Path to the repository root
        all_issues: List of issue dictionaries to organize
        file_content_provider: Optional FileContentProvider instance
        pickled_index_path: Optional path to pickled index for FileContentProvider
        update_file_paths: Whether to update file_path fields based on directory assignment
        create_unknown_directory: Whether to create an "Unknown" directory for unassigned issues
        exclude_directories: Optional list of directory patterns to exclude from organization
        
    Returns:
        Tuple containing:
        - assignment_stats: Dictionary with assignment statistics
        - repo_hierarchy: RepositoryDirHierarchy instance
        - issue_organizer: IssueDirectoryOrganizer instance
        - unknown_node: DirectoryNode for "Unknown" directory (if created), None otherwise
    """
    # Step 1: Organize issues by directory
    assignment_stats, repo_hierarchy, issue_organizer = organize_issues_by_directory(
        repo_path, all_issues, file_content_provider, pickled_index_path, exclude_directories
    )
    
    # Step 2: Update file paths if requested
    if update_file_paths:
        update_issue_file_paths(all_issues, issue_organizer, repo_hierarchy)
    
    # Step 3: Create unknown directory if requested
    unknown_node = None
    if create_unknown_directory:
        unknown_node = create_unknown_directory_for_unassigned_issues(issue_organizer, repo_hierarchy)
    
    return assignment_stats, repo_hierarchy, issue_organizer, unknown_node