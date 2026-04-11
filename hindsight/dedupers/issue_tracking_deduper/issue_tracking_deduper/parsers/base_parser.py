"""
Abstract base class for report parsers.

This module defines the interface that all report parsers must implement,
enabling support for multiple report formats.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from ..deduper.issue import Issue


class BaseReportParser(ABC):
    """
    Abstract base class for report parsers.
    
    All report parsers must inherit from this class and implement
    the required methods to parse their specific report format.
    """
    
    @abstractmethod
    def can_parse(self, report_path: Path) -> bool:
        """
        Check if this parser can handle the given report.
        
        Args:
            report_path: Path to the report file.
        
        Returns:
            True if this parser can handle the report, False otherwise.
        """
        pass
    
    @abstractmethod
    def parse(self, report_path: Path) -> List[Issue]:
        """
        Parse the report and return a list of issues.
        
        Args:
            report_path: Path to the report file.
        
        Returns:
            List of Issue objects extracted from the report.
        """
        pass
    
    @abstractmethod
    def get_format_name(self) -> str:
        """
        Return the name of the format this parser handles.
        
        Returns:
            A human-readable name for the report format.
        """
        pass
    
    def get_report_metadata(self, report_path: Path) -> dict:
        """
        Extract metadata from the report (optional).
        
        Args:
            report_path: Path to the report file.
        
        Returns:
            Dictionary containing report metadata (e.g., title, date, etc.).
        """
        return {}


class ParserRegistry:
    """
    Registry of available report parsers.
    
    This class maintains a list of registered parsers and provides
    methods to find the appropriate parser for a given report.
    """
    
    def __init__(self):
        """Initialize the parser registry."""
        self._parsers: List[BaseReportParser] = []
    
    def register(self, parser: BaseReportParser) -> None:
        """
        Register a parser.
        
        Args:
            parser: The parser instance to register.
        """
        self._parsers.append(parser)
    
    def get_parser(self, report_path: Path) -> Optional[BaseReportParser]:
        """
        Find a parser that can handle the given report.
        
        Args:
            report_path: Path to the report file.
        
        Returns:
            A parser that can handle the report, or None if no parser is found.
        """
        for parser in self._parsers:
            if parser.can_parse(report_path):
                return parser
        return None
    
    def get_all_parsers(self) -> List[BaseReportParser]:
        """
        Get all registered parsers.
        
        Returns:
            List of all registered parser instances.
        """
        return list(self._parsers)
    
    def get_supported_formats(self) -> List[str]:
        """
        Get names of all supported formats.
        
        Returns:
            List of format names supported by registered parsers.
        """
        return [parser.get_format_name() for parser in self._parsers]


# Global default registry
_default_registry: Optional[ParserRegistry] = None


def get_default_registry() -> ParserRegistry:
    """
    Get the default parser registry.
    
    The registry is lazily initialized with all built-in parsers.
    
    Returns:
        The default ParserRegistry instance.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = ParserRegistry()
        # Register built-in parsers
        _register_builtin_parsers(_default_registry)
    return _default_registry


def _register_builtin_parsers(registry: ParserRegistry) -> None:
    """
    Register all built-in parsers with the registry.
    
    Args:
        registry: The registry to register parsers with.
    """
    # Import here to avoid circular imports
    from .html_report_parser import StaticIntelligenceParser
    
    registry.register(StaticIntelligenceParser())


def parse_report(report_path: Path) -> List[Issue]:
    """
    Parse a report using the appropriate parser from the default registry.
    
    Args:
        report_path: Path to the report file.
    
    Returns:
        List of Issue objects extracted from the report.
    
    Raises:
        ValueError: If no parser can handle the report.
    """
    registry = get_default_registry()
    parser = registry.get_parser(report_path)
    
    if parser is None:
        supported = registry.get_supported_formats()
        raise ValueError(
            f"No parser available for report: {report_path}\n"
            f"Supported formats: {', '.join(supported) if supported else 'none'}"
        )
    
    return parser.parse(report_path)
