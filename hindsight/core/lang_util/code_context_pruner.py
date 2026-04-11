#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
CodeContextPruner - Utility class for removing comments from code text while preserving line numbers.
Used to clean up code sent to LLM for analysis by removing unnecessary comments.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Tuple


class CodeContextPruner:
    """
    Utility class for pruning comments or pruning code from files while preserving line numbers.

    This class provides static methods to remove both single-line (//) and multi-line (/* */)
    comments from code text, replacing comment lines with "..." while maintaining the
    original line numbering structure for LLM analysis.
    """

    @staticmethod
    def prune_comments(code_text: str) -> str:
        """
        Remove comments from code text while preserving line numbers.

        Args:
            code_text: Input code text, potentially with line numbers in format "   123 | code"

        Returns:
            str: Code text with comments removed and replaced with "..."
        """
        if not code_text:
            return code_text

        lines = code_text.split('\n')
        processed_lines = []
        in_multiline_comment = False
        consecutive_comment_lines = 0
        last_code_indentation = ""

        for line in lines:
            # Check if line has line number format (e.g., "  123 | code")
            line_number_match = re.match(r'^(\s*\d+\s*\|\s*)', line)
            if line_number_match:
                line_prefix = line_number_match.group(1)
                code_part = line[len(line_prefix):]
            else:
                line_prefix = ""
                code_part = line

            # Process the code part for comments
            processed_code, in_multiline_comment = CodeContextPruner._process_line_for_comments(
                code_part, in_multiline_comment
            )

            # Check if this line is entirely a comment or empty after comment removal
            is_comment_line = CodeContextPruner._is_comment_line(processed_code, code_part)

            if is_comment_line:
                consecutive_comment_lines += 1
                # For the first comment line in a series, add "// ...comments..." with proper indentation but no line number
                if consecutive_comment_lines == 1:
                    processed_lines.append(last_code_indentation + "// ...comments...")
                # Skip subsequent consecutive comment lines (they're already represented by the single "// ...comments...")
            else:
                consecutive_comment_lines = 0
                # Track indentation of this code line for future comment replacements
                if processed_code.strip():  # Only track indentation from non-empty lines
                    # Extract leading whitespace from the processed code
                    last_code_indentation = processed_code[:len(processed_code) - len(processed_code.lstrip())]

                # Add the processed line with original line number prefix if it existed
                if line_prefix:
                    processed_lines.append(line_prefix + processed_code)
                else:
                    processed_lines.append(processed_code)

        return '\n'.join(processed_lines)

    @staticmethod
    def _process_line_for_comments(code_line: str, in_multiline_comment: bool) -> Tuple[str, bool]:
        """
        Process a single line to remove comments.

        Args:
            code_line: The code part of the line (without line number prefix)
            in_multiline_comment: Whether we're currently inside a multiline comment

        Returns:
            Tuple[str, bool]: (processed_line, still_in_multiline_comment)
        """
        if not code_line.strip():
            return code_line, in_multiline_comment

        result = ""
        i = 0

        while i < len(code_line):
            if in_multiline_comment:
                # Look for end of multiline comment
                end_pos = code_line.find('*/', i)
                if end_pos != -1:
                    # Found end of multiline comment
                    in_multiline_comment = False
                    i = end_pos + 2
                else:
                    # Entire rest of line is in multiline comment
                    break
            else:
                # Look for start of comments
                single_comment_pos = code_line.find('//', i)
                multi_comment_pos = code_line.find('/*', i)

                # Find the earliest comment start
                next_comment_pos = None
                comment_type = None

                if single_comment_pos != -1 and multi_comment_pos != -1:
                    if single_comment_pos < multi_comment_pos:
                        next_comment_pos = single_comment_pos
                        comment_type = '//'
                    else:
                        next_comment_pos = multi_comment_pos
                        comment_type = '/*'
                elif single_comment_pos != -1:
                    next_comment_pos = single_comment_pos
                    comment_type = '//'
                elif multi_comment_pos != -1:
                    next_comment_pos = multi_comment_pos
                    comment_type = '/*'

                if next_comment_pos is not None:
                    # Add code before comment
                    result += code_line[i:next_comment_pos]

                    if comment_type == '//':
                        # Single line comment - rest of line is comment
                        break
                    else:  # comment_type == '/*'
                        # Start of multiline comment
                        in_multiline_comment = True
                        i = next_comment_pos + 2
                else:
                    # No more comments in this line
                    result += code_line[i:]
                    break

        return result, in_multiline_comment

    @staticmethod
    def _is_comment_line(processed_code: str, original_code: str) -> bool:
        """
        Determine if a line should be considered a comment line.

        Args:
            processed_code: Code after comment removal
            original_code: Original code before processing
            was_in_multiline: Whether we were in a multiline comment

        Returns:
            bool: True if this line should be replaced with "..."
        """
        # If the processed code is empty or only whitespace, it was likely all comments
        if not processed_code.strip():
            # But only consider it a comment line if the original had some content
            return bool(original_code.strip())

        # Check if original line starts with comment (after whitespace)
        stripped_original = original_code.strip()
        if stripped_original.startswith('//') or stripped_original.startswith('/*'):
            return True

        return False

    @staticmethod
    def prune_comments_simple(code_text: str) -> str:
        """
        Simple version that removes comments without complex line number handling.
        Useful for code that doesn't have line number prefixes.

        Args:
            code_text: Input code text

        Returns:
            str: Code text with comments removed and replaced with "..."
        """
        if not code_text:
            return code_text

        lines = code_text.split('\n')
        original_line_count = len(lines)
        processed_lines = []
        in_multiline_comment = False
        consecutive_comment_lines = 0
        last_code_indentation = ""

        # DEBUG: Log pruning details
        # Removed print statement - use proper logging instead
        # print(f"DEBUG LINE_NUMBERS: CodeContextPruner.prune_comments_simple - input has {original_line_count} lines")

        for line in lines:
            processed_code, in_multiline_comment = CodeContextPruner._process_line_for_comments(
                line, in_multiline_comment
            )

            is_comment_line = CodeContextPruner._is_comment_line(processed_code, line)

            if is_comment_line:
                consecutive_comment_lines += 1
                if consecutive_comment_lines == 1:
                    processed_lines.append(last_code_indentation + "// ...comments...")
            else:
                consecutive_comment_lines = 0
                # Track indentation of this code line for future comment replacements
                if processed_code.strip():  # Only track indentation from non-empty lines
                    # Extract leading whitespace from the processed code
                    last_code_indentation = processed_code[:len(processed_code) - len(processed_code.lstrip())]
                processed_lines.append(processed_code)

        result = '\n'.join(processed_lines)
        pruned_line_count = len(processed_lines)
        
        # DEBUG: Log pruning results
        # Removed print statement - use proper logging instead
        # print(f"DEBUG LINE_NUMBERS: CodeContextPruner.prune_comments_simple - output has {pruned_line_count} lines (removed {original_line_count - pruned_line_count} lines)")
        
        return result
    
    @staticmethod
    def prune_code(code_text: str) -> str:
        """
        Prune code to keep comments, function signatures, and property declarations
        while removing function implementations. Supports multiple programming languages.
        
        Features:
        - Keeps comments (truncated to 10 lines if longer)
        - Keeps function/method signatures
        - Keeps property/variable declarations
        - Removes function/method body implementations
        - Preserves line numbers if present
        - Supports C/C++, Java, JavaScript, Python, Swift, Kotlin, Go, C#, etc.
        
        Args:
            code_text: Input code text, potentially with line numbers in format "   123 | code"
            
        Returns:
            str: Pruned code with signatures and comments but no implementations
        """
        if not code_text:
            return code_text
            
        lines = code_text.split('\n')
        processed_lines = []
        i = 0
        in_multiline_comment = False
        multiline_comment_lines = 0
        brace_depth = 0
        paren_depth = 0
        in_function_body = False
        function_signature_complete = False
        
        while i < len(lines):
            line = lines[i]
            
            # Extract line number prefix if present
            line_number_match = re.match(r'^(\s*\d+\s*\|\s*)', line)
            if line_number_match:
                line_prefix = line_number_match.group(1)
                code_part = line[len(line_prefix):]
            else:
                line_prefix = ""
                code_part = line
                
            original_code_part = code_part
            stripped_code = code_part.strip()
            
            # Handle multiline comments
            if in_multiline_comment:
                multiline_comment_lines += 1
                if '*/' in code_part:
                    in_multiline_comment = False
                    multiline_comment_lines = 0
                
                # Include comment line if within 10 line limit
                if multiline_comment_lines <= 10:
                    processed_lines.append(line_prefix + code_part)
                elif multiline_comment_lines == 11:
                    # Add truncation indicator
                    indent = code_part[:len(code_part) - len(code_part.lstrip())]
                    processed_lines.append(line_prefix + indent + "// ...comment truncated...")
                i += 1
                continue
                
            # Check for start of multiline comment
            if '/*' in code_part and not in_multiline_comment:
                in_multiline_comment = True
                multiline_comment_lines = 1
                processed_lines.append(line_prefix + code_part)
                if '*/' in code_part:
                    in_multiline_comment = False
                    multiline_comment_lines = 0
                i += 1
                continue
                
            # Handle single line comments
            if stripped_code.startswith('//') or stripped_code.startswith('#'):
                processed_lines.append(line_prefix + code_part)
                i += 1
                continue
                
            # Skip empty lines
            if not stripped_code:
                processed_lines.append(line_prefix + code_part)
                i += 1
                continue
                
            # Check if this looks like a function/method signature
            is_function_signature = CodeContextPruner._is_function_signature(stripped_code)
            
            # Check if this looks like a property/variable declaration
            is_declaration = CodeContextPruner._is_declaration(stripped_code)
            
            # Handle function signatures
            if is_function_signature and not in_function_body:
                # This is a function signature - keep it
                processed_lines.append(line_prefix + code_part)
                
                # Count braces and parentheses to determine when signature is complete
                open_braces = code_part.count('{')
                close_braces = code_part.count('}')
                open_parens = code_part.count('(')
                close_parens = code_part.count(')')
                
                brace_depth += open_braces - close_braces
                paren_depth += open_parens - close_parens
                
                # Check if signature is complete and function body starts
                if '{' in code_part and paren_depth == 0:
                    in_function_body = True
                    function_signature_complete = True
                elif paren_depth == 0 and not code_part.rstrip().endswith(';'):
                    # Signature might continue on next line
                    function_signature_complete = False
                    
            elif in_function_body:
                # We're inside a function body - skip implementation
                open_braces = code_part.count('{')
                close_braces = code_part.count('}')
                brace_depth += open_braces - close_braces
                
                # Keep the closing brace line
                if close_braces > 0 and brace_depth <= 0:
                    processed_lines.append(line_prefix + code_part)
                    in_function_body = False
                    brace_depth = 0
                # Skip function body content
                
            elif is_declaration:
                # Keep property/variable declarations
                processed_lines.append(line_prefix + code_part)
                
            elif not function_signature_complete and paren_depth > 0:
                # Continuation of function signature
                processed_lines.append(line_prefix + code_part)
                open_parens = code_part.count('(')
                close_parens = code_part.count(')')
                paren_depth += open_parens - close_parens
                
                if paren_depth == 0:
                    function_signature_complete = True
                    if '{' in code_part:
                        in_function_body = True
                        brace_depth = code_part.count('{') - code_part.count('}')
                        
            else:
                # Keep other structural elements (imports, class declarations, etc.)
                if CodeContextPruner._is_structural_element(stripped_code):
                    processed_lines.append(line_prefix + code_part)
                    
                    # Handle class/interface/struct opening braces
                    if '{' in code_part:
                        brace_depth = code_part.count('{') - code_part.count('}')
                        
            i += 1
            
        return '\n'.join(processed_lines)
    
    @staticmethod
    def _is_function_signature(code_line: str) -> bool:
        """Check if a line looks like a function/method signature."""
        # Remove leading/trailing whitespace
        line = code_line.strip()
        
        if not line or line.startswith('//') or line.startswith('/*'):
            return False
            
        # Common function patterns across languages
        function_patterns = [
            # C/C++/Java/C#/JavaScript/TypeScript - improved pattern
            r'^\s*(?:public|private|protected|static|virtual|override|async|extern|inline|const|volatile)*\s*(?:\w+(?:\s*\*|\s*&|\s*<[^>]*>)?\s+)*\w+\s*\([^)]*\)\s*(?:const|override|final|noexcept)*\s*(?:\{|;|$)',
            # Python
            r'^\s*def\s+\w+\s*\([^)]*\)\s*(?:->\s*[\w\[\],\s]+)?\s*:',
            # Swift
            r'^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public|private|internal|fileprivate|open)?\s*(?:static|class)?\s*func\s+\w+\s*\([^)]*\)\s*(?:->\s*[\w\[\],\s]+)?\s*\{?',
            # Kotlin
            r'^\s*(?:public|private|internal|protected)?\s*(?:suspend|inline|infix|operator)?\s*fun\s+\w+\s*\([^)]*\)\s*(?::\s*[\w\[\],\s<>?]+)?\s*\{?',
            # Go
            r'^\s*func\s+(?:\(\s*\w+\s+\*?\w+\s*\)\s+)?\w+\s*\([^)]*\)\s*(?:\([^)]*\)|[\w\[\],\s\*]+)?\s*\{?',
            # Rust
            r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+\w+\s*\([^)]*\)\s*(?:->\s*[\w\[\],\s<>&]+)?\s*\{?',
            # Constructor patterns (C++/Java/C#)
            r'^\s*(?:public|private|protected)?\s*\w+\s*\([^)]*\)\s*(?::\s*[^{]*)?(?:\{|;)',
        ]
        
        for pattern in function_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
                
        # Additional heuristics for function signatures
        if ('(' in line and ')' in line and
            not line.startswith('if') and not line.startswith('while') and
            not line.startswith('for') and not line.startswith('switch') and
            not line.startswith('catch') and not line.startswith('return') and
            not line.startswith('print') and not line.startswith('std::cout')):
            
            # Check if it looks like a function call vs declaration
            # Function declarations typically have type information or keywords
            if (any(keyword in line for keyword in ['int ', 'void ', 'string ', 'bool ', 'float ', 'double ', 'char ', 'def ', 'func ', 'fun ']) or
                line.strip().endswith('{') or line.strip().endswith(';') or line.strip().endswith(':')):
                return True
            
        return False
    
    @staticmethod
    def _is_declaration(code_line: str) -> bool:
        """Check if a line looks like a property/variable declaration."""
        line = code_line.strip()
        
        if not line or line.startswith('//') or line.startswith('/*'):
            return False
            
        # Common declaration patterns
        declaration_patterns = [
            # C/C++/Java/C# variable declarations
            r'^\s*(?:public|private|protected|static|final|const|volatile)?\s*\w+\s+\w+\s*(?:=|;)',
            # Python variable assignments
            r'^\s*\w+\s*:\s*\w+\s*(?:=|$)',
            # Swift properties
            r'^\s*(?:public|private|internal|fileprivate|open)?\s*(?:static|class)?\s*(?:let|var)\s+\w+\s*:\s*\w+',
            # Kotlin properties
            r'^\s*(?:public|private|internal|protected)?\s*(?:val|var)\s+\w+\s*:\s*\w+',
            # Go variable declarations
            r'^\s*(?:var|const)\s+\w+\s+\w+',
            # JavaScript/TypeScript
            r'^\s*(?:const|let|var)\s+\w+\s*(?::\s*\w+)?\s*=',
        ]
        
        for pattern in declaration_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
                
        return False
    
    @staticmethod
    def _is_structural_element(code_line: str) -> bool:
        """Check if a line is a structural element to keep (imports, class declarations, etc.)."""
        line = code_line.strip()
        
        if not line:
            return False
            
        # Structural patterns to keep
        structural_patterns = [
            # Imports/includes
            r'^\s*(?:import|include|using|from|#include)',
            # Package/namespace declarations
            r'^\s*(?:package|namespace)',
            # Class/interface/struct/enum declarations
            r'^\s*(?:public|private|protected|internal)?\s*(?:abstract|final|static)?\s*(?:class|interface|struct|enum|protocol|extension)',
            # Annotations/attributes
            r'^\s*[@\[]',
            # Preprocessor directives
            r'^\s*#',
            # Module declarations
            r'^\s*module\s+',
        ]
        
        for pattern in structural_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
                
        return False


    @staticmethod
    def add_line_numbers(code_text: str, start_line: int) -> str:
        """
        Add line numbers to code text in the format used by TraceAnalysisPromptBuilder.
        ENHANCED: Now supports starting line number to preserve original file context.

        Args:
            code_text: Input code text without line numbers
            start_line: Starting line number (1-based, defaults to 1)

        Returns:
            str: Code text with line numbers in format "  123 | code"
        """
        if not code_text:
            return code_text

        lines = code_text.split('\n')
        numbered_lines = []

        for i, line in enumerate(lines):
            line_number = start_line + i
            numbered_lines.append(f"{line_number:4d} | {line}")

        return '\n'.join(numbered_lines)

    @staticmethod
    def validate_line_numbers(content: str) -> Tuple[bool, str]:
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
        
        line_number_format_regex = r'^(\s*)(\d+)(\s*\|\s*)'
        
        for i, line in enumerate(lines):
            match = re.match(line_number_format_regex, line)
            
            if match:
                current_line = int(match.group(2))
                
                if expected_line is None:
                    expected_line = current_line
                elif current_line != expected_line:
                    return False, f"Line number inconsistency at line {i+1}: expected {expected_line}, got {current_line}"
                
                expected_line += 1
            # Lines without numbers are allowed (e.g., continuation lines, comments)
        
        return True, ""

    @staticmethod
    def extract_line_range(content: str) -> Tuple[int, int]:
        """
        Extract the actual line number range from content with line numbers.
        
        Args:
            content: Content with line numbers
            
        Returns:
            Tuple[int, int]: (start_line, end_line) or (1, 1) if no line numbers found
        """
        if not content:
            return (1, 1)
        
        lines = content.split('\n')
        first_line = None
        last_line = None
        
        line_number_regex = r'^(\s*)(\d+)(\s*\|\s*)'
        
        for line in lines:
            match = re.match(line_number_regex, line)
            if match:
                current_line = int(match.group(2))
                if first_line is None:
                    first_line = current_line
                last_line = current_line
        
        if first_line is not None and last_line is not None:
            return (first_line, last_line)
        
        return (1, 1)


