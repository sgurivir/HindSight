"""
Parsers package for parsing issue files and HTML reports.

This package provides:
- IssueParser: Parse issue markdown files
- BaseReportParser: Abstract base class for report parsers
- StaticIntelligenceParser: Parser for StaticIntelligence HTML reports
- GenericHTMLParser: Fallback parser for generic HTML reports
- ParserRegistry: Registry of available parsers
- FormatValidationError: Exception for format validation failures
"""

from .issue_parser import IssueParser, parse_issue_file, parse_issue_directory
from .base_parser import (
    BaseReportParser,
    ParserRegistry,
    get_default_registry,
    parse_report,
)
from .html_report_parser import (
    StaticIntelligenceParser,
    GenericHTMLParser,
    FormatValidationError,
)

__all__ = [
    # Issue parsing
    'IssueParser',
    'parse_issue_file',
    'parse_issue_directory',
    # Report parsing
    'BaseReportParser',
    'ParserRegistry',
    'get_default_registry',
    'parse_report',
    'StaticIntelligenceParser',
    'GenericHTMLParser',
    'FormatValidationError',
]
