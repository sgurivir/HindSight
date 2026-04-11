"""
Issue markdown file parser.

This module provides functionality to parse issue description markdown files
and extract structured information for ingestion into the vector database.
"""

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from ..deduper.issue import IssueEntry

logger = logging.getLogger("issue_tracking_deduper.issue_parser")


class IssueParser:
    """
    Parser for issue markdown files.
    
    Issue files are expected to follow a naming convention like:
    - rdar_123456789_title_with_underscores.md
    - 123456789_title.md
    
    The content is expected to be markdown with optional sections for
    title, description, component, and keywords.
    """
    
    # Regex patterns for extracting issue ID from filename
    ISSUE_ID_PATTERNS = [
        r'rdar[_-]?(\d{8,12})',  # rdar_123456789 or rdar-123456789
        r'^(\d{8,12})[_-]',      # 123456789_title
        r'(\d{8,12})',           # Just the number anywhere
    ]
    
    # Regex patterns for extracting metadata from content
    TITLE_PATTERNS = [
        r'^#\s+(.+)$',           # # Title
        r'^##\s+(.+)$',          # ## Title
        r'^Title:\s*(.+)$',      # Title: Something
        r'^\*\*Title\*\*:\s*(.+)$',  # **Title**: Something
    ]
    
    COMPONENT_PATTERNS = [
        r'^Component:\s*(.+)$',
        r'^\*\*Component\*\*:\s*(.+)$',
        r'^##\s*Component\s*\n+(.+)$',
    ]
    
    KEYWORDS_PATTERNS = [
        r'^Keywords?:\s*(.+)$',
        r'^\*\*Keywords?\*\*:\s*(.+)$',
        r'^Tags?:\s*(.+)$',
    ]
    
    def __init__(self):
        """Initialize the issue parser."""
        pass
    
    def parse_file(self, file_path: Path) -> Optional[IssueEntry]:
        """
        Parse an issue markdown file.
        
        Args:
            file_path: Path to the markdown file.
        
        Returns:
            IssueEntry if parsing succeeds, None otherwise.
        """
        if not file_path.exists():
            logger.warning(f"File not found: {file_path}")
            return None
        
        if not file_path.is_file():
            logger.warning(f"Not a file: {file_path}")
            return None
        
        # Extract issue ID from filename
        issue_id = self._extract_issue_id(file_path.name)
        if not issue_id:
            logger.warning(f"Could not extract issue ID from: {file_path.name}")
            return None
        
        # Read file content
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return None
        
        # Parse content
        title, description, component, keywords = self._parse_content(content, file_path.stem)
        
        # Create IssueEntry
        issue = IssueEntry(
            issue_id=issue_id,
            title=title,
            description=description,
            component=component,
            keywords=keywords,
            file_path=str(file_path),
        )
        
        # Compute content hash
        issue.content_hash = issue.compute_content_hash()
        
        logger.debug(f"Parsed issue {issue_id}: {title}")
        return issue
    
    def _extract_issue_id(self, filename: str) -> Optional[str]:
        """
        Extract issue ID from filename.
        
        Args:
            filename: The filename to parse.
        
        Returns:
            The issue ID string, or None if not found.
        """
        for pattern in self.ISSUE_ID_PATTERNS:
            match = re.search(pattern, filename, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
    
    def _parse_content(
        self,
        content: str,
        fallback_title: str
    ) -> Tuple[str, str, Optional[str], List[str]]:
        """
        Parse the content of an issue markdown file.
        
        Args:
            content: The markdown content.
            fallback_title: Title to use if none found in content.
        
        Returns:
            Tuple of (title, description, component, keywords).
        """
        lines = content.split('\n')
        
        title = None
        component = None
        keywords = []
        description_lines = []
        
        # Track what we've found
        found_title = False
        in_description = False
        
        for line in lines:
            stripped = line.strip()
            
            # Skip empty lines at the start
            if not stripped and not found_title:
                continue
            
            # Try to extract title
            if not found_title:
                for pattern in self.TITLE_PATTERNS:
                    match = re.match(pattern, stripped, re.IGNORECASE | re.MULTILINE)
                    if match:
                        title = match.group(1).strip()
                        found_title = True
                        break
                
                # If first non-empty line and no title pattern matched, use it as title
                if not found_title and stripped:
                    title = stripped
                    found_title = True
                continue
            
            # Try to extract component
            if not component:
                for pattern in self.COMPONENT_PATTERNS:
                    match = re.match(pattern, stripped, re.IGNORECASE)
                    if match:
                        component = match.group(1).strip()
                        break
                if component:
                    continue
            
            # Try to extract keywords
            if not keywords:
                for pattern in self.KEYWORDS_PATTERNS:
                    match = re.match(pattern, stripped, re.IGNORECASE)
                    if match:
                        keywords_str = match.group(1).strip()
                        # Split by comma, semicolon, or whitespace
                        keywords = [k.strip() for k in re.split(r'[,;\s]+', keywords_str) if k.strip()]
                        break
                if keywords:
                    continue
            
            # Everything else is description
            description_lines.append(line)
        
        # Use fallback title if none found
        if not title:
            # Clean up fallback title (replace underscores with spaces)
            title = re.sub(r'[_-]+', ' ', fallback_title)
            # Remove issue ID prefix if present
            title = re.sub(r'^rdar\s*\d+\s*', '', title, flags=re.IGNORECASE)
            title = title.strip() or "Untitled Issue"
        
        # Clean up description
        description = '\n'.join(description_lines).strip()
        
        # If description is empty, use the full content
        if not description:
            description = content.strip()
        
        return title, description, component, keywords
    
    def parse_directory(
        self,
        directory: Path,
        recursive: bool = True,
        extensions: Optional[List[str]] = None
    ) -> List[IssueEntry]:
        """
        Parse all issue files in a directory.
        
        Args:
            directory: Path to the directory to scan.
            recursive: Whether to scan subdirectories.
            extensions: List of file extensions to include (default: ['.md', '.txt']).
        
        Returns:
            List of parsed IssueEntry objects.
        """
        if extensions is None:
            extensions = ['.md', '.txt', '.markdown']
        
        if not directory.exists():
            logger.error(f"Directory not found: {directory}")
            return []
        
        if not directory.is_dir():
            logger.error(f"Not a directory: {directory}")
            return []
        
        # Find all matching files
        pattern = '**/*' if recursive else '*'
        files = []
        for ext in extensions:
            files.extend(directory.glob(f"{pattern}{ext}"))
        
        logger.info(f"Found {len(files)} files to parse in {directory}")
        
        # Parse each file
        issues = []
        for file_path in sorted(files):
            issue = self.parse_file(file_path)
            if issue:
                issues.append(issue)
        
        logger.info(f"Successfully parsed {len(issues)} issues")
        return issues


def parse_issue_file(file_path: Path) -> Optional[IssueEntry]:
    """
    Convenience function to parse a single issue file.
    
    Args:
        file_path: Path to the markdown file.
    
    Returns:
        IssueEntry if parsing succeeds, None otherwise.
    """
    parser = IssueParser()
    return parser.parse_file(file_path)


def parse_issue_directory(
    directory: Path,
    recursive: bool = True
) -> List[IssueEntry]:
    """
    Convenience function to parse all issue files in a directory.
    
    Args:
        directory: Path to the directory to scan.
        recursive: Whether to scan subdirectories.
    
    Returns:
        List of parsed IssueEntry objects.
    """
    parser = IssueParser()
    return parser.parse_directory(directory, recursive)
