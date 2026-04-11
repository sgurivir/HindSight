"""
Matching components for hybrid deduplication.

This module provides:
- FilePathMatcher: Computes similarity scores based on file paths
- FunctionNameMatcher: Computes similarity scores based on function names
- FilePathExtractor: Extracts file paths from text
- FunctionNameExtractor: Extracts function names from text
"""

from pathlib import Path
from typing import List, Set
import re


class FilePathMatcher:
    """
    Computes similarity scores based on file paths.
    
    Scoring rules:
    - Exact match: 1.0
    - Same filename, different directory: 0.8 + directory similarity bonus
    - Same directory, different filename: 0.4
    - Partial path overlap: 0.2 - 0.6 (based on overlap)
    - No match: 0.0
    """
    
    @staticmethod
    def compute_score(issue_path: str, candidate_path: str) -> float:
        """
        Compute file path similarity score (0.0 - 1.0).
        
        Args:
            issue_path: File path from the issue
            candidate_path: File path from the candidate issue
            
        Returns:
            Similarity score between 0.0 and 1.0
        """
        if not issue_path or not candidate_path:
            return 0.0
        
        # Normalize paths
        issue_path = issue_path.strip()
        candidate_path = candidate_path.strip()
        
        issue_p = Path(issue_path)
        candidate_p = Path(candidate_path)
        
        # Exact match
        if issue_path == candidate_path:
            return 1.0
        
        # Same filename
        if issue_p.name == candidate_p.name:
            # Check directory similarity
            issue_parts = issue_p.parts[:-1]
            candidate_parts = candidate_p.parts[:-1]
            
            if not issue_parts and not candidate_parts:
                # Both are just filenames with no directory
                return 1.0
            
            # Calculate directory overlap
            common = len(set(issue_parts) & set(candidate_parts))
            total = max(len(issue_parts), len(candidate_parts), 1)
            dir_similarity = common / total
            
            return 0.8 + (0.2 * dir_similarity)
        
        # Same directory
        if issue_p.parent == candidate_p.parent and str(issue_p.parent) != ".":
            return 0.4
        
        # Partial path overlap
        issue_parts = set(issue_p.parts)
        candidate_parts = set(candidate_p.parts)
        common = len(issue_parts & candidate_parts)
        total = max(len(issue_parts), len(candidate_parts), 1)
        
        if common > 0:
            return 0.2 * (common / total)
        
        return 0.0
    
    @staticmethod
    def compute_best_score(issue_path: str, candidate_paths: List[str]) -> float:
        """
        Compute the best file path similarity score against multiple candidate paths.
        
        Args:
            issue_path: File path from the issue
            candidate_paths: List of file paths from the candidate issue
            
        Returns:
            Best similarity score between 0.0 and 1.0
        """
        if not issue_path or not candidate_paths:
            return 0.0
        
        scores = [
            FilePathMatcher.compute_score(issue_path, candidate_path)
            for candidate_path in candidate_paths
        ]
        return max(scores) if scores else 0.0


