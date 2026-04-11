#!/usr/bin/env python3
"""
Issue Helper Module

Provides common functionality for interacting with Apple's Radar system:
- Authentication
- Querying issues by keyword
- Downloading issue content to markdown files

This module is designed to be imported by other scripts.

Installation:
    python3 -m pip install --user -i https://pypi.apple.com/simple radarclient
    python3 -m pip install --user tqdm
"""

import re
from pathlib import Path
from time import sleep
from typing import List, Optional

# Default output directory for downloaded issues
DEFAULT_ISSUE_DOWNLOAD_DIR = Path.home() / "issues_on_file"

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm not installed. Install with: pip install tqdm")
    def tqdm(iterable, **kwargs):
        return iterable

try:
    import radarclient
    from radarclient import RadarClient, ClientSystemIdentifier, AppleDirectoryQuery
    # Use AuthenticationStrategyAppleConnect (AuthenticationStrategySPNego is deprecated)
    try:
        from radarclient import AuthenticationStrategyAppleConnect
    except ImportError:
        from radarclient import AuthenticationStrategySPNego as AuthenticationStrategyAppleConnect
except ImportError:
    print("radarclient not installed.")
    print("Install with: python3 -m pip install --user -i https://pypi.apple.com/simple radarclient")
    raise


def get_current_user() -> Optional[str]:
    """
    Get the currently logged in AppleConnect username.
    
    Returns:
        Username string or None if not detected
    """
    try:
        accounts = AppleDirectoryQuery.logged_in_appleconnect_accounts()
        if accounts:
            return accounts[0].username
    except Exception:
        pass
    return None


def sanitize_filename(title: str, max_length: int = 50) -> str:
    """
    Sanitize issue title for use as filename.
    
    Args:
        title: Issue title
        max_length: Maximum length of filename (excluding extension)
    
    Returns:
        Sanitized filename string
    """
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', title)
    sanitized = re.sub(r'[\s_]+', '_', sanitized)
    sanitized = sanitized.strip('_')
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip('_')
    return sanitized


def format_description(description_items) -> str:
    """
    Format description items into a string.
    
    Args:
        description_items: Iterable of description entries
    
    Returns:
        Formatted description string
    """
    parts = []
    for item in description_items:
        text = str(item).strip()
        if text:
            parts.append(text)
    return '\n\n'.join(parts) if parts else '*No description available*'


def create_markdown_content(issue_id: int, title: str, description: str,
                            state: str = None, component: str = None,
                            keywords: list = None, reported_by: str = None) -> str:
    """
    Create markdown content for an issue.
    
    Args:
        issue_id: Issue ID
        title: Issue title
        description: Formatted description
        state: Issue state (optional)
        component: Component name (optional)
        keywords: List of keywords (optional)
        reported_by: Reporter username (optional)
    
    Returns:
        Markdown formatted string
    """
    content = f"""# rdar://{issue_id}

## {title}

| Field | Value |
|-------|-------|
| **Issue ID** | {issue_id} |
| **URL** | rdar://{issue_id} |
"""
    
    if state:
        content += f"| **State** | {state} |\n"
    
    if component:
        content += f"| **Component** | {component} |\n"
    
    if keywords:
        content += f"| **Keywords** | {', '.join(keywords)} |\n"
    
    if reported_by:
        content += f"| **Reported By** | {reported_by} |\n"
    
    content += f"""
---

## Description

{description}
"""
    
    return content


