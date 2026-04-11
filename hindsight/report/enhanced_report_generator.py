#!/usr/bin/env python3

# Created by Sridhar Gurivireddy

"""
Enhanced Hindsight Report Generator with Callstack Overlay Support
Reads all files from claude output directory and generates an HTML report with interactive callstack overlays
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
from ..utils.output_directory_provider import get_output_directory_provider
from ..utils.hash_util import HashUtil

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


DEFAULT_HTML_REPORT = f"hindsight_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

def extract_callstack_data(issues):
    """
    Extract callstack information from issues and create separate callstack data files.
    Uses the original_callstack data embedded in the analysis results, and tries to match
    issues without embedded callstack data to existing callstack data.
    Issues with the same Callstack text should share the same callstack data.

    Args:
        issues: List of issue dictionaries

    Returns:
        tuple: (issues_with_callstack_refs, callstack_data_map)
    """
    callstack_data_map = {}
    issues_with_refs = []
    callstack_text_to_id = {}  # Map callstack text to callstack ID for sharing

    # Load original callstack data to help with matching

    for issue in issues:
        # Create a copy of the issue
        issue_copy = issue.copy()

        # Check if issue has embedded original callstack data
        callstack_info = None
        callstack_text = issue.get('Callstack', '')

        # First, check if we already have a callstack ID for this callstack text
        if callstack_text and callstack_text in callstack_text_to_id:
            # Reuse existing callstack ID for issues with the same callstack
            callstack_id = callstack_text_to_id[callstack_text]
            issue_copy['callstack_id'] = callstack_id
            issue_copy['has_callstack'] = True
            issues_with_refs.append(issue_copy)
            continue

        # Check if issue has embedded original_callstack data
        if 'original_callstack' in issue and issue['original_callstack']:
            callstack_info = issue['original_callstack']
            # Remove the original_callstack from the issue copy to avoid duplication
            if 'original_callstack' in issue_copy:
                del issue_copy['original_callstack']
        else:
            # If no embedded callstack found, try to match with existing callstack data
            # Note: This can happen when multiple issues are generated from the same analysis
            # but only some have embedded callstack data
            callstack_info = None

        if callstack_info:
            # Generate a unique ID based on the callstack text (not issue-specific context)
            # This ensures issues with the same callstack share the same ID
            if callstack_text:
                # Use callstack text as the primary key for ID generation
                callstack_id = HashUtil.hash_for_callstack_md5(callstack_text, truncate_length=12)
            else:
                # Fallback to issue-specific context if no callstack text
                callstack_with_context = {
                    'callstack_data': callstack_info,
                    'issue_file': issue.get('file_name', ''),
                    'issue_function': issue.get('function', ''),
                    'issue_line': issue.get('lines', ''),
                    'issue_severity': issue.get('kind', issue.get('severity', ''))
                }
                callstack_id = HashUtil.hash_for_callstack_context_md5(callstack_with_context, truncate_length=12)

            # Store the mapping from callstack text to ID
            if callstack_text:
                callstack_text_to_id[callstack_text] = callstack_id

            # Check if this callstack already exists in the map
            if callstack_id in callstack_data_map:
                # Use existing callstack data - don't overwrite the issue context
                existing_callstack = callstack_data_map[callstack_id]

                # Only update issue context if the existing one is empty or if this is a better match
                existing_context = existing_callstack.get('issue_context', {})
                current_issue_context = {
                    'file': issue.get('file_path', ''),
                    'function': issue.get('function', ''),
                    'line': issue.get('lines', ''),
                    'severity': issue.get('kind', issue.get('severity', ''))
                }

                # If existing context is empty or if this issue has more specific information, update it
                if (not existing_context.get('file') or not existing_context.get('function') or
                    (current_issue_context.get('file') and current_issue_context.get('function'))):
                    existing_callstack['issue_context'] = current_issue_context
            else:
                # Store new callstack data with proper issue context
                issue_context = {
                    'file': issue.get('file_path', ''),
                    'function': issue.get('function', ''),
                    'line': issue.get('lines', ''),
                    'severity': issue.get('kind', issue.get('severity', ''))
                }

                # If this is original callstack data, preserve its original context
                if isinstance(callstack_info, dict) and callstack_info.get('issue_context'):
                    issue_context = callstack_info['issue_context']

                callstack_data_map[callstack_id] = {
                    'id': callstack_id,
                    'data': callstack_info,
                    'issue_context': issue_context
                }

            # Add callstack reference to issue
            issue_copy['callstack_id'] = callstack_id
            issue_copy['has_callstack'] = True
        else:
            # Keep original issue type even if no callstack data is available
            issue_copy['has_callstack'] = False

        issues_with_refs.append(issue_copy)

    return issues_with_refs, callstack_data_map

def save_callstack_data(callstack_data_map, output_dir="."):
    """
    Save callstack data to separate JSON files.

    Args:
        callstack_data_map: Dictionary mapping callstack IDs to callstack data
        output_dir: Directory to save callstack files

    Returns:
        str: Path to the main callstack index file
    """
    # Create callstack directory
    callstack_dir = Path(output_dir) / "callstacks"
    callstack_dir.mkdir(exist_ok=True)

    # Save individual callstack files
    callstack_index = {}

    for callstack_id, callstack_info in callstack_data_map.items():
        # Save individual callstack file
        callstack_file = callstack_dir / f"{callstack_id}.json"
        with open(callstack_file, 'w', encoding='utf-8') as f:
            json.dump(callstack_info, f, indent=2)

        # Add to index
        callstack_index[callstack_id] = {
            'file': f"callstacks/{callstack_id}.json",
            'context': callstack_info['issue_context']
        }

    # Save callstack index
    index_file = Path(output_dir) / "callstack_index.json"
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(callstack_index, f, indent=2)

    return str(index_file)

def read_claude_output_files(directory, file_suffix="_analysis.json"):
    """Read all analysis JSON files from the given directory"""
    all_issues = []
    file_pattern = os.path.join(directory, f"*{file_suffix}")
    files_processed = 0

    for file_path in glob.glob(file_pattern):
        data = read_json_file(file_path)
        if data is not None:
            if isinstance(data, list):
                # Validate each item in the list is a dictionary
                for item in data:
                    if isinstance(item, dict):
                        all_issues.append(item)
                    else:
                        print(f"Warning: Skipping non-dictionary item in {file_path}: {type(item).__name__}")
                files_processed += 1
            elif isinstance(data, dict):
                # Only add if it's a dictionary
                all_issues.append(data)
                files_processed += 1
            else:
                print(f"Warning: Skipping non-dictionary data in {file_path}: {type(data).__name__}")
                continue
        else:
            print(f"Warning: Could not parse {file_path}")
            continue

    print(f"Processed {files_processed} files with data")
    return all_issues

def extract_directory_from_file(file_path):
    """
    Extract directory from file path.

    This function should only be used for directory categorization in the HTML report.
    The actual directory assignment should be done by IssueDirectoryOrganizer in the analyzers.
    """
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

    for issue in issues:
        file_path = issue.get('file_path', issue.get('file', ''))
        directory = extract_directory_from_file(file_path)
        directory_counts[directory] += 1

        if directory == "uncategorized" or directory == "root" or directory == "Unknown":
            continue

        # Split directory path into parts
        parts = directory.split('/')
        current = tree

        # Build nested structure
        for part in parts:
            if part not in current:
                current[part] = {'_count': 0, '_children': {}}
            current[part]['_count'] += 1
            current = current[part]['_children']

    return tree, dict(directory_counts)

def calculate_stats(issues):
    """Calculate statistics from the issues"""
    # Use 'kind' instead of 'severity' for the new schema
    severity_counts = Counter(issue.get('kind', issue.get('severity', 'unknown')) for issue in issues)

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


def generate_html_report_with_callstacks(issues, output_file=DEFAULT_HTML_REPORT, project_name=None):
    """Generate HTML report with callstack overlay functionality"""

    # Extract callstack data and create separate files
    issues_with_refs, callstack_data_map = extract_callstack_data(issues)

    # Save callstack data to separate files
    output_dir = Path(output_file).parent
    callstack_index_file = save_callstack_data(callstack_data_map, output_dir)

    print(f"Saved {len(callstack_data_map)} callstack data files")
    print(f"Callstack index: {callstack_index_file}")

    # Calculate stats
    stats = calculate_stats(issues_with_refs)

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

    # Load callstack index for embedding in HTML
    try:
        with open(callstack_index_file, 'r', encoding='utf-8') as f:
            callstack_index_data = json.load(f)
    except:
        callstack_index_data = {}

    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LLM_StaticAnalysys analysis report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display",
                         "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif;
            background: #f5f5f7;
            min-height: 100vh;
            display: flex;
            overflow-x: hidden;
            color: #1d1d1f;
        }}

        .sidebar {{
            width: 320px;
            background: #ffffff;
            color: #1d1d1f;
            padding: 20px;
            box-shadow: 0 0 0 1px rgba(0,0,0,0.06);
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
            text-align: center;
            border-bottom: 1px solid rgba(0,0,0,0.08);
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 20px;
            font-weight: 600;
            letter-spacing: -0.02em;
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
            color: #6e6e73;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        .critical {{ color: #b00020; }}
        .high {{ color: #bf2600; }}
        .medium {{ color: #b35c00; }}
        .low {{ color: #2a7f3f; }}
        .total {{ color: #06c; }}

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
            justify-content: center;
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

        .severity-container {{
            display: flex;
            align-items: center;
            gap: 8px;
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
            justify-content: space-between;
            align-items: center;
        }}

        .kv dt {{
            font-weight: 600;
            color: #424245;
        }}

        .kv dd {{
            margin: 0 .25rem 0 0;
        }}

        .kv .line-item {{
            margin-left: auto;
        }}

        .issue__body {{
            padding: 1rem 1.25rem;
        }}

        .issue__title {{
            font-size: 1rem;
            margin: .25rem 0 .5rem;
        }}

        .badge {{
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: .8rem;
            font-weight: 600;
            height: 24px;
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

        /* Callstack overlay styles */
        .callstack-link {{
            color: #007bff;
            text-decoration: underline;
            cursor: pointer;
            font-weight: 500;
        }}

        .callstack-link:hover {{
            color: #0056b3;
            text-decoration: none;
        }}

        .callstack-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.7);
            z-index: 1000;
            display: none;
            justify-content: center;
            align-items: center;
        }}

        .callstack-content {{
            background: #ffffff;
            border-radius: 12px;
            padding: 30px;
            max-width: 90%;
            max-height: 90%;
            overflow-y: auto;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
            position: relative;
        }}

        .callstack-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            border-bottom: 2px solid #e5e5ea;
            padding-bottom: 15px;
        }}

        .callstack-title {{
            font-size: 1.5em;
            font-weight: 600;
            color: #1d1d1f;
        }}

        .callstack-close {{
            background: #f5f5f7;
            border: none;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            cursor: pointer;
            font-size: 1.2em;
            color: #666;
            transition: background-color 0.2s ease;
        }}

        .callstack-close:hover {{
            background: #e5e5ea;
            color: #333;
        }}

        .callstack-data {{
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            background: #f8f9fa;
            border: 1px solid #e5e5ea;
            border-radius: 8px;
            padding: 20px;
            white-space: pre-wrap;
            font-size: 0.9em;
            line-height: 1.5;
            color: #333;
        }}

        .callstack-context {{
            margin-bottom: 20px;
            padding: 15px;
            background: #f0f7f0;
            border-radius: 8px;
            border-left: 4px solid #28a745;
        }}

        .callstack-context h4 {{
            margin: 0 0 10px 0;
            color: #155724;
            font-size: 1.1em;
        }}

        .callstack-context-item {{
            margin: 5px 0;
            font-size: 0.9em;
        }}

        .callstack-context-label {{
            font-weight: 600;
            color: #155724;
        }}

        /* Enhanced callstack display styles */
        .callstack-type {{
            font-weight: 600;
            color: #1d1d1f;
            margin-bottom: 15px;
            padding: 8px 12px;
            background: #e8f4fd;
            border-radius: 6px;
            border-left: 4px solid #007bff;
        }}

        .callstack-entries {{
            margin-bottom: 20px;
        }}

        .callstack-entry {{
            margin-bottom: 8px;
            padding: 8px 12px;
            background: #f8f9fa;
            border-radius: 6px;
            border-left: 3px solid #28a745;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 0.9em;
            line-height: 1.4;
        }}

        .function-info {{
            margin-bottom: 6px;
        }}

        .function-name {{
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-weight: 600;
            color: #2c3e50;
        }}

        .cost-percentage {{
            background: #28a745;
            color: white;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
            margin-left: 8px;
        }}

        /* Normalized cost badge for issue boxes */
        .normalized-cost-badge {{
            background: #28a745;
            color: white;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: 600;
            margin-right: 8px;
            display: inline-flex;
            align-items: center;
            height: 24px;
        }}

        /* Issue type badge for issue titles */
        .issue-type-badge {{
            background: #6c757d;
            color: white;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.75em;
            font-weight: 600;
            margin-left: 8px;
            display: inline-flex;
            align-items: center;
            height: 20px;
        }}

        .entry-detail {{
            font-size: 0.9em;
            color: #6c757d;
            margin: 2px 0;
        }}

        .callstack-summary {{
            background: #fff3cd;
            border: 1px solid #ffeaa7;
            border-radius: 6px;
            padding: 12px;
            margin-top: 15px;
        }}

        .callstack-summary h5 {{
            margin: 0 0 8px 0;
            color: #856404;
            font-size: 1em;
        }}

        .callstack-summary div {{
            margin: 4px 0;
            font-size: 0.9em;
            color: #856404;
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

            .callstack-content {{
                max-width: 95%;
                max-height: 95%;
                padding: 20px;
            }}

            .callstack-title {{
                font-size: 1.2em;
            }}
        }}
    </style>
</head>
<body>
    <div class="sidebar">
        <h3>LLM_StaticAnalysys</h3>
        <div class="sidebar-item active" data-directory="all">
            <span class="sidebar-item-text">All Issues</span>
            <span class="sidebar-count">({stats['total']})</span>
        </div>
        {generate_sidebar_items(stats['directories'], stats['directory_tree'])}
    </div>

    <div class="main-content">
        <div class="container">
            <div class="header">
                <h1>Trace Analysis</h1>
                <div style="display: flex; align-items: center; font-size: 1.1em; opacity: 0.8;">
                    <span style="flex: 0 0 33.33%;">{repository_text}</span>
                    <span style="flex: 0 0 33.33%; text-align: center;"></span>
                    <span style="flex: 0 0 33.33%; text-align: right;">{date_text}</span>
                </div>
            </div>

            <div class="filters">
                <div class="filter-group">
                    <button class="filter-btn active" data-filter="all">All Issues ({stats['total']})</button>
                    <button class="filter-btn" data-filter="critical">Critical ({stats['critical']})</button>
                    <button class="filter-btn" data-filter="high">High ({stats['high']})</button>
                    <button class="filter-btn" data-filter="medium">Medium ({stats['medium']})</button>
                    <button class="filter-btn" data-filter="low">Low ({stats['low']})</button>
                </div>
            </div>

            <div class="issues-container" id="issuesContainer">
                <!-- Issues will be populated by JavaScript -->
            </div>
        </div>
    </div>

    <!-- Callstack Overlay -->
    <div class="callstack-overlay" id="callstackOverlay">
        <div class="callstack-content">
            <div class="callstack-header">
                <h3 class="callstack-title">Callstack</h3>
                <button class="callstack-close" onclick="hideCallstack()">&times;</button>
            </div>
            <div class="callstack-context" id="callstackContext">
                <!-- Context will be populated by JavaScript -->
            </div>
            <div class="callstack-data" id="callstackData">
                <!-- Callstack data will be populated by JavaScript -->
            </div>
        </div>
    </div>

    <script>
        const issuesData = {json.dumps({"issues": issues_with_refs}, indent=8)};
        const callstackIndex = {json.dumps(callstack_index_data, indent=8)};
        const embeddedCallstackData = {json.dumps(callstack_data_map, indent=8)};

        // Store currently displayed issues for copy functionality
        let currentlyDisplayedIssues = [];

        // Function to extract the smallest normalized cost (top of callstack) from an issue
        function getSmallestNormalizedCost(issue) {{
            if (!issue.has_callstack || !issue.callstack_id) {{
                return null;
            }}

            const callstackData = embeddedCallstackData[issue.callstack_id];
            if (!callstackData || !callstackData.data) {{
                return null;
            }}

            const data = callstackData.data;
            let smallestCost = null;

            // Handle different callstack data formats
            if (data.entries && Array.isArray(data.entries)) {{
                // Find the smallest non-zero normalized cost
                for (const entry of data.entries) {{
                    const normalizedCost = entry.normalized_cost || entry.normalizedCost;
                    if (normalizedCost !== undefined && normalizedCost !== null && normalizedCost > 0) {{
                        if (smallestCost === null || normalizedCost < smallestCost) {{
                            smallestCost = normalizedCost;
                        }}
                    }}
                }}
            }} else if (data.normalized_cost && Array.isArray(data.normalized_cost)) {{
                // Find the smallest non-zero normalized cost from the array
                for (const cost of data.normalized_cost) {{
                    if (cost !== undefined && cost !== null && cost > 0) {{
                        if (smallestCost === null || cost < smallestCost) {{
                            smallestCost = cost;
                        }}
                    }}
                }}
            }}

            return smallestCost;
        }}

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

                // Use file_name if available, otherwise extract from file_path
                const fileName = issue.file_name || (issue.file_path ? issue.file_path.split('/').pop() : 'Unknown File');
                const categoryTag = formatIssueType(issue.issueType || 'unknown');
                const severityUpper = (issue.kind || issue.severity || 'unknown').toUpperCase();

                // Remove tangential label - no longer needed
                const tangentialLabel = '';

                // Create callstack link if available (for function field)
                let callstackLink = '';
                if (issue.has_callstack && issue.callstack_id) {{
                    callstackLink = ` <span class="callstack-link" onclick="showCallstack('${{issue.callstack_id}}')">↗</span>`;
                }}

                // Get normalized cost badge if available
                const smallestCost = getSmallestNormalizedCost(issue);
                const normalizedCostBadge = smallestCost !== null && smallestCost > 0
                    ? `<span class="normalized-cost-badge">${{smallestCost.toFixed(3)}}%</span>`
                    : '';

                issueSection.innerHTML = `
                    <header class="issue__head">
                        <h2 class="issue__file">${{fileName}}<button class="copy-btn" onclick="copyIssueToClipboard(${{index}})">Copy</button></h2>
                        <div class="severity-container">
                            ${{tangentialLabel}}
                            ${{normalizedCostBadge}}
                            <span class="badge badge--severity">${{severityUpper}}</span>
                        </div>
                    </header>

                    <div class="issue__meta">
                        <dl class="kv">
                            <div><dt>Function</dt><dd><code>${{issue.function_name || issue.function || 'Unknown'}}</code>${{callstackLink}}</dd></div>
                            <div class="line-item"><dt>Line</dt><dd>${{issue.line_number || issue.lines || issue.line || 'N/A'}}</dd></div>
                        </dl>
                    </div>

                    <article class="issue__body">
                        <h3 class="issue__title">Issue<span class="issue-type-badge">${{categoryTag}}</span></h3>
                        <p>${{issue.description || issue.issue || 'No description available'}}</p>

                        ${{(issue.Impact || issue.impact) ? `
                        <details class="callout callout--impact" open>
                            <summary>Impact</summary>
                            <p>${{issue.Impact || issue.impact}}</p>
                        </details>
                        ` : ''}}

                        <details class="callout callout--solution" open>
                            <summary>Potential Solution</summary>
                            <p>${{issue['Potential solution'] || issue.potentialSolution || 'No solution provided'}}</p>
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

        // Callstack overlay functions
        function showCallstack(callstackId) {{
            const overlay = document.getElementById('callstackOverlay');
            const contextDiv = document.getElementById('callstackContext');
            const dataDiv = document.getElementById('callstackData');

            if (embeddedCallstackData[callstackId]) {{
                const callstackData = embeddedCallstackData[callstackId];

                // Hide context section - user doesn't want it
                contextDiv.style.display = 'none';

                // Format and display callstack data with enhanced formatting
                dataDiv.innerHTML = formatCallstackData(callstackData.data);
                overlay.style.display = 'flex';
            }} else {{
                contextDiv.innerHTML = '<h4>Callstack Not Found</h4>';
                dataDiv.textContent = 'Callstack data not available for ID: ' + callstackId;
                overlay.style.display = 'flex';
            }}
        }}

        function formatCallstackData(data) {{
            let formattedHtml = '';

            // Handle different callstack data formats
            if (data.type === 'trace_analysis_issue') {{
                // Handle minimal trace analysis callstack info
                formattedHtml += '<div class="callstack-entries">';
                formattedHtml += '<div class="callstack-type">Trace Analysis Issue</div>';
                formattedHtml += '<div class="callstack-entry">';
                formattedHtml += `<span class="function-name">${{data.function || 'Unknown function'}}</span>`;
                if (data.file) {{
                    formattedHtml += ` in ${{data.file}}`;
                }}
                if (data.line) {{
                    formattedHtml += ` at line ${{data.line}}`;
                }}
                formattedHtml += '</div>';
                formattedHtml += '<div style="margin-top: 15px; padding: 10px; background: #fff3cd; border-radius: 6px; font-size: 0.9em;">';
                formattedHtml += 'This issue was identified from trace analysis. The complete callstack data may be available in the original trace files.';
                formattedHtml += '</div>';
                formattedHtml += '</div>';
            }} else if (data.entries && Array.isArray(data.entries)) {{
                // Handle structured entries format: function_name file:owner <normalizedCost>%
                formattedHtml += '<div class="callstack-entries">';

                data.entries.forEach((entry, index) => {{
                    const functionPath = entry.function_path || entry.function || entry.path || 'Unknown function';
                    const normalizedCost = entry.normalized_cost || entry.normalizedCost;
                    const ownerName = entry.owner_name || entry.ownerName || '';
                    const sourcePath = entry.source_path || entry.sourcePath || '';
                    const filename = entry.filename || (sourcePath ? sourcePath.split('/').pop() : '');

                    formattedHtml += '<div class="callstack-entry">';
                    formattedHtml += `<span class="function-name">${{functionPath}}</span>`;

                    // Add file:owner format
                    if (filename || ownerName) {{
                        formattedHtml += ' ';
                        if (filename && ownerName) {{
                            formattedHtml += `${{filename}}:${{ownerName}}`;
                        }} else if (filename) {{
                            formattedHtml += filename;
                        }} else if (ownerName) {{
                            formattedHtml += ownerName;
                        }}
                    }}

                    // Add normalized cost percentage if available
                    if (normalizedCost !== undefined && normalizedCost !== null) {{
                        formattedHtml += ` <span class="cost-percentage">${{normalizedCost.toFixed(3)}}%</span>`;
                    }}

                    formattedHtml += '</div>';
                }});

                formattedHtml += '</div>';
            }} else if (Array.isArray(data)) {{
                // Handle simple array of function names (complete callstack)
                formattedHtml += '<div class="callstack-entries">';
                formattedHtml += '<div class="callstack-type">Complete Callstack</div>';

                data.forEach((functionName, index) => {{
                    formattedHtml += '<div class="callstack-entry">';
                    formattedHtml += `<span class="function-name">${{functionName}}</span>`;
                    formattedHtml += '</div>';
                }});

                formattedHtml += '</div>';
            }} else if (data.callstack && Array.isArray(data.callstack)) {{
                // Handle callstack field containing array of function names
                formattedHtml += '<div class="callstack-entries">';
                formattedHtml += '<div class="callstack-type">Complete Callstack</div>';

                data.callstack.forEach((functionName, index) => {{
                    formattedHtml += '<div class="callstack-entry">';
                    formattedHtml += `<span class="function-name">${{functionName}}</span>`;
                    formattedHtml += '</div>';
                }});

                formattedHtml += '</div>';
            }} else if (typeof data === 'string') {{
                // Handle callstack as a string (split by newlines or arrows)
                let lines;
                if (data.includes('→')) {{
                    // Split by arrows for trace analysis callstacks
                    lines = data.split('→').map(line => line.trim()).filter(line => line);
                }} else {{
                    // Split by newlines for traditional callstacks
                    lines = data.split('\\n').filter(line => line.trim());
                }}

                formattedHtml += '<div class="callstack-entries">';
                formattedHtml += '<div class="callstack-type">Callstack</div>';

                lines.forEach((line, index) => {{
                    const cleanLine = line.trim().replace(/^["']|["']$/g, ''); // Remove quotes
                    if (cleanLine) {{
                        formattedHtml += '<div class="callstack-entry">';
                        formattedHtml += `<span class="function-name">${{cleanLine}}</span>`;
                        formattedHtml += '</div>';
                    }}
                }});

                formattedHtml += '</div>';
            }} else {{
                // Fallback to JSON display for other formats
                formattedHtml = `<pre>${{JSON.stringify(data, null, 2)}}</pre>`;
            }}

            return formattedHtml;
        }}

        function hideCallstack() {{
            const overlay = document.getElementById('callstackOverlay');
            overlay.style.display = 'none';
        }}

        // Function to show direct callstack from new format
        function showDirectCallstack(issueIndex) {{
            const issue = issuesData.issues[issueIndex];
            const overlay = document.getElementById('callstackOverlay');
            const contextDiv = document.getElementById('callstackContext');
            const dataDiv = document.getElementById('callstackData');

            if (issue.Callstack) {{
                // Hide context section
                contextDiv.style.display = 'none';

                // Display the callstack directly
                let formattedHtml = '';
                if (typeof issue.Callstack === 'string') {{
                    const lines = issue.Callstack.split('\\n').filter(line => line.trim());
                    formattedHtml += '<div class="callstack-entries">';
                    formattedHtml += '<div class="callstack-type">Callstack</div>';

                    lines.forEach((line, index) => {{
                        const cleanLine = line.trim();
                        if (cleanLine) {{
                            formattedHtml += '<div class="callstack-entry">';
                            formattedHtml += `<span class="function-name">${{cleanLine}}</span>`;
                            formattedHtml += '</div>';
                        }}
                    }});

                    formattedHtml += '</div>';
                }} else {{
                    formattedHtml = `<pre>${{JSON.stringify(issue.Callstack, null, 2)}}</pre>`;
                }}

                dataDiv.innerHTML = formattedHtml;
                overlay.style.display = 'flex';
            }} else {{
                contextDiv.innerHTML = '<h4>Callstack Not Found</h4>';
                dataDiv.textContent = 'No callstack data available for this issue';
                overlay.style.display = 'flex';
            }}
        }}

        // Close overlay when clicking outside the content
        document.getElementById('callstackOverlay').addEventListener('click', function(e) {{
            if (e.target === this) {{
                hideCallstack();
            }}
        }});

        // Close overlay with Escape key
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') {{
                hideCallstack();
            }}
        }});

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
            const functionName = issue.function_name || issue.function || 'Unknown';
            
            // Extract filename from the full path for the title
            let fileName = 'Unknown File';
            if (issue.file_name) {{
                fileName = issue.file_name;
            }} else if (filePath && filePath !== 'Unknown File') {{
                if (filePath.includes('/')) {{
                    fileName = filePath.split('/').pop();
                }} else if (filePath.includes('\\\\')) {{
                    fileName = filePath.split('\\\\').pop();
                }} else {{
                    fileName = filePath;
                }}
            }}

            // Get normalized cost if available
            const smallestCost = getSmallestNormalizedCost(issue);
            const normalizedCostText = smallestCost !== null && smallestCost > 0
                ? `\\nNormalized Cost: ${{smallestCost.toFixed(3)}}%`
                : '';

            // Get callstack text if available
            const callstackText = getCallstackText(issue);
            const callstackSection = callstackText && callstackText !== 'No callstack available'
                ? `\\nOriginal Trace Callstack:\\n${{callstackText}}`
                : '';

            const content = `Title: In ${{functionName}}() in ${{fileName}}, there is potential issue

LLM_StaticAnalysys has found a potential issue

In ${{functionName}}() in ${{filePath}}, there is potential issue${{normalizedCostText}}

Impact: ${{issue.Impact || issue.impact || 'No impact information available'}}

Potential Solution: ${{issue['Potential solution'] || issue.potentialSolution || 'No solution provided'}}${{callstackSection}}`;

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

        function getCallstackText(issue) {{
            if (!issue.has_callstack || !issue.callstack_id) {{
                return 'No callstack available';
            }}

            const callstackData = embeddedCallstackData[issue.callstack_id];
            if (!callstackData) {{
                return 'Callstack data not found';
            }}

            const data = callstackData.data;
            let callstackText = '';

            // Handle different callstack data formats
            if (data.type === 'trace_analysis_issue') {{
                // Handle minimal trace analysis callstack info
                callstackText = `${{data.function || 'Unknown function'}}`;
                if (data.file) {{
                    callstackText += ` in ${{data.file}}`;
                }}
                if (data.line) {{
                    callstackText += ` at line ${{data.line}}`;
                }}
                callstackText += '\\n(Complete callstack data may be available in original trace files)';
            }} else if (data.entries && Array.isArray(data.entries)) {{
                data.entries.forEach((entry, index) => {{
                    const functionPath = entry.function_path || entry.function || entry.path || 'Unknown function';
                    const normalizedCost = entry.normalized_cost || entry.normalizedCost;
                    const ownerName = entry.owner_name || entry.ownerName || '';
                    const sourcePath = entry.source_path || entry.sourcePath || '';
                    const filename = entry.filename || (sourcePath ? sourcePath.split('/').pop() : '');

                    callstackText += functionPath;

                    if (filename || ownerName) {{
                        callstackText += ' ';
                        if (filename && ownerName) {{
                            callstackText += `${{filename}}:${{ownerName}}`;
                        }} else if (filename) {{
                            callstackText += filename;
                        }} else if (ownerName) {{
                            callstackText += ownerName;
                        }}
                    }}

                    if (normalizedCost !== undefined && normalizedCost !== null) {{
                        callstackText += ` ${{normalizedCost.toFixed(3)}}%`;
                    }}

                    callstackText += '\\n';
                }});
            }} else if (Array.isArray(data)) {{
                callstackText = data.join('\\n');
            }} else if (data.callstack && Array.isArray(data.callstack)) {{
                callstackText = data.callstack.join('\\n');
            }} else if (typeof data === 'string') {{
                let lines;
                if (data.includes('→')) {{
                    // Split by arrows for trace analysis callstacks
                    lines = data.split('→').map(line => line.trim()).filter(line => line);
                }} else {{
                    // Split by newlines for traditional callstacks
                    lines = data.split('\\n').filter(line => line.trim());
                }}
                callstackText = lines.map(line => line.trim().replace(/^["']|["']$/g, '')).join('\\n');
            }} else {{
                callstackText = JSON.stringify(data, null, 2);
            }}

            return callstackText || 'No callstack data available';
        }}


        // Global filter state
        let currentFilters = {{
            severity: 'all',
            directory: 'all',
        }};

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
                critical: issues.filter(issue => (issue.kind || issue.severity) === 'critical').length,
                high: issues.filter(issue => (issue.kind || issue.severity) === 'high').length,
                medium: issues.filter(issue => (issue.kind || issue.severity) === 'medium').length,
                low: issues.filter(issue => (issue.kind || issue.severity) === 'low').length
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
                filteredIssues = filteredIssues.filter(issue => (issue.kind || issue.severity) === currentFilters.severity);
            }}


            // Apply directory filter
            if (currentFilters.directory !== 'all') {{
                filteredIssues = filteredIssues.filter(issue => {{
                    const issueDir = getIssueDirectory(issue.file_path || issue.file);

                    // Handle "Unknown" directory specially - it contains issues that couldn't be assigned
                    if (currentFilters.directory === 'Unknown') {{
                        // Show issues that have no valid directory assignment or are explicitly marked as unknown
                        return issueDir === 'uncategorized' || !issue.file || issue.file === '' ||
                               (issue.hasOwnProperty('directory_assigned') && !issue.directory_assigned);
                    }}

                    // Show issues that are in the selected directory OR in any of its subdirectories
                    return issueDir === currentFilters.directory || issueDir.startsWith(currentFilters.directory + '/');
                }});
            }}

            renderIssues(filteredIssues);
        }}

        function updateStatsForDirectory() {{
            // Only update stats when filtering by directory, not severity
            let directoryFilteredIssues = issuesData.issues;


            if (currentFilters.directory !== 'all') {{
                directoryFilteredIssues = directoryFilteredIssues.filter(issue => {{
                    const issueDir = getIssueDirectory(issue.file_path || issue.file);

                    // Handle "Unknown" directory specially - it contains issues that couldn't be assigned
                    if (currentFilters.directory === 'Unknown') {{
                        // Show issues that have no valid directory assignment or are explicitly marked as unknown
                        return issueDir === 'uncategorized' || !issue.file || issue.file === '' ||
                               (issue.hasOwnProperty('directory_assigned') && !issue.directory_assigned);
                    }}

                    // Show issues that are in the selected directory OR in any of its subdirectories
                    return issueDir === currentFilters.directory || issueDir.startsWith(currentFilters.directory + '/');
                }});
            }}

            updateStats(directoryFilteredIssues);
        }}

        function filterIssues(severity) {{
            currentFilters.severity = severity;
            applyFilters();
            // Don't update stats when filtering by severity
        }}

        function filterByDirectory(directory) {{
            currentFilters.directory = directory;
            applyFilters();
            updateStatsForDirectory(); // Only update stats when filtering by directory
        }}

        // Initialize
        renderIssues(issuesData.issues);

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
                directory: 'all',
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
    </script>
</body>
</html>'''

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_file

def main(directory="claudeOutput"):
    """Main function to generate the report"""
    print("Enhanced LLM_StaticAnalysys Report Generator with Callstack Support")
    print("=" * 60)

    # Read all issues from specified directory
    print(f"Reading files from {directory} directory...")
    issues = read_claude_output_files(directory)

    if not issues:
        print(f"No issues found in {directory} directory!")
        return

    print(f"Found {len(issues)} issues")

    # Generate HTML report with callstack support
    print("Generating HTML report with callstack overlay support...")
    output_file = generate_html_report_with_callstacks(issues)

    print(f"Enhanced report generated successfully: {output_file}")

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