#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Hindsight Report Generator
Reads all files from llm output directory and generates an HTML report
"""

import os
import sys
import json
import glob
import re
from pathlib import Path
from datetime import datetime
from collections import Counter

from ..utils.file_util import read_json_file

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

DEFAULT_HTML_REPORT = f"hindsight_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

def read_llm_output_files(directory, file_suffix="_analysis.json"):
    """Read all analysis JSON files from the given directory"""
    all_issues = []
    file_pattern = os.path.join(directory, f"*{file_suffix}")
    files_processed = 0

    for file_path in glob.glob(file_pattern):
        data = read_json_file(file_path)
        if data is not None:
            # Handle new schema format with metadata wrapper
            if isinstance(data, dict) and 'results' in data:
                # New schema format - extract results array and add metadata to each issue
                results = data.get('results', [])
                file_path_meta = data.get('file_path', '')
                function_meta = data.get('function', '')
                checksum_meta = data.get('checksum', '')

                if isinstance(results, list):
                    # Add metadata to each result item
                    for result in results:
                        if isinstance(result, dict):
                            # Add metadata if not already present
                            if not result.get('file_path') and file_path_meta:
                                result['file_path'] = file_path_meta
                            # Always preserve the original file path from metadata
                            if file_path_meta:
                                result['original_file_path'] = file_path_meta
                            if not result.get('function') and function_meta:
                                result['function'] = function_meta
                            if not result.get('checksum') and checksum_meta:
                                result['checksum'] = checksum_meta
                            all_issues.append(result)
                elif isinstance(results, dict):
                    # Single result item
                    if not results.get('file_path') and file_path_meta:
                        results['file_path'] = file_path_meta
                    # Always preserve the original file path from metadata
                    if file_path_meta:
                        results['original_file_path'] = file_path_meta
                    if not results.get('function') and function_meta:
                        results['function'] = function_meta
                    if not results.get('checksum') and checksum_meta:
                        results['checksum'] = checksum_meta
                    all_issues.append(results)
                files_processed += 1
            else:
                print(f"Warning: Unexpected data type {type(data)} in {file_path}, skipping")
                continue
        else:
            print(f"Warning: Could not parse {file_path}")
            continue

    print(f"Processed {files_processed} files with data")
    return all_issues

def extract_directory_from_file(file_path):
    """Extract directory from file path"""
    if not file_path:
        return "uncategorized"

    # Handle special "Unknown" directory for unassigned issues
    if file_path == "Unknown":
        return "Unknown"

    # Handle different path formats
    if '/' in file_path:
        directory = '/'.join(file_path.split('/')[:-1])
        return directory if directory else "root"
    elif '\\' in file_path:
        directory = '\\'.join(file_path.split('\\')[:-1])
        return directory if directory else "root"
    else:
        # For strings without path separators, we need to distinguish between:
        # 1. Directory names (e.g., "common", "daemon") - return as-is
        # 2. File names (e.g., "file.c") - return "root"
        trimmed = file_path.strip()
        if not trimmed:
            return "uncategorized"

        # If it looks like a file (has an extension), treat as root-level file
        if '.' in trimmed and not trimmed.startswith('.'):
            return "root"

        # Otherwise, treat as directory name
        return trimmed

def build_directory_tree(issues):
    """Build a hierarchical directory tree from issues"""
    tree = {}
    directory_counts = Counter()

    # First pass: count all directories
    for issue in issues:
        file_path = issue.get('file_path', '') or issue.get('file', '')
        directory = extract_directory_from_file(file_path)
        directory_counts[directory] += 1

    # Second pass: build tree structure properly
    # Each issue should only be counted once at its deepest directory level
    # But parent directories should show the sum of all their children
    for issue in issues:
        file_path = issue.get('file_path', '') or issue.get('file', '')
        directory = extract_directory_from_file(file_path)

        if directory == "uncategorized" or directory == "root" or directory == "Unknown":
            continue

        # Split directory path into parts
        parts = directory.split('/')

        # Navigate through the tree and create nodes as needed
        current = tree
        for _, part in enumerate(parts):
            if part not in current:
                current[part] = {'_count': 0, '_children': {}}
            current = current[part]['_children']

        # Increment count at the deepest level (the actual directory containing the file)
        # Navigate to the deepest directory node (not its children)
        current = tree
        for part in parts:
            if part in current:
                current = current[part]
            elif '_children' in current and part in current['_children']:
                current = current['_children'][part]
            else:
                # This shouldn't happen if the above logic is correct, but let's be safe
                break
        else:
            # Only increment if we successfully navigated to the end
            current['_count'] += 1

    # Third pass: propagate counts up the tree (parent directories get sum of children)
    def propagate_counts(node_dict):
        for _, data in node_dict.items():
            if isinstance(data, dict) and '_children' in data:
                children_dict = data['_children']
                if children_dict:
                    # Recursively propagate counts in children first
                    propagate_counts(children_dict)
                    # Add children counts to this node's count
                    for _, child_data in children_dict.items():
                        if isinstance(child_data, dict) and '_count' in child_data:
                            data['_count'] += child_data['_count']

    propagate_counts(tree)

    return tree, dict(directory_counts)

def calculate_stats(issues):
    """Calculate statistics from the issues"""
    # Use 'severity' field from the schema
    severity_counts = Counter(issue.get('severity', 'unknown') for issue in issues)

    # Build directory tree and get counts
    directory_tree, directory_counts = build_directory_tree(issues)

    return {
        'total': len(issues),
        'critical': severity_counts.get('critical', 0),
        'high': severity_counts.get('high', 0),
        'medium': severity_counts.get('medium', 0),
        'low': severity_counts.get('low', 0),
        'directories': directory_counts,
        'directory_tree': directory_tree
    }

def generate_html_report(issues, output_file=DEFAULT_HTML_REPORT, project_name=None, analysis_type="Code Analysis"):
    """Generate HTML report"""
    stats = calculate_stats(issues)

    # Use project name in filename if provided and output_file is default
    if project_name and output_file == DEFAULT_HTML_REPORT:
        repository_text = f"<strong>Repository:</strong> {project_name.replace(' - Trace Analysis', '')}"
        date_text = f"<strong>Date:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"
        project_name_for_file = project_name.replace(' ', '_')
        output_file = f"{project_name_for_file}_{output_file}"
    elif project_name:
        repository_text = f"<strong>Repository:</strong> {project_name.replace(' - Trace Analysis', '')}"
        date_text = f"<strong>Date:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"
    else:
        repository_text = f"<strong>Repository:</strong> Unknown"
        date_text = f"<strong>Date:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"

    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HindSight analysis report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display",
                         "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif;
            background: #f5f5f7; /* flat, Apple-like */
            min-height: 100vh;
            display: flex;
            overflow-x: hidden;
            color: #1d1d1f; /* Apple body text */
        }}

        .sidebar {{
            width: 320px;
            background: #ffffff;
            color: #1d1d1f;
            padding: 20px;
            box-shadow: 0 0 0 1px rgba(0,0,0,0.06); /* subtle hairline */
            position: fixed;
            height: 100vh;
            overflow-y: auto;
            overflow-x: hidden;
        }}

        .sidebar h3 {{
            margin-bottom: 20px;
            font-size: 1.2em;
            color: #1d1d1f;
            border-bottom: 1px solid rgba(0,0,0,0.08);
            padding-bottom: 10px;
            font-weight: 600;
        }}

        .sidebar-item {{
            padding: 12px 15px;
            margin: 8px 0;
            border-radius: 10px; /* keep soft but subtle */
            cursor: pointer;
            transition: background-color 0.2s ease, transform 0.15s ease;
            font-size: 0.95em;
            display: flex;
            justify-content: space-between;
            align-items: center;
            min-height: 20px;
            word-wrap: break-word;
            line-height: 1.4;
            color: #1d1d1f;
        }}

        .sidebar-item:hover {{
            background-color: rgba(0,0,0,0.06);
            transform: translateX(2px);
        }}

        .sidebar-item.active {{
            background-color: #e8e8ed;
            box-shadow: inset 0 0 0 1px rgba(0,0,0,0.08);
        }}

        .sidebar-item-text {{
            flex: 1;
            margin-right: 8px;
            font-weight: 500;
            color: #1d1d1f;
            text-shadow: none;
        }}

        .sidebar-count {{
            background: #e8e8ed;
            color: #1d1d1f;
            border-radius: 12px;
            padding: 4px 10px;
            font-size: 0.8em;
            font-weight: 600;
            min-width: 20px;
            text-align: center;
            box-shadow: none;
            flex-shrink: 0;
        }}

        /* Directory tree styles */
        .directory-item {{
            position: relative;
            display: flex;
            align-items: center;
            padding: 4px 8px;
            margin: 2px 0;
            border-radius: 6px;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display",
                         "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif;
            font-size: 0.8em;
            line-height: 1.2;
        }}

        .tree-prefix {{
            color: #999;
            white-space: pre;
            font-family: monospace;
            font-size: 0.9em;
            line-height: 1.2;
        }}

        .directory-toggle {{
            width: 16px;
            height: 16px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            user-select: none;
            font-size: 1.1em;
            font-weight: bold;
            margin-right: 6px;
            color: #007bff;
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 3px;
            transition: all 0.2s ease;
        }}

        .directory-toggle:hover {{
            background: #e9ecef;
            border-color: #007bff;
            color: #0056b3;
        }}

        .leaf-indicator {{
            width: 16px;
            height: 16px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            margin-right: 6px;
            background: #f0f4ff;
            border-radius: 3px;
            position: relative;
        }}

        .leaf-indicator::after {{
            content: '';
            width: 8px;
            height: 8px;
            background: #a5b4fc;
            border-radius: 2px;
        }}

        .folder-icon {{
            margin-right: 4px;
            font-size: 0.9em;
        }}

        .directory-children {{
            margin: 0;
        }}

        .directory-item[data-level="0"] {{
            font-weight: 600;
            background: rgba(0,0,0,0.02);
        }}

        .expandable .directory-toggle:hover {{
            color: #1d1d1f;
        }}

        .directory-item:hover {{
            background-color: rgba(0,0,0,0.04);
        }}

        .directory-item.expandable:hover {{
            background-color: rgba(0,0,0,0.06);
            cursor: pointer;
        }}

        .directory-item.active {{
            background-color: #e8e8ed;
        }}

        .main-content {{
            margin-left: 320px;
            flex: 1;
            padding: 20px;
            overflow-x: hidden;
            max-width: calc(100vw - 320px);
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 15px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            overflow: hidden;
        }}

        .header {{
            background: #ffffff;
            color: #1d1d1f;
            padding: 20px 30px 30px 30px;
            border-bottom: 1px solid rgba(0,0,0,0.08);
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 20px;
            font-weight: 600;
            letter-spacing: -0.02em;
            text-align: center;
        }}

        .header p {{
            font-size: 1.1em;
            opacity: 0.8;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f5f5f7;
        }}

        .stat-card {{
            background: #ffffff;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 1px 2px rgba(0,0,0,0.06);
            transition: transform 0.2s ease;
            border: 1px solid rgba(0,0,0,0.06);
        }}

        .stat-card:hover {{
            transform: translateY(-3px);
        }}

        .stat-number {{
            font-size: 2.5em;
            font-weight: 700;
            margin-bottom: 5px;
            letter-spacing: -0.02em;
        }}

        .stat-label {{
            color: #6e6e73; /* Apple secondary text */
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        .critical {{ color: #b00020; }}
        .high {{ color: #bf2600; }}
        .medium {{ color: #b35c00; }}
        .low {{ color: #2a7f3f; }}
        .total {{ color: #06c; }} /* Apple link blue */

        .filters {{
            padding: 20px 30px;
            background: #f5f5f7;
            border-bottom: 1px solid rgba(0,0,0,0.08);
        }}

        .filter-group {{
            display: flex;
            gap: 15px;
            align-items: center;
            flex-wrap: wrap;
            justify-content: flex-start;
        }}

        .filter-label {{
            font-weight: 600;
            color: #1d1d1f;
        }}

        .filter-btn {{
            padding: 8px 16px;
            border: 1px solid rgba(0,0,0,0.15);
            background: #ffffff;
            color: #1d1d1f;
            border-radius: 20px;
            cursor: pointer;
            transition: background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease;
            font-size: 0.9em;
        }}

        .filter-btn:hover,
        .filter-btn.active {{
            background: #1d1d1f;
            color: #fff;
            border-color: #1d1d1f;
        }}

        .issues-container {{
            padding: 30px;
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 2rem;
            align-items: start;
        }}

        .issue {{
            background: #fff;
            border: 1px solid #e5e5ea;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 1px 2px rgba(0,0,0,.04);
            height: fit-content;
        }}

        .issue:nth-child(even) {{
            background: #f8f9fa;
            border-color: #dee2e6;
        }}

        .issue:nth-child(even) .issue__meta {{
            background: #e9ecef;
        }}

        .issue:nth-child(even) .issue__head {{
            background: #f1f3f4;
        }}

        .issue__head {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1rem 1.25rem;
            border-bottom: 1px solid #e5e5ea;
        }}

        .issue__file {{
            font-size: 1.05rem;
            margin: 0;
        }}

        .issue__meta {{
            padding: .75rem 1.25rem;
            background: #f5f5f7;
            border-bottom: 1px solid #e5e5ea;
        }}

        .kv {{
            display: flex;
            gap: 1.5rem;
            flex-wrap: wrap;
            margin: 0;
        }}

        .kv dt {{
            font-weight: 600;
            color: #424245;
        }}

        .kv dd {{
            margin: 0 .25rem 0 0;
        }}

        .issue__body {{
            padding: 1rem 1.25rem;
        }}

        .issue__title {{
            font-size: 1rem;
            margin: .25rem 0 .5rem;
        }}

        .badge {{
            display: inline-block;
            padding: .2rem .6rem;
            border-radius: 999px;
            font-size: .75rem;
            font-weight: 600;
        }}

        .badge--severity {{
            background: #fee;
            color: #b00020;
            border: 1px solid #f6d;
        }}

        .badge--warning {{
            background: #eef3ff;
            color: #063;
            border: 1px solid #dfe2ea;
        }}

        .callout {{
            border: 1px solid #e5e5ea;
            border-radius: 10px;
            padding: .75rem .9rem;
            margin: .75rem 0;
            background: #fff;
        }}

        .callout summary {{
            font-weight: 600;
            cursor: pointer;
        }}

        .callout--impact {{
            background: #fff8e1;
            border-color: #ffe4a3;
        }}

        .callout--solution {{
            background: #f0f7f0;
            border-color: #cfe8cf;
        }}

        .callout--evidence {{
            background: #e8f4fd;
            border-color: #b3d9f2;
        }}

        code {{
            background: #f2f2f7;
            padding: 0 .25rem;
            border-radius: 4px;
        }}

        .hidden {{
            display: none;
        }}

        /* Copy button styles */
        .copy-btn {{
            background: #007bff;
            color: white;
            border: none;
            border-radius: 12px;
            padding: 4px 10px;
            font-size: 0.8em;
            cursor: pointer;
            margin-left: 10px;
            transition: background-color 0.2s ease;
            font-weight: 600;
            height: 24px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }}

        .copy-btn:hover {{
            background: #0056b3;
        }}

        .copy-btn:active {{
            background: #004085;
            transform: translateY(1px);
        }}

        .copy-btn.copied {{
            background: #28a745;
        }}

        /* Pagination styles */
        .pagination-container {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-left: 20px;
        }}

        .pagination-btn {{
            padding: 6px 12px;
            border: 1px solid rgba(0,0,0,0.15);
            background: #ffffff;
            color: #1d1d1f;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s ease;
        }}

        .pagination-btn:hover:not(:disabled) {{
            background: #f5f5f7;
            border-color: #1d1d1f;
        }}

        .pagination-btn:disabled {{
            opacity: 0.4;
            cursor: not-allowed;
        }}

        .pagination-pages {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}

        .pagination-page {{
            min-width: 32px;
            height: 32px;
            padding: 0 8px;
            border: 1px solid rgba(0,0,0,0.15);
            background: #ffffff;
            color: #1d1d1f;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s ease;
        }}

        .pagination-page:hover {{
            background: #f5f5f7;
            border-color: #1d1d1f;
        }}

        .pagination-page.active {{
            background: #1d1d1f;
            color: #ffffff;
            border-color: #1d1d1f;
        }}

        .pagination-ellipsis {{
            padding: 0 4px;
            color: #6e6e73;
        }}

        .pagination-current {{
            padding: 6px 14px;
            background: #1d1d1f;
            color: #ffffff;
            border-radius: 6px;
            font-size: 0.9em;
            font-weight: 600;
            min-width: 40px;
            text-align: center;
        }}

        @media (max-width: 768px) {{
            .sidebar {{
                width: 280px;
            }}

            .main-content {{
                margin-left: 280px;
                max-width: calc(100vw - 280px);
                padding: 10px;
            }}

            .container {{
                margin: 5px;
                border-radius: 10px;
                max-width: 100%;
            }}

            .header {{
                padding: 20px;
            }}

            .header h1 {{
                font-size: 2em;
            }}

            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
                padding: 15px;
                gap: 10px;
            }}

            .filter-group {{
                flex-direction: column;
                align-items: flex-start;
                gap: 10px;
            }}

            .filter-btn {{
                font-size: 0.8em;
                padding: 6px 12px;
            }}

            .issues-container {{
                grid-template-columns: 1fr;
                gap: 1.5rem;
                padding: 15px;
            }}
        }}
    </style>
</head>
<body>
    <div class="sidebar">
        <h3>HindSight</h3>
        <div class="sidebar-item active" data-directory="all">
            <span class="sidebar-item-text">All Issues</span>
            <span class="sidebar-count">({total})</span>
        </div>
        {sidebar_items}
    </div>

    <div class="main-content">
        <div class="container">
            <div class="header">
                <h1>{analysis_type}</h1>
                <div style="display: flex; align-items: center; font-size: 1.1em; opacity: 0.8;">
                    <span style="flex: 1;">{repository_text}</span>
                    <span style="display: flex; align-items: center; justify-content: flex-end;">
                        {date_text}
                        <button class="copy-btn" onclick="copyAllIssues()" style="margin-left: 15px;">Copy All</button>
                    </span>
                </div>
            </div>

            <div class="filters">
                <div class="filter-group">
                    <button class="filter-btn active" data-filter="all">All Issues ({total})</button>
                    <button class="filter-btn" data-filter="critical">Critical ({critical})</button>
                    <button class="filter-btn" data-filter="high">High ({high})</button>
                    <button class="filter-btn" data-filter="medium">Medium ({medium})</button>
                    <button class="filter-btn" data-filter="low">Low ({low})</button>
                    <div class="pagination-container" id="paginationContainer" style="display: none; margin-left: auto;">
                        <button class="pagination-btn" id="firstPagesBtn" onclick="jumpBackward()">«</button>
                        <button class="pagination-btn" id="prevPageBtn" onclick="prevPage()">‹</button>
                        <span class="pagination-current" id="currentPageDisplay">1 / 1</span>
                        <button class="pagination-btn" id="nextPageBtn" onclick="nextPage()">›</button>
                        <button class="pagination-btn" id="lastPagesBtn" onclick="jumpForward()">»</button>
                    </div>
                </div>
            </div>

            <div class="issues-container" id="issuesContainer">
                <!-- Issues will be populated by JavaScript -->
            </div>
        </div>
    </div>

    <script>
        const issuesData = {issues_json};

        // Store currently displayed issues for copy functionality
        let currentlyDisplayedIssues = [];
        
        // Store all filtered issues (across all pages) for Copy All functionality
        let allFilteredIssues = [];
        
        // Pagination state
        let paginationState = {{
            currentPage: 1,
            itemsPerPage: 20,
            totalPages: 1,
            totalFilteredItems: 0
        }};

        function renderIssues(issues) {{
            const container = document.getElementById('issuesContainer');
            container.innerHTML = '';

            // Store the currently displayed issues for copy functionality
            currentlyDisplayedIssues = issues;

            issues.forEach((issue, index) => {{
                const issueSection = document.createElement('section');
                issueSection.className = 'issue';
                issueSection.setAttribute('data-severity', issue.kind || issue.severity);
                issueSection.setAttribute('data-directory', getIssueDirectory(issue.file_path || issue.file));

                // Extract filename from original file_path (before directory assignment)
                // Use the original file_path from metadata if available, otherwise fall back to current file_path
                let originalFilePath = issue.file_path || issue.file || 'Unknown File';
                
                // Check if this looks like a directory name (no path separators and no file extension)
                // If so, try to reconstruct from other available fields
                if (originalFilePath && !originalFilePath.includes('/') && !originalFilePath.includes('\\\\') &&
                    !originalFilePath.includes('.') && originalFilePath !== 'Unknown' && originalFilePath !== 'root') {{
                    // This looks like a directory name, try to get original file path from other fields
                    // The analyzers may have preserved the original in other fields
                    if (issue.original_file_path) {{
                        originalFilePath = issue.original_file_path;
                    }} else if (issue.function_name && issue.function_name.includes('::')) {{
                        // Try to extract file info from function name if it contains class info
                        const parts = issue.function_name.split('::');
                        if (parts.length > 1) {{
                            // This might give us a clue about the file
                            originalFilePath = `${{originalFilePath}}/${{parts[0]}}.java`; // Assume Java for now
                        }}
                    }}
                }}
                
                let fileName = 'Unknown File';
                if (originalFilePath && originalFilePath !== 'Unknown File') {{
                    if (originalFilePath.includes('/')) {{
                        fileName = originalFilePath.split('/').pop();
                    }} else if (originalFilePath.includes('\\\\')) {{
                        fileName = originalFilePath.split('\\\\').pop();
                    }} else {{
                        fileName = originalFilePath;
                    }}
                }}
                const categoryTag = formatIssueType(issue.category || 'unknown');
                const severity = issue.severity || 'unknown';
                const severityUpper = severity.toUpperCase();

                issueSection.innerHTML = `
                    <header class="issue__head">
                        <h2 class="issue__file">${{fileName}}<button class="copy-btn" onclick="copyIssueToClipboard(${{index}})">Copy</button></h2>
                        <span class="badge badge--severity">${{severityUpper}}</span>
                    </header>

                    <div class="issue__meta">
                        <dl class="kv">
                            <div><dt>Function</dt><dd><code>${{issue.function_name || 'Unknown'}}</code></dd></div>
                            <div><dt>Line</dt><dd>${{issue.lines || issue.line_number || issue.lineNumber || 'N/A'}}</dd></div>
                            <div><dt>File</dt><dd><code>${{fileName}}</code></dd></div>
                            <div><dt>Status</dt><dd><span class="badge badge--warning">${{categoryTag}}</span></dd></div>
                        </dl>
                    </div>

                    <article class="issue__body">
                        <h3 class="issue__title">Issue</h3>
                        <p>${{issue.issue || 'No description available'}}</p>

                        ${{issue.description ? `
                        <details class="callout callout--impact" open>
                            <summary>Impact</summary>
                            <p>${{issue.description || 'No impact information available'}}</p>
                        </details>
                        ` : ''}}

                        <details class="callout callout--solution" open>
                            <summary>Potential Solution</summary>
                            <p>${{issue.suggestion || 'No solution provided'}}</p>
                        </details>

                        ${{issue.evidence ? `
                        <details class="callout callout--evidence" open>
                            <summary>Evidence</summary>
                            <p>${{issue.evidence}}</p>
                        </details>
                        ` : ''}}
                    </article>
                `;

                container.appendChild(issueSection);
            }});
        }}

        function formatIssueType(type) {{
            if (type === 'unknown') return 'Unknown';

            // Handle camelCase by inserting spaces before capital letters
            let formatted = type.replace(/([a-z])([A-Z])/g, '$1 $2');
            // Handle consecutive capitals (like "HTTPRequest" -> "HTTP Request")
            formatted = formatted.replace(/([A-Z])([A-Z][a-z])/g, '$1 $2');
            // Replace underscores with spaces and title case
            formatted = formatted.replace(/_/g, ' ');
            // Title case
            formatted = formatted.replace(/\b\w/g, l => l.toUpperCase());

            return formatted;
        }}

        // Global filter state
        let currentFilters = {{
            severity: 'all',
            directory: 'all'
        }};

        // Pagination functions
        function calculatePagination(filteredIssues) {{
            paginationState.totalFilteredItems = filteredIssues.length;
            paginationState.totalPages = Math.ceil(filteredIssues.length / paginationState.itemsPerPage);
            
            // Reset to page 1 if current page exceeds total pages
            if (paginationState.currentPage > paginationState.totalPages) {{
                paginationState.currentPage = 1;
            }}
            
            // Ensure at least 1 page
            if (paginationState.totalPages < 1) {{
                paginationState.totalPages = 1;
            }}
        }}

        function getPagedIssues(filteredIssues) {{
            const startIndex = (paginationState.currentPage - 1) * paginationState.itemsPerPage;
            const endIndex = startIndex + paginationState.itemsPerPage;
            return filteredIssues.slice(startIndex, endIndex);
        }}

        function goToPage(pageNumber) {{
            if (pageNumber >= 1 && pageNumber <= paginationState.totalPages) {{
                paginationState.currentPage = pageNumber;
                applyFilters();
            }}
        }}

        function nextPage() {{
            if (paginationState.currentPage < paginationState.totalPages) {{
                goToPage(paginationState.currentPage + 1);
            }}
        }}

        function prevPage() {{
            if (paginationState.currentPage > 1) {{
                goToPage(paginationState.currentPage - 1);
            }}
        }}

        function renderPaginationControls() {{
            const container = document.getElementById('paginationContainer');
            const currentPageDisplay = document.getElementById('currentPageDisplay');
            const prevBtn = document.getElementById('prevPageBtn');
            const nextBtn = document.getElementById('nextPageBtn');
            const firstPagesBtn = document.getElementById('firstPagesBtn');
            const lastPagesBtn = document.getElementById('lastPagesBtn');
            
            // Hide pagination if 20 or fewer items
            if (paginationState.totalFilteredItems <= paginationState.itemsPerPage) {{
                container.style.display = 'none';
                return;
            }}
            
            container.style.display = 'flex';
            
            // Update current page display with format "current / total"
            currentPageDisplay.textContent = `${{paginationState.currentPage}} / ${{paginationState.totalPages}}`;
            
            // Enable/disable navigation buttons
            prevBtn.disabled = paginationState.currentPage === 1;
            nextBtn.disabled = paginationState.currentPage === paginationState.totalPages;
            firstPagesBtn.disabled = paginationState.currentPage <= 5;
            lastPagesBtn.disabled = paginationState.currentPage > paginationState.totalPages - 5;
        }}

        // Jump backward by 5 pages
        function jumpBackward() {{
            const newPage = Math.max(1, paginationState.currentPage - 5);
            goToPage(newPage);
        }}

        // Jump forward by 5 pages
        function jumpForward() {{
            const newPage = Math.min(paginationState.totalPages, paginationState.currentPage + 5);
            goToPage(newPage);
        }}

        function getIssueDirectory(filePath) {{
            if (!filePath) return 'uncategorized';

            // Handle "Unknown" directory specially
            if (filePath === "Unknown") {{
                return "Unknown";
            }}

            // Handle different path formats
            if (filePath.includes('/')) {{
                const directory = filePath.split('/').slice(0, -1).join('/');
                return directory || 'root';
            }} else if (filePath.includes('\\\\')) {{
                const directory = filePath.split('\\\\').slice(0, -1).join('\\\\');
                return directory || 'root';
            }} else {{
                // For strings without path separators, treat them as directory names
                // This handles cases where file_path is already set to a directory name
                // by the IssueDirectoryOrganizer (e.g., "daemon", "common")
                return filePath.trim() || 'root';
            }}
        }}

        function updateStats(issues) {{
            const stats = {{
                total: issues.length,
                critical: issues.filter(issue => issue.severity === 'critical').length,
                high: issues.filter(issue => issue.severity === 'high').length,
                medium: issues.filter(issue => issue.severity === 'medium').length,
                low: issues.filter(issue => issue.severity === 'low').length
            }};

            // Update filter button text with new counts
            const allBtn = document.querySelector('.filter-btn[data-filter="all"]');
            const criticalBtn = document.querySelector('.filter-btn[data-filter="critical"]');
            const highBtn = document.querySelector('.filter-btn[data-filter="high"]');
            const mediumBtn = document.querySelector('.filter-btn[data-filter="medium"]');
            const lowBtn = document.querySelector('.filter-btn[data-filter="low"]');

            if (allBtn) allBtn.textContent = `All Issues (${{stats.total}})`;
            if (criticalBtn) criticalBtn.textContent = `Critical (${{stats.critical}})`;
            if (highBtn) highBtn.textContent = `High (${{stats.high}})`;
            if (mediumBtn) mediumBtn.textContent = `Medium (${{stats.medium}})`;
            if (lowBtn) lowBtn.textContent = `Low (${{stats.low}})`;
        }}

        function applyFilters() {{
            let filteredIssues = issuesData.issues;

            // Apply severity filter
            if (currentFilters.severity !== 'all') {{
                filteredIssues = filteredIssues.filter(issue => issue.severity === currentFilters.severity);
            }}

            // Apply directory filter
            if (currentFilters.directory !== 'all') {{
                filteredIssues = filteredIssues.filter(issue => {{
                    const issueDir = getIssueDirectory(issue.file_path || issue.file);
                    // Show issues that are in the selected directory OR in any of its subdirectories
                    const matches = issueDir === currentFilters.directory || issueDir.startsWith(currentFilters.directory + '/');

                    // Debug logging for the first few issues when filtering by directory
                    if (window.debugDirectoryFiltering && filteredIssues.indexOf(issue) < 5) {{
                        const debugFilePath = issue.file_path || issue.file || 'Unknown';
                        const debugFileName = debugFilePath.includes('/') ? debugFilePath.split('/').pop() :
                                            debugFilePath.includes('\\\\') ? debugFilePath.split('\\\\').pop() : debugFilePath;
                        console.log(`Issue: ${{debugFileName}}, IssueDir: "${{issueDir}}", SelectedDir: "${{currentFilters.directory}}", Matches: ${{matches}}`);
                    }}

                    return matches;
                }});
            }}

            // Store ALL filtered issues for Copy All functionality
            allFilteredIssues = filteredIssues;

            // Calculate pagination based on filtered results
            calculatePagination(filteredIssues);
            
            // Get only the issues for the current page
            const pagedIssues = getPagedIssues(filteredIssues);
            
            // Render the paged issues
            renderIssues(pagedIssues);
            
            // Update pagination controls
            renderPaginationControls();
        }}

        function updateStatsForDirectory() {{
            // Only update stats when filtering by directory, not severity
            let directoryFilteredIssues = issuesData.issues;

            if (currentFilters.directory !== 'all') {{
                directoryFilteredIssues = directoryFilteredIssues.filter(issue => {{
                    const issueDir = getIssueDirectory(issue.file_path || issue.file);
                    // Show issues that are in the selected directory OR in any of its subdirectories
                    return issueDir === currentFilters.directory || issueDir.startsWith(currentFilters.directory + '/');
                }});
            }}

            updateStats(directoryFilteredIssues);
        }}

        function filterIssues(severity) {{
            currentFilters.severity = severity;
            paginationState.currentPage = 1; // Reset to first page on filter change
            applyFilters();
            // Don't update stats when filtering by severity
        }}

        function filterByDirectory(directory) {{
            currentFilters.directory = directory;
            paginationState.currentPage = 1; // Reset to first page on filter change
            applyFilters();
            updateStatsForDirectory(); // Only update stats when filtering by directory
        }}

        // Initialize - use applyFilters to properly set up pagination
        applyFilters();

        // Filter functionality
        document.querySelectorAll('.filter-btn').forEach(btn => {{
            btn.addEventListener('click', (e) => {{
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                filterIssues(e.target.getAttribute('data-filter'));
            }});
        }});

        // Sidebar functionality
        document.querySelectorAll('.sidebar-item').forEach(item => {{
            item.addEventListener('click', (e) => {{
                document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
                // Handle clicks on both the item and its child elements
                const clickedItem = e.target.closest('.sidebar-item');
                clickedItem.classList.add('active');
                filterByDirectory(clickedItem.getAttribute('data-directory'));
            }});
        }});

        // Function to reset filters
        function resetFilters() {{
            currentFilters = {{
                severity: 'all',
                directory: 'all'
            }};

            // Reset UI states
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            document.querySelector('.filter-btn[data-filter="all"]').classList.add('active');

            document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
            const allIssuesItem = document.querySelector('.sidebar-item[data-directory="all"]');
            if (allIssuesItem) {{
                allIssuesItem.classList.add('active');
            }}

            applyFilters();
            updateStats(issuesData.issues); // Reset stats to show all issues
        }}

        // Directory tree expand/collapse functionality
        document.addEventListener('DOMContentLoaded', function() {{
            // Handle expand/collapse for expandable directories
            document.querySelectorAll('.directory-item.expandable').forEach(item => {{
                const toggle = item.querySelector('.directory-toggle');
                const directoryText = item.querySelector('.sidebar-item-text');

                // Function to handle expand/collapse
                function toggleExpansion(e) {{
                    e.stopPropagation();
                    const children = item.nextElementSibling;

                    if (children && children.classList.contains('directory-children')) {{
                        const isExpanded = children.style.display !== 'none';
                        children.style.display = isExpanded ? 'none' : 'block';
                        if (toggle) {{
                            toggle.textContent = isExpanded ? '+' : '-';
                        }}
                    }}
                }}

                // Function to handle directory filtering
                function handleDirectoryFilter(e) {{
                    e.stopPropagation();
                    document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
                    item.classList.add('active');
                    filterByDirectory(item.getAttribute('data-directory'));
                }}

                // Add click handler for toggle arrow
                if (toggle) {{
                    toggle.addEventListener('click', toggleExpansion);
                }}

                // Add double-click handler for directory filtering
                item.addEventListener('dblclick', handleDirectoryFilter);

                // Add single click for expand/collapse
                item.addEventListener('click', function(e) {{
                    // Only expand/collapse if not clicking on the toggle (to avoid double action)
                    if (!e.target.classList.contains('directory-toggle')) {{
                        toggleExpansion(e);
                    }}
                }});

                // Add hover effect to indicate clickability
                item.style.cursor = 'pointer';
            }});
        }});

        // Debug function to help diagnose directory filtering issues
        window.debugDirectoryIssues = function(directoryName) {{
            console.log(`=== DEBUG: Directory "${{directoryName}}" ===`);

            // Count issues by directory extraction method
            const issuesByDir = {{}};
            issuesData.issues.forEach(issue => {{
                const dir = getIssueDirectory(issue.file_path || issue.file);
                if (!issuesByDir[dir]) issuesByDir[dir] = [];
                issuesByDir[dir].push(issue);
            }});

            console.log('All directories found:', Object.keys(issuesByDir));
            console.log(`Issues in "${{directoryName}}":`, issuesByDir[directoryName] ? issuesByDir[directoryName].length : 0);

            // Check for subdirectories
            const subdirs = Object.keys(issuesByDir).filter(dir => dir.startsWith(directoryName + '/'));
            console.log(`Subdirectories of "${{directoryName}}":`, subdirs);

            let totalInSubdirs = 0;
            subdirs.forEach(subdir => {{
                const count = issuesByDir[subdir].length;
                console.log(`  ${{subdir}}: ${{count}} issues`);
                totalInSubdirs += count;
            }});

            const directCount = issuesByDir[directoryName] ? issuesByDir[directoryName].length : 0;
            console.log(`Total: ${{directCount}} direct + ${{totalInSubdirs}} in subdirs = ${{directCount + totalInSubdirs}}`);

            // Enable debug logging for filtering
            window.debugDirectoryFiltering = true;
            filterByDirectory(directoryName);
            window.debugDirectoryFiltering = false;
        }};

        // Add instructions for debugging
        console.log('To debug directory filtering issues, use: debugDirectoryIssues("DirectoryName")');

        // Copy issue to clipboard function - generates text based on what user sees (WYSIWYG)
        function copyIssueToClipboard(issueIndex) {{
            // Use the currently displayed issues array instead of the original issues array
            const issue = currentlyDisplayedIssues[issueIndex];
            if (!issue) {{
                console.error('Issue not found at index:', issueIndex);
                return;
            }}

            // Get the file path - use original_file_path if available for full relative path, otherwise use file_path
            const filePath = issue.original_file_path || issue.file_path || 'Unknown File';
            const functionName = issue.function_name || 'Unknown';
            
            // Extract filename from the full path for the title
            let fileName = 'Unknown File';
            if (filePath && filePath !== 'Unknown File') {{
                if (filePath.includes('/')) {{
                    fileName = filePath.split('/').pop();
                }} else if (filePath.includes('\\\\')) {{
                    fileName = filePath.split('\\\\').pop();
                }} else {{
                    fileName = filePath;
                }}
            }}

            // Build content with optional evidence field
            const impact = issue.impact || issue.description || 'No impact information available';
            const evidence = issue.evidence || '';
            const solution = issue.suggestion || issue.potential_solution || issue.solution || 'No solution provided';

            let content = `Title: In ${{functionName}}() in ${{fileName}}, there is potential issue

HindSight has found a potential issue

In ${{functionName}}() in ${{filePath}}, there is potential issue

Impact: ${{impact}}`;

            // Add evidence if it exists
            if (evidence) {{
                content += `

Evidence: ${{evidence}}`;
            }}

            content += `

Potential Solution: ${{solution}}`;

            navigator.clipboard.writeText(content).then(() => {{
                // Show feedback
                const button = event.target;
                const originalText = button.textContent;
                button.textContent = 'Copied!';
                button.classList.add('copied');

                setTimeout(() => {{
                    button.textContent = originalText;
                    button.classList.remove('copied');
                }}, 2000);
            }}).catch(err => {{
                console.error('Failed to copy: ', err);
                // Fallback for older browsers
                const textArea = document.createElement('textarea');
                textArea.value = content;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);

                // Show feedback
                const button = event.target;
                const originalText = button.textContent;
                button.textContent = 'Copied!';
                button.classList.add('copied');

                setTimeout(() => {{
                    button.textContent = originalText;
                    button.classList.remove('copied');
                }}, 2000);
            }});
        }}

        // Copy all issues to clipboard function
        function copyAllIssues() {{
            // Use allFilteredIssues which contains ALL issues matching current filters
            // (not just the current page)
            const issuesToCopy = allFilteredIssues.length > 0 ? allFilteredIssues : issuesData.issues;
            
            // Filter out Low priority issues
            const issues = issuesToCopy.filter(issue => issue.severity && issue.severity.toLowerCase() !== 'low');
            
            if (issues.length === 0) {{
                alert('No issues to copy (Low priority issues are excluded)');
                return;
            }}

            const separator = '\\n======================\\n';
            
            const formattedIssues = issues.map(issue => {{
                // Get the file path - use original_file_path if available for full relative path
                const filePath = issue.original_file_path || issue.file_path || issue.file || 'Unknown';
                const lineNumber = issue.lines || issue.line_number || issue.lineNumber || 'N/A';
                const issueText = issue.issue || 'No description available';
                const impact = issue.impact || issue.description || 'No impact information available';
                const evidence = issue.evidence || '';
                const solution = issue.suggestion || issue.potential_solution || issue.solution || 'No solution provided';

                // Build the output, only including evidence if it exists
                let output = `file name: ${{filePath}},
line number: ${{lineNumber}},
issue: ${{issueText}},
impact: ${{impact}}`;
                
                if (evidence) {{
                    output += `,
potential evidence: ${{evidence}}`;
                }}
                
                output += `,
Potential Solution: ${{solution}}`;
                
                return output;
            }}).join(separator);

            navigator.clipboard.writeText(formattedIssues).then(() => {{
                // Show feedback on the Copy All button
                const button = event.target;
                const originalText = button.textContent;
                button.textContent = 'Copied!';
                button.classList.add('copied');

                setTimeout(() => {{
                    button.textContent = originalText;
                    button.classList.remove('copied');
                }}, 2000);
            }}).catch(err => {{
                console.error('Failed to copy: ', err);
                // Fallback for older browsers
                const textArea = document.createElement('textarea');
                textArea.value = formattedIssues;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);

                // Show feedback
                const button = event.target;
                const originalText = button.textContent;
                button.textContent = 'Copied!';
                button.classList.add('copied');

                setTimeout(() => {{
                    button.textContent = originalText;
                    button.classList.remove('copied');
                }}, 2000);
            }});
        }}
    </script>
</body>
</html>'''.format(
        total=stats['total'],
        critical=stats['critical'],
        high=stats['high'],
        medium=stats['medium'],
        low=stats['low'],
        sidebar_items=generate_sidebar_items(stats['directories'], stats['directory_tree']),
        issues_json=json.dumps({"issues": issues}, indent=8),
        repository_text=repository_text,
        date_text=date_text,
        analysis_type=analysis_type
    )

    # If output_file is None, return HTML content as string (for API usage)
    if output_file is None:
        return html_content
    
    # Otherwise, write to file (for CLI usage)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_file

def generate_dropped_issues_html_report(dropped_issues, output_file=None, project_name=None, analysis_type="Dropped Issues Report"):
    """
    Generate HTML report for dropped issues with amber/orange color theme.
    
    This report shows all issues that were filtered out during the 3-level filtering process,
    with metadata indicating which stage dropped each issue.
    
    Args:
        dropped_issues: List of dropped issue dictionaries with filter metadata
        output_file: Output file path (optional, auto-generated if not provided)
        project_name: Project name for the report
        analysis_type: Type of analysis (default: "Dropped Issues Report")
    
    Returns:
        Path to generated HTML report or HTML content if output_file is None
    """
    if not dropped_issues:
        return None
    
    # Calculate statistics for dropped issues
    stats = calculate_dropped_issues_stats(dropped_issues)
    
    # Use project name in filename if provided
    if project_name and output_file is None:
        repository_text = f"<strong>Repository:</strong> {project_name}"
        date_text = f"<strong>Date:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"
        project_name_for_file = project_name.replace(' ', '_')
        output_file = f"dropped_issues_{project_name_for_file}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    elif project_name:
        repository_text = f"<strong>Repository:</strong> {project_name}"
        date_text = f"<strong>Date:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"
    else:
        repository_text = f"<strong>Repository:</strong> Unknown"
        date_text = f"<strong>Date:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"

    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dropped Issues Report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display",
                         "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif;
            background: #fffbeb; /* amber-50 */
            min-height: 100vh;
            display: flex;
            overflow-x: hidden;
            color: #78350f; /* amber-900 */
        }}

        .sidebar {{
            width: 320px;
            background: #fef3c7; /* amber-100 */
            color: #78350f;
            padding: 20px;
            box-shadow: 0 0 0 1px rgba(217, 119, 6, 0.2);
            position: fixed;
            height: 100vh;
            overflow-y: auto;
            overflow-x: hidden;
        }}

        .sidebar h3 {{
            margin-bottom: 20px;
            font-size: 1.2em;
            color: #92400e; /* amber-800 */
            border-bottom: 1px solid rgba(217, 119, 6, 0.3);
            padding-bottom: 10px;
            font-weight: 600;
        }}

        .sidebar-item {{
            padding: 12px 15px;
            margin: 8px 0;
            border-radius: 10px;
            cursor: pointer;
            transition: background-color 0.2s ease, transform 0.15s ease;
            font-size: 0.95em;
            display: flex;
            justify-content: space-between;
            align-items: center;
            min-height: 20px;
            word-wrap: break-word;
            line-height: 1.4;
            color: #78350f;
        }}

        .sidebar-item:hover {{
            background-color: rgba(217, 119, 6, 0.15);
            transform: translateX(2px);
        }}

        .sidebar-item.active {{
            background-color: #fcd34d; /* amber-300 */
            box-shadow: inset 0 0 0 1px rgba(217, 119, 6, 0.3);
        }}

        .sidebar-item-text {{
            flex: 1;
            margin-right: 8px;
            font-weight: 500;
            color: #78350f;
        }}

        .sidebar-count {{
            background: #fcd34d; /* amber-300 */
            color: #78350f;
            border-radius: 12px;
            padding: 4px 10px;
            font-size: 0.8em;
            font-weight: 600;
            min-width: 20px;
            text-align: center;
            flex-shrink: 0;
        }}

        /* Directory tree styles */
        .directory-item {{
            position: relative;
            display: flex;
            align-items: center;
            padding: 4px 8px;
            margin: 2px 0;
            border-radius: 6px;
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display",
                         "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif;
            font-size: 0.8em;
            line-height: 1.2;
        }}

        .tree-prefix {{
            color: #b45309;
            white-space: pre;
            font-family: monospace;
            font-size: 0.9em;
            line-height: 1.2;
        }}

        .directory-toggle {{
            width: 16px;
            height: 16px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            user-select: none;
            font-size: 1.1em;
            font-weight: bold;
            margin-right: 6px;
            color: #d97706;
            background: #fef3c7;
            border: 1px solid #fcd34d;
            border-radius: 3px;
            transition: all 0.2s ease;
        }}

        .directory-toggle:hover {{
            background: #fcd34d;
            border-color: #d97706;
            color: #92400e;
        }}

        .leaf-indicator {{
            width: 16px;
            height: 16px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            margin-right: 6px;
            background: #fef3c7;
            border-radius: 3px;
            position: relative;
        }}

        .leaf-indicator::after {{
            content: '';
            width: 8px;
            height: 8px;
            background: #fbbf24;
            border-radius: 2px;
        }}

        .folder-icon {{
            margin-right: 4px;
            font-size: 0.9em;
        }}

        .directory-children {{
            margin: 0;
        }}

        .directory-item[data-level="0"] {{
            font-weight: 600;
            background: rgba(217, 119, 6, 0.05);
        }}

        .expandable .directory-toggle:hover {{
            color: #78350f;
        }}

        .directory-item:hover {{
            background-color: rgba(217, 119, 6, 0.1);
        }}

        .directory-item.expandable:hover {{
            background-color: rgba(217, 119, 6, 0.15);
            cursor: pointer;
        }}

        .directory-item.active {{
            background-color: #fcd34d;
        }}

        .main-content {{
            margin-left: 320px;
            flex: 1;
            padding: 20px;
            overflow-x: hidden;
            max-width: calc(100vw - 320px);
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 15px;
            box-shadow: 0 1px 3px rgba(217, 119, 6, 0.15);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); /* amber gradient */
            color: #ffffff;
            padding: 20px 30px 30px 30px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 20px;
            font-weight: 600;
            letter-spacing: -0.02em;
        }}

        .header p {{
            font-size: 1.1em;
            opacity: 0.9;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #fef3c7; /* amber-100 */
        }}

        .stat-card {{
            background: #ffffff;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 1px 2px rgba(217, 119, 6, 0.1);
            transition: transform 0.2s ease;
            border: 1px solid rgba(217, 119, 6, 0.2);
        }}

        .stat-card:hover {{
            transform: translateY(-3px);
        }}

        .stat-number {{
            font-size: 2.5em;
            font-weight: 700;
            margin-bottom: 5px;
            letter-spacing: -0.02em;
        }}

        .stat-label {{
            color: #92400e;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        .level-1 {{ color: #dc2626; }} /* red-600 */
        .level-2 {{ color: #d97706; }} /* amber-600 */
        .level-3 {{ color: #2563eb; }} /* blue-600 */
        .total {{ color: #78350f; }} /* amber-900 */

        .filters {{
            padding: 20px 30px;
            background: #fef3c7;
            border-bottom: 1px solid rgba(217, 119, 6, 0.2);
        }}

        .filter-group {{
            display: flex;
            gap: 15px;
            align-items: center;
            flex-wrap: wrap;
            justify-content: center;
        }}

        .filter-label {{
            font-weight: 600;
            color: #78350f;
        }}

        .filter-btn {{
            padding: 8px 16px;
            border: 1px solid rgba(217, 119, 6, 0.4);
            background: #ffffff;
            color: #78350f;
            border-radius: 20px;
            cursor: pointer;
            transition: background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease;
            font-size: 0.9em;
        }}

        .filter-btn:hover,
        .filter-btn.active {{
            background: #f59e0b;
            color: #fff;
            border-color: #f59e0b;
        }}

        .issues-container {{
            padding: 30px;
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 2rem;
            align-items: start;
        }}

        .issue {{
            background: #fff;
            border: 1px solid #fcd34d;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 1px 2px rgba(217, 119, 6, 0.1);
            height: fit-content;
        }}

        .issue:nth-child(even) {{
            background: #fffbeb;
            border-color: #fbbf24;
        }}

        .issue:nth-child(even) .issue__meta {{
            background: #fef3c7;
        }}

        .issue:nth-child(even) .issue__head {{
            background: #fef3c7;
        }}

        .issue__head {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1rem 1.25rem;
            border-bottom: 1px solid #fcd34d;
            background: #fffbeb;
        }}

        .issue__file {{
            font-size: 1.05rem;
            margin: 0;
            color: #78350f;
        }}

        .issue__meta {{
            padding: .75rem 1.25rem;
            background: #fef3c7;
            border-bottom: 1px solid #fcd34d;
        }}

        .kv {{
            display: flex;
            gap: 1.5rem;
            flex-wrap: wrap;
            margin: 0;
        }}

        .kv dt {{
            font-weight: 600;
            color: #92400e;
        }}

        .kv dd {{
            margin: 0 .25rem 0 0;
            color: #78350f;
        }}

        .issue__body {{
            padding: 1rem 1.25rem;
        }}

        .issue__title {{
            font-size: 1rem;
            margin: .25rem 0 .5rem;
            color: #78350f;
        }}

        .badge {{
            display: inline-block;
            padding: .2rem .6rem;
            border-radius: 999px;
            font-size: .75rem;
            font-weight: 600;
        }}

        .badge--level {{
            margin-right: 5px;
        }}

        .badge--level-1 {{
            background: #fee2e2;
            color: #991b1b;
            border: 1px solid #fca5a5;
        }}

        .badge--level-2 {{
            background: #fef3c7;
            color: #92400e;
            border: 1px solid #fcd34d;
        }}

        .badge--level-3 {{
            background: #dbeafe;
            color: #1e40af;
            border: 1px solid #93c5fd;
        }}

        .badge--severity {{
            background: #fef3c7;
            color: #92400e;
            border: 1px solid #fcd34d;
        }}

        .badge--category {{
            background: #f3f4f6;
            color: #374151;
            border: 1px solid #d1d5db;
        }}

        .callout {{
            border: 1px solid #fcd34d;
            border-radius: 10px;
            padding: .75rem .9rem;
            margin: .75rem 0;
            background: #fff;
        }}

        .callout summary {{
            font-weight: 600;
            cursor: pointer;
            color: #78350f;
        }}

        .callout--reason {{
            background: #fef3c7;
            border-color: #fbbf24;
        }}

        .callout--original {{
            background: #f3f4f6;
            border-color: #d1d5db;
        }}

        .callout--suggestion {{
            background: #f0fdf4;
            border-color: #86efac;
        }}

        code {{
            background: #fef3c7;
            padding: 0 .25rem;
            border-radius: 4px;
            color: #92400e;
        }}

        .hidden {{
            display: none;
        }}

        @media (max-width: 768px) {{
            .sidebar {{
                width: 280px;
            }}

            .main-content {{
                margin-left: 280px;
                max-width: calc(100vw - 280px);
                padding: 10px;
            }}

            .issues-container {{
                grid-template-columns: 1fr;
                gap: 1.5rem;
                padding: 15px;
            }}
        }}
    </style>
</head>
<body>
    <div class="sidebar">
        <h3>🗑️ Dropped Issues</h3>
        <div class="sidebar-item active" data-directory="all">
            <span class="sidebar-item-text">All Dropped Issues</span>
            <span class="sidebar-count">({total})</span>
        </div>
        {sidebar_items}
    </div>

    <div class="main-content">
        <div class="container">
            <div class="header">
                <h1>🗑️ {analysis_type}</h1>
                <div style="display: flex; align-items: center; font-size: 1.1em; opacity: 0.9;">
                    <span style="flex: 0 0 50%;">{repository_text}</span>
                    <span style="flex: 0 0 50%; text-align: right;">{date_text}</span>
                </div>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-number total">{total}</div>
                    <div class="stat-label">Total Dropped</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number level-1">{level1}</div>
                    <div class="stat-label">Category Filter</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number level-2">{level2}</div>
                    <div class="stat-label">LLM Filter</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number level-3">{level3}</div>
                    <div class="stat-label">Response Challenger</div>
                </div>
            </div>

            <div class="filters">
                <div class="filter-group">
                    <span class="filter-label">Filter by Type:</span>
                    <button class="filter-btn active" data-filter="all">All ({total})</button>
                    <button class="filter-btn" data-filter="level1">Category Filter ({level1})</button>
                    <button class="filter-btn" data-filter="level2">LLM Filter ({level2})</button>
                    <button class="filter-btn" data-filter="level3">Response Challenger ({level3})</button>
                </div>
            </div>

            <div class="issues-container" id="issuesContainer">
                <!-- Issues will be populated by JavaScript -->
            </div>
        </div>
    </div>

    <script>
        const droppedIssuesData = {issues_json};

        function getLevelFromIssue(issue) {{
            const filterLevel = issue.filter_level || '';
            if (filterLevel.includes('Level 1') || filterLevel.includes('level1')) return 'level1';
            if (filterLevel.includes('Level 2') || filterLevel.includes('level2')) return 'level2';
            if (filterLevel.includes('Level 3') || filterLevel.includes('level3')) return 'level3';
            return 'unknown';
        }}

        function getLevelBadgeClass(level) {{
            switch(level) {{
                case 'level1': return 'badge--level-1';
                case 'level2': return 'badge--level-2';
                case 'level3': return 'badge--level-3';
                default: return 'badge--level-1';
            }}
        }}

        function getLevelDisplayName(level) {{
            switch(level) {{
                case 'level1': return 'Level 1';
                case 'level2': return 'Level 2';
                case 'level3': return 'Level 3';
                default: return 'Unknown';
            }}
        }}

        function renderDroppedIssues(issues) {{
            const container = document.getElementById('issuesContainer');
            container.innerHTML = '';

            issues.forEach((droppedIssue, index) => {{
                const issueSection = document.createElement('section');
                issueSection.className = 'issue';
                
                const level = getLevelFromIssue(droppedIssue);
                issueSection.setAttribute('data-level', level);

                // Extract the original issue data
                const originalIssue = droppedIssue.original_issue || droppedIssue.results?.[0] || {{}};
                const filterLevel = droppedIssue.filter_level || 'Unknown';
                const filterType = droppedIssue.filter_type || 'Unknown';
                const reason = droppedIssue.reason || 'No reason provided';
                
                // Get file path and function name
                const filePath = originalIssue.file_path || originalIssue.filePath || 'Unknown';
                const functionName = originalIssue.function_name || originalIssue.functionName || 'Unknown';
                const category = originalIssue.category || 'Unknown';
                const severity = originalIssue.severity || 'Unknown';
                const issueText = originalIssue.issue || 'No description';
                const suggestion = originalIssue.suggestion || 'No suggestion provided';
                
                // Extract filename from path
                let fileName = 'Unknown File';
                if (filePath && filePath !== 'Unknown') {{
                    if (filePath.includes('/')) {{
                        fileName = filePath.split('/').pop();
                    }} else if (filePath.includes('\\\\')) {{
                        fileName = filePath.split('\\\\').pop();
                    }} else {{
                        fileName = filePath;
                    }}
                }}

                const levelBadgeClass = getLevelBadgeClass(level);
                const levelDisplayName = getLevelDisplayName(level);

                issueSection.innerHTML = `
                    <header class="issue__head">
                        <h2 class="issue__file">${{fileName}}</h2>
                        <div>
                            <span class="badge badge--level ${{levelBadgeClass}}">${{levelDisplayName}}</span>
                            <span class="badge badge--severity">${{severity.toUpperCase()}}</span>
                        </div>
                    </header>

                    <div class="issue__meta">
                        <dl class="kv">
                            <div><dt>Filter Type</dt><dd>${{filterType}}</dd></div>
                            <div><dt>Function</dt><dd><code>${{functionName}}</code></dd></div>
                            <div><dt>Category</dt><dd><span class="badge badge--category">${{category}}</span></dd></div>
                        </dl>
                    </div>

                    <article class="issue__body">
                        <h3 class="issue__title">Original Issue</h3>
                        <p>${{issueText}}</p>

                        <details class="callout callout--reason" open>
                            <summary>🚫 Reason for Dropping</summary>
                            <p>${{reason}}</p>
                        </details>

                        <details class="callout callout--suggestion">
                            <summary>💡 Original Suggestion</summary>
                            <p>${{suggestion}}</p>
                        </details>
                    </article>
                `;

                container.appendChild(issueSection);
            }});
        }}

        // Global filter state
        let currentFilters = {{
            level: 'all',
            directory: 'all'
        }};

        function getIssueDirectory(filePath) {{
            if (!filePath) return 'uncategorized';
            if (filePath === "Unknown") return "Unknown";
            
            if (filePath.includes('/')) {{
                const directory = filePath.split('/').slice(0, -1).join('/');
                return directory || 'root';
            }} else if (filePath.includes('\\\\')) {{
                const directory = filePath.split('\\\\').slice(0, -1).join('\\\\');
                return directory || 'root';
            }} else {{
                return filePath.trim() || 'root';
            }}
        }}

        function applyFilters() {{
            let filteredIssues = droppedIssuesData.issues;
            
            // Apply level filter
            if (currentFilters.level !== 'all') {{
                filteredIssues = filteredIssues.filter(issue => getLevelFromIssue(issue) === currentFilters.level);
            }}
            
            // Apply directory filter
            if (currentFilters.directory !== 'all') {{
                filteredIssues = filteredIssues.filter(droppedIssue => {{
                    const originalIssue = droppedIssue.original_issue || droppedIssue.results?.[0] || {{}};
                    const filePath = originalIssue.file_path || originalIssue.filePath || '';
                    const issueDir = getIssueDirectory(filePath);
                    return issueDir === currentFilters.directory || issueDir.startsWith(currentFilters.directory + '/');
                }});
            }}
            
            renderDroppedIssues(filteredIssues);
        }}

        function filterByLevel(level) {{
            currentFilters.level = level;
            applyFilters();
        }}

        function filterByDirectory(directory) {{
            currentFilters.directory = directory;
            applyFilters();
        }}

        // Initialize
        renderDroppedIssues(droppedIssuesData.issues);

        // Filter button functionality (for levels)
        document.querySelectorAll('.filter-btn').forEach(btn => {{
            btn.addEventListener('click', (e) => {{
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                filterByLevel(e.target.getAttribute('data-filter'));
            }});
        }});

        // Sidebar functionality (for directories)
        document.querySelectorAll('.sidebar-item').forEach(item => {{
            item.addEventListener('click', (e) => {{
                document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
                const clickedItem = e.target.closest('.sidebar-item');
                clickedItem.classList.add('active');
                filterByDirectory(clickedItem.getAttribute('data-directory'));
            }});
        }});

        // Directory tree expand/collapse functionality
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.directory-item.expandable').forEach(item => {{
                const toggle = item.querySelector('.directory-toggle');

                function toggleExpansion(e) {{
                    e.stopPropagation();
                    const children = item.nextElementSibling;

                    if (children && children.classList.contains('directory-children')) {{
                        const isExpanded = children.style.display !== 'none';
                        children.style.display = isExpanded ? 'none' : 'block';
                        if (toggle) {{
                            toggle.textContent = isExpanded ? '+' : '-';
                        }}
                    }}
                }}

                function handleDirectoryFilter(e) {{
                    e.stopPropagation();
                    document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
                    item.classList.add('active');
                    filterByDirectory(item.getAttribute('data-directory'));
                }}

                if (toggle) {{
                    toggle.addEventListener('click', toggleExpansion);
                }}

                item.addEventListener('dblclick', handleDirectoryFilter);

                item.addEventListener('click', function(e) {{
                    if (!e.target.classList.contains('directory-toggle')) {{
                        toggleExpansion(e);
                    }}
                }});

                item.style.cursor = 'pointer';
            }});
        }});
    </script>
</body>
</html>'''.format(
        total=stats['total'],
        level1=stats['level1'],
        level2=stats['level2'],
        level3=stats['level3'],
        sidebar_items=generate_sidebar_items(stats.get('directories', {}), stats.get('directory_tree', {})),
        issues_json=json.dumps({"issues": dropped_issues}, indent=8),
        repository_text=repository_text,
        date_text=date_text,
        analysis_type=analysis_type
    )

    # If output_file is None, return HTML content as string (for API usage)
    if output_file is None:
        return html_content
    
    # Otherwise, write to file (for CLI usage)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_file


def calculate_dropped_issues_stats(dropped_issues):
    """
    Calculate statistics for dropped issues by filter level and directory.
    
    Args:
        dropped_issues: List of dropped issue dictionaries
        
    Returns:
        Dictionary with statistics by filter level and directory info
    """
    stats = {
        'total': len(dropped_issues),
        'level1': 0,
        'level2': 0,
        'level3': 0
    }
    
    for issue in dropped_issues:
        filter_level = issue.get('filter_level', '').lower()
        if 'level 1' in filter_level or 'level1' in filter_level:
            stats['level1'] += 1
        elif 'level 2' in filter_level or 'level2' in filter_level:
            stats['level2'] += 1
        elif 'level 3' in filter_level or 'level3' in filter_level:
            stats['level3'] += 1
    
    # Build directory tree from dropped issues
    # Extract original issues and build tree
    extracted_issues = []
    for dropped_issue in dropped_issues:
        original_issue = dropped_issue.get('original_issue') or (dropped_issue.get('results', [{}])[0] if dropped_issue.get('results') else {})
        if original_issue:
            extracted_issues.append(original_issue)
    
    directory_tree, directory_counts = build_directory_tree(extracted_issues)
    stats['directories'] = directory_counts
    stats['directory_tree'] = directory_tree
    
    return stats


def format_issue_type_name(issue_type):
    """Convert camelCase issue type to readable format while preserving acronyms.
    
    Examples:
        'logicBug' -> 'Logic Bug'
        'HTTPRequest' -> 'HTTP Request'
        'null_pointer' -> 'Null Pointer'
        'unknown' -> 'Unknown'
    """
    if issue_type == 'unknown':
        return 'Unknown'

    # Handle camelCase by inserting spaces before capital letters
    # Insert space before capital letters that follow lowercase letters
    formatted = re.sub(r'([a-z])([A-Z])', r'\1 \2', issue_type)
    # Handle consecutive capitals (like "HTTPRequest" -> "HTTP Request")
    formatted = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', formatted)
    # Replace underscores with spaces
    formatted = formatted.replace('_', ' ')
    
    # Title case each word, but preserve consecutive uppercase (acronyms)
    words = formatted.split()
    result_words = []
    for word in words:
        if word.isupper():
            # Keep acronyms as-is (e.g., 'HTTP', 'API')
            result_words.append(word)
        else:
            # Capitalize the first letter, keep the rest as-is
            result_words.append(word[0].upper() + word[1:] if word else word)
    
    return ' '.join(result_words)

def generate_directory_tree_html(tree, path_prefix="", level=0, is_last_items=None):
    """Generate HTML for hierarchical directory tree with tree-like structure"""
    items = []
    sorted_items = sorted(tree.items())

    if is_last_items is None:
        is_last_items = []

    for i, (name, data) in enumerate(sorted_items):
        full_path = f"{path_prefix}/{name}" if path_prefix else name
        count = data['_count']
        children = data['_children']
        is_last = i == len(sorted_items) - 1

        # Create tree-like prefix without vertical lines
        tree_prefix = ""
        for _ in range(level):
            tree_prefix += "   "  # Just empty space for indentation

        if level > 0:
            tree_prefix += "└─ " if is_last else "├─ "

        has_children = len(children) > 0
        expand_class = "expandable" if has_children else ""
        folder_icon = "📁" if has_children else "📄"

        # Add leaf indicator for nodes without children
        toggle_or_indicator = f'<span class="directory-toggle">{"+" if has_children else ""}</span>' if has_children else '<span class="leaf-indicator"></span>'

        items.append(f'''        <div class="sidebar-item directory-item {expand_class}" data-directory="{full_path}" data-level="{level}">
            <span class="tree-prefix">{tree_prefix}</span>
            {toggle_or_indicator}
            <span class="folder-icon">{folder_icon}</span>
            <span class="sidebar-item-text">{name}</span>
            <span class="sidebar-count">({count})</span>
        </div>''')

        # Add children (initially hidden if this is expandable)
        if has_children:
            new_is_last = is_last_items + [is_last]
            child_html = generate_directory_tree_html(children, full_path, level + 1, new_is_last)
            items.append(f'''        <div class="directory-children" style="display: none;">
{child_html}
        </div>''')

    return '\n'.join(items)

def generate_sidebar_items(directories, directory_tree):
    """Generate sidebar items for directories with hierarchical structure"""
    items = []

    # Add special directories first (except Unknown)
    for directory, count in directories.items():
        if directory in ["uncategorized", "root"]:
            formatted_directory = format_directory_name(directory)
            items.append(f'''        <div class="sidebar-item" data-directory="{directory}">
            <span class="sidebar-item-text">{formatted_directory}</span>
            <span class="sidebar-count">({count})</span>
        </div>''')

    # Add hierarchical tree
    if directory_tree:
        tree_html = generate_directory_tree_html(directory_tree)
        items.append(tree_html)

    # Add Unknown directory at the bottom
    if "Unknown" in directories:
        count = directories["Unknown"]
        formatted_directory = format_directory_name("Unknown")
        items.append(f'''        <div class="sidebar-item directory-item" data-directory="Unknown" data-level="0">
            <span class="tree-prefix"></span>
            <span class="directory-toggle"></span>
            <span class="folder-icon">📄</span>
            <span class="sidebar-item-text">{formatted_directory}</span>
            <span class="sidebar-count">({count})</span>
        </div>''')

    return '\n'.join(items)

def format_directory_name(directory):
    """Format directory name for display"""
    if directory == "uncategorized":
        return "Uncategorized"
    elif directory == "root":
        return "Root Directory"
    elif directory == "Unknown":
        return "Unknown"
    else:
        # Show only the last part of the directory path for cleaner display
        return directory.split('/')[-1] if '/' in directory else directory.split('\\')[-1] if '\\' in directory else directory

def main(directory="llmOutput"):
    """Main function to generate the report"""
    print("HindSight Report Generator")
    print("=" * 40)

    # Read all issues from specified directory
    print(f"Reading files from {directory} directory...")
    issues = read_llm_output_files(directory)

    if not issues:
        print(f"No issues found in {directory} directory!")
        return

    print(f"Found {len(issues)} issues")

    # Generate HTML report
    print("Generating HTML report...")
    output_file = generate_html_report(issues)

    print(f"Report generated successfully: {output_file}")

    # Print summary statistics
    stats = calculate_stats(issues)
    print("\nSummary:")
    print(f"  Total Issues: {stats['total']}")
    print(f"  Critical Severity: {stats['critical']}")
    print(f"  High Severity: {stats['high']}")
    print(f"  Medium Severity: {stats['medium']}")
    print(f"  Low Severity: {stats['low']}")
    print(f"  Directories: {len(stats['directories'])}")

if __name__ == "__main__":
    main()