def main():
    """
    Main function for command-line usage of CodeContextPruner.
    Supports --file and --out arguments to process entire files.
    """
    parser = argparse.ArgumentParser(
        description="Process code files with different pruning options",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Remove comments only
  python CodeContextPruner.py --file input.c --out output.txt --mode comments
  
  # Keep signatures and comments, remove implementations
  python CodeContextPruner.py --file input.cpp --out pruned.txt --mode code
        """
    )

    parser.add_argument(
        '--file',
        required=True,
        help='Input file path to process'
    )

    parser.add_argument(
        '--out',
        required=True,
        help='Output file path for pruned content'
    )
    
    parser.add_argument(
        '--mode',
        choices=['comments', 'code'],
        default='comments',
        help='Pruning mode: "comments" removes comments only, "code" keeps signatures/comments but removes implementations'
    )

    args = parser.parse_args()

    # Validate input file
    input_file = Path(args.file)
    if not input_file.exists():
        print(f"Error: Input file does not exist: {input_file}", file=sys.stderr)
        sys.exit(1)

    if not input_file.is_file():
        print(f"Error: Input path is not a file: {input_file}", file=sys.stderr)
        sys.exit(1)

    # Create output directory if needed
    output_file = Path(args.out)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Read input file
        print(f"Reading input file: {input_file}")
        with open(input_file, 'r', encoding='utf-8') as f:
            original_content = f.read()

        print(f"Original file has {len(original_content.splitlines())} lines")

        # Add line numbers to match TraceAnalysisPromptBuilder format
        print("Adding line numbers...")
        numbered_content = CodeContextPruner.add_line_numbers(original_content, 1)

        # Apply pruning based on mode
        if args.mode == 'code':
            print("Pruning code (keeping signatures and comments, removing implementations)...")
            pruned_content = CodeContextPruner.prune_code(numbered_content)
        else:  # mode == 'comments'
            print("Pruning comments...")
            pruned_content = CodeContextPruner.prune_comments(numbered_content)

        # Write output file
        print(f"Writing pruned content to: {output_file}")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(pruned_content)

        # Print statistics
        original_lines = len(original_content.splitlines())
        pruned_lines = len(pruned_content.splitlines())
        original_chars = len(original_content)
        pruned_chars = len(pruned_content)

        print(f"\nProcessing complete!")
        print(f"Original lines: {original_lines}")
        print(f"Pruned lines: {pruned_lines}")
        print(f"Lines reduced: {original_lines - pruned_lines} ({((original_lines - pruned_lines) / original_lines * 100):.1f}%)")
        print(f"Original characters: {original_chars:,}")
        print(f"Pruned characters: {pruned_chars:,}")
        print(f"Characters reduced: {original_chars - pruned_chars:,} ({((original_chars - pruned_chars) / original_chars * 100):.1f}%)")
        print(f"Output saved to: {output_file}")

    except Exception as e:
        print(f"Error processing file: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()