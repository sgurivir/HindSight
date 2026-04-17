#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
JSON Utility Module
Provides centralized JSON operations including LLM response parsing, cleanup, and validation

ARCHITECTURAL PRINCIPLE: All JSON parsing operations should be implemented in this module
to ensure consistency, proper error handling, and maintainability across the codebase.
For complex JSON operations, use utilities in this module instead of inline JSON handling.
"""

# Standard library imports
import json
import re
from typing import Optional, Dict, Any, Union, Tuple

# Local imports
from .log_util import get_logger

logger = get_logger(__name__)


def parse_json(json_content: str) -> Optional[Union[Dict, list]]:
    """
    Parse JSON content from string.

    Args:
        json_content (str): JSON content as string

    Returns:
        Union[Dict, list]: Parsed JSON object or None if error
    """
    try:
        parsed = json.loads(json_content)
        logger.debug(f"Successfully parsed JSON content ({len(json_content)} characters)")
        return parsed
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON: {e}")
        return None


def clean_json_response(content: str) -> str:
    """
    Clean JSON response by removing markdown formatting, tool results, file content, and extracting only the JSON content.
    Enhanced to handle explanatory text before JSON blocks and support both JSON objects and arrays.

    Args:
        content: Raw content that may contain markdown, tool results, file content, or extra text

    Returns:
        str: Cleaned JSON content containing only the analysis results
    """
    cleaned_content = content.strip()

    # Strategy 1: Look for JSON objects/arrays that are likely to be valid responses
    # Find all potential JSON structures and validate them
    json_candidates = []
    
    # Find all { } pairs
    brace_positions = []
    for i, char in enumerate(cleaned_content):
        if char == '{':
            # Look for the matching closing brace
            brace_count = 1
            for j in range(i + 1, len(cleaned_content)):
                if cleaned_content[j] == '{':
                    brace_count += 1
                elif cleaned_content[j] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        potential_json = cleaned_content[i:j + 1]
                        try:
                            parsed = json.loads(potential_json)
                            if isinstance(parsed, dict):
                                json_candidates.append((i, potential_json))
                        except json.JSONDecodeError:
                            pass
                        break
    
    # Find all [ ] pairs
    for i, char in enumerate(cleaned_content):
        if char == '[':
            # Look for the matching closing bracket
            bracket_count = 1
            for j in range(i + 1, len(cleaned_content)):
                if cleaned_content[j] == '[':
                    bracket_count += 1
                elif cleaned_content[j] == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        potential_json = cleaned_content[i:j + 1]
                        try:
                            json.loads(potential_json)
                            json_candidates.append((i, potential_json))
                        except json.JSONDecodeError:
                            pass
                        break
    
    # Return the LARGEST valid JSON candidate (most likely to be the complete response).
    # This prevents returning nested inner objects instead of the full outer structure.
    if json_candidates:
        # Sort by length (largest first), then by position (later is better for ties)
        json_candidates.sort(key=lambda x: (-len(x[1]), -x[0]))
        return json_candidates[0][1].strip()

    # Strategy 2: Look for markdown code blocks and extract content from them
    lines = cleaned_content.split('\n')
    json_start_line = -1
    json_end_line = -1

    # Look for ```json or ``` markers to find the JSON block
    in_json_block = False
    for i, line in enumerate(lines):
        line_stripped = line.strip()

        # Check for start of JSON code block
        if line_stripped == '```json' or (line_stripped == '```' and not in_json_block):
            in_json_block = True
            continue

        # Check for end of code block
        if line_stripped == '```' and in_json_block:
            json_end_line = i
            break

        # If we're in a JSON block and find a line that starts with [ or {, this might be our JSON
        if in_json_block and (line_stripped.startswith('[') or line_stripped.startswith('{')):
            json_start_line = i
            # Don't break here, continue to find the end

    # If we found a JSON block, extract it
    if json_start_line != -1:
        if json_end_line != -1:
            # Extract lines between start and end markers
            json_lines = lines[json_start_line:json_end_line]
        else:
            # Extract from start to end of content
            json_lines = lines[json_start_line:]

        potential_json = '\n'.join(json_lines).strip()
        try:
            json.loads(potential_json)
            return potential_json
        except json.JSONDecodeError:
            pass

    # Strategy 3: Remove markdown code block markers and look for JSON at the end
    if cleaned_content.startswith('```json'):
        cleaned_content = cleaned_content[7:]
    elif cleaned_content.startswith('```'):
        cleaned_content = cleaned_content[3:]
    if cleaned_content.endswith('```'):
        cleaned_content = cleaned_content[:-3]

    cleaned_content = cleaned_content.strip()

    # Look for JSON patterns at the end of the content (common for LLM responses)
    # Split by lines and look for JSON starting from the end
    lines = cleaned_content.split('\n')
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line.startswith('{') or line.startswith('['):
            # Found potential JSON start, try to parse from here to end
            potential_json = '\n'.join(lines[i:]).strip()
            try:
                json.loads(potential_json)
                return potential_json
            except json.JSONDecodeError:
                # Try just this line
                try:
                    json.loads(line)
                    return line
                except json.JSONDecodeError:
                    continue

    # Strategy 4: Enhanced filtering to remove explanatory text before JSON
    lines = cleaned_content.split('\n')
    filtered_lines = []
    found_json_start = False

    for line in lines:
        line_stripped = line.strip()

        # Skip empty lines before JSON starts
        if not found_json_start and not line_stripped:
            continue

        # Skip common explanatory text patterns
        if not found_json_start and (
            line_stripped.startswith('Looking at') or
            line_stripped.startswith('Analyzing') or
            line_stripped.startswith('I need to') or
            line_stripped.startswith('Let me') or
            line_stripped.startswith('Based on') or
            line_stripped.startswith('Examining') or
            line_stripped.startswith('The diff') or
            line_stripped.startswith('This diff') or
            'analyze' in line_stripped.lower() and 'systematically' in line_stripped.lower() or
            'examine the changes' in line_stripped.lower() or
            'potential issues' in line_stripped.lower()
        ):
            continue

        # Skip lines that indicate tool results or file content
        if (line_stripped.startswith('Tool Result:') or
            line_stripped.startswith('File:') or
            line_stripped.startswith('Command:') or
            line_stripped.startswith('/*') or
            line_stripped.startswith('*') or
            line_stripped.startswith('//') or
            line_stripped.startswith('#include') or
            line_stripped.startswith('#pragma') or
            line_stripped.startswith('#define') or
            line_stripped.startswith('#ifndef') or
            line_stripped.startswith('#endif')):
            continue

        # Look for the start of JSON array or object
        if line_stripped.startswith('[') or line_stripped.startswith('{'):
            found_json_start = True

        # Only include lines after we've found the JSON start
        if found_json_start:
            filtered_lines.append(line)

    if filtered_lines:
        potential_json = '\n'.join(filtered_lines).strip()
        try:
            json.loads(potential_json)
            return potential_json
        except json.JSONDecodeError:
            pass

    # Final fallback: try to extract any valid JSON from the content
    # Look for the last occurrence of valid JSON
    for i in range(len(cleaned_content) - 1, -1, -1):
        if cleaned_content[i] in ['{', '[']:
            for j in range(i, len(cleaned_content)):
                if cleaned_content[j] in ['}', ']']:
                    potential_json = cleaned_content[i:j + 1]
                    try:
                        json.loads(potential_json)
                        return potential_json
                    except json.JSONDecodeError:
                        continue

    return cleaned_content.strip()


def validate_and_format_json(content: str) -> tuple[bool, str]:
    """
    Validate and format JSON content.

    Args:
        content (str): JSON content to validate and format

    Returns:
        tuple[bool, str]: (is_valid, formatted_content)
    """
    try:
        # Parse to validate JSON structure
        parsed_json = json.loads(content)
        # Re-serialize with proper formatting
        formatted_content = json.dumps(parsed_json, indent=2, ensure_ascii=False)
        logger.debug("JSON content is valid and formatted")
        return True, formatted_content
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON content: {e}")
        return False, content