class FunctionNameMatcher:
    """
    Computes similarity scores based on function names.
    
    Scoring rules:
    - Exact match (normalized): 1.0
    - Substring match: 0.7
    - Token overlap (camelCase split): 0.3 - 0.6
    - No match: 0.0
    """
    
    @staticmethod
    def normalize(func_name: str) -> str:
        """
        Normalize function name for comparison.
        
        Handles:
        - Objective-C method prefixes (-[, +[)
        - Common suffixes (WithOptions, Handler)
        - Case normalization
        
        Args:
            func_name: The function name to normalize
            
        Returns:
            Normalized function name
        """
        if not func_name:
            return ""
        
        name = func_name.strip()
        
        # Remove Objective-C method prefixes
        name = re.sub(r'^[-+]\[', '', name)
        name = re.sub(r'\]$', '', name)
        
        # Remove common suffixes
        name = re.sub(r'WithOptions?:?$', '', name)
        name = re.sub(r'Handler$', '', name)
        name = re.sub(r'Callback$', '', name)
        
        # Convert to lowercase
        return name.lower().strip()
    
    @staticmethod
    def tokenize(func_name: str) -> Set[str]:
        """
        Split function name into tokens (handles camelCase and snake_case).
        
        Args:
            func_name: The function name to tokenize
            
        Returns:
            Set of lowercase tokens
        """
        if not func_name:
            return set()
        
        # Split on camelCase boundaries
        tokens = re.findall(r'[a-z]+', func_name.lower())
        
        # Also split on underscores and colons (Objective-C)
        additional = re.split(r'[_:]', func_name.lower())
        tokens.extend([t for t in additional if t])
        
        return set(tokens)
    
    @staticmethod
    def compute_score(issue_func: str, candidate_func: str) -> float:
        """
        Compute function name similarity score (0.0 - 1.0).
        
        Args:
            issue_func: Function name from the issue
            candidate_func: Function name from the candidate issue
            
        Returns:
            Similarity score between 0.0 and 1.0
        """
        if not issue_func or not candidate_func:
            return 0.0
        
        norm_issue = FunctionNameMatcher.normalize(issue_func)
        norm_candidate = FunctionNameMatcher.normalize(candidate_func)
        
        if not norm_issue or not norm_candidate:
            return 0.0
        
        # Exact match
        if norm_issue == norm_candidate:
            return 1.0
        
        # Substring match
        if norm_issue in norm_candidate or norm_candidate in norm_issue:
            return 0.7
        
        # Token overlap (split camelCase)
        issue_tokens = FunctionNameMatcher.tokenize(norm_issue)
        candidate_tokens = FunctionNameMatcher.tokenize(norm_candidate)
        
        if not issue_tokens or not candidate_tokens:
            return 0.0
        
        common = len(issue_tokens & candidate_tokens)
        total = max(len(issue_tokens), len(candidate_tokens))
        
        if common > 0:
            return 0.3 + (0.3 * common / total)
        
        return 0.0
    
    @staticmethod
    def compute_best_score(issue_func: str, candidate_funcs: List[str]) -> float:
        """
        Compute the best function name similarity score against multiple candidate functions.
        
        Args:
            issue_func: Function name from the issue
            candidate_funcs: List of function names from the candidate issue
            
        Returns:
            Best similarity score between 0.0 and 1.0
        """
        if not issue_func or not candidate_funcs:
            return 0.0
        
        scores = [
            FunctionNameMatcher.compute_score(issue_func, candidate_func)
            for candidate_func in candidate_funcs
        ]
        return max(scores) if scores else 0.0


