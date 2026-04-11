"""
Common data models for deduplication modules.

This module defines the core data structures used for issue deduplication:
- AnalyzerIssue: Represents an issue from any analyzer (code, trace, diff)
- DuplicateMatch: Represents a detected duplicate match
"""

import hashlib
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any


@dataclass
class AnalyzerIssue:
    """
    Represents an issue from any analyzer (code, trace, diff).
    
    This is the common format used by issue_deduper for deduplication.
    It can be created from various analyzer output formats.
    
    Attributes:
        id: Unique identifier (generated if not provided)
        title: Issue title/summary
        description: Detailed description
        file_path: Source file path
        function_name: Function name where issue occurs
        severity: Severity level (e.g., "high", "medium", "low")
        category: Issue category (e.g., "memory leak", "performance")
        line_number: Line number in source file
        evidence: Supporting evidence for the issue
        callstack: Call stack for trace analyzer issues
        raw_data: Original data preserved for output
    """
    id: str = ""
    title: str = ""
    description: str = ""
    file_path: Optional[str] = None
    function_name: Optional[str] = None
    severity: Optional[str] = None
    category: Optional[str] = None
    line_number: Optional[int] = None
    evidence: Optional[str] = None
    callstack: Optional[List[str]] = None
    raw_data: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Generate ID if not provided."""
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
    
    @property
    def file_name(self) -> Optional[str]:
        """Extract just the filename from file_path."""
        if self.file_path:
            return Path(self.file_path).name
        return None
    
    @property
    def directory_path(self) -> Optional[str]:
        """Extract the directory path without filename."""
        if self.file_path:
            return str(Path(self.file_path).parent)
        return None
    
    def to_embedding_text(self) -> str:
        """
        Generate text suitable for embedding generation.
        
        Combines the title, description, and optional context fields
        into a single string for semantic similarity matching.
        
        Returns:
            A string containing the combined text for embedding.
        """
        parts = []
        
        if self.title:
            parts.append(self.title)
        if self.description:
            parts.append(self.description)
        if self.file_path:
            parts.append(f"File: {self.file_path}")
        if self.function_name:
            parts.append(f"Function: {self.function_name}")
        if self.category:
            parts.append(f"Category: {self.category}")
        if self.evidence:
            parts.append(f"Evidence: {self.evidence}")
        
        return " ".join(parts)
    
    def compute_content_hash(self) -> str:
        """
        Compute hash for exact duplicate detection.
        
        Uses title, description, file_path, and function_name to create
        a unique hash that identifies identical issues.
        
        Returns:
            A 16-character hash string.
        """
        content = f"{self.title}|{self.description}|{self.file_path or ''}|{self.function_name or ''}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    @classmethod
    def from_analyzer_result(cls, result: Dict[str, Any]) -> 'AnalyzerIssue':
        """
        Create an AnalyzerIssue from analyzer output format.
        
        This method handles various field names that different analyzers
        might use for the same concept.
        
        Args:
            result: Dictionary from analyzer output.
        
        Returns:
            An AnalyzerIssue instance.
        """
        # Handle various field name conventions
        title = (
            result.get('title') or 
            result.get('issue') or 
            result.get('summary') or 
            result.get('name') or
            ''
        )
        
        description = (
            result.get('description') or 
            result.get('details') or 
            result.get('explanation') or
            result.get('issue_description') or
            ''
        )
        
        file_path = (
            result.get('file_path') or 
            result.get('file') or 
            result.get('source_file') or
            result.get('path') or
            None
        )
        
        function_name = (
            result.get('function_name') or 
            result.get('function') or 
            result.get('method') or
            result.get('func') or
            None
        )
        
        severity = (
            result.get('severity') or 
            result.get('priority') or 
            result.get('level') or
            None
        )
        
        category = (
            result.get('category') or 
            result.get('type') or 
            result.get('issue_type') or
            result.get('kind') or
            None
        )
        
        line_number = result.get('line_number') or result.get('line') or None
        if line_number is not None:
            try:
                line_number = int(line_number)
            except (ValueError, TypeError):
                line_number = None
        
        evidence = (
            result.get('evidence') or 
            result.get('code_snippet') or 
            result.get('snippet') or
            None
        )
        
        callstack = result.get('callstack') or result.get('call_stack') or None
        
        # Generate ID from existing ID or create new one
        issue_id = result.get('id') or result.get('issue_id') or str(uuid.uuid4())[:8]
        
        return cls(
            id=str(issue_id),
            title=title,
            description=description,
            file_path=file_path,
            function_name=function_name,
            severity=severity,
            category=category,
            line_number=line_number,
            evidence=evidence,
            callstack=callstack,
            raw_data=result
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert back to dictionary format.
        
        If raw_data is available, returns that to preserve original format.
        Otherwise, returns a dictionary of all fields.
        
        Returns:
            Dictionary representation of the issue.
        """
        if self.raw_data:
            return self.raw_data
        
        result = {
            'id': self.id,
            'title': self.title,
            'description': self.description,
        }
        
        if self.file_path:
            result['file_path'] = self.file_path
        if self.function_name:
            result['function_name'] = self.function_name
        if self.severity:
            result['severity'] = self.severity
        if self.category:
            result['category'] = self.category
        if self.line_number is not None:
            result['line_number'] = self.line_number
        if self.evidence:
            result['evidence'] = self.evidence
        if self.callstack:
            result['callstack'] = self.callstack
        
        return result
    
    def to_metadata(self) -> Dict[str, str]:
        """
        Convert to metadata dictionary for vector database storage.
        
        All values are converted to strings for ChromaDB compatibility.
        
        Returns:
            Dictionary containing metadata fields as strings.
        """
        return {
            'id': self.id,
            'title': self.title or '',
            'file_path': self.file_path or '',
            'function_name': self.function_name or '',
            'severity': self.severity or '',
            'category': self.category or '',
            'content_hash': self.compute_content_hash(),
        }
    
    def __str__(self) -> str:
        """Return a human-readable string representation."""
        return f"AnalyzerIssue({self.id}): {self.title}"
    
    def __eq__(self, other: object) -> bool:
        """Check equality based on content hash."""
        if not isinstance(other, AnalyzerIssue):
            return False
        return self.compute_content_hash() == other.compute_content_hash()
    
    def __hash__(self) -> int:
        """Hash based on content hash for use in sets/dicts."""
        return hash(self.compute_content_hash())


