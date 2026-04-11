#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Line Number Utility Module

Provides utilities for working with line numbers in code content:
- Validation of line number consistency
- Normalization of line number formats
- Extraction of line ranges from content
- Line number parsing and removal
"""

import re
from typing import Optional, Tuple


# Regex patterns for line number matching
LINE_NUMBER_FORMAT_REGEX = r'^(\s*)(\d+)(\s*\|\s*)'
LINE_NUMBER_PATTERNS = [
    r'^(\s*)(\d+)(\s*\|\s*)(.*)',  # Standard format: "  123 | code"
    r'^(\s*)(\d+)(\s*:\s*)(.*)',   # Colon format: "  123: code"
    r'^(\s*)(\d+)(\s+)(.*)',       # Space format: "  123 code"
]


def validate_line_number_consistency(content: str) -> Tuple[bool, str]:
    """
    Validate that line numbers in content are consistent and properly formatted.
    
    Args:
        content: Content with line numbers to validate
        
    Returns:
        Tuple[bool, str]: (is_valid, error_message)
    """
    if not content:
        return True, ""
    
    lines = content.split('\n')
    expected_line = None
    
    for i, line in enumerate(lines):
        match = re.match(LINE_NUMBER_FORMAT_REGEX, line)
        
        if match:
            current_line = int(match.group(2))
            
            if expected_line is None:
                expected_line = current_line
            elif current_line != expected_line:
                return False, f"Line number inconsistency at line {i+1}: expected {expected_line}, got {current_line}"
            
            expected_line += 1
        # Lines without numbers are allowed (e.g., continuation lines, comments)
    
    return True, ""


def normalize_line_number_format(content: str) -> str:
    """
    Normalize line number format to ensure consistency.
    
    Args:
        content: Content that may have inconsistent line number formats
        
    Returns:
        str: Content with normalized line number format
    """
    if not content:
        return content
    
    lines = content.split('\n')
    normalized_lines = []
    
    for line in lines:
        matched = False
        for pattern in LINE_NUMBER_PATTERNS:
            match = re.match(pattern, line)
            if match:
                line_number = int(match.group(2))
                code_part = match.group(4)
                # Normalize to standard format
                normalized_lines.append(f"{line_number:4d} | {code_part}")
                matched = True
                break
        
        if not matched:
            # Line doesn't have a number, keep as-is
            normalized_lines.append(line)
    
    return '\n'.join(normalized_lines)


def extract_line_range_from_content(content: str) -> Optional[Tuple[int, int]]:
    """
    Extract the actual line number range from content with line numbers.
    
    Args:
        content: Content with line numbers
        
    Returns:
        Optional[Tuple[int, int]]: (start_line, end_line) or None if no line numbers found
    """
    if not content:
        return None
    
    lines = content.split('\n')
    first_line = None
    last_line = None
    
    for line in lines:
        match = re.match(LINE_NUMBER_FORMAT_REGEX, line)
        if match:
            current_line = int(match.group(2))
            if first_line is None:
                first_line = current_line
            last_line = current_line
    
    if first_line is not None and last_line is not None:
        return (first_line, last_line)
    
    return None


def get_line_number_from_content_line(line: str) -> Optional[int]:
    """
    Extract line number from a single line of content.
    
    Args:
        line: Single line that may contain a line number
        
    Returns:
        Optional[int]: Line number if found, None otherwise
    """
    match = re.match(r'^\s*(\d+)\s*\|\s*', line)
    if match:
        return int(match.group(1))
    return None


def remove_line_numbers_from_content(content: str) -> str:
    """
    Remove line numbers from content, leaving just the code.
    
    Args:
        content: Content with line numbers
        
    Returns:
        str: Content without line numbers
    """
    if not content:
        return content
    
    lines = content.split('\n')
    clean_lines = []
    
    for line in lines:
        # Remove line number prefix if present
        clean_line = re.sub(r'^\s*\d+\s*\|\s*', '', line)
        clean_lines.append(clean_line)
    
    return '\n'.join(clean_lines)


def add_line_numbers(content: str, start_line: int = 1) -> str:
    """
    Add line numbers to content.
    
    Args:
        content: Content without line numbers
        start_line: Starting line number (default: 1)
        
    Returns:
        str: Content with line numbers added
    """
    if not content:
        return content
    
    lines = content.split('\n')
    numbered_lines = []
    
    for i, line in enumerate(lines):
        line_number = start_line + i
        numbered_lines.append(f"{line_number:4d} | {line}")
    
    return '\n'.join(numbered_lines)


def has_line_numbers(content: str) -> bool:
    """
    Check if content already has line numbers.
    
    Args:
        content: Content to check
        
    Returns:
        bool: True if content has line numbers, False otherwise
    """
    if not content:
        return False
    
    lines = content.split('\n')
    # Check first few non-empty lines
    checked = 0
    for line in lines:
        if line.strip():
            if re.match(LINE_NUMBER_FORMAT_REGEX, line):
                return True
            checked += 1
            if checked >= 3:
                break
    
    return False