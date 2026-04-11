#!/usr/bin/env python3
"""
Print Additional Context from Prompt Conversations

This script scans markdown documents in a prompts directory and extracts
the additional_context collected in the first step of code_analyzer.

Usage:
    python dev/print_additional_context.py --prompts-dir /path/to/prompts_sent
    python dev/print_additional_context.py -p /path/to/prompts_sent

Example:
    python dev/print_additional_context.py -p /Users/sgurivireddy/hindsight_artifacts/Signal-Android/prompts_sent
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional


def extract_json_from_markdown(content: str) -> Optional[Dict]:
    """
    Extract JSON content from markdown code blocks.
    
    Args:
        content: Markdown content
        
    Returns:
        Parsed JSON dict or None if not found
    """
    # Look for JSON code blocks with additional_context
    json_pattern = r'```(?:json)?\s*\n(\{[^`]*"additional_context"[^`]*\})\s*\n```'
    matches = re.findall(json_pattern, content, re.DOTALL)
    
    for match in matches:
        try:
            parsed = json.loads(match)
            if isinstance(parsed, dict) and 'additional_context' in parsed:
                return parsed
        except json.JSONDecodeError as e:
            # Try to fix common JSON issues
            try:
                # Sometimes there might be trailing content after the JSON
                # Try to find the closing brace
                brace_count = 0
                end_pos = 0
                for i, char in enumerate(match):
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_pos = i + 1
                            break
                
                if end_pos > 0:
                    cleaned = match[:end_pos]
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, dict) and 'additional_context' in parsed:
                        return parsed
            except:
                continue
    
    return None


def extract_additional_context_from_conversation(file_path: str) -> Optional[Dict]:
    """
    Extract additional_context from a conversation markdown file.
    
    Args:
        file_path: Path to the markdown conversation file
        
    Returns:
        Dict with function info and additional_context, or None if not found
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if this is a context collection conversation
        if 'context_collection' not in content.lower():
            return None
        
        # Extract function name and file from the **Context:** line
        function_name = "Unknown"
        file_name = "Unknown"
        
        # Look for context line like: **Context:** function_name in file_path
        context_match = re.search(r'\*\*Context:\*\*\s+(.+?)\s+in\s+(.+?)(?:\n|$)', content)
        if context_match:
            function_name = context_match.group(1).strip()
            file_name = context_match.group(2).strip()
        
        # Also try the user prompt format
        if function_name == "Unknown":
            function_match = re.search(r'Function:\s*([^\n]+)', content)
            if function_match:
                function_name = function_match.group(1).strip()
        
        if file_name == "Unknown":
            file_match = re.search(r'File:\s*([^\n]+)', content)
            if file_match:
                file_name = file_match.group(1).strip()
        
        # Extract additional_context from assistant responses
        # The JSON appears in the assistant response, sometimes in code blocks, sometimes not
        
        # Strategy 1: Look for JSON in code blocks within ASSISTANT RESPONSE sections
        assistant_sections = re.findall(r'### ASSISTANT RESPONSE\s*```(.*?)```', content, re.DOTALL)
        
        additional_context = None
        for section in assistant_sections:
            parsed_json = extract_json_from_markdown(f"```json\n{section}\n```")
            if parsed_json and 'additional_context' in parsed_json:
                additional_context = parsed_json['additional_context']
                break
        
        # Strategy 2: Look for JSON pattern directly in the content (not in code blocks)
        if not additional_context:
            # Find JSON objects that contain "additional_context" field
            # This regex looks for { ... "additional_context": "..." ... }
            json_pattern = r'\{\s*"additional_context"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
            matches = re.findall(json_pattern, content, re.DOTALL)
            
            for match in matches:
                # The match is the value of additional_context (with escaped characters)
                # Unescape the JSON string
                try:
                    # Create a minimal JSON to parse
                    test_json = f'{{"additional_context": "{match}"}}'
                    parsed = json.loads(test_json)
                    additional_context = parsed['additional_context']
                    break
                except json.JSONDecodeError:
                    continue
        
        if additional_context:
            return {
                'file': file_name,
                'function': function_name,
                'additional_context': additional_context,
                'conversation_file': os.path.basename(file_path)
            }
        
        return None
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return None