class FilePathExtractor:
    """
    Extracts file paths from issue descriptions.
    """
    
    # Common file extensions in bug reports
    FILE_EXTENSIONS = [
        '.mm', '.m', '.swift', '.cpp', '.c', '.h', '.hpp',
        '.py', '.js', '.ts', '.java', '.kt', '.rb', '.go',
        '.rs', '.cs', '.php', '.pl', '.sh', '.bash'
    ]
    
    # Regex patterns for file paths
    PATH_PATTERNS = [
        # Explicit file references
        r'File:\s*([^\s\n]+)',
        r'file:\s*([^\s\n]+)',
        r'Path:\s*([^\s\n]+)',
        r'path:\s*([^\s\n]+)',
        # In-text references
        r'in\s+([^\s]+\.(?:mm|m|swift|cpp|c|h|hpp|py|js|ts|java|kt|rb|go|rs))',
        r'at\s+([^\s]+\.(?:mm|m|swift|cpp|c|h|hpp|py|js|ts|java|kt|rb|go|rs))',
        # Generic file paths with extensions
        r'([A-Za-z][A-Za-z0-9_/\-\.]+\.(?:mm|m|swift|cpp|c|h|hpp|py|js|ts|java|kt|rb|go|rs))',
        # Unix-style paths
        r'(/[A-Za-z0-9_/\-\.]+\.(?:mm|m|swift|cpp|c|h|hpp|py|js|ts|java|kt|rb|go|rs))',
    ]
    
    @classmethod
    def extract_file_paths(cls, text: str) -> List[str]:
        """
        Extract all file paths from text.
        
        Args:
            text: The text to extract file paths from
            
        Returns:
            List of unique file paths found
        """
        if not text:
            return []
        
        paths = []
        for pattern in cls.PATH_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                # Validate it looks like a file path
                if cls._is_valid_file_path(match):
                    paths.append(match)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_paths = []
        for path in paths:
            if path not in seen:
                seen.add(path)
                unique_paths.append(path)
        
        return unique_paths
    
    @classmethod
    def _is_valid_file_path(cls, path: str) -> bool:
        """
        Check if a string looks like a valid file path.
        
        Args:
            path: The string to validate
            
        Returns:
            True if it looks like a valid file path
        """
        if not path:
            return False
        
        # Must have a recognized extension
        has_extension = any(path.endswith(ext) for ext in cls.FILE_EXTENSIONS)
        if not has_extension:
            return False
        
        # Should not be too short (just extension)
        if len(path) < 4:
            return False
        
        # Should not contain certain characters
        invalid_chars = ['<', '>', '"', "'", '`', '|', '*', '?']
        if any(c in path for c in invalid_chars):
            return False
        
        return True


class FunctionNameExtractor:
    """
    Extracts function names from issue descriptions.
    """
    
    FUNCTION_PATTERNS = [
        # Explicit function references
        r'Function:\s*([^\s\n(]+)',
        r'function:\s*([^\s\n(]+)',
        r'Method:\s*([^\s\n(]+)',
        r'method:\s*([^\s\n(]+)',
        # In-text references
        r'in\s+function\s+([^\s\n(]+)',
        r'in\s+method\s+([^\s\n(]+)',
        r'calling\s+([^\s\n(]+)',
        # Objective-C methods
        r'(-\[[^\]]+\])',  # Instance method
        r'(\+\[[^\]]+\])',  # Class method
        # Swift functions
        r'func\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        # C/C++ style functions
        r'([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*{',
        # Generic function-like patterns
        r'([a-zA-Z_][a-zA-Z0-9_]*)\(\)',
    ]
    
    @classmethod
    def extract_function_names(cls, text: str) -> List[str]:
        """
        Extract all function names from text.
        
        Args:
            text: The text to extract function names from
            
        Returns:
            List of unique function names found
        """
        if not text:
            return []
        
        functions = []
        for pattern in cls.FUNCTION_PATTERNS:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                # Validate it looks like a function name
                if cls._is_valid_function_name(match):
                    functions.append(match)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_functions = []
        for func in functions:
            if func not in seen:
                seen.add(func)
                unique_functions.append(func)
        
        return unique_functions
    
    @classmethod
    def _is_valid_function_name(cls, name: str) -> bool:
        """
        Check if a string looks like a valid function name.
        
        Args:
            name: The string to validate
            
        Returns:
            True if it looks like a valid function name
        """
        if not name:
            return False
        
        # Should not be too short
        if len(name) < 2:
            return False
        
        # Should not be a common keyword
        keywords = {
            'if', 'else', 'for', 'while', 'do', 'switch', 'case',
            'return', 'break', 'continue', 'try', 'catch', 'throw',
            'class', 'struct', 'enum', 'interface', 'protocol',
            'public', 'private', 'protected', 'static', 'const',
            'void', 'int', 'float', 'double', 'bool', 'string',
            'true', 'false', 'null', 'nil', 'self', 'this',
            'import', 'from', 'as', 'in', 'is', 'not', 'and', 'or',
        }
        if name.lower() in keywords:
            return False
        
        # Objective-C methods are valid
        if name.startswith('-[') or name.startswith('+['):
            return True
        
        # Should start with a letter or underscore
        if not re.match(r'^[a-zA-Z_]', name):
            return False
        
        return True
