#!/usr/bin/env python3
"""
Lomo Perf Issue Downloader

Downloads issues filed with the "Lomo Perf Found by AI Static Analysis" keyword
to individual markdown files.

Usage:
    python3 issue_downloader.py
    python3 issue_downloader.py -k "custom keyword"
    python3 issue_downloader.py -o ./my_issues
"""

import argparse
from pathlib import Path
from issue_helper import IssueDownloader, get_current_user, DEFAULT_ISSUE_DOWNLOAD_DIR


# Default keyword for Lomo Perf issues
DEFAULT_KEYWORD = "Lomo Perf Found by AI Static Analysis"


def main():
    # Get current user for display
    current_user = get_current_user()
    
    parser = argparse.ArgumentParser(
        description='Download Lomo Perf issues to markdown files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  # Use default keyword
  python3 issue_downloader.py
  
  # Custom keyword
  python3 issue_downloader.py -k "memory leak"
  
  # Custom output directory
  python3 issue_downloader.py -o ./my_issues

Defaults:
  Keyword: "{DEFAULT_KEYWORD}"
  Output: {DEFAULT_ISSUE_DOWNLOAD_DIR}
  Current user: {current_user or '(not detected)'}
  
Note: Issues already downloaded (file exists) will be skipped.
        """
    )
    
    parser.add_argument('--keyword', '-k', type=str, default=DEFAULT_KEYWORD,
                        help=f'Keyword to search for (default: "{DEFAULT_KEYWORD}")')
    parser.add_argument('--output-dir', '-o', type=str, default=None,
                        help=f'Output directory for markdown files (default: {DEFAULT_ISSUE_DOWNLOAD_DIR})')
    parser.add_argument('--rate-limit', type=float, default=0.1,
                        help='Delay between API calls in seconds (default: 0.1)')
    
    args = parser.parse_args()
    
    print(f"Current AppleConnect user: {current_user or '(not detected)'}")
    print(f"Using keyword: '{args.keyword}'")
    
    # Create downloader and download issues
    downloader = IssueDownloader(
        output_dir=args.output_dir,
        client_name='LomoPerfIssueDownloader'
    )
    
    downloaded = downloader.download_issues_by_keyword(
        keyword=args.keyword,
        rate_limit_delay=args.rate_limit
    )
    
    # Summary
    print(f"\n{'='*50}")
    print(f"Downloaded {len(downloaded)} issues to: {downloader.output_dir}/")
    if downloaded:
        print(f"Issue IDs: {', '.join(str(r) for r in downloaded)}")


if __name__ == '__main__':
    main()