@dataclass
class DuplicateMatch:
    """
    Represents a detected duplicate match between two issues.
    
    Attributes:
        original_id: ID of the original (first seen) issue
        duplicate_id: ID of the duplicate issue
        similarity_score: Similarity score between 0.0 and 1.0
        match_type: Type of match ("exact" or "semantic")
    """
    original_id: str
    duplicate_id: str
    similarity_score: float
    match_type: str  # "exact" or "semantic"
    
    @property
    def similarity_percentage(self) -> int:
        """Return similarity score as a percentage (0-100)."""
        return int(self.similarity_score * 100)
    
    @property
    def confidence_level(self) -> str:
        """
        Return a confidence level string based on similarity score.
        
        Returns:
            One of: "very_high", "high", "moderate", "low"
        """
        if self.match_type == "exact":
            return "very_high"
        if self.similarity_score > 0.9:
            return "very_high"
        elif self.similarity_score > 0.8:
            return "high"
        elif self.similarity_score > 0.7:
            return "moderate"
        else:
            return "low"
    
    def __str__(self) -> str:
        """Return a human-readable string representation."""
        return (
            f"DuplicateMatch({self.duplicate_id} -> {self.original_id}, "
            f"{self.similarity_percentage}%, {self.match_type})"
        )
    
    def __lt__(self, other: 'DuplicateMatch') -> bool:
        """Enable sorting by similarity score (descending)."""
        return self.similarity_score > other.similarity_score
