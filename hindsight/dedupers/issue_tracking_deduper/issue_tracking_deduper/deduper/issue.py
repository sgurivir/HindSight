"""
Data models for the Issue Deduper tool.

This module defines the core data structures used throughout the deduplication process:
- Issue: Represents an issue from an LLM static analyzer report
- IssueEntry: Represents an issue entry in the vector database
- DedupeMatch: Represents a potential duplicate match
- HybridMatch: Represents a match with hybrid scoring from multiple signals
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any
import hashlib
import re


@dataclass
class Issue:
    """
    Represents an issue from an LLM static analyzer report.
    
    Attributes:
        id: Unique identifier for the issue
        title: Issue title/summary
        description: Detailed issue description
        file_path: Source file path where the issue was found
        function_name: Function name where the issue occurs
        severity: Issue severity level (e.g., "high", "medium", "low")
        category: Issue category (e.g., "memory leak", "performance")
        raw_html: Original HTML content of the issue element
    """
    id: str
    title: str
    description: str
    file_path: Optional[str] = None
    function_name: Optional[str] = None
    severity: Optional[str] = None
    category: Optional[str] = None
    raw_html: Optional[str] = None
    
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
    
    @property
    def normalized_function_name(self) -> Optional[str]:
        """Normalize function name for matching (lowercase, strip prefixes)."""
        if self.function_name:
            # Remove common prefixes like '-[', '+[' for Objective-C
            name = self.function_name.strip()
            name = re.sub(r'^[-+]\[', '', name)
            name = re.sub(r'\]$', '', name)
            return name.lower()
        return None
    
    def to_embedding_text(self) -> str:
        """
        Generate text suitable for embedding generation.
        
        Combines the title, description, and optional context fields
        into a single string for semantic similarity matching.
        
        Returns:
            A string containing the combined text for embedding.
        """
        parts = [self.title, self.description]
        if self.file_path:
            parts.append(f"File: {self.file_path}")
        if self.function_name:
            parts.append(f"Function: {self.function_name}")
        if self.category:
            parts.append(f"Category: {self.category}")
        return " ".join(parts)
    
    def __str__(self) -> str:
        """Return a human-readable string representation."""
        return f"Issue({self.id}): {self.title}"


@dataclass
class IssueEntry:
    """
    Represents an issue entry in the vector database.
    
    Attributes:
        issue_id: Issue ID (e.g., "123456789")
        title: Issue title
        description: Issue description/summary
        component: Component name (e.g., "CoreLocation")
        keywords: List of keywords associated with the issue
        file_path: Source markdown file path
        content_hash: SHA-256 hash of content for deduplication
    """
    issue_id: str
    title: str
    description: str
    component: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    file_path: str = ""
    content_hash: str = ""
    function_name: Optional[str] = None
    extracted_files: List[str] = field(default_factory=list)
    extracted_functions: List[str] = field(default_factory=list)
    
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
    
    @property
    def normalized_function_name(self) -> Optional[str]:
        """Normalize function name for matching (lowercase, strip prefixes)."""
        if self.function_name:
            # Remove common prefixes like '-[', '+[' for Objective-C
            name = self.function_name.strip()
            name = re.sub(r'^[-+]\[', '', name)
            name = re.sub(r'\]$', '', name)
            return name.lower()
        return None
    
    def to_embedding_text(self) -> str:
        """
        Generate text suitable for embedding generation.
        
        Combines the title, description, and optional context fields
        into a single string for semantic similarity matching.
        
        Returns:
            A string containing the combined text for embedding.
        """
        parts = [self.title, self.description]
        if self.component:
            parts.append(f"Component: {self.component}")
        if self.keywords:
            parts.append(f"Keywords: {', '.join(self.keywords)}")
        return " ".join(parts)
    
    def to_metadata(self) -> dict:
        """
        Convert to metadata dictionary for vector database storage.
        
        Returns:
            Dictionary containing all metadata fields.
        """
        return {
            "issue_id": self.issue_id,
            "title": self.title,
            "component": self.component or "",
            "keywords": ",".join(self.keywords) if self.keywords else "",
            "file_path": self.file_path,
            "content_hash": self.content_hash,
            "function_name": self.function_name or "",
            "extracted_files": ",".join(self.extracted_files) if self.extracted_files else "",
            "extracted_functions": ",".join(self.extracted_functions) if self.extracted_functions else "",
        }
    
    @classmethod
    def from_metadata(cls, metadata: dict, description: str = "") -> "IssueEntry":
        """
        Create an IssueEntry from vector database metadata.
        
        Args:
            metadata: Dictionary containing metadata fields
            description: The description text (stored as document in vector DB)
        
        Returns:
            An IssueEntry instance.
        """
        keywords_str = metadata.get("keywords", "")
        keywords = [k for k in keywords_str.split(",") if k] if keywords_str else []
        
        extracted_files_str = metadata.get("extracted_files", "")
        extracted_files = [f for f in extracted_files_str.split(",") if f] if extracted_files_str else []
        
        extracted_functions_str = metadata.get("extracted_functions", "")
        extracted_functions = [f for f in extracted_functions_str.split(",") if f] if extracted_functions_str else []
        
        return cls(
            issue_id=metadata.get("issue_id", ""),
            title=metadata.get("title", ""),
            description=description,
            component=metadata.get("component") or None,
            keywords=keywords,
            file_path=metadata.get("file_path", ""),
            content_hash=metadata.get("content_hash", ""),
            function_name=metadata.get("function_name") or None,
            extracted_files=extracted_files,
            extracted_functions=extracted_functions,
        )
    
    def compute_content_hash(self) -> str:
        """
        Compute SHA-256 hash of the issue content.
        
        This is used to detect if an issue's content has changed
        since it was last ingested.
        
        Returns:
            SHA-256 hash string prefixed with "sha256:".
        """
        content = f"{self.title}\n{self.description}\n{self.component or ''}"
        hash_value = hashlib.sha256(content.encode()).hexdigest()
        return f"sha256:{hash_value}"
    
    def __str__(self) -> str:
        """Return a human-readable string representation."""
        return f"IssueEntry(rdar://{self.issue_id}): {self.title}"


@dataclass
class DedupeMatch:
    """
    Represents a potential duplicate match between an issue and an existing issue.
    
    Attributes:
        issue_id: Matched issue ID
        issue_title: Matched issue title
        similarity_score: Similarity score between 0.0 and 1.0
        issue_url: URL to the issue (rdar://...)
        match_reason: Brief explanation of why this is a match
        issue_description: Description/summary of the matched issue
    """
    issue_id: str
    issue_title: str
    similarity_score: float
    issue_url: str = ""
    match_reason: str = ""
    issue_description: str = ""
    
    def __post_init__(self):
        """Set default issue URL if not provided."""
        if not self.issue_url:
            self.issue_url = f"rdar://{self.issue_id}"
        if not self.match_reason:
            self.match_reason = self._generate_default_reason()
    
    def _generate_default_reason(self) -> str:
        """Generate a default match reason based on similarity score."""
        if self.similarity_score > 0.9:
            return "Very high similarity - likely duplicate"
        elif self.similarity_score > 0.8:
            return "High similarity - probable duplicate"
        elif self.similarity_score > 0.7:
            return "Moderate similarity - possible duplicate"
        else:
            return "Low similarity - may be related"
    
    @property
    def similarity_percentage(self) -> int:
        """Return similarity score as a percentage (0-100)."""
        return int(self.similarity_score * 100)
    
    @property
    def confidence_level(self) -> str:
        """
        Return a confidence level string based on similarity score.
        
        Returns:
            One of: "high", "moderate", "low"
        """
        if self.similarity_score > 0.8:
            return "high"
        elif self.similarity_score > 0.7:
            return "moderate"
        else:
            return "low"
    
    def __str__(self) -> str:
        """Return a human-readable string representation."""
        return f"DedupeMatch({self.issue_url}, {self.similarity_percentage}%): {self.issue_title}"
    
    def __lt__(self, other: "DedupeMatch") -> bool:
        """Enable sorting by similarity score (descending)."""
        return self.similarity_score > other.similarity_score


@dataclass
class HybridMatch:
    """
    Represents a match with hybrid scoring from multiple signals.
    
    This class combines file path matching, function name matching,
    and cosine similarity into a single hybrid score for more accurate
    duplicate detection.
    
    Attributes:
        issue_id: Matched issue ID
        issue_title: Matched issue title
        issue_url: URL to the issue (rdar://...)
        issue_description: Description/summary of the matched issue
        file_path_score: Score from file path matching (0.0 - 1.0)
        function_name_score: Score from function name matching (0.0 - 1.0)
        cosine_similarity_score: Score from semantic similarity (0.0 - 1.0)
        hybrid_score: Combined weighted score (0.0 - 1.0)
        match_reasons: List of human-readable match reasons
    """
    issue_id: str
    issue_title: str
    issue_url: str
    issue_description: str
    
    # Individual scores (0.0 - 1.0)
    file_path_score: float = 0.0
    function_name_score: float = 0.0
    cosine_similarity_score: float = 0.0
    
    # Combined hybrid score
    hybrid_score: float = 0.0
    
    # Match details for transparency
    match_reasons: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        """Set default issue URL if not provided."""
        if not self.issue_url:
            self.issue_url = f"rdar://{self.issue_id}"
    
    @property
    def confidence_level(self) -> str:
        """Return confidence level based on hybrid score."""
        if self.hybrid_score > 0.85:
            return "very_high"
        elif self.hybrid_score > 0.70:
            return "high"
        elif self.hybrid_score > 0.55:
            return "moderate"
        else:
            return "low"
    
    @property
    def hybrid_percentage(self) -> int:
        """Return hybrid score as a percentage (0-100)."""
        return int(self.hybrid_score * 100)
    
    @property
    def file_path_percentage(self) -> int:
        """Return file path score as a percentage (0-100)."""
        return int(self.file_path_score * 100)
    
    @property
    def function_name_percentage(self) -> int:
        """Return function name score as a percentage (0-100)."""
        return int(self.function_name_score * 100)
    
    @property
    def cosine_similarity_percentage(self) -> int:
        """Return cosine similarity score as a percentage (0-100)."""
        return int(self.cosine_similarity_score * 100)
    
    def to_dedupe_match(self) -> DedupeMatch:
        """
        Convert to a DedupeMatch for backward compatibility.
        
        Returns:
            A DedupeMatch instance with the hybrid score as similarity_score.
        """
        return DedupeMatch(
            issue_id=self.issue_id,
            issue_title=self.issue_title,
            similarity_score=self.hybrid_score,
            issue_url=self.issue_url,
            match_reason="; ".join(self.match_reasons) if self.match_reasons else "",
            issue_description=self.issue_description,
        )
    
    def __str__(self) -> str:
        """Return a human-readable string representation."""
        return f"HybridMatch({self.issue_url}, {self.hybrid_percentage}%): {self.issue_title}"
    
    def __lt__(self, other: "HybridMatch") -> bool:
        """Enable sorting by hybrid score (descending)."""
        return self.hybrid_score > other.hybrid_score