class IssueDownloader:
    """
    Downloads issues to individual markdown files.
    
    Handles authentication, querying, and file management.
    """
    
    def __init__(self, output_dir: str = None, client_name: str = 'IssueDownloader'):
        """
        Initialize the IssueDownloader.
        
        Args:
            output_dir: Directory to save markdown files (default: ~/issues_on_file/)
            client_name: Name to identify this client to the Radar API
        """
        system_identifier = ClientSystemIdentifier(client_name, '1.0')
        self.client = RadarClient(AuthenticationStrategyAppleConnect(), system_identifier)
        
        # Use default output directory if not specified
        if output_dir is None:
            self.output_dir = DEFAULT_ISSUE_DOWNLOAD_DIR
        else:
            self.output_dir = Path(output_dir)
        
        # Create directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Output directory: {self.output_dir}")
    
    def find_issues_by_keyword(self, keyword: str) -> List[int]:
        """
        Find issue IDs matching a keyword.
        
        Args:
            keyword: Keyword to search for
        
        Returns:
            List of issue IDs
        """
        # Try API v2.1 format first
        query = {'keyword': [keyword]}
        print(f"Searching for issues with keyword: '{keyword}'")
        print(f"Query: {query}")
        
        try:
            issue_ids = self.client.find_radar_ids(query)
            print(f"Found {len(issue_ids)} issues with keyword '{keyword}'")
            return issue_ids
        except Exception as e:
            print(f"Error with API v2.1 query: {e}")
            print("\nTrying API v2.2+ format...")
            
            # Try API v2.2+ format
            try:
                query = {'keyword': {'like': keyword}}
                print(f"Retry query: {query}")
                issue_ids = self.client.find_radar_ids(query)
                print(f"Found {len(issue_ids)} issues with keyword '{keyword}'")
                return issue_ids
            except Exception as e2:
                print(f"Error with API v2.2+ query: {e2}")
                print("\nCheck radarclient docs for correct query field names.")
                return []
    
    def download_issue(self, issue_id: int) -> Optional[str]:
        """
        Download a single issue to a markdown file.
        
        Args:
            issue_id: Issue ID to download
        
        Returns:
            Filepath of downloaded file, or None if skipped/failed
        """
        # Check if file already exists for this issue ID
        existing_files = list(self.output_dir.glob(f"rdar_{issue_id}_*.md"))
        if existing_files:
            return None  # Already exists
        
        try:
            issue = self.client.radar_for_id(issue_id)
            
            title = issue.title or f"Issue {issue_id}"
            
            # Get reporter/originator username
            reporter = None
            if hasattr(issue, 'originator') and issue.originator:
                try:
                    if isinstance(issue.originator, dict):
                        reporter = issue.originator.get('username', issue.originator.get('name', str(issue.originator)))
                    elif hasattr(issue.originator, 'username'):
                        reporter = issue.originator.username
                    else:
                        reporter = str(issue.originator)
                except:
                    reporter = str(issue.originator)
            
            # Get description
            description_text = "*No description available*"
            if hasattr(issue, 'description') and issue.description:
                try:
                    description_text = format_description(issue.description.items())
                except Exception as desc_error:
                    error_msg = str(desc_error)
                    if '403' in error_msg or 'permission' in error_msg.lower():
                        description_text = "*Description unavailable - API permission required*\n\n> **Note:** Your account needs the 'Get Problem Description' API endpoint permission.\n> Request access at: https://radar.apple.com/portal/profiles"
                    else:
                        description_text = f"*Error reading description: {type(desc_error).__name__}*"
            
            # Get component name
            comp_name = None
            if hasattr(issue, 'component') and issue.component:
                comp_name = issue.component.get('name', '')
            
            # Get state
            issue_state = getattr(issue, 'state', None)
            
            # Get keywords if available
            issue_keywords = None
            if hasattr(issue, 'keywords'):
                try:
                    issue_keywords = list(issue.keywords) if issue.keywords else None
                except:
                    pass
            
            # Create markdown content
            md_content = create_markdown_content(
                issue_id=issue_id,
                title=title,
                description=description_text,
                state=issue_state,
                component=comp_name,
                keywords=issue_keywords,
                reported_by=reporter
            )
            
            # Create filename
            safe_title = sanitize_filename(title)
            filename = f"rdar_{issue_id}_{safe_title}.md"
            filepath = self.output_dir / filename
            
            # Write file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(md_content)
            
            return str(filepath)
            
        except Exception as e:
            print(f"\n  Error downloading issue {issue_id}: {e}")
            return None
    
    def download_issues_by_keyword(
        self,
        keyword: str,
        reported_by: List[str] = None,
        rate_limit_delay: float = 0.1
    ) -> List[int]:
        """
        Download all issues matching a keyword.
        
        Args:
            keyword: Keyword to search for
            reported_by: List of usernames to filter by (optional, currently disabled)
            rate_limit_delay: Delay between API calls in seconds
        
        Returns:
            List of downloaded issue IDs
        """
        issue_ids = self.find_issues_by_keyword(keyword)
        
        if not issue_ids:
            print("No issues found.")
            return []
        
        downloaded = []
        skipped_count = 0
        already_exists_count = 0
        
        for issue_id in tqdm(issue_ids, desc="Processing issues"):
            # Check if file already exists
            existing_files = list(self.output_dir.glob(f"rdar_{issue_id}_*.md"))
            if existing_files:
                already_exists_count += 1
                continue
            
            filepath = self.download_issue(issue_id)
            if filepath:
                downloaded.append(issue_id)
            
            sleep(rate_limit_delay)
        
        if already_exists_count > 0:
            print(f"Skipped {already_exists_count} issues (already downloaded)")
        
        return downloaded