def check_if_context_used_in_next_conversation(prompts_dir: str, current_file: str, function_name: str, additional_context: str) -> bool:
    """
    Check if the additional_context from current conversation appears in the next conversation.
    
    Args:
        prompts_dir: Path to the prompts_sent directory
        current_file: Current conversation file name (e.g., "conversation_2.md")
        function_name: Function name to match
        additional_context: The additional context to search for
        
    Returns:
        True if context appears in next conversation, False otherwise
    """
    try:
        # Extract conversation number
        match = re.search(r'conversation_(\d+)\.md', current_file)
        if not match:
            return False
        
        current_num = int(match.group(1))
        next_file = f"conversation_{current_num + 1}.md"
        next_path = Path(prompts_dir) / next_file
        
        if not next_path.exists():
            return False
        
        # Read next conversation
        with open(next_path, 'r', encoding='utf-8') as f:
            next_content = f.read()
        
        # Check if it's about the same function
        if function_name not in next_content:
            return False
        
        # Check if additional_context appears in the next conversation
        # Look for a snippet of the additional_context (first 100 chars)
        context_snippet = additional_context[:100].strip()
        if context_snippet in next_content:
            return True
        
        # Also check if "additional_context" field is mentioned in user prompt
        if 'additional_context' in next_content.lower():
            return True
        
        return False
        
    except Exception as e:
        return False


def scan_prompts_directory(prompts_dir: str) -> List[Dict]:
    """
    Scan all markdown files in the prompts directory.
    
    Args:
        prompts_dir: Path to the prompts_sent directory
        
    Returns:
        List of dicts containing additional_context information
    """
    results = []
    prompts_path = Path(prompts_dir)
    
    if not prompts_path.exists():
        print(f"Error: Directory does not exist: {prompts_dir}")
        return results
    
    # Find all markdown files
    md_files = list(prompts_path.glob('*.md'))
    
    print(f"Scanning {len(md_files)} markdown files in {prompts_dir}...")
    print()
    
    for md_file in md_files:
        context_info = extract_additional_context_from_conversation(str(md_file))
        if context_info:
            # Check if this context was used in the next conversation
            context_used = check_if_context_used_in_next_conversation(
                prompts_dir,
                context_info['conversation_file'],
                context_info['function'],
                context_info['additional_context']
            )
            context_info['context_used_in_next'] = context_used
            results.append(context_info)
    
    return results


def print_results(results: List[Dict], verbose: bool = False):
    """
    Print the extracted additional_context results.
    
    Args:
        results: List of context information dicts
        verbose: If True, print full additional_context content
    """
    if not results:
        print("No additional_context found in any conversation files.")
        return
    
    print(f"Found {len(results)} conversations with additional_context")
    print("=" * 80)
    print()
    
    for i, result in enumerate(results, 1):
        print(f"[{i}] Function: {result['function']}")
        print(f"    File: {result['file']}")
        print(f"    Conversation: {result['conversation_file']}")
        
        # Show if context was used in next conversation
        if result.get('context_used_in_next', False):
            print(f"    ✓ Additional context was passed to next conversation")
        else:
            print(f"    ✗ Additional context was NOT found in next conversation")
        
        if verbose:
            print(f"    Additional Context:")
            print(f"    {'-' * 76}")
            # Print additional_context with indentation
            context_lines = result['additional_context'].split('\n')
            for line in context_lines:
                print(f"    {line}")
            print(f"    {'-' * 76}")
        else:
            # Print just a preview
            context_preview = result['additional_context'][:200]
            if len(result['additional_context']) > 200:
                context_preview += "..."
            print(f"    Context Preview: {context_preview}")
        
        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Extract and print additional_context from code analyzer prompt conversations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan prompts directory and show previews
  python dev/print_additional_context.py -p ~/hindsight_artifacts/Signal-Android/prompts_sent
  
  # Show full additional_context content
  python dev/print_additional_context.py -p ~/hindsight_artifacts/Signal-Android/prompts_sent --verbose
        """
    )
    
    parser.add_argument(
        '--prompts-dir', '-p',
        required=True,
        help='Path to the prompts_sent directory containing conversation markdown files'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Print full additional_context content instead of just previews'
    )
    
    args = parser.parse_args()
    
    # Scan the directory
    results = scan_prompts_directory(args.prompts_dir)
    
    # Print results
    print_results(results, verbose=args.verbose)
    
    # Print summary
    if results:
        print("=" * 80)
        print(f"Summary: Found additional_context in {len(results)} conversations")


if __name__ == "__main__":
    